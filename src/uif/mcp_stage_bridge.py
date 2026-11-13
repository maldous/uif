"""Consume original stage evidence supplied by the registered UIF owner."""

from dataclasses import dataclass
import os

from uif.host_jobs import lease_id
from uif.ledger import RowKey
from uif.publication import PublisherReceiptPaths, fingerprint, record_digest
from uif.source import SourceHold, exact_path, oid
from uif.stokowski import UIF
from uif.validation import decode, invocation_uuid, read_evidence, sha, wire


def _observation(authority, task, field, schema):
    if (task.get("store_id"), task.get("owner"), task.get("epoch")) != (
            authority.store_id, authority.owner, authority.epoch):
        raise SourceHold("uif_mcp_stage_owner")
    entry, runtime = authority.admission._entry(RowKey(task["store_id"], task["row_id"]))
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
        if set(record) != {"schema", "task", "job", "runner_receipt"}:
            raise SourceHold("uif_mcp_validation_shape")
        job = record["job"]
        if (not isinstance(job, dict) or not invocation_uuid(job.get("id"))
                or path != f"/w/foo/jobs_u/get/{job['id']}" or follow_redirects is not False):
            raise SourceHold("uif_mcp_validation_job")
        if job.get("type") == "CompletedJob":
            self._check_receipt(job, record["runner_receipt"])
        return _JobResponse(wire(job))

    def _check_receipt(self, job, receipt):
        flat = job.get("result")
        if isinstance(flat, str):
            flat = decode(flat)
        if receipt is None and (flat is None or isinstance(flat, dict) and flat.get("status") == "unknown"):
            raise FileNotFoundError("original runner receipt")
        args = job.get("args")
        if (not isinstance(flat, dict) or not isinstance(args, dict) or not isinstance(receipt, dict)
                or flat != {**receipt, "job_succeeded": flat.get("job_succeeded"), "incident": flat.get("incident")}
                or not {"job_succeeded", "incident"} <= flat.keys()
                or {"job_succeeded", "incident"} & receipt.keys()):
            raise SourceHold("uif_mcp_validation_runner_receipt")
        expected = {
            "workspace": "foo", "namespace": "uif-foo-user-job/1",
            "job_id": args.get("job_id"), "request_sha256": args.get("request_sha256"),
            "descriptor_sha256": self.descriptor_sha256,
        }
        if any(key not in receipt or receipt[key] != value for key, value in expected.items()):
            raise SourceHold("uif_mcp_validation_runner_binding")
        if receipt.get("status") in {"succeeded", "failed", "timed_out", "cancelled"}:
            if (receipt.get("lease_id") != lease_id(receipt)
                    or any(name not in args or name not in receipt or args[name] != receipt[name]
                           for name in ("mode", "resources", "timeout_seconds"))):
                raise SourceHold("uif_mcp_validation_runner_lease")

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
            record = _observation(self.authority, task, "mcp_publication_observation", "uif-mcp-publication/1")
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
                                  "uif-mcp-canonical-readback/1")
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
