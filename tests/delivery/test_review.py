import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from uif.review import LEDGER_ORIGIN, NativeReviewBackend, ORIGIN, ReviewAdapter, ReviewSpec
from uif.source import SourceHold
from uif.stokowski import UIF
from uif.validation import decode, sha, wire

from test_validation import LedgerValidationFixture, ValidationFixture


REVIEW_INVOCATION = "259e7bd1-4729-4f6d-9dfa-d5f31f3c5b91"


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.f = f = ValidationFixture(Path(temporary.name))
        f.completed()
        await f.adapter.tick(f.spec)
        self.proof = await f.adapter.accepted(f.spec)

        self.reviewer = "independent-reviewer"
        self.executable = f.root / "review-worker"
        self.executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > launched\n')
        self.executable.chmod(0o700)
        self.db = f.root / "state.sqlite"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS leases "
                       "(id TEXT PRIMARY KEY,binding TEXT,fingerprint TEXT,status TEXT,outcome TEXT)")
        self.put_lease("coding-lease", f.author_binding)
        binding = f.spec.binding
        self.directory = f.root / "attempts" / ("review-" + binding.workspace_key)
        ledger = binding.dispatch.ledger
        self.source = dict(root=str(binding.candidate), head=binding.head, base=binding.base, tree=binding.tree,
                           branch=binding.branch, outputs=f.files, files_sha256=sha(wire(f.files)))
        self.pool = dict(owner=self.reviewer, mode="native-review-read-only", write_roots=[],
                         read_roots=[str(binding.candidate)], head=binding.head, branch=binding.branch,
                         source_sha256=self.source["files_sha256"], task_sha256="a" * 64, resources={})
        target = {"issue": "UIF-1"}
        origin = ORIGIN
        invocation = None
        if binding.task is not None:
            origin = LEDGER_ORIGIN
            invocation = REVIEW_INVOCATION
            target = dict(task=binding.task.to_record(), workspace_key=binding.workspace_key,
                          invocation_id=invocation, lease="review-lease")
        self.policy = dict(
            schema=origin, project_id=UIF.project_id, **target,
            owner=f.owner, reviewer_owner=self.reviewer, current_runtime=str(f.root),
            validation_identity=self.proof.operation_key, stage_identity=self.proof.operation_key,
            validation_receipt=dict(sha256=self.proof.receipt_sha256, windmill_job_id=self.proof.windmill_job_id,
                                    runner_job_id=self.proof.runner_job_id),
            unit_binding=dict(store_id=ledger.store_id, row_id=ledger.row_id, operation_id=ledger.original_operation_id,
                              owner=ledger.owner, epoch=ledger.epoch, dispatch_sha256=binding.dispatch.receipt_sha256,
                              coding_sha256=binding.coding_receipt_sha256),
            author_receipts=[dict(path=str(f.coding), sha256=sha(f.coding.read_bytes()))],
            excluded_owners=[f.owner], excluded_sessions=["author-session"], require_stopped_child=True,
            timeout_seconds=30, acceptance={}, resources={}, provider="codex-chatgpt",
            model="gpt-6.1-sol", effort="medium", source=self.source,
        )
        policy = f.root / "review.json"
        policy.write_bytes(wire(self.policy))
        self.spec = ReviewSpec(f.spec, self.proof.receipt_sha256, self.reviewer, "review-lease", policy, self.directory,
                               sha(policy.read_bytes()), wire(self.pool), first_submission=True,
                               invocation_id=invocation)
        self.backend = NativeReviewBackend(self.executable, executable_sha256=sha(self.executable.read_bytes()),
                                           evidence_uid=os.getuid(), pool_database=self.db)
        self.adapter = ReviewAdapter(f.journal, f.adapter.fence, self.backend, validation=f.adapter)

    def put_lease(self, lease, binding, *, status="finished", outcome="succeeded"):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?,?)",
                       (lease, wire(binding).decode(), sha(wire(binding)), status, outcome))

    def review_receipt(self, *, session="review-session", verdict="accept"):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.put_lease(self.spec.lease, self.pool)
        artifact = dict(schema=self.spec.origin, source=self.source, verdict=verdict,
                        findings=[] if verdict == "accept" else ["Missing regression coverage"])
        events = b"\n".join([wire(dict(type="thread.started", thread_id=session)), wire(dict(type="turn.completed"))])
        request = self.backend._request(self.spec)
        receipt = dict(
            origin=self.spec.origin, **self.spec.target_record(),
            lease=self.spec.lease, parent_binding=self.pool, status="succeeded",
            binding_sha256=sha(wire(self.pool)), request=request, policy_sha256=sha(wire(self.policy)),
            accepted_delivery=False, child_tree_gone=True, turn_completed=True, session_id=session,
            source_before=self.source["files_sha256"], source_after=self.source["files_sha256"],
            artifact=artifact, artifact_sha256=sha(wire(artifact)), events_sha256=sha(events),
        )
        (self.directory / "intent.json").write_bytes(wire(dict(
            lease=self.spec.lease, binding=self.pool, request=request, phase="spawn-pending",
            history=[dict(phase="admitted"), dict(phase="spawn-pending")],
        )))
        (self.directory / "final-message.txt").write_bytes(wire(artifact))
        (self.directory / "stdout.jsonl").write_bytes(events)
        raw = wire(receipt) + b"\n"
        (self.directory / "receipt.json").write_bytes(raw)
        return raw

    async def test_accept_reads_original_review_receipt_and_distinct_identities(self):
        raw = self.review_receipt()
        self.assertEqual((await self.adapter.tick(self.spec)).status, "succeeded")
        proof = await self.adapter.accepted(self.spec)
        self.assertEqual((proof.reviewer, proof.lease, proof.native_session_id),
                         (self.reviewer, "review-lease", "review-session"))
        self.assertEqual((proof.receipt, proof.receipt_sha256), (raw, sha(raw)))
        self.assertEqual(proof.validation_receipt_sha256, self.proof.receipt_sha256)
        self.assertFalse((self.f.root / "launched").exists())

    async def test_author_cannot_review_and_cannot_reuse_author_session(self):
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            replace(self.spec, reviewer=self.f.owner)
        self.review_receipt(session="author-session")
        self.assertEqual((await self.adapter.tick(self.spec)).status, "quarantined")

    async def test_worker_launch_runs_once_and_exit_does_not_qualify(self):
        self.assertEqual((await self.adapter.tick(self.spec)).status, "submitted")
        await asyncio.gather(*self.backend._children)
        args = (self.f.root / "launched").read_text().splitlines()
        self.assertEqual(args, ["--runtime", str(self.f.root), "--issue", "UIF-1",
                               "--native-review-policy", str(self.spec.policy),
                               "--native-review-policy-sha256", self.spec.policy_sha256])
        (self.f.root / "launched").unlink()
        restarted = ReviewAdapter(self.f.journal, self.f.adapter.fence, self.backend, validation=self.f.adapter)
        self.assertEqual((await restarted.tick(self.spec)).status, "unknown")
        self.assertFalse((self.f.root / "launched").exists())
        self.review_receipt()
        self.assertEqual((await restarted.tick(self.spec)).status, "succeeded")

    async def test_worker_intent_without_pool_or_receipt_blocks_first_launch(self):
        self.directory.mkdir(parents=True)
        (self.directory / "intent.json").write_text("{}")
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        self.assertFalse((self.f.root / "launched").exists())

    async def test_requires_completed_pool_and_unchanged_session_evidence(self):
        self.review_receipt()
        self.put_lease(self.spec.lease, self.pool, status="running", outcome=None)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "unknown")
        self.put_lease(self.spec.lease, self.pool)
        self.assertEqual((await self.adapter.tick(self.spec)).status, "succeeded")
        (self.directory / "stdout.jsonl").write_bytes(b"")
        with self.assertRaisesRegex(SourceHold, "original_receipt_drift"):
            await self.adapter.accepted(self.spec)

    async def test_failed_pool_requires_matching_stopped_receipt(self):
        receipt = json.loads(self.review_receipt())
        for status, outcome, stopped, expected in (
                ("failed", "failed", True, "failed"),
                ("cancelled", "cancelled", True, "failed"),
                ("cancelled", "failed", True, "unknown"),
                ("failed", "failed", False, "unknown")):
            with self.subTest(status=status, outcome=outcome, stopped=stopped):
                raw = wire(dict(receipt, status=status, child_tree_gone=stopped))
                (self.directory / "receipt.json").write_bytes(raw)
                self.put_lease(self.spec.lease, self.pool, status="failed", outcome=outcome)
                observed = await self.backend.inspect(self.spec)
                self.assertEqual(observed.status, expected)
                self.assertEqual(observed.receipt, raw if expected == "failed" else None)

    async def test_stopped_failure_and_cancellation_require_original_intent(self):
        receipt = json.loads(self.review_receipt())
        path = self.directory / "intent.json"
        intent = json.loads(path.read_bytes())
        (self.directory / "final-message.txt").unlink()
        (self.directory / "stdout.jsonl").unlink()
        for status in ("failed", "cancelled"):
            self.put_lease(self.spec.lease, self.pool, status="failed", outcome=status)
            raw = wire(dict(receipt, status=status))
            (self.directory / "receipt.json").write_bytes(raw)
            path.unlink(missing_ok=True)
            self.assertEqual((await self.backend.inspect(self.spec)).status, "unknown")
            for name, value in (("lease", "other-lease"), ("binding", {}),
                                ("request", {}), ("phase", "admitted"), ("history", [])):
                with self.subTest(status=status, field=name):
                    path.write_bytes(wire(dict(intent, **{name: value})))
                    observed = await self.backend.inspect(self.spec)
                    self.assertEqual((observed.status, observed.receipt), ("quarantined", None))
            path.write_bytes(wire(intent))
            observed = await self.backend.inspect(self.spec)
            self.assertEqual((observed.status, observed.receipt), ("failed", raw))

    async def test_rework_is_terminal_and_never_authorizes_publication(self):
        self.review_receipt(verdict="rework")
        self.assertEqual((await self.adapter.tick(self.spec)).status, "rework")
        with self.assertRaisesRegex(SourceHold, "acceptance_pending"):
            await self.adapter.accepted(self.spec)

    async def test_validation_receipt_mismatch_holds_before_worker_effect(self):
        with self.assertRaisesRegex(SourceHold, "validation_receipt_binding"):
            await self.adapter.tick(replace(self.spec, validation_receipt_sha256="f" * 64))
        self.assertFalse((self.f.root / "launched").exists())


async def ledger_review_fixture(test):
    fixture = ReviewTests()
    fixture.addCleanup = test.addCleanup
    with patch(__name__ + ".ValidationFixture", LedgerValidationFixture):
        await fixture.asyncSetUp()
    return fixture


class LedgerReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = await ledger_review_fixture(self)

    async def test_accept_carries_the_original_ledger_task_and_distinct_reviewer(self):
        f = self.f
        raw = f.review_receipt()
        self.assertEqual((await f.adapter.tick(f.spec)).status, "succeeded")
        evidence = await f.adapter.accepted(f.spec)
        result = decode(evidence.receipt)
        self.assertEqual(evidence.binding.task, f.f.spec.binding.task)
        self.assertEqual(evidence.reviewer, f.reviewer)
        self.assertEqual(evidence.receipt, raw)
        self.assertEqual(result["task"], evidence.binding.task.to_record())
        self.assertEqual(result["workspace_key"], evidence.binding.workspace_key)
        self.assertEqual(result["invocation_id"], REVIEW_INVOCATION)
        self.assertNotEqual(result["invocation_id"], evidence.binding.dispatch.operation_id)
        self.assertNotIn("issue", result)

    async def test_changed_invocation_in_original_receipt_quarantines(self):
        f = self.f
        original = decode(f.review_receipt())
        original["invocation_id"] = "ac61804e-2a42-4806-b16b-8c660a5cf1a2"
        (f.directory / "receipt.json").write_bytes(wire(original))
        self.assertEqual((await f.adapter.tick(f.spec)).status, "quarantined")

    def test_author_or_write_root_never_qualifies(self):
        f = self.f
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            replace(f.spec, reviewer=f.f.owner)
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            replace(f.spec, pool_binding_json=wire(dict(f.pool, write_roots=[str(f.f.candidate)])))
