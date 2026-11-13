"""Exercise owner observations through the existing stage and publication fences."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from test_publication import PublicationFixture, SNAPSHOT
from test_validation import LedgerValidationFixture
from uif.host_jobs import lease_id, sha as host_sha, wire as host_wire
from uif.ledger import RowKey
from uif.mcp_stage_bridge import MCPGitReadback, MCPPublisherCommand, MCPValidationClient
from uif.publication import Publisher, fingerprint
from uif.refill import DeliveryCompletion, VerifiedRefill
from uif.source import SourceHold
from uif.stokowski import UIF
from uif.validation import ValidationAdapter, WindmillValidationBackend, decode, sha, wire


class Observations:
    def __init__(self, root, task):
        self.root, self.task = root, task
        self.entry = {}
        self.key = RowKey(task["store_id"], task["row_id"])
        self.authority = SimpleNamespace(
            store_id=self.key.store_id, owner=task["owner"], epoch=task["epoch"], snapshot_path=SNAPSHOT,
            admission=SimpleNamespace(_entry=self.get_entry, _claim=self.get_claim),
        )

    def get_entry(self, key):
        assert key == self.key
        return self.entry, self.root

    def get_claim(self, key, entry, runtime, issue):
        assert (entry, runtime) == self.get_entry(key) and issue is None
        return {"ledger": self.task}

    def write(self, field, schema, **values):
        path = self.root / (field + ".json")
        raw = wire({"schema": schema, "task": self.task, **values})
        path.write_bytes(raw)
        path.chmod(0o600)
        self.entry[field] = {"path": str(path), "sha256": sha(raw)}


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.fixture = LedgerValidationFixture(root)
        self.spec = replace(self.fixture.spec, first_submission=False, args_json=wire(dict(
            decode(self.fixture.spec.args_json), request_sha256="a" * 64, mode="write",
            resources={"workers": 1}, timeout_seconds=120,
        )))
        self.observations = Observations(root, self.spec.binding.task.to_record())
        self.descriptor = "b" * 64
        self.client = MCPValidationClient(self.observations.authority, self.observations.key, self.descriptor)
        backend = WindmillValidationBackend(self.client, workspace="foo", script_path="u/admin/foo_jobs")
        self.adapter = ValidationAdapter(self.fixture.journal, self.fixture.fence, backend)

    def completion(self, **runner_updates):
        args = decode(self.spec.args_json)
        receipt = dict(
            self.fixture.completed(), namespace="uif-foo-user-job/1", workspace="foo",
            request_sha256=args["request_sha256"], descriptor_sha256=self.descriptor,
            mode=args["mode"], resources=args["resources"], timeout_seconds=args["timeout_seconds"],
        )
        receipt.update(runner_updates)
        receipt["lease_id"] = lease_id(receipt)
        result = dict(receipt, job_succeeded=True, incident=None)
        job = dict(self.fixture.client.job, args=args, script_path="u/admin/foo_jobs", result=result)
        return job, receipt

    def observe(self, job, receipt, *, release=None):
        self.observations.write("mcp_validation_observation", "uif-mcp-validation/1",
                                job=job, runner_receipt=receipt, runner_release=release)

    @staticmethod
    def release(receipt, **updates):
        fields = ("namespace", "workspace", "job_id", "descriptor_sha256", "request_sha256",
                  "command_sha256", "mode", "timeout_seconds", "resources", "capacity_policy_sha256",
                  "runner_sha256")
        binding = {name: receipt[name] for name in fields}
        return dict({
            "schema": "uif-shared-job-admission/1", "status": "finished",
            "binding_sha256": host_sha(host_wire(binding)), "lease_id": receipt["lease_id"],
            "receipt_sha256": host_sha(host_wire(receipt)),
        }, **updates)

    async def test_missing_observation_holds_then_accepts_original_job(self):
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        job, receipt = self.completion()
        self.observe(job, None)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        self.observe(job, receipt)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        self.observe(job, receipt, release=self.release(receipt))
        self.assertEqual((await self.adapter.tick(self.spec)).status, "succeeded")
        self.assertEqual((await self.adapter.accepted(self.spec)).receipt, wire(job["result"]))
        with self.assertRaisesRegex(SourceHold, "owner_submission_only"):
            await self.client.post("unused")

    async def test_immutable_release_pending_result_recovers_after_original_lease_release(self):
        job, receipt = self.completion()
        job["result"].update(reconcile_required=True, job_succeeded=False)
        self.observe(job, receipt)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        release = self.release(receipt, outcome="succeeded", pool_status="finished")
        self.observe(job, receipt, release=release)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "succeeded")
        accepted = decode((await self.adapter.accepted(self.spec)).receipt)
        self.assertEqual(accepted, dict(receipt, job_succeeded=True, incident=None))
        self.assertTrue(job["result"]["reconcile_required"])
        self.assertFalse(job["result"]["job_succeeded"])

    async def test_release_for_different_receipt_quarantines_original(self):
        job, receipt = self.completion()
        self.observe(job, receipt, release=self.release(receipt, receipt_sha256="e" * 64))
        with self.assertRaisesRegex(SourceHold, "runner_release"):
            await self.adapter.tick(self.spec)

    async def test_release_pending_does_not_excuse_a_different_result_or_failed_wrapper(self):
        job, receipt = self.completion()
        job["result"].update(reconcile_required=True, job_succeeded=False)
        job["success"] = False
        self.observe(job, receipt, release=self.release(receipt))
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        job["result"]["exit_code"] = 3
        self.observe(job, receipt, release=self.release(receipt))
        with self.assertRaisesRegex(SourceHold, "runner_receipt"):
            await self.adapter.tick(self.spec)

    async def test_validation_rejects_prior_epoch_evidence(self):
        self.observations.authority.epoch = "successor"
        job, receipt = self.completion()
        self.observe(job, receipt, release=self.release(receipt))
        with self.assertRaisesRegex(SourceHold, "stage_owner"):
            await self.adapter.tick(self.spec)

    async def test_wrong_host_descriptor_quarantines_original(self):
        job, receipt = self.completion(descriptor_sha256="c" * 64)
        self.observations.write("mcp_validation_observation", "uif-mcp-validation/1",
                                job=job, runner_receipt=receipt)
        with self.assertRaisesRegex(SourceHold, "runner_binding"):
            await self.adapter.tick(self.spec)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "quarantined")

    async def test_lost_host_reply_remains_unknown(self):
        job, _ = self.completion()
        job["result"] = {"status": "unknown", "job_succeeded": False, "reconcile_required": True}
        self.observations.write("mcp_validation_observation", "uif-mcp-validation/1",
                                job=job, runner_receipt=None)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")


class PublicationTests(PublicationFixture):
    ledger_only = True

    def setUp(self):
        super().setUp()
        self.observations = Observations(self.root, self.request.task.to_record())
        authority = self.observations.authority
        self.publisher = Publisher(self.owner.fence, MCPPublisherCommand(authority, self.closure, self.paths, 321),
                                   MCPGitReadback(authority, 321))

    def publish_observation(self, result):
        self.observations.write(
            "mcp_publication_observation", "uif-mcp-publication/1", publisher_uid=1000,
            envelope=self.request.envelope(observe=True), response=result,
            invocation_reference={"path": self.paths.invocation(self.request), "sha256": result["receipt"]["sha256"]},
            accepted_reference=result["accepted_source"]["reference"],
        )

    def canonical_observation(self, result, **updates):
        request = self.request
        value = dict(
            reader_uid=1000, repository=UIF.repository, repository_id=321, remote=UIF.origin,
            ref=f"refs/heads/{UIF.accepted_branch}", publisher_operation_id=request.publisher_operation_id,
            invocation_id=request.invocation_id, request_sha256=fingerprint(request.envelope(observe=True)["request"]),
            invocation_receipt_sha256=result["receipt"]["sha256"],
            accepted_source_sha256=result["accepted_source"]["reference"]["sha256"],
            merge_head=self.merge, tree=request.source_tree, parents=[request.source_base, request.source_head],
            ancestor_exit_code=0, ref_head=self.merge,
        )
        value.update(updates)
        self.observations.write("mcp_canonical_readback", "uif-mcp-canonical-readback/1", **value)

    def test_durable_intent_then_original_receipt_and_canonical_readback(self):
        with self.assertRaisesRegex(SourceHold, "owner_observation_pending"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.owner.intent, self.request)
        result = self.result()
        self.publish_observation(result)
        with self.assertRaisesRegex(SourceHold, "canonical_readback_pending"):
            self.publisher.consume_boundary(self.request)
        self.canonical_observation(result, reader_uid=879)
        with self.assertRaisesRegex(SourceHold, "canonical_readback_binding"):
            self.publisher.consume_boundary(self.request)
        self.canonical_observation(result)
        self.assertEqual(self.publisher.consume_boundary(self.request).head, self.merge)
        self.assertEqual(self.calls(), [])

    def test_publication_requires_original_stopped_child_proof(self):
        result = self.result(stopped=False)
        self.publish_observation(result)
        self.canonical_observation(result)
        with self.assertRaisesRegex(SourceHold, "terminal_proof"):
            self.publisher.consume_boundary(self.request)
        self.assertEqual(self.calls(), [])

    def test_successor_refill_reads_only_the_exact_historical_intent(self):
        self.owner.intent = self.request
        self.observations.entry["publication_intent"] = self.request.to_record()
        result = self.result()
        self.publish_observation(result)
        self.canonical_observation(result)
        publication = self.publisher.consume_boundary(self.request)
        completion = DeliveryCompletion(publication, "completed", "e" * 64)
        self.observations.authority.epoch = "successor"
        self.observations.authority.owner = "successor-owner"
        self.owner.current = replace(self.request, invocation_id="249e3556-ea9e-482a-bdb7-bc1871b4a597")
        operation = self.request.original_operation_id
        refill = VerifiedRefill(self.request.key.store_id,
                                lambda key: completion if key == operation else None, self.publisher)
        self.assertEqual(refill.verify_refill(operation), self.request.key)
        self.observations.entry["publication_intent"] = self.owner.current.to_record()
        with self.assertRaisesRegex(SourceHold, "stage_original_intent"):
            refill.verify_refill(operation)
        self.assertEqual(self.calls(), [])
