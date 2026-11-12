"""Owner-fenced UIF integration through the native publisher invocation wire.

The wire and stopped-child receipt follow native_harness_runtime's
consume_boundary/invocation_transaction and native_publisher_invocation's
accepted-source path at migration reference 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e.
The existing owner supplies the lock and journal. This module creates no store.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
from threading import Thread
from typing import Literal, Protocol
from uuid import UUID

from .ledger import RowKey, TERMINAL_LINKS
from .source import SourceHold, exact_path, file_digest, oid
from .stokowski import LedgerAdmission, PolledIssue, UIF


PUBLISHER_MAX_WORK_SECONDS = 600
PUBLISHER_CLEANUP_SECONDS = 2
# The default caller wait also allows for launcher startup and durable writes.
PUBLISHER_CALL_SECONDS = PUBLISHER_MAX_WORK_SECONDS + PUBLISHER_CLEANUP_SECONDS + 30


def require(condition, reason):
    if not condition:
        raise SourceHold(reason)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def identifier(value):
    return isinstance(value, str) and bool(value) and value.strip() == value


def uuid(value):
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def fingerprint(value):
    return sha256(wire(value)).hexdigest()


def record_digest(value):
    return sha256(wire(value) + b"\n").hexdigest()


def unique_json_object(items):
    result = dict(items)
    require(len(items) == len(result), "uif_publisher_duplicate_response_key")
    return result


@dataclass(frozen=True)
class PublicationRequest:
    admission: LedgerAdmission
    issue: PolledIssue
    source_base: str
    source_head: str
    source_tree: str
    source_branch: str
    source_sha256: str  # Native handoff source fingerprint, distinct from the row digest.
    policy_sha256: str
    unit_sha256: str
    packet_sha256: str
    capacity_lease_id: str
    validation_operation_id: str
    validation_job_id: str
    validation_receipt_sha256: str
    review_operation_id: str
    reviewer: str
    review_receipt_sha256: str
    readiness_invocation_id: str
    readiness_receipt_sha256: str
    publisher_operation_id: str
    invocation_id: str
    effect_mode: str = "step"

    def __post_init__(self):
        self.admission.require_implementation()
        require(self.issue.project_id == UIF.project_id and self.issue.project_slug == UIF.project_slug
                and uuid(self.issue.issue_id) and identifier(self.issue.identifier)
                and self.issue.identifier not in TERMINAL_LINKS,
                "uif_publication_issue")
        require(all(oid(value) for value in (self.source_base, self.source_head, self.source_tree))
                and self.source_head != self.source_base and identifier(self.source_branch),
                "uif_publication_source")
        require(all(digest(value) for value in (
            self.source_sha256, self.policy_sha256, self.unit_sha256, self.packet_sha256,
            self.validation_operation_id, self.validation_receipt_sha256,
            self.review_receipt_sha256, self.readiness_receipt_sha256, self.publisher_operation_id,
        )) and all(uuid(value) for value in (
            self.validation_job_id, self.invocation_id, self.readiness_invocation_id,
        )) and self.readiness_invocation_id != self.invocation_id
                and self.effect_mode in ("step", "publish")
                and self.reviewer != self.admission.owner
                and all(identifier(value) for value in (
                    self.reviewer, self.review_operation_id, self.capacity_lease_id,
                )), "uif_publication_pins")
        require(self.source_sha256 == fingerprint({
            "repository": UIF.repository, "branch": self.source_branch, "base": self.source_base,
            "head": self.source_head, "tree": self.source_tree, "target": UIF.accepted_branch,
            "policy_sha256": self.policy_sha256,
        }), "uif_publication_handoff_source")

    @property
    def key(self):
        return RowKey(self.admission.store_id, self.admission.row_id)

    def envelope(self, *, observe):
        # Recovery changes only the outer mode. The original inner request survives.
        return {
            "version": 1, "mode": "observe" if observe else "invoke",
            "invocation_id": self.invocation_id,
            "request": {
                "version": 1, "mode": self.effect_mode, "operation_id": self.publisher_operation_id,
                "issue_id": self.issue.issue_id, "project_id": self.issue.project_id,
                "owner": self.admission.owner, "unit_sha256": self.unit_sha256,
                "packet_sha256": self.packet_sha256, "source_sha256": self.source_sha256,
            },
        }


@dataclass(frozen=True)
class AcceptedPublication:
    request: PublicationRequest
    head: str
    tree: str
    pr_url: str
    invocation_receipt_sha256: str
    accepted_source_sha256: str


class OwnerTransaction(Protocol):
    """An existing, exclusive owner transaction scoped to the selected row.

    The fence must cover the effect and check active owner/epoch, stage,
    cancellation, selected source, lease and applicable counterpart readiness. Readback uses completed authority
    for the exact historic binding, independent of a later owner or row revision.
    """

    def read_intent(self, key: RowKey, original_operation_id: str) -> PublicationRequest | None:
        """Return None only for authoritative absence in the original journal."""

    def create_intent(self, request: PublicationRequest) -> None:
        """Durable create-only CAS before invoking the publisher.

        Compare current claim, source, validation job and independent review
        receipts, and the existing publisher readiness/stopped receipt. Reject
        if another invocation or known/uncertain effect already occupies this
        original admission or publisher operation. No fallback journal is permitted.
        """

    def recheck(self, request: PublicationRequest) -> None:
        """Recheck current ownership before any continuation under this fence."""


# Recovery may read a fenced prior epoch if the existing authority binds its
# original request to the current recovery owner. Completion is read-only.
FenceMode = Literal["active", "recovery", "completion"]
Fence = Callable[[PublicationRequest, FenceMode], AbstractContextManager[OwnerTransaction]]


@dataclass(frozen=True)
class PublisherReceiptPaths:
    """Registered canonical locations. The supervised command reads their bodies."""

    accepted_root: Path
    claim_root: Path

    def __post_init__(self):
        require(all(isinstance(path, Path) and path.is_absolute() and ".." not in path.parts
                    for path in (self.accepted_root, self.claim_root)), "uif_publisher_receipt_roots")

    def accepted(self, request):
        return str(self.accepted_root / "by-operation" / f"{request.publisher_operation_id}.json")

    def invocation(self, request):
        return str(self.claim_root / f"transaction-{request.publisher_operation_id}"
                   / f"native-invocation-{request.invocation_id}.json")


@dataclass(frozen=True)
class PublisherCommand:
    """Installed owner-approved supervised native publisher, using stdin/stdout JSON."""

    executable: Path
    executable_sha256: str
    closure_sha256: str
    paths: PublisherReceiptPaths
    fixed_args: tuple[str, ...] = ()
    timeout: int = PUBLISHER_CALL_SECONDS

    def call(self, envelope):
        path = exact_path(self.executable)
        require(digest(self.executable_sha256) and digest(self.closure_sha256)
                and type(self.timeout) is int and self.timeout > 0
                and type(self.fixed_args) is tuple and all(isinstance(arg, str) for arg in self.fixed_args)
                and file_digest(path) == self.executable_sha256, "uif_publisher_command_pin")
        payload = wire(envelope)
        require(len(payload) <= 16_384, "uif_publisher_request_size")
        try:
            # The registered launcher owns the effect and its original journal.
            # A caller deadline must never SIGKILL it before it can reap its
            # worker and persist the terminal receipt. File-backed streams can
            # outlive this wait without blocking a detached launcher on pipes.
            with TemporaryFile() as request_file, TemporaryFile() as response_file:
                request_file.write(payload)
                request_file.seek(0)
                process = subprocess.Popen(
                    [str(path), *self.fixed_args], stdin=request_file, stdout=response_file,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, cwd="/",
                )
                try:
                    status = process.wait(timeout=self.timeout)
                finally:
                    if process.returncode is None:
                        Thread(target=process.wait, daemon=True).start()
                require(status == 0, "uif_publisher_outcome_unknown")
                response_file.seek(0)
                raw = response_file.read(65_537)
                require(len(raw) <= 65_536, "uif_publisher_response_size")
                return json.loads(raw, object_pairs_hook=unique_json_object)
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            raise SourceHold("uif_publisher_outcome_unknown") from error


@dataclass(frozen=True)
class GitReadback:
    """Read the canonical ref independently in a disposable object database.

    A Path selects an explicitly configured local repository for offline
    qualification. The production URL is the canonical UIF origin from the
    registered profile. The owner can bind an installed read-only askpass
    helper for HTTPS authentication.
    """

    remote: str | Path = UIF.origin
    askpass: Path | None = None

    def verify(self, publication):
        local = isinstance(self.remote, Path)
        require(local or self.remote == UIF.origin, "uif_publication_destination")
        remote = str(exact_path(self.remote)) if local else self.remote
        with TemporaryDirectory(prefix="uif-publication-readback-") as path:
            self._git(path, "init", "--bare", "--quiet")
            self._git(path, "fetch", "--no-tags", "--quiet", "--", remote,
                      f"refs/heads/{UIF.accepted_branch}:refs/heads/readback")
            self._git(path, "merge-base", "--is-ancestor", publication.head, "refs/heads/readback")
            actual_tree = self._git(path, "rev-parse", publication.head + "^{tree}")
            parent_line = self._git(path, "rev-list", "--parents", "-n", "1", publication.head)
            request = publication.request
            require(actual_tree == publication.tree == request.source_tree
                    and parent_line.split() == [publication.head, request.source_base, request.source_head],
                    "uif_publication_readback_source")

    def _git(self, cwd, *args):
        local = isinstance(self.remote, Path)
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null"}
        if not local and self.askpass is not None:
            env["GIT_ASKPASS"] = str(exact_path(self.askpass))
            if "HOME" in os.environ:
                env["HOME"] = os.environ["HOME"]
        protocol = "file" if local else "https"
        try:
            return subprocess.run(
                ["/usr/bin/git", "-c", "core.hooksPath=/dev/null",
                 "-c", "http.followRedirects=false", "-c", "protocol.allow=never",
                 "-c", f"protocol.{protocol}.allow=always", *args],
                cwd=cwd, env=env, capture_output=True, text=True, timeout=90, check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError) as error:
            raise SourceHold("uif_publication_remote_readback") from error


def _stopped_child(terminal):
    return (isinstance(terminal, dict)
            and all(terminal.get(key) is True for key in (
                "child_started", "subreaper_verified", "direct_child_reaped",
                "waitpid_echild", "child_tree_gone",
            ))
            and type(terminal.get("child_pid")) is int and terminal["child_pid"] > 0
            and type(terminal.get("child_starttime")) is int and terminal["child_starttime"] > 0)


def _verify_original_reconciliation(record):
    terminal = record["terminal"]
    if "reconciliation" not in terminal:
        return
    proof = terminal["reconciliation"]
    reason = "uif_publication_reconciliation_proof"
    require(isinstance(proof, dict) and set(proof) == {"original_terminal", "original_receipt_sha256"}, reason)
    original = proof["original_terminal"]
    require(_stopped_child(original) and "reconciliation" not in original
            and proof["original_receipt_sha256"] == record_digest({**record, "terminal": original}), reason)

    changed = {"response", "response_sha256", "evidence", "reconciliation"}
    if original.get("outcome") == "timed_out":
        intent = record["intent"]
        require(original["child_pid"] != intent["supervisor_pid"]
                and original["child_starttime"] >= intent["supervisor_starttime"]
                and all(key in original and original[key] is None for key in (
                    "response", "response_sha256", "evidence",
                )), reason)
        changed.add("outcome")
    else:
        response = {**terminal["response"], "status": "unknown"}
        require(original.get("outcome") == "succeeded"
                and original.get("response") == response
                and original.get("response_sha256") == fingerprint(response)
                and (original.get("evidence") is None or original["evidence"] == terminal.get("evidence")), reason)
    require({key: value for key, value in original.items() if key not in changed}
            == {key: value for key, value in terminal.items() if key not in changed}, reason)


def accepted_result(value, request, envelope, command):
    """Check original invocation identity, stopped proof and accepted source facet."""
    try:
        inner = envelope["request"]
        require(type(value["version"]) is int and value["version"] == 1
                and value["kind"] == "native-publisher-invocation-v1"
                and value["operation_id"] == request.publisher_operation_id
                and value["invocation_id"] == request.invocation_id
                and value["request_sha256"] == fingerprint(inner)
                and value["status"] == "complete", "uif_publication_unresolved")
        receipt = value["receipt"]
        record = receipt["body"]
        intent, terminal = record["intent"], record["terminal"]
        require(receipt["sha256"] == record_digest(record)
                and type(record["version"]) is int and record["version"] == 1
                and record["kind"] == "native-publisher-invocation-record-v1"
                and intent["operation_id"] == request.publisher_operation_id
                and intent["invocation_id"] == request.invocation_id
                and intent["request"] == inner and intent["request_sha256"] == fingerprint(inner)
                and intent["phase"] == "integration"
                and intent["closure_sha256"] == command.closure_sha256
                and type(intent["supervisor_pid"]) is int and intent["supervisor_pid"] > 0
                and type(intent["supervisor_starttime"]) is int and intent["supervisor_starttime"] > 0,
                "uif_publication_receipt_identity")
        response = terminal["response"]
        require(_stopped_child(terminal) and terminal["outcome"] == "succeeded"
                and terminal["response_sha256"] == fingerprint(response)
                and response == value["response"]
                and type(response["version"]) is int
                and response == {
                    **{key: val for key, val in inner.items() if key != "mode"},
                    "kind": "native-publisher-response-v1", "request_sha256": fingerprint(inner),
                    "phase": "integration", "status": response["status"],
                },
                "uif_publication_terminal_proof")
        require(response["status"] in ("ready", "pending", "unknown", "verified", "denied", "failed"),
                "uif_publication_response_status")
        require(response["status"] == "verified", "uif_publication_" + response["status"])
        _verify_original_reconciliation(record)
        accepted = value["accepted_source"]
        fact = accepted["fact"]
        evidence = terminal.get("evidence")
        require(isinstance(evidence, dict) and set(evidence) == {"pull_request", "merge_head"}
                and oid(evidence["merge_head"]), "uif_publication_terminal_evidence")
        pr = evidence["pull_request"]
        require(isinstance(pr, dict) and set(pr) == {"repository_id", "number", "pull_request_id"}
                and all(type(item) is int and item > 0 for item in pr.values())
                and fact["head"] == evidence["merge_head"]
                and fact["pr_url"] == f"https://github.com/{UIF.repository}/pull/{pr['number']}",
                "uif_publication_terminal_evidence")
        require(accepted["reference"] == {
                    "path": command.paths.accepted(request), "sha256": record_digest(fact),
                }
                and fact["schema"] == "uif-accepted-project-source/1"
                and fact["repository"] == UIF.repository and fact["branch"] == UIF.accepted_branch
                and fact["base"] == request.source_base and fact["candidate_head"] == request.source_head
                and fact["parents"] == [request.source_base, request.source_head]
                and fact["tree"] == request.source_tree and fact["policy_sha256"] == request.policy_sha256
                and all(fact[k] == inner[k] for k in ("operation_id", "unit_sha256", "packet_sha256"))
                and fact["helper_receipt"] == {
                    "path": command.paths.invocation(request), "sha256": receipt["sha256"],
                }, "uif_publication_accepted_source")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, SourceHold):
            raise
        raise SourceHold("uif_publication_malformed_receipt") from error
    return AcceptedPublication(request, fact["head"], fact["tree"], fact["pr_url"],
                               receipt["sha256"], accepted["reference"]["sha256"])


@dataclass(frozen=True)
class Publisher:
    fence: Fence
    command: PublisherCommand
    remote: GitReadback

    def consume_boundary(self, request: PublicationRequest) -> AcceptedPublication:
        """Run one already-approved publication, or observe its original invocation."""
        with self.fence(request, "active") as owner:
            prior = owner.read_intent(request.key, request.admission.original_operation_id)
            if prior is not None:
                require(prior == request, "uif_publication_intent_drift")
                return self._transaction(request, owner, observe=True)
            owner.recheck(request)
            owner.create_intent(request)
            return self._transaction(request, owner, observe=False)

    def observe_original(self, request: PublicationRequest, owner: OwnerTransaction) -> AcceptedPublication:
        require(owner.read_intent(request.key, request.admission.original_operation_id) == request,
                "uif_publication_original_intent")
        return self._transaction(request, owner, observe=True)

    def _transaction(self, request, owner, *, observe):
        owner.recheck(request)
        envelope = request.envelope(observe=observe)
        value = self.command.call(envelope)
        publication = accepted_result(value, request, envelope, self.command)
        self.remote.verify(publication)
        owner.recheck(request)
        return publication
