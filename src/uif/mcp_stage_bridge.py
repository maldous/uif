"""Consume original stage evidence supplied by the registered UIF owner."""

from dataclasses import dataclass
import os

from uif.host_jobs import lease_id, sha as host_sha, wire as host_wire
from uif.ledger import RowKey
from uif.publication import PublicationRequest, PublisherReceiptPaths, fingerprint, record_digest
from uif.source import SourceHold, exact_path, oid
from uif.stokowski import UIF
from uif.validation import decode, digest, invocation_uuid, read_evidence, sha, wire


def _observation(authority, task, field, schema, *, publication_envelope=None):
    if task.get("store_id") != authority.store_id:
        raise SourceHold("uif_mcp_stage_owner")
    entry, runtime = authority.admission._entry(RowKey(task["store_id"], task["row_id"]))
    if (task.get("owner"), task.get("epoch")) != (authority.owner, authority.epoch):
        # Publisher has already rechecked the historical fence. Bind this
        # catalogue read to the exact durable intent again before using its pin.
        if publication_envelope is None or publication_envelope.get("mode") != "observe":
            raise SourceHold("uif_mcp_stage_owner")
        original = PublicationRequest.from_ledger_record(entry.get("publication_intent"),
                                                         snapshot_path=authority.snapshot_path)
        if original.envelope(observe=True) != publication_envelope:
            raise SourceHold("uif_mcp_stage_original_intent")
    ref = entry.get(field)
    if ref is None:
        raise FileNotFoundError(field)
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise SourceHold("uif_mcp_stage_reference")
    path = exact_path(ref["path"])
    if path == runtime or not path.is_relative_to(runtime):
        raise SourceHold("uif_mcp_stage_custody")
    raw = read_evidence(path, uid=os.geteuid(), limit=1048576)
    if sha(raw) != ref["sha256"]:
        raise SourceHold("uif_mcp_stage_pin")
    record = decode(raw)
    if (not isinstance(record, dict) or record.get("schema") != schema
            or record.get("task") != task):
        raise SourceHold("uif_mcp_stage_original")
    return record


@dataclass(frozen=True)
class _JobResponse:
    content: bytes
    status_code: int = 200

    def raise_for_status(self):
        return None


@dataclass(frozen=True)
class MCPValidationClient:
    """Supply the existing Windmill verifier with the original owner observation."""

    authority: object
    key: RowKey
    descriptor_sha256: str

    async def get(self, path, *, follow_redirects):
        entry, runtime = self.authority.admission._entry(self.key)
        current = self.authority.admission._claim(self.key, entry, runtime, None)
        if current is None:
            raise SourceHold("uif_mcp_validation_claim")
        task = current["ledger"]
        record = _observation(self.authority, task, "mcp_validation_observation", "uif-mcp-validation/1")
        required = {"schema", "task", "job", "runner_receipt"}
        if not required <= record.keys() or set(record) - required - {"runner_release"}:
            raise SourceHold("uif_mcp_validation_shape")
        job = record["job"]
        if (not isinstance(job, dict) or not invocation_uuid(job.get("id"))
                or path != f"/w/foo/jobs_u/get/{job['id']}" or follow_redirects is not False):
            raise SourceHold("uif_mcp_validation_job")
        if job.get("type") == "CompletedJob":
            job = {**job, "result": self._verified_result(job, record["runner_receipt"],
                                                         record.get("runner_release"))}
        return _JobResponse(wire(job))

    def _verified_result(self, job, receipt, release):
        flat = job.get("result")
        if isinstance(flat, str):
            flat = decode(flat)
        if receipt is None:
            raise FileNotFoundError("original runner receipt")
        args = job.get("args")
        if (not isinstance(flat, dict) or not isinstance(args, dict) or not isinstance(receipt, dict)
                or not {"job_succeeded", "incident"} <= flat.keys()
                or {"job_succeeded", "incident"} & receipt.keys()):
            raise SourceHold("uif_mcp_validation_runner_receipt")
        identity = {
            "workspace": "foo", "namespace": "uif-foo-user-job/1",
            "job_id": args.get("job_id"), "request_sha256": args.get("request_sha256"),
            "descriptor_sha256": self.descriptor_sha256,
        }
        if any(key not in receipt or receipt[key] != value for key, value in identity.items()):
            raise SourceHold("uif_mcp_validation_runner_binding")
        terminal = receipt.get("status") in {"succeeded", "failed", "timed_out", "cancelled"}
        result = {**receipt, "job_succeeded": flat["job_succeeded"], "incident": flat["incident"]}
        if terminal:
            binding = self._terminal_binding(receipt, args, identity)
            # The host returns the same terminal bytes with reconciliation set
            # while its lease release is outstanding. Windmill retains that reply.
            pending = {**result, "reconcile_required": True, "job_succeeded": False}
            if wire(flat) == wire(pending):
                result["job_succeeded"] = receipt["status"] == "succeeded"
            elif wire(flat) != wire(result):
                raise SourceHold("uif_mcp_validation_runner_receipt")
            self._check_release(release, receipt, binding)
        elif wire(flat) != wire(result):
            raise SourceHold("uif_mcp_validation_runner_receipt")
        return result

    @staticmethod
    def _terminal_binding(receipt, args, identity):
        request_fields = ("mode", "resources", "timeout_seconds")
        pin_fields = ("command_sha256", "capacity_policy_sha256", "runner_sha256")
        if (receipt.get("lease_id") != lease_id(identity)
                or receipt.get("child_tree_gone") is not True
                or receipt.get("reconcile_required") is not False
                or any(name not in args or name not in receipt or wire(args[name]) != wire(receipt[name])
                       for name in request_fields)
                or any(not digest(receipt.get(name)) for name in pin_fields)):
            raise SourceHold("uif_mcp_validation_runner_lease")
        return {**identity, **{name: receipt[name] for name in (*request_fields, *pin_fields)}}

    @staticmethod
    def _check_release(release, receipt, binding):
        if release is None:
            raise FileNotFoundError("original runner lease release")
        expected = {
            "schema": "uif-shared-job-admission/1", "status": "finished",
            "binding_sha256": host_sha(host_wire(binding)), "lease_id": receipt["lease_id"],
            "receipt_sha256": host_sha(host_wire(receipt)),
        }
        outcome = "failed" if receipt["status"] == "timed_out" else receipt["status"]
        pool_status = "finished" if outcome == "succeeded" else "failed"
        if release not in (expected, {**expected, "outcome": outcome, "pool_status": pool_status}):
            raise SourceHold("uif_mcp_validation_runner_release")

    async def post(self, *args, **kwargs):
        raise SourceHold("uif_mcp_validation_owner_submission_only")


@dataclass(frozen=True)
class MCPPublisherCommand:
    """Read UID1000 publication evidence under the existing owner intent."""

    authority: object
    closure_sha256: str
    paths: PublisherReceiptPaths
    repository_id: int

    def call(self, envelope):
        inner = envelope["request"]
        task = inner["task"]
        try:
            record = _observation(self.authority, task, "mcp_publication_observation", "uif-mcp-publication/1",
                                  publication_envelope=envelope)
        except FileNotFoundError as error:
            raise SourceHold("uif_mcp_publication_owner_observation_pending") from error
        if (set(record) != {"schema", "task", "publisher_uid", "envelope", "response",
                            "invocation_reference", "accepted_reference"}
                or type(record["publisher_uid"]) is not int or record["publisher_uid"] != 1000
                or record["envelope"] not in ({**envelope, "mode": "invoke"},
                                              {**envelope, "mode": "observe"})):
            raise SourceHold("uif_mcp_publication_original")
        value = record["response"]
        if not isinstance(value, dict):
            raise SourceHold("uif_mcp_publication_response")
        if value.get("status") != "complete":
            raise SourceHold("uif_mcp_publication_owner_observation_pending")
        try:
            operation, invocation = inner["operation_id"], envelope["invocation_id"]
            receipt = value["receipt"]
            invocation_path = self.paths.claim_root / f"transaction-{operation}" / f"native-invocation-{invocation}.json"
            expected = {"path": str(invocation_path), "sha256": record_digest(receipt["body"])}
            if record["invocation_reference"] != expected or receipt["sha256"] != expected["sha256"]:
                raise SourceHold("uif_mcp_publication_native_journal")
            if value["response"]["status"] == "verified":
                accepted = value["accepted_source"]
                accepted_path = self.paths.accepted_root / "by-operation" / f"{operation}.json"
                expected = {"path": str(accepted_path), "sha256": record_digest(accepted["fact"])}
                pr = receipt["body"]["terminal"]["evidence"]["pull_request"]
                if (record["accepted_reference"] != expected or accepted["reference"] != expected
                        or type(pr["repository_id"]) is not int or pr["repository_id"] != self.repository_id):
                    raise SourceHold("uif_mcp_publication_native_journal")
        except (KeyError, TypeError, ValueError) as error:
            raise SourceHold("uif_mcp_publication_response") from error
        return value


@dataclass(frozen=True)
class MCPGitReadback:
    """Verify the owner's independent canonical Git observations."""

    authority: object
    repository_id: int

    def verify(self, publication):
        request = publication.request
        try:
            record = _observation(self.authority, request.task.to_record(), "mcp_canonical_readback",
                                  "uif-mcp-canonical-readback/1",
                                  publication_envelope=request.envelope(observe=True))
        except FileNotFoundError as error:
            raise SourceHold("uif_mcp_canonical_readback_pending") from error
        expected = {
            "schema": "uif-mcp-canonical-readback/1", "task": request.task.to_record(),
            "reader_uid": 1000, "repository": UIF.repository, "remote": UIF.origin,
            "repository_id": self.repository_id,
            "ref": f"refs/heads/{UIF.accepted_branch}",
            "publisher_operation_id": request.publisher_operation_id,
            "invocation_id": request.invocation_id,
            "request_sha256": fingerprint(request.envelope(observe=True)["request"]),
            "invocation_receipt_sha256": publication.invocation_receipt_sha256,
            "accepted_source_sha256": publication.accepted_source_sha256,
            "merge_head": publication.head, "tree": publication.tree,
            "parents": [request.source_base, request.source_head],
            "ancestor_exit_code": 0, "ref_head": record.get("ref_head"),
        }
        if (not oid(record.get("ref_head")) or type(record.get("ancestor_exit_code")) is not int
                or type(record.get("reader_uid")) is not int
                or type(record.get("repository_id")) is not int or record != expected):
            raise SourceHold("uif_mcp_canonical_readback_binding")
