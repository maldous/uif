"""Independent read-only review of the original validated UIF contribution.

Ports the native review receipt and Pool qualification from the retained
Stokowski worker at OffMenu 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e.
"""

import asyncio
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3

from .source import SourceHold, exact_path, file_digest, git
from .validation import (
    StageBinding, ValidationSpec, _Observation, _Stage, decode, digest, identity, read_evidence, sha, wire,
)


ORIGIN = "native-readonly-review-v1"


@dataclass(frozen=True)
class ReviewSpec:
    validation: ValidationSpec
    validation_receipt_sha256: str
    reviewer: str
    lease: str
    policy: Path
    evidence_directory: Path
    policy_sha256: str
    pool_binding_json: bytes
    first_submission: bool = False

    @property
    def binding(self):
        return self.validation.binding

    def __post_init__(self):
        pool = decode(self.pool_binding_json)
        attempts = self.binding.dispatch.workspace.parent.parent / "attempts"
        directory = exact_path(self.evidence_directory)
        if (directory.parent != attempts
                or directory.is_relative_to(self.binding.candidate)
                or (self.first_submission and directory.name != "review-" + self.binding.dispatch.issue.identifier)):
            raise SourceHold("uif_review_original_receipt_path")
        if (not identity(self.reviewer) or self.reviewer == self.binding.dispatch.ledger.owner
                or not identity(self.lease)
                or not all(digest(v) for v in (self.validation_receipt_sha256, self.policy_sha256))
                or type(self.first_submission) is not bool
                or not isinstance(pool, dict) or wire(pool) != self.pool_binding_json
                or any(pool.get(k) != v for k, v in {
                    "owner": self.reviewer, "mode": "native-review-read-only",
                    "read_roots": [str(self.binding.candidate)], "write_roots": [],
                    "head": self.binding.head, "branch": self.binding.branch,
                }.items())
                or not digest(pool.get("source_sha256"))
                or not digest(pool.get("task_sha256"))):
            raise SourceHold("uif_review_read_only_binding")


@dataclass(frozen=True)
class ReviewEvidence:
    binding: StageBinding
    reviewer: str
    lease: str
    native_session_id: str
    validation_receipt_sha256: str
    receipt_sha256: str
    receipt: bytes

    @property
    def operation_key(self):
        return self.binding.key("review")


class NativeReviewBackend:
    """Launch the registered finite worker and qualify its existing Pool and receipts.

    The executable is the deployment's guarded read-only review worker. Its
    existing supervisor owns admission, provider isolation and child cleanup.
    This adapter never writes the Pool or considers a launcher exit a verdict.
    """

    def __init__(self, executable, *, executable_sha256, evidence_uid, pool_database):
        if not digest(executable_sha256) or type(evidence_uid) is not int or evidence_uid < 0:
            raise SourceHold("uif_review_registration")
        self.executable = exact_path(executable)
        self.executable_sha256 = executable_sha256
        self.evidence_uid = evidence_uid
        self.pool_database = exact_path(pool_database)
        self.registration = dict(executable=self.executable, executable_sha256=executable_sha256,
                                 evidence_uid=evidence_uid, pool_database=self.pool_database)
        self._children = set()

    @staticmethod
    def _runtime(spec):
        return spec.binding.dispatch.workspace.parent.parent

    @staticmethod
    def _request(spec):
        return dict(kind=ORIGIN, policy=dict(path=str(spec.policy), sha256=spec.policy_sha256))

    def _read(self, path, *, limit=1048576):
        return read_evidence(path, uid=self.evidence_uid, limit=limit)

    def _lease(self, lease):
        path = exact_path(self.pool_database)
        info = path.stat()
        if not path.is_file() or info.st_uid != self.evidence_uid or info.st_mode & 0o022:
            raise SourceHold("uif_review_pool_custody")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            row = db.execute("SELECT binding,fingerprint,status,outcome FROM leases WHERE id=?", (lease,)).fetchone()
        if row is None:
            return None
        binding = decode(row[0])
        if sha(wire(binding)) != row[1]:
            raise SourceHold("uif_review_pool_fingerprint")
        return binding, row[2], row[3]

    def qualify(self, spec):
        binding = spec.binding
        dispatch = binding.dispatch
        admission = dispatch.ledger
        runtime = self._runtime(spec)
        if (not exact_path(spec.policy).is_relative_to(runtime)
                or spec.policy.is_relative_to(binding.candidate)
                or file_digest(self.executable) != self.executable_sha256):
            raise SourceHold("uif_review_runtime_binding")
        raw = self._read(spec.policy)
        if sha(raw) != spec.policy_sha256:
            raise SourceHold("uif_review_policy_drift")
        policy = decode(raw)
        pool = decode(spec.pool_binding_json)
        source = policy.get("source", {})
        if (any(policy.get(k) != v for k, v in {
                "schema": ORIGIN, "project_id": dispatch.issue.project_id,
                "issue": dispatch.issue.identifier, "owner": admission.owner,
                "reviewer_owner": spec.reviewer, "current_runtime": str(runtime),
                "validation_identity": spec.validation.operation_key,
                "stage_identity": spec.validation.operation_key,
                "validation_receipt": {
                    "sha256": spec.validation_receipt_sha256,
                    "windmill_job_id": spec.validation.windmill_job_id,
                    "runner_job_id": spec.validation.runner_job_id,
                },
                "unit_binding": {
                    "store_id": admission.store_id, "row_id": admission.row_id,
                    "operation_id": dispatch.operation_id, "owner": admission.owner, "epoch": admission.epoch,
                    "dispatch_sha256": dispatch.receipt_sha256, "coding_sha256": binding.coding_receipt_sha256,
                },
                "require_stopped_child": True,
            }.items())
                or any(source.get(k) != v for k, v in {
                    "root": str(binding.candidate), "head": binding.head, "tree": binding.tree,
                    "base": binding.base, "branch": binding.branch, "files_sha256": pool["source_sha256"],
                }.items())
                or type(policy.get("timeout_seconds")) is not int
                or not 1 <= policy["timeout_seconds"] <= 600
                or not isinstance(policy.get("acceptance"), dict)
                or not isinstance(policy.get("resources"), dict)
                or pool.get("resources") != policy["resources"]
                or policy.get("provider") != "codex-chatgpt"
                or not identity(policy.get("model")) or not identity(policy.get("effort"))):
            raise SourceHold("uif_review_policy_binding")
        files = {
            name: file_digest(binding.candidate / name)
            for name in git(binding.candidate, "ls-tree", "-r", "--name-only", "-z", binding.head).split("\0")
            if name
        }
        outputs = source.get("outputs")
        if (sha(wire(files)) != pool["source_sha256"] or not isinstance(outputs, dict) or not outputs
                or any(files.get(name) != value for name, value in outputs.items())):
            raise SourceHold("uif_review_source_bytes")
        excluded_owners = policy.get("excluded_owners")
        excluded_sessions = policy.get("excluded_sessions")
        if (not isinstance(excluded_owners, list) or not isinstance(excluded_sessions, list)
                or not all(identity(value) for value in (*excluded_owners, *excluded_sessions))
                or spec.reviewer in excluded_owners or admission.owner not in excluded_owners):
            raise SourceHold("uif_review_author_exclusion")
        refs = policy.get("author_receipts")
        original = dict(path=str(binding.coding_receipt), sha256=binding.coding_receipt_sha256)
        if not isinstance(refs, list) or original not in refs:
            raise SourceHold("uif_review_original_author_receipt")
        for ref in refs:
            path = exact_path(ref["path"])
            if not path.is_relative_to(runtime) or path.is_relative_to(binding.candidate):
                raise SourceHold("uif_review_author_receipt_path")
            author_raw = self._read(path)
            author = decode(author_raw)
            parent = author.get("parent_binding", {})
            if (sha(author_raw) != ref.get("sha256")
                    or author.get("origin") != "native-coding" or author.get("status") != "succeeded"
                    or author.get("child_tree_gone") is not True
                    or parent.get("owner") not in excluded_owners
                    or path == binding.coding_receipt and parent.get("owner") != admission.owner
                    or author.get("session_id") not in excluded_sessions
                    or not identity(author.get("lease")) or author["lease"] == spec.lease
                    or not source.get("outputs") or author.get("output_sha256") != source["outputs"]
                    or author.get("binding_sha256") != sha(wire(parent))
                    or self._lease(author["lease"]) != (parent, "finished", "succeeded")):
                raise SourceHold("uif_review_author_receipt_binding")
        return policy

    async def inspect(self, spec):
        policy = self.qualify(spec)
        pool = decode(spec.pool_binding_json)
        lease = self._lease(spec.lease)
        if lease is not None and lease[0] != pool:
            return _Observation("quarantined")
        directory = exact_path(spec.evidence_directory)
        try:
            raw = self._read(directory / "receipt.json")
        except FileNotFoundError:
            return _Observation("unknown") if lease is not None or directory.exists() else None
        result = decode(raw)
        if (any(result.get(k) != v for k, v in {
                "origin": ORIGIN, "issue": spec.binding.dispatch.issue.identifier,
                "lease": spec.lease, "parent_binding": pool, "binding_sha256": sha(wire(pool)),
                "request": self._request(spec), "policy_sha256": sha(wire(policy)),
            }.items()) or result.get("accepted_delivery") is not False):
            return _Observation("quarantined")
        if result.get("child_tree_gone") is not True or lease is None:
            return _Observation("unknown")
        status = result.get("status")
        stopped_failure = status in ("failed", "cancelled") and lease[1:] == ("failed", status)
        if not stopped_failure and (status != "succeeded" or lease[1] != "finished"):
            return _Observation("unknown")
        try:
            intent = decode(self._read(directory / "intent.json"))
        except FileNotFoundError:
            return _Observation("unknown")
        except ValueError:
            return _Observation("quarantined")
        if (not isinstance(intent, dict) or intent.get("lease") != spec.lease
                or intent.get("binding") != pool or intent.get("request") != self._request(spec)
                or intent.get("phase") != "spawn-pending"
                or not isinstance(history := intent.get("history"), list)
                or history[-2:] != [{"phase": "admitted"}, {"phase": "spawn-pending"}]):
            return _Observation("quarantined")
        if stopped_failure:
            return _Observation("failed", raw)
        if (lease[2] != "succeeded" or result.get("source_before") != pool["source_sha256"]
                or result.get("source_after") != pool["source_sha256"]
                or result.get("turn_completed") is not True
                or not identity(result.get("session_id")) or result["session_id"] in policy["excluded_sessions"]):
            return _Observation("quarantined")
        try:
            artifact_raw = self._read(directory / "final-message.txt")
            events_raw = self._read(directory / "stdout.jsonl", limit=16777216)
        except FileNotFoundError:
            return _Observation("unknown")
        artifact = decode(artifact_raw)
        if (sha(artifact_raw) != result.get("artifact_sha256") or artifact != result.get("artifact")
                or sha(events_raw) != result.get("events_sha256") or not self._events(events_raw, result["session_id"])
                or not isinstance(artifact, dict) or set(artifact) != {"schema", "source", "verdict", "findings"}
                or artifact["schema"] != ORIGIN or artifact["source"] != policy["source"]
                or artifact["verdict"] not in ("accept", "rework")
                or not isinstance(artifact["findings"], list)
                or not all(identity(finding) for finding in artifact["findings"])
                or (artifact["verdict"] == "accept" and artifact["findings"])):
            return _Observation("quarantined")
        return _Observation("succeeded" if artifact["verdict"] == "accept" else "rework", raw)

    @staticmethod
    def _events(raw, session_id):
        started = False
        completed = False
        for line in raw.splitlines():
            try:
                event = decode(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started":
                if started or event.get("thread_id") != session_id:
                    return False
                started = True
            if started and event.get("type") == "turn.completed":
                completed = True
        return completed

    async def submit(self, spec):
        self.qualify(spec)
        runtime = self._runtime(spec)
        directory = exact_path(spec.evidence_directory)
        # A previously written worker intent is evidence of a possible effect
        # even when its Pool row and original receipt are both missing.
        if directory.exists() or directory.is_symlink():
            raise SourceHold("uif_review_original_attempt_exists")
        proc = await asyncio.create_subprocess_exec(
            str(self.executable), "--runtime", str(runtime),
            "--issue", spec.binding.dispatch.issue.identifier,
            "--native-review-policy", str(spec.policy), "--native-review-policy-sha256", spec.policy_sha256,
            cwd=runtime, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        waiter = asyncio.create_task(proc.wait())
        self._children.add(waiter)
        waiter.add_done_callback(self._children.discard)


class ReviewAdapter(_Stage):
    stage = "review"

    def __init__(self, journal, fence, backend, *, validation):
        super().__init__(journal, fence, backend)
        if validation.journal.path != journal.path or validation.fence is not fence:
            raise SourceHold("uif_review_validation_registration")
        self.validation = validation

    def _qualify(self, spec):
        super()._qualify(spec)
        if self.validation._accepted(spec.validation)[1] != spec.validation_receipt_sha256:
            raise SourceHold("uif_review_validation_receipt_binding")
        self.backend.qualify(spec)

    async def accepted(self, spec):
        async with self.fence(self.stage, spec):
            self._qualify(spec)
            raw, receipt_sha256 = self._accepted(spec)
            observed = await self.backend.inspect(spec)
            if observed is None or observed.status != "succeeded" or observed.receipt != raw:
                raise SourceHold("uif_review_original_receipt_drift")
            return ReviewEvidence(spec.binding, spec.reviewer, spec.lease,
                                  decode(raw)["session_id"], spec.validation_receipt_sha256, receipt_sha256, raw)
