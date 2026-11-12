"""Run real local Git readback with a deterministic GitHub CLI process."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import time

from test_publication import PublicationFixture, git
from uif.publication import fingerprint
from uif.publication_rpc import (
    CandidateSource, CanonicalGit, Executable, GitHubPullRequests, PublicationObservation, PublicationRPC,
    PullRequestCandidate, PullRequestPin,
)
from uif.source import SourceHold, file_digest
from uif.stokowski import UIF


FAKE_GH = """#!/usr/bin/python3
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ["GH_CONFIG_DIR"])
state_path = root / "state.json"
state = json.loads(state_path.read_text())
argv = sys.argv[1:]
assert argv[:4] == ["api", "--hostname", "github.com", "--method"]
method, endpoint = argv[4], argv[-1]
assert endpoint.startswith("/repos/maldous/uif/pulls")
payload = json.load(sys.stdin) if "--input" in argv else None
with (root / "requests.jsonl").open("a") as log:
    log.write(json.dumps([method, endpoint, payload]) + "\\n")

pr = state["pr"]
if method == "GET" and endpoint.endswith("/reviews"):
    assert "--paginate" in argv and "--slurp" in argv
    output = state["reviews"]
elif method == "GET" and endpoint.endswith("/comments"):
    assert "--paginate" in argv and "--slurp" in argv
    output = state["comments"]
elif method == "GET" and "/pulls?" in endpoint:
    assert "head=maldous%3Afeature%2Ftest" in endpoint and "base=develop" in endpoint
    assert "--paginate" in argv and "--slurp" in argv
    output = [[pr]] if state["exists"] else [[]]
elif method == "GET" and endpoint.endswith("/123"):
    output = pr
elif method == "POST" and endpoint.endswith("/pulls"):
    assert payload == state["expected_create"] and not state["exists"]
    if state["create"] == "refused":
        sys.exit(1)
    state["exists"] = True
    state_path.write_text(json.dumps(state))
    if state["create"] == "lost":
        sys.exit(1)
    output = pr
elif method == "PUT" and endpoint.endswith("/123/merge"):
    assert payload == {"sha": pr["head"]["sha"], "merge_method": "merge"}
    assert not pr["merged"]
    if state["merge"] == "refused":
        sys.exit(1)
    subprocess.run(
        ["/usr/bin/git", "-C", state["work"], "push", "--quiet", state["bare"],
         state["actual_merge"] + ":refs/heads/develop"],
        check=True,
    )
    pr.update(state="closed", merged=True, merged_at="2026-11-12T00:00:00Z",
              merge_commit_sha=state["actual_merge"])
    state_path.write_text(json.dumps(state))
    if state["merge"] == "lost":
        sys.exit(1)
    output = {"merged": True, "sha": state["actual_merge"]}
else:
    sys.exit(2)
print(json.dumps(output))
"""


class RPCFixture(PublicationFixture):
    stage_candidate = True

    def setUp(self):
        super().setUp()
        git(self.work, "push", "--quiet", str(self.bare), f"{self.merge}:refs/pull/123/merge")
        if self.stage_candidate:
            git(self.work, "push", "--quiet", str(self.bare), "feature/test")
        self.pr = PullRequestPin(321, 123, 789)
        self.candidate = PullRequestCandidate(
            self.request.source_base, self.request.source_head, self.request.source_tree,
            self.request.source_branch, "Reviewed UIF change", "Bound to the original UIF operation.",
        )
        self.config = self.root / "github-config"
        self.config.mkdir(mode=0o700)
        self.fake_gh = self.root / "fake-gh"
        self.fake_gh.write_text(FAKE_GH)
        self.fake_gh.chmod(0o700)
        git_path = Path("/usr/bin/git").resolve()
        self.rpc = PublicationRPC(
            GitHubPullRequests(Executable(self.fake_gh, file_digest(self.fake_gh)), self.config),
            CanonicalGit(Executable(git_path, file_digest(git_path)), remote=self.bare),
        )
        repo = {"id": self.pr.repository_id, "full_name": UIF.repository}
        self.state_path = self.config / "state.json"
        self.state = {
            "pr": {
                "id": self.pr.pull_request_id, "number": self.pr.number, "html_url": self.pr.url,
                "title": self.candidate.title, "body": self.candidate.body,
                "head": {"sha": self.request.source_head, "ref": self.request.source_branch, "repo": repo},
                "base": {"sha": self.request.source_base, "ref": UIF.accepted_branch, "repo": repo},
                "state": "open", "draft": False, "merged": False, "mergeable": True,
                "merge_commit_sha": self.merge,
            },
            "reviews": [[{"id": 908, "user": {"id": 77}, "state": "APPROVED",
                          "commit_id": self.request.source_head}]],
            "comments": [[]],
            "exists": True,
            "actual_merge": self.merge,
            "work": str(self.work), "bare": str(self.bare), "merge": "success", "create": "success",
            "expected_create": {"head": self.request.source_branch, "base": UIF.accepted_branch,
                                "title": self.candidate.title, "body": self.candidate.body, "draft": False},
        }
        self.review_sha = fingerprint({
            "head": self.request.source_head, "base": self.request.source_base,
            "reviews": [{"id": 908, "reviewer_id": 77, "state": "APPROVED", "head": self.request.source_head}],
            "findings": [], "qualification": "facts-only-requires-independent-owner-review-and-thread-resolution",
        })
        self.valid_until = int(time.time()) + 60
        self.write_state()

    def candidate_bundle(self, path):
        git(self.work, "bundle", "create", str(path), "refs/heads/feature/test",
            "^" + self.request.source_base)
        path.chmod(0o600)
        content = git(self.work, "show", f"{self.request.source_head}:fixture") + "\n"
        return CandidateSource(self.request.source_base, self.request.source_head, self.request.source_tree,
                               self.request.source_branch, file_digest(path),
                               {"fixture": sha256(content.encode()).hexdigest()}, [], [])

    def write_state(self):
        self.state_path.write_text(json.dumps(self.state))

    def writes(self):
        log = self.config / "requests.jsonl"
        if not log.exists():
            return []
        return [item for line in log.read_text().splitlines()
                if (item := json.loads(line))[0] in ("POST", "PUT")]

    def publish(self, *, valid_until=None):
        return self.rpc.publish(self.request, self.pr, test_merge_head=self.merge,
                                review_facts_sha256=self.review_sha,
                                valid_until=self.valid_until if valid_until is None else valid_until)


class PublicationRPCTests(RPCFixture):
    def test_existing_prepared_pr_is_observed_without_creation(self):
        self.assertEqual(self.rpc.prepare_pull_request(self.candidate, self.pr.repository_id,
                                                       valid_until=self.valid_until), ("verified", self.pr))
        self.assertEqual(self.writes(), [])

    def test_creates_once_and_reads_back_after_lost_post_reply(self):
        self.state.update(exists=False, create="lost")
        self.write_state()
        self.assertEqual(self.rpc.prepare_pull_request(self.candidate, self.pr.repository_id,
                                                       valid_until=self.valid_until), ("verified", self.pr))
        self.assertEqual(self.writes(), [["POST", "/repos/maldous/uif/pulls", self.state["expected_create"]]])

    def test_create_refuses_a_drifted_candidate_before_mutation(self):
        self.state.update(exists=False)
        self.write_state()
        changed = replace(self.candidate, source_tree="2" * 40)
        self.assertEqual(self.rpc.prepare_pull_request(changed, self.pr.repository_id,
                                                       valid_until=self.valid_until), ("denied", None))
        self.assertEqual(self.writes(), [])

    def test_merge_uses_ordinary_pr_endpoint_with_exact_head(self):
        self.assertEqual(self.publish(), PublicationObservation("verified", self.merge))
        self.assertEqual(self.writes(), [["PUT", "/repos/maldous/uif/pulls/123/merge",
                                         {"sha": self.request.source_head, "merge_method": "merge"}]])

    def test_lost_put_reply_reads_back_the_original_effect(self):
        self.state["merge"] = "lost"
        self.write_state()
        self.assertEqual(self.publish(), PublicationObservation("verified", self.merge))
        self.assertEqual(len(self.writes()), 1)

    def test_refused_or_lost_effect_does_not_retry(self):
        self.state["merge"] = "refused"
        self.write_state()
        self.assertEqual(self.publish(), PublicationObservation("unknown"))
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/develop"), self.request.source_base)

    def test_drifted_actual_merge_is_held_after_effect(self):
        newer = git(self.work, "commit-tree", self.request.source_tree,
                    "-p", self.request.source_base, "-m", "competing accepted base")
        actual = git(self.work, "commit-tree", self.request.source_tree,
                     "-p", newer, "-p", self.request.source_head, "-m", "drifted actual merge")
        self.state["actual_merge"] = actual
        self.write_state()
        self.assertEqual(self.publish(), PublicationObservation("unknown", effect_hint=actual))
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/develop"), actual)
        self.assertEqual(len(self.writes()), 1)

    def test_holds_drifted_review_facts_and_expired_authority_before_put(self):
        self.assertEqual(self.publish(valid_until=1), PublicationObservation("denied"))
        self.state["reviews"].append([{"id": 909, "user": {"id": 88},
                                      "state": "CHANGES_REQUESTED", "commit_id": self.request.source_head}])
        self.write_state()
        self.assertEqual(self.publish(), PublicationObservation("denied"))
        self.assertEqual(self.writes(), [])

    def test_existing_pr_with_changed_metadata_is_held_without_creating_another(self):
        self.state["pr"]["body"] = "unexpected metadata"
        self.write_state()
        self.assertEqual(self.rpc.prepare_pull_request(self.candidate, self.pr.repository_id,
                                                       valid_until=self.valid_until), ("denied", None))
        self.assertEqual(self.writes(), [])


class CandidateRPCTests(RPCFixture):
    stage_candidate = False

    def setUp(self):
        super().setUp()
        self.bundle = self.root / "candidate.bundle"
        self.source = self.candidate_bundle(self.bundle)

    def test_lost_push_reply_reads_back_exact_candidate(self):
        class LostReplyGit(CanonicalGit):
            def create_candidate(self, directory, source):
                super().create_candidate(directory, source)
                raise SourceHold("lost_reply")

        self.rpc = replace(self.rpc, git=LostReplyGit(self.rpc.git.executable, remote=self.bare))
        self.assertEqual(self.rpc.publish_candidate(self.source, self.bundle, valid_until=self.valid_until),
                         PublicationObservation("verified", self.request.source_head))
        self.assertEqual(git(self.bare, "rev-parse", self.source.ref), self.request.source_head)
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/develop"), self.request.source_base)

    def test_candidate_ref_created_by_competing_actor_is_not_overwritten(self):
        base = self.request.source_base
        bare = self.bare

        class RacingGit(CanonicalGit):
            def create_candidate(self, directory, source):
                git(bare, "update-ref", source.ref, base)
                super().create_candidate(directory, source)

        self.rpc = replace(self.rpc, git=RacingGit(self.rpc.git.executable, remote=bare))
        self.assertEqual(self.rpc.publish_candidate(self.source, self.bundle, valid_until=self.valid_until),
                         PublicationObservation("unknown"))
        self.assertEqual(git(bare, "rev-parse", self.source.ref), base)

    def test_unapproved_changed_bytes_and_exclusions_refuse_before_push(self):
        blob = git(self.work, "rev-parse", f"{self.request.source_head}:fixture")
        for source in (replace(self.source, changed_files={"fixture": "1" * 64}),
                       replace(self.source, excluded_blobs=[blob]),
                       replace(self.source, excluded_paths=["fixture"]),
                       replace(self.source, bundle_sha256="2" * 64)):
            self.assertEqual(self.rpc.publish_candidate(source, self.bundle, valid_until=self.valid_until),
                             PublicationObservation("denied"))
        refs = git(self.bare, "for-each-ref", "--format=%(refname)", "refs/heads")
        self.assertEqual(refs, "refs/heads/develop")
