"""Protected GitHub PR merge with exact preflight and canonical Git readback.

The retained merge API conditions the write on the PR head. It cannot
atomically compare the base. The qualified base is checked immediately before
the single request, and any different merge parents or tree hold acceptance.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlencode

from .publication import PublicationRequest, digest, fingerprint, require, unique_json_object, wire
from .source import SourceHold, branch_name, exact_path, file_digest, oid
from .stokowski import UIF


TARGET = f"refs/heads/{UIF.accepted_branch}"
OBSERVED = "refs/heads/uif-readback"
CANDIDATE_MERGE = "refs/uif/candidate-merge"
CANDIDATE = "refs/uif/candidate"


@dataclass(frozen=True)
class Executable:
    path: Path
    sha256: str

    def verify(self):
        path = exact_path(self.path)
        require(digest(self.sha256) and file_digest(path) == self.sha256
                and os.access(path, os.X_OK), "uif_publisher_executable_pin")
        return str(path)


@dataclass(frozen=True)
class PullRequestPin:
    repository_id: int
    number: int
    pull_request_id: int

    def __post_init__(self):
        require(all(type(value) is int and value > 0 for value in (
            self.repository_id, self.number, self.pull_request_id,
        )), "uif_publisher_pr_pin")

    @property
    def url(self):
        return f"https://github.com/{UIF.repository}/pull/{self.number}"

    @property
    def merge_ref(self):
        return f"refs/pull/{self.number}/merge"


@dataclass(frozen=True)
class PullRequestCandidate:
    source_base: str
    source_head: str
    source_tree: str
    source_branch: str
    title: str
    body: str

    def __post_init__(self):
        require(all(oid(value) for value in (self.source_base, self.source_head, self.source_tree))
                and self.source_base != self.source_head and branch_name(self.source_branch)
                and isinstance(self.title, str) and 0 < len(self.title.strip()) <= 256
                and isinstance(self.body, str) and len(self.body.encode()) <= 8192,
                "uif_publisher_pr_candidate")


@dataclass(frozen=True)
class CandidateSource:
    source_base: str
    source_head: str
    source_tree: str
    source_branch: str
    bundle_sha256: str
    changed_files: dict[str, str | None]
    excluded_paths: list[str]
    excluded_blobs: list[str]

    def __post_init__(self):
        def safe_path(path):
            if not isinstance(path, str) or not path or any(ord(c) < 32 for c in path):
                return False
            value = PurePosixPath(path)
            return (bool(value.parts) and not value.is_absolute() and value.as_posix() == path
                    and all(part not in (".", "..", ".git") for part in value.parts))

        require(all(oid(value) for value in (self.source_base, self.source_head, self.source_tree))
                and self.source_head != self.source_base and branch_name(self.source_branch)
                and digest(self.bundle_sha256) and isinstance(self.changed_files, dict)
                and bool(self.changed_files)
                and all(safe_path(path) and (pin is None or digest(pin))
                        for path, pin in self.changed_files.items())
                and isinstance(self.excluded_paths, list) and all(safe_path(path) for path in self.excluded_paths)
                and isinstance(self.excluded_blobs, list) and all(oid(blob) for blob in self.excluded_blobs),
                "uif_candidate_source_pins")

    @property
    def ref(self):
        return f"refs/heads/{self.source_branch}"

    @property
    def exclusions_sha256(self):
        return fingerprint({"paths": self.excluded_paths, "blobs": self.excluded_blobs})


@dataclass(frozen=True)
class PublicationObservation:
    status: str
    merge_head: str | None = None
    effect_hint: str | None = None


def run(argv, *, cwd, env, timeout, payload=None):
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            **({"input": payload} if payload is not None else {"stdin": subprocess.DEVNULL}),
            timeout=timeout, check=True,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceHold("uif_publisher_transport_unresolved") from error


@dataclass(frozen=True)
class GitHubPullRequests:
    """Read PR metadata using the registered existing gh authentication."""

    executable: Executable
    config_dir: Path
    timeout: int = 15

    def _api(self, endpoint, *, method="GET", pages=False, payload=None):
        env = {
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GH_PROMPT_DISABLED": "1",
            "GH_CONFIG_DIR": str(exact_path(self.config_dir)), "GH_NO_UPDATE_NOTIFIER": "1",
        }
        argv = [self.executable.verify(), "api", "--hostname", "github.com", "--method", method]
        if pages:
            argv.extend(("--paginate", "--slurp"))
        if payload is not None:
            argv.extend(("--input", "-"))
        argv.append(f"/repos/{UIF.repository}/pulls{endpoint}")
        raw = run(argv, cwd="/", env=env, timeout=self.timeout, payload=payload)
        try:
            require(0 < len(raw) <= 2_097_152, "uif_publisher_pr_read_size")
            return json.loads(raw, object_pairs_hook=unique_json_object)
        except (ValueError, RecursionError) as error:
            raise SourceHold("uif_publisher_pr_read_shape") from error

    def read(self, pin):
        return self._api(f"/{pin.number}")

    def find_prepared(self, candidate, repository_id):
        query = urlencode({"state": "all", "head": f"{UIF.repository.split('/')[0]}:{candidate.source_branch}",
                           "base": UIF.accepted_branch})
        pages = self._api("?" + query, pages=True)
        require(isinstance(pages, list) and all(isinstance(page, list) for page in pages),
                "uif_publisher_pr_list_shape")
        matches = [pr for page in pages for pr in page]
        require(len(matches) <= 1, "uif_publisher_pr_lookup_ambiguous")
        if not matches:
            return None
        pin = self.verify_prepared(matches[0], candidate, repository_id)
        require(self.verify_prepared(self.read(pin), candidate, repository_id) == pin,
                "uif_publisher_prepared_pr_readback")
        return pin

    def verify_prepared(self, pr, candidate, repository_id):
        try:
            pin = PullRequestPin(repository_id, pr["number"], pr["id"])
            verify_pr_identity(pr, pin, candidate)
            require(pr["state"] == "open" and pr["draft"] is False
                    and pr["base"]["sha"] == candidate.source_base
                    and pr["title"] == candidate.title and pr["body"] == candidate.body,
                    "uif_publisher_prepared_pr_drift")
            return pin
        except (KeyError, TypeError) as error:
            raise SourceHold("uif_publisher_prepared_pr_shape") from error

    def create(self, candidate, repository_id):
        pr = self._api("", method="POST", payload=wire({
            "head": candidate.source_branch, "base": UIF.accepted_branch,
            "title": candidate.title, "body": candidate.body, "draft": False,
        }))
        return self.verify_prepared(pr, candidate, repository_id)

    def exact_head_review(self, pin, request):
        reviews = self._api(f"/{pin.number}/reviews", pages=True)
        comments = self._api(f"/{pin.number}/comments", pages=True)
        require(all(isinstance(pages, list) and all(isinstance(page, list) for page in pages)
                    for pages in (reviews, comments)), "uif_publisher_pr_review_shape")
        try:
            selected = [
                {"id": item["id"], "reviewer_id": item["user"]["id"], "state": item["state"],
                 "head": request.source_head}
                for page in reviews for item in page if item["commit_id"] == request.source_head
            ]
            findings = [
                item for page in comments for item in page if item["commit_id"] == request.source_head
            ]
            require(not findings and all(
                type(item["id"]) is int and item["id"] > 0
                and type(item["reviewer_id"]) is int and item["reviewer_id"] > 0
                and item["state"] in ("APPROVED", "COMMENTED", "DISMISSED") for item in selected
            ), "uif_publisher_pr_review_held")
        except (KeyError, TypeError) as error:
            raise SourceHold("uif_publisher_pr_review_shape") from error
        return fingerprint({
            "head": request.source_head, "base": request.source_base, "reviews": selected,
            "findings": [], "qualification": "facts-only-requires-independent-owner-review-and-thread-resolution",
        })

    def merge(self, pin, expected_head):
        require(oid(expected_head), "uif_publisher_pr_head_pin")
        response = self._api(f"/{pin.number}/merge", method="PUT", payload=wire({
            "sha": expected_head, "merge_method": "merge",
        }))
        require(isinstance(response, dict) and response.get("merged") is True and oid(response.get("sha")),
                "uif_publisher_merge_reply")
        return response["sha"]


@dataclass(frozen=True)
class CanonicalGit:
    executable: Executable
    askpass: Executable | None = None
    # A local repository is used in deterministic component qualification.
    remote: str | Path = UIF.origin
    timeout: int = 30
    candidate_askpass: Executable | None = None

    def command(self, cwd, *args, raw=False, upload=False, bundle=False):
        local = isinstance(self.remote, Path)
        require(local or self.remote == UIF.origin, "uif_publisher_canonical_remote")
        env = {
            "PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
        }
        require(not upload or local or self.candidate_askpass is not None, "uif_candidate_upload_identity")
        auth = self.candidate_askpass if upload else self.askpass
        if not local and not bundle and auth is not None:
            env["GIT_ASKPASS"] = auth.verify()
        protocol = "file" if local or bundle else "https"
        argv = [
            self.executable.verify(), "-c", "core.hooksPath=/dev/null",
            "-c", "http.followRedirects=false", "-c", "protocol.allow=never",
            "-c", f"protocol.{protocol}.allow=always", "-c", "gc.auto=0",
            "-c", "maintenance.auto=false", *args,
        ]
        result = run(argv, cwd=cwd, env=env, timeout=self.timeout)
        return result if raw else result.decode("ascii").strip()

    def location(self):
        return str(exact_path(self.remote)) if isinstance(self.remote, Path) else self.remote

    def fetch(self, directory, *refs):
        self.command(directory, "fetch", "--no-tags", "--quiet", "--",
                     self.location(), *refs)

    def tips(self, directory, *refs, optional=()):
        raw = self.command(directory, "ls-remote", "--refs", "--", self.location(), *refs, *optional)
        rows = [line.split() for line in raw.splitlines()]
        require(all(len(row) == 2 and oid(row[0]) for row in rows), "uif_publisher_remote_ref")
        tips = {name: sha for sha, name in rows}
        require(len(tips) == len(rows) and set(refs) <= tips.keys() <= set(refs) | set(optional),
                "uif_publisher_remote_ref")
        return tips

    def create_candidate(self, directory, source):
        # An empty expected value allows creation only while the ref is absent.
        require(source.ref != TARGET and branch_name(source.source_branch) and oid(source.source_head),
                "uif_candidate_target")
        self.command(directory, "push", "--porcelain", "--no-follow-tags",
                     f"--force-with-lease={source.ref}:", "--",
                     self.location(), f"{source.source_head}:{source.ref}", upload=True)



def verify_pr_identity(pr, pin, request):
    head, base = pr["head"], pr["base"]
    require(type(pr["id"]) is int and pr["id"] == pin.pull_request_id
            and type(pr["number"]) is int and pr["number"] == pin.number
            and pr["html_url"] == pin.url
            and head["sha"] == request.source_head and head["ref"] == request.source_branch
            and base["ref"] == UIF.accepted_branch
            and all(ref["repo"]["full_name"] == UIF.repository
                    and type(ref["repo"]["id"]) is int
                    and ref["repo"]["id"] == pin.repository_id for ref in (head, base)),
            "uif_publisher_pr_identity")


def verify_pr(pr, pin, request, *, merged):
    try:
        verify_pr_identity(pr, pin, request)
        require(pr["merged"] is merged, "uif_publisher_pr_outcome")
        if merged:
            require(pr["state"] == "closed" and isinstance(pr["merged_at"], str) and bool(pr["merged_at"]),
                    "uif_publisher_pr_terminal")
        else:
            require(pr["state"] == "open" and pr["draft"] is False
                    and pr["base"]["sha"] == request.source_base
                    and pr["mergeable"] is True, "uif_publisher_pr_readiness")
        require(oid(pr["merge_commit_sha"]), "uif_publisher_pr_merge_object")
        return pr["merge_commit_sha"]
    except (KeyError, TypeError) as error:
        raise SourceHold("uif_publisher_pr_read_shape") from error


@dataclass(frozen=True)
class PublicationRPC:
    github: GitHubPullRequests
    git: CanonicalGit

    def publish_candidate(self, source, bundle_path, *, valid_until):
        """Publish one exact commit and reconcile a lost reply by canonical readback."""
        with TemporaryDirectory(prefix="uif-native-candidate-") as directory:
            try:
                self.git.command(directory, "init", "--quiet", "--bare", "--template=")
                self.git.fetch(directory, f"{TARGET}:{OBSERVED}")
                require(self.git.command(directory, "rev-parse", OBSERVED) == source.source_base,
                        "uif_candidate_remote_base")
                self.verify_candidate_bundle(directory, source, bundle_path)
                tips = self.git.tips(directory, TARGET, optional=(source.ref,))
                require(tips[TARGET] == source.source_base
                        and tips.get(source.ref) in (None, source.source_head),
                        "uif_candidate_before_state")
                if source.ref in tips:
                    self.verify_candidate_readback(directory, source)
                    return PublicationObservation("verified", source.source_head)
                require(time.time() < valid_until, "uif_publisher_claim_expired")
            except (SourceHold, UnicodeError):
                return PublicationObservation("denied")

            try:
                self.git.create_candidate(directory, source)
            except (SourceHold, UnicodeError):
                pass  # Read back the original ref, without repeating the push.
            try:
                self.verify_candidate_readback(directory, source)
            except (SourceHold, UnicodeError):
                return PublicationObservation("unknown")
            return PublicationObservation("verified", source.source_head)

    def observe_candidate(self, source):
        with TemporaryDirectory(prefix="uif-candidate-readback-") as directory:
            self.git.command(directory, "init", "--quiet", "--bare", "--template=")
            self.verify_candidate_readback(directory, source)
            return source.source_head

    def verify_candidate_readback(self, directory, source):
        require(self.git.tips(directory, source.ref)[source.ref] == source.source_head,
                "uif_candidate_after_state")
        self.git.fetch(directory, f"{source.ref}:{CANDIDATE}")
        require(self.git.command(directory, "rev-parse", CANDIDATE) == source.source_head
                and self.git.command(directory, "rev-parse", CANDIDATE + "^{tree}") == source.source_tree
                and self.git.command(directory, "rev-list", "--parents", "-n", "1", CANDIDATE).split()
                == [source.source_head, source.source_base], "uif_candidate_readback")

    def verify_candidate_bundle(self, directory, source, bundle_path):
        require(file_digest(bundle_path) == source.bundle_sha256, "uif_candidate_bundle_digest")
        # Only the pinned branch enters a fresh object database. A single
        # parent at the already published base excludes unpublished history.
        heads = self.git.command(directory, "bundle", "list-heads", str(bundle_path))
        require(heads.splitlines() == [f"{source.source_head} {source.ref}"],
                "uif_candidate_bundle_heads")
        self.git.command(directory, "fetch", "--no-tags", "--quiet", "--", str(bundle_path),
                         f"{source.ref}:{CANDIDATE}", bundle=True)
        require(self.git.command(directory, "rev-list", "--parents", "-n", "1", CANDIDATE).split()
                == [source.source_head, source.source_base]
                and self.git.command(directory, "rev-parse", CANDIDATE + "^{tree}") == source.source_tree,
                "uif_candidate_bundle_source")
        paths = self.git.command(directory, "diff-tree", "--no-commit-id", "--name-only",
                                 "--no-renames", "-r", "-z", source.source_base, CANDIDATE, raw=True)
        changed = paths.decode("utf-8").split("\0")[:-1]
        require(set(changed) == source.changed_files.keys(), "uif_candidate_change_set")
        new_objects = self.git.command(directory, "rev-list", "--objects", "--no-object-names",
                                       CANDIDATE, "^" + source.source_base)
        require(not set(new_objects.splitlines()).intersection(source.excluded_blobs),
                "uif_candidate_excluded_blob")
        for path in changed:
            require(not any(path == denied or path.startswith(denied + "/")
                            for denied in source.excluded_paths), "uif_candidate_excluded_path")
            entry = self.git.command(directory, "--literal-pathspecs", "ls-tree", "-z", CANDIDATE,
                                     "--", path, raw=True)
            expected = source.changed_files[path]
            if expected is None:
                require(not entry, "uif_candidate_deletion")
                continue
            meta, _, name = entry.partition(b"\t")
            info = meta.split()
            require(name == path.encode() + b"\0" and len(info) == 3
                    and info[0] in (b"100644", b"100755") and info[1] == b"blob",
                    "uif_candidate_regular_file")
            content = self.git.command(directory, "cat-file", "blob", info[2].decode(), raw=True)
            require(sha256(content).hexdigest() == expected, "uif_candidate_file_digest")

    def prepare_pull_request(self, candidate, repository_id, *, valid_until):
        """Observe before create, then verify the canonical original PR."""
        try:
            with TemporaryDirectory(prefix="uif-native-pr-") as directory:
                self.git.command(directory, "init", "--quiet", "--bare", "--template=")
                head_ref = f"refs/heads/{candidate.source_branch}"
                self.git.fetch(directory, f"{TARGET}:{OBSERVED}", f"{head_ref}:{CANDIDATE}")
                require(self.git.command(directory, "rev-parse", OBSERVED) == candidate.source_base
                        and self.git.command(directory, "rev-parse", CANDIDATE) == candidate.source_head
                        and self.git.command(directory, "rev-parse", CANDIDATE + "^{tree}") == candidate.source_tree,
                        "uif_publisher_prepared_pr_source")
                prior = self.github.find_prepared(candidate, repository_id)
                if prior is not None:
                    return "verified", prior
                require(self.git.tips(directory, TARGET, head_ref) == {
                    TARGET: candidate.source_base, head_ref: candidate.source_head,
                }, "uif_publisher_pr_create_ref_drift")
                require(time.time() < valid_until, "uif_publisher_claim_expired")
        except (SourceHold, UnicodeError):
            return "denied", None

        result = None
        try:
            result = self.github.create(candidate, repository_id)
        except (SourceHold, UnicodeError):
            pass
        try:
            observed = self.observe_prepared_pr(candidate, repository_id, result)
        except (SourceHold, UnicodeError):
            return "unknown", result
        return "verified", observed

    def observe_prepared_pr(self, candidate, repository_id, expected=None):
        observed = self.github.find_prepared(candidate, repository_id)
        require(observed is not None and (expected is None or observed == expected),
                "uif_publisher_pr_create_readback")
        return observed

    def qualify(self, directory, request, pr, test_merge_head, review_facts_sha256):
        self.git.command(directory, "init", "--quiet", "--bare", "--template=")
        test_merge = verify_pr(self.github.read(pr), pr, request, merged=False)
        require(test_merge == test_merge_head, "uif_publisher_pr_merge_pin")
        self.git.fetch(directory, f"{TARGET}:{OBSERVED}", f"{pr.merge_ref}:{CANDIDATE_MERGE}")
        require(self.git.command(directory, "rev-parse", OBSERVED) == request.source_base
                and self.git.command(directory, "rev-parse", CANDIDATE_MERGE) == test_merge,
                "uif_publisher_remote_preimage")
        self.verify_object(directory, request, test_merge)
        require(self.github.exact_head_review(pr, request) == review_facts_sha256,
                "uif_publisher_pr_review_facts_drift")
        require(verify_pr(self.github.read(pr), pr, request, merged=False) == test_merge,
                "uif_publisher_pr_drift")
        require(self.git.tips(directory, TARGET) == {TARGET: request.source_base},
                "uif_publisher_remote_base_drift")

    def readiness(self, request, pr, *, test_merge_head, review_facts_sha256, valid_until):
        try:
            with TemporaryDirectory(prefix="uif-native-readiness-") as directory:
                self.qualify(directory, request, pr, test_merge_head, review_facts_sha256)
                require(time.time() < valid_until, "uif_publisher_claim_expired")
        except (SourceHold, UnicodeError):
            return "denied"
        return "ready"

    def publish(self, request: PublicationRequest, pr: PullRequestPin, *,
                test_merge_head: str, review_facts_sha256: str, valid_until: int) -> PublicationObservation:
        """Use the normal PR merge endpoint once, then verify the original effect."""
        with TemporaryDirectory(prefix="uif-native-publication-") as directory:
            try:
                self.qualify(directory, request, pr, test_merge_head, review_facts_sha256)
                require(time.time() < valid_until, "uif_publisher_claim_expired")
            except (SourceHold, UnicodeError):
                return PublicationObservation("denied")

            merge_reply = None
            try:
                merge_reply = self.github.merge(pr, request.source_head)
            except (SourceHold, UnicodeError):
                # A transport failure can follow an accepted update. Read the
                # exact effect below, with no second PUT.
                pass

            try:
                merge = self.read_merge(directory, request, pr, merge_reply)
            except (SourceHold, UnicodeError):
                return PublicationObservation("unknown", effect_hint=merge_reply)
            return PublicationObservation("verified", merge)

    def observe_merge(self, request, pr, expected=None):
        with TemporaryDirectory(prefix="uif-merge-readback-") as directory:
            self.git.command(directory, "init", "--quiet", "--bare", "--template=")
            return self.read_merge(directory, request, pr, expected)

    def read_merge(self, directory, request, pr, expected):
        merge = verify_pr(self.github.read(pr), pr, request, merged=True)
        require(expected is None or expected == merge, "uif_publisher_pr_readback_drift")
        self.git.fetch(directory, f"+{TARGET}:{OBSERVED}")
        self.git.command(directory, "merge-base", "--is-ancestor", merge, OBSERVED)
        self.verify_object(directory, request, merge)
        return merge

    def verify_object(self, directory, request, merge):
        actual = self.git.command(directory, "rev-list", "--parents", "-n", "1", merge).split()
        tree = self.git.command(directory, "rev-parse", merge + "^{tree}")
        candidate_tree = self.git.command(directory, "rev-parse", request.source_head + "^{tree}")
        require(actual == [merge, request.source_base, request.source_head]
                and tree == candidate_tree == request.source_tree,
                "uif_publisher_exact_merge")
