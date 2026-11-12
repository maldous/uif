from contextlib import contextmanager
from dataclasses import replace
import shutil
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_review
import uif

from uif import role_broker, supervisor as shared_supervisor
from uif.role_broker import LaunchObservation, projection_digest, slot_for_lease
from uif.review_launcher import (
    ReviewAttempt, ReviewInvocation, ReviewLauncher, ReviewLaunchSelection, ReviewSettings,
)
from uif.source import SourceHold, file_digest
from uif.validation import decode, sha, wire


class ReviewLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = f = test_review.ReviewTests()
        f.addCleanup = self.addCleanup
        class GuardedCandidate(test_review.ValidationFixture):
            def __init__(self, root):
                super().__init__(root)
                target = root / "contributions" / "UIF-1" / "candidate"
                target.parent.mkdir(parents=True)
                self.candidate.rename(target)
                self.candidate = target
                self.spec = replace(self.spec, binding=replace(self.spec.binding, candidate=target))

        with patch.object(test_review, "ValidationFixture", GuardedCandidate):
            await f.asyncSetUp()
        resources = {"output_bytes": 65536, "cpu_millis": 1000, "memory_bytes": 4096}
        f.pool["resources"] = resources
        f.policy["resources"] = resources
        f.spec.policy.write_bytes(wire(f.policy))
        f.spec = replace(f.spec, policy_sha256=sha(f.spec.policy.read_bytes()), pool_binding_json=wire(f.pool))
        self.settings = ReviewSettings("codex-chatgpt", "gpt-6.1-sol", "medium")
        self.attempt = ReviewAttempt(f.spec, settings=self.settings, backend=f.backend, validation=f.f.adapter)
        self.stopped = {"status": "succeeded", "exit_code": 0, "child_tree_gone": True}

    def test_fixed_invocation_requires_exact_pinned_policy(self):
        f = self.fixture
        argv = ["--runtime", str(f.f.root), "--issue", "UIF-1", "--native-review-policy", str(f.spec.policy),
                "--native-review-policy-sha256", f.spec.policy_sha256]
        selection = ReviewInvocation.parse(argv)
        selection.require(f.spec)
        with self.assertRaisesRegex(SourceHold, "invocation_mismatch"):
            replace(selection, policy_sha256="9" * 64).require(f.spec)

    def test_preflight_binds_original_inputs_and_explicit_review_model(self):
        f = self.fixture
        policy = self.attempt.preflight()
        self.assertEqual(self.attempt.intent()["lease"], f.spec.lease)
        self.assertEqual(self.attempt.intent()["binding"]["write_roots"], [])
        self.assertEqual(self.attempt.receipt_fields(policy)["policy_sha256"], sha(wire(policy)))
        different = ReviewAttempt(f.spec, settings=replace(self.settings, effort="high"),
                                  backend=f.backend, validation=f.f.adapter)
        with self.assertRaisesRegex(SourceHold, "policy_settings"):
            different.preflight()

    def test_recovery_and_retained_attempts_do_not_grant_launch(self):
        f = self.fixture
        f.directory.mkdir(parents=True)
        with self.assertRaisesRegex(SourceHold, "reconciliation"):
            self.attempt.preflight()
        retained = ReviewAttempt(replace(f.spec, first_submission=False), settings=self.settings,
                                 backend=f.backend, validation=f.f.adapter)
        with self.assertRaisesRegex(SourceHold, "observation_only"):
            retained.preflight()

    def test_existing_pool_lease_blocks_even_without_attempt_files(self):
        f = self.fixture
        f.put_lease(f.spec.lease, {"owner": "another-reviewer"}, status="running", outcome=None)
        with self.assertRaisesRegex(SourceHold, "reconciliation"):
            self.attempt.preflight()

    def test_result_requires_stopped_child_and_excludes_author_session(self):
        f = self.fixture
        policy = self.attempt.preflight()
        f.review_receipt(session="author-session")
        with self.assertRaisesRegex(SourceHold, "stopped_success_required"):
            self.attempt.qualify_result(dict(self.stopped, child_tree_gone=False), policy)
        with self.assertRaisesRegex(SourceHold, "author_session"):
            self.attempt.qualify_result(self.stopped, policy)

    def test_result_receipt_fields_round_trip_through_frozen_reader(self):
        f = self.fixture
        policy = self.attempt.preflight()
        f.review_receipt(verdict="rework")
        evidence = self.attempt.qualify_result(self.stopped, policy)
        original = decode((f.directory / "receipt.json").read_bytes())
        emitted = dict(self.stopped, **self.attempt.receipt_fields(policy), **evidence)
        for key in original:
            self.assertEqual(emitted[key], original[key], key)
        self.assertIsNone(evidence["resolved_model"])
        self.assertEqual(evidence["resolution_status"], "unknown")

    def test_validation_digest_mismatch_stops_preflight(self):
        f = self.fixture
        wrong = ReviewAttempt(replace(f.spec, validation_receipt_sha256="f" * 64), settings=self.settings,
                              backend=f.backend, validation=f.f.adapter)
        with self.assertRaisesRegex(SourceHold, "validation_mismatch"):
            wrong.preflight()

    def test_output_cannot_be_replaced_with_other_candidate(self):
        f = self.fixture
        policy = self.attempt.preflight()
        f.review_receipt()
        path = f.directory / "final-message.txt"
        artifact = decode(path.read_bytes())
        artifact["source"]["tree"] = "9" * 40
        path.write_bytes(wire(artifact))
        with self.assertRaisesRegex(SourceHold, "artifact_binding"):
            self.attempt.qualify_result(self.stopped, policy)

    def _run_with_supervisor(self, *, outcome="succeeded", admitted_reused=False, stale_at=None,
                             stage="submit-intent", policy_drift=False, child_gone=None, other_runtime_root=False,
                             usage=(1, 4096), host_pressure=None, process_limit=4, role=None, max_processes=4):
        f = self.fixture
        runtime = f.f.root
        (runtime / "attempts").mkdir(mode=0o700, exist_ok=True)
        records = {
            "installation.json": {"supervisor_policy_sha256": "d" * 64},
            "capacity-policy.json": {
                "symphony": {"pool_path": str(f.db.parent)}, "host": {"cpu_reserve": 0},
                "windmill": {"computation_subprocesses_per_job": process_limit},
            },
            "provider-proof.json": {"route": "codex-chatgpt"},
        }
        for name, record in records.items():
            (runtime / name).write_bytes(wire(record))
        selection = ReviewLaunchSelection(f.spec, *(sha(wire(record)) for record in records.values()))
        invocation = ReviewInvocation.for_spec(f.spec)
        review = f.adapter
        if stage is not None:
            review.journal.save(review._journal_key(f.spec), review._pin(f.spec), stage)
        self.calls = calls = []
        self.role_calls = []
        fenced = False
        checks = 0

        @contextmanager
        def fence(selected):
            nonlocal fenced
            self.assertEqual(selected, selection)
            fenced = True
            try:
                yield
            finally:
                fenced = False

        def check_current(selected):
            nonlocal checks
            self.assertTrue(fenced)
            self.assertEqual(selected, selection)
            checks += 1
            if (stale_at is not None and checks >= stale_at
                    or role == "cancelled" and self.role_calls == ["start"]):
                raise SourceHold("stale review owner")

        launcher = ReviewLauncher(settings=self.settings, review=review, fence=fence, check_current=check_current)
        supervisor = ModuleType("uif.supervisor")

        def prepare(selected_runtime, installation, *, role_launch):
            self.assertIs(role_launch, True)
            calls.append("prepare")
            self.assertEqual(selected_runtime, runtime)
            self.assertEqual(installation, records["installation.json"])
            cfg = {"pool_root": str(f.db.parent), "runtime_root": runtime / "other" if other_runtime_root else runtime}
            if role is not None:
                cfg.update(schema="uif-coding-supervisor/2", controller_root=runtime)
                if role != "missing-broker":
                    cfg.update(role_broker=SimpleNamespace(start=role_start, observe=role_observe, cancel=role_cancel),
                               role_limits={"review": {"max_processes": max_processes, "max_projection_bytes": 2**20}})
            return cfg

        def admit(lease, binding, provider, host):
            calls.append("admit")
            intent = decode((f.directory / "intent.json").read_bytes())
            self.assertEqual(intent["history"], [{"phase": "admission-pending"}])
            self.assertEqual((lease, binding, provider, host),
                             (f.spec.lease, f.pool, records["provider-proof.json"], {"fixture": True}))
            f.put_lease(lease, binding, status="running", outcome=None)
            return dict(id=lease, binding=binding, fingerprint=sha(wire(binding)), status="running",
                        reused=admitted_reused)

        self.resource_checks = []

        def sample():
            self.resource_checks.append("tree")
            return usage

        def pressure(selected_runtime, policy):
            self.assertEqual((selected_runtime, policy), (runtime, records["capacity-policy.json"]))
            self.resource_checks.append("host")
            return host_pressure

        def run_turn(cfg, *, runtime, issue, workspace, directory, request, mode, stop, resource_check):
            calls.append("supervise")
            self.assertEqual((issue, workspace, directory, mode), ("UIF-1", f.f.candidate, f.directory, "read-only"))
            self.assertEqual(set(request), {"prompt", "timeout_seconds"})
            self.assertIn("read-only", request["prompt"])
            reason = resource_check()
            self.assertEqual(stop(), stale_at == 5)
            intent = decode((f.directory / "intent.json").read_bytes())
            self.assertEqual(intent["history"], [
                {"phase": "admission-pending"}, {"phase": "admitted"}, {"phase": "spawn-pending"},
            ])
            if reason is not None:
                return {"status": "failed", "reason": reason, "exit_code": 1,
                        "child_tree_gone": child_gone is not False}
            if outcome == "succeeded":
                artifact = dict(schema=f.policy["schema"], source=f.source, verdict="accept", findings=[])
                (f.directory / "final-message.txt").write_bytes(wire(artifact))
                events = [dict(type="thread.started", thread_id="review-session"),
                          dict(type="turn.completed", model="observed-model")]
                (f.directory / "stdout.jsonl").write_bytes(b"\n".join(wire(e) for e in events))
            if policy_drift:
                f.spec.policy.write_text("{}")
            return {"status": outcome, "exit_code": 0,
                    "child_tree_gone": (outcome != "uncertain") if child_gone is None else child_gone}

        def finish(pool, lease, path, result):
            calls.append("finish")
            self.assertEqual((lease, path), (f.spec.lease, f.directory / "receipt.json"))
            with path.open("xb") as receipt:
                receipt.write(wire(result))
            if result["child_tree_gone"] is True:
                status = "finished" if result["status"] == "succeeded" else "failed"
                f.put_lease(lease, f.pool, status=status, outcome=result["status"])

        def role_start(launch):
            self.role_calls.append("start")
            key = launch.key
            admission = f.spec.binding.dispatch.ledger
            self.assertEqual((key.role, key.store_id, key.operation_id, key.lease_id),
                             ("review", admission.store_id, admission.original_operation_id, f.spec.lease))
            if f.spec.binding.task is None:
                self.assertEqual((key.row_id, key.owner, key.epoch),
                                 (admission.row_id, admission.owner, admission.epoch))
                self.assertEqual((key.row_source_sha256, key.issue_id, key.issue_identifier, key.attempt_id),
                                 (admission.source_sha256, f.spec.binding.dispatch.issue.issue_id,
                                  "UIF-1", f.directory.name))
            else:
                self.assertEqual(key.task, f.spec.binding.task.to_record())
                self.assertEqual(key.workspace_key, f.spec.binding.workspace_key)
                self.assertEqual(key.invocation_id, f.spec.invocation_id)
                self.assertNotIn("issue", launch.to_record()["key"])
                self.assertEqual(decode((f.directory / "intent.json").read_bytes())["binding"]["write_roots"], [])
            self.assertEqual(launch.policy_sha256, f.spec.policy_sha256)
            self.assertEqual(launch.admission_sha256, file_digest(runtime / "current-admission.json"))
            self.assertEqual(launch.slot, slot_for_lease(f.spec.lease))
            self.assertEqual(key.intent_sha256, file_digest(f.directory / "intent.json"))
            self.assertEqual(launch.request_sha256, file_digest(f.directory / "request.json"))
            self.assertEqual((f.directory / "request.json").stat().st_mode & 0o777, 0o600)
            slot = runtime / launch.slot
            self.assertEqual((slot / "request.json").read_bytes(), (f.directory / "request.json").read_bytes())
            request = decode((slot / "request.json").read_bytes())
            self.assertEqual(request, {"prompt": self.attempt.prompt(f.policy),
                                       "timeout_seconds": f.policy["timeout_seconds"]})
            self.assertEqual(decode((slot / "intent.json").read_bytes())["phase"], "spawn-pending")
            self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "running", None))
            if role == "uncertain":
                return LaunchObservation("uncertain", launch.digest)
            if role == "cancelled":
                return LaunchObservation("running", launch.digest)
            if role == "failed":
                return LaunchObservation("exited-clean", launch.digest, "e" * 64, 17)

            returned = slot / "returned"
            workspace = returned / "workspace"
            returned.mkdir()
            shutil.copytree(slot / "projection", workspace)
            if role == "writes":
                (workspace / "value").write_text("held returned edit")
            (returned / "stdout.jsonl").write_bytes(b'{"type":"thread.started","thread_id":"review-session"}\n'
                                                    b'{"type":"turn.completed","model":"observed-model"}\n')
            (returned / "stderr.log").write_bytes(b"")
            (returned / "final-message.txt").write_bytes(wire(dict(
                schema=f.policy["schema"], source=f.source, verdict="accept", findings=[])))
            files = ("stdout.jsonl", "stderr.log", "final-message.txt")
            manifest = returned / "manifest.json"
            manifest.write_bytes(wire(dict(workspace_sha256=projection_digest(workspace, custody_root=runtime),
                                          artifacts={name: file_digest(returned / name) for name in files})))
            return LaunchObservation("exited-clean", launch.digest, "e" * 64, 0, file_digest(manifest))

        def role_observe(launch):
            self.role_calls.append("observe")
            return LaunchObservation("uncertain", launch.digest)

        def role_cancel(launch):
            self.role_calls.append("cancel")
            return LaunchObservation("exited-clean", launch.digest, "e" * 64, 15)

        def finish_role(lease, gone, outcome):
            self.assertIs(gone, True)
            self.assertEqual(decode((f.directory / "receipt.json").read_bytes())["status"], outcome)
            f.put_lease(lease, f.pool, status="finished" if outcome == "succeeded" else "failed", outcome=outcome)

        if role is not None:
            (runtime / "current-admission.json").write_bytes(wire({"fixture": "current admission"}))
        supervisor.prepare_guarded_runtime = prepare
        supervisor.load_capacity = lambda cfg: SimpleNamespace(
            Pool=lambda root, policy: SimpleNamespace(admit=admit, finish=finish_role),
            host_snapshot=lambda runtime: {"fixture": True}, host_pressure=pressure,
        )
        supervisor._usage = sample
        supervisor.run_guarded_turn = run_turn
        supervisor._exclusive = shared_supervisor._exclusive
        supervisor.run_registered_role_turn = shared_supervisor.run_registered_role_turn
        supervisor.finish_attempt = shared_supervisor.finish_attempt if role is not None else finish
        with (patch.object(uif, "supervisor", supervisor, create=True),
              patch("uif.review_launcher._restrict_cpu")):
            return launcher.run(invocation, selection)

    async def test_launch_uses_one_shared_read_only_turn_and_original_receipt(self):
        result = self._run_with_supervisor()
        self.assertEqual(self.calls, ["prepare", "admit", "supervise", "finish"])
        self.assertEqual((result["status"], result["session_id"], result["resolved_model"]),
                         ("succeeded", "review-session", "observed-model"))
        observed = await self.fixture.backend.inspect(self.fixture.spec)
        self.assertEqual(observed.status, "succeeded")

    def test_uncertain_cleanup_holds_the_original_lease(self):
        result = self._run_with_supervisor(outcome="succeeded", child_gone=False)
        self.assertEqual(result["status"], "uncertain")
        f = self.fixture
        self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "running", None))
        self.assertTrue((f.directory / "intent.json").is_file())

    def test_stale_owner_and_unwarranted_launch_do_not_start_a_turn(self):
        with self.assertRaisesRegex(SourceHold, "dispatch_intent_required"):
            self._run_with_supervisor(stage="unknown")
        self.assertEqual(self.calls, [])
        self.fixture.f.root.joinpath("attempts").rmdir()
        with self.assertRaisesRegex(SourceHold, "stale review owner"):
            self._run_with_supervisor(stale_at=3)
        self.assertEqual(self.calls, ["prepare", "admit"])

    def test_reused_pool_lease_does_not_launch_or_finish(self):
        with self.assertRaisesRegex(SourceHold, "lease_requires_reconciliation"):
            self._run_with_supervisor(admitted_reused=True)
        self.assertEqual(self.calls, ["prepare", "admit"])

    def test_policy_drift_after_stopped_turn_produces_failed_receipt(self):
        result = self._run_with_supervisor(policy_drift=True)
        self.assertEqual(result["status"], "failed")
        self.assertIs(result["child_tree_gone"], True)
        self.assertNotIn("artifact", result)

    def test_fence_loss_during_turn_blocks_successful_receipt(self):
        result = self._run_with_supervisor(stale_at=5)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("artifact", result)

    def test_coding_scope_is_refused(self):
        f = self.fixture
        coding = dict(f.pool, mode="workspace-write", write_roots=["src"])
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            replace(f.spec, pool_binding_json=wire(coding))

    def test_supervisor_workspace_policy_is_qualified_before_pool_admission(self):
        with self.assertRaisesRegex(SourceHold, "guarded_workspace"):
            self._run_with_supervisor(other_runtime_root=True)
        self.assertEqual(self.calls, ["prepare"])
        self.assertFalse(self.fixture.directory.exists())

    def test_review_memory_overrun_fails_after_proved_cleanup(self):
        result = self._run_with_supervisor(usage=(1, 4097))
        f = self.fixture
        self.assertEqual((result["status"], result["reason"]), ("failed", "memory_budget"))
        self.assertEqual(self.resource_checks, ["tree"])
        self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "failed", "failed"))

    def test_review_process_overrun_holds_lease_if_cleanup_is_uncertain(self):
        result = self._run_with_supervisor(usage=(5, 4096), child_gone=False)
        f = self.fixture
        self.assertEqual((result["status"], result["reason"]), ("uncertain", "process_budget"))
        self.assertEqual(self.resource_checks, ["tree"])
        self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "running", None))

    def test_review_at_budget_still_checks_host_pressure(self):
        result = self._run_with_supervisor(usage=(4, 4096), host_pressure="host_disk_pressure")
        self.assertEqual((result["status"], result["reason"]), ("failed", "host_disk_pressure"))
        self.assertEqual(self.resource_checks, ["tree", "host"])

    def test_invalid_process_ceiling_refuses_before_admission(self):
        with self.assertRaisesRegex(SourceHold, "uif_review_launcher_process_limit"):
            self._run_with_supervisor(process_limit=True)
        self.assertEqual(self.calls, ["prepare"])
        self.assertFalse(self.fixture.directory.exists())

    async def test_registered_review_seals_original_turn_and_imports_read_only_result(self):
        result = self._run_with_supervisor(role="succeeded")
        f = self.fixture
        self.assertEqual((result["status"], result["session_id"], result["resolved_model"]),
                         ("succeeded", "review-session", "observed-model"))
        self.assertEqual(result["launch_receipt_sha256"], "e" * 64)
        self.assertEqual(self.calls, ["prepare", "admit"])
        self.assertEqual(self.role_calls, ["start"])
        self.assertEqual((await f.backend.inspect(f.spec)).status, "succeeded")

    async def test_registered_review_preserves_failed_receipt_without_import(self):
        result = self._run_with_supervisor(role="failed")
        f = self.fixture
        self.assertEqual((result["status"], result["exit_code"], result["child_tree_gone"]), ("failed", 17, True))
        self.assertEqual(self.role_calls, ["start"])
        self.assertEqual((await f.backend.inspect(f.spec)).status, "failed")

    def test_registered_uncertain_turn_observes_original_and_retains_lease(self):
        result = self._run_with_supervisor(role="uncertain")
        f = self.fixture
        self.assertEqual((result["status"], result["child_tree_gone"]), ("uncertain", False))
        self.assertEqual(self.role_calls, ["start", "observe"])
        self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "running", None))

    def test_registered_review_rejects_returned_candidate_edits(self):
        result = self._run_with_supervisor(role="writes")
        f = self.fixture
        self.assertEqual((result["status"], result["child_tree_gone"]), ("failed", True))
        self.assertEqual((f.f.candidate / "value").read_text(), "one\n")
        self.assertFalse((f.directory / "final-message.txt").exists())

    def test_registered_process_ceiling_cannot_exceed_admitted_limit(self):
        with self.assertRaisesRegex(SourceHold, "uif_review_launcher_role_process_limit"):
            self._run_with_supervisor(role="succeeded", max_processes=5)
        self.assertEqual(self.calls, ["prepare"])
        self.assertFalse(self.fixture.directory.exists())

    def test_installed_v2_without_broker_has_no_direct_fallback(self):
        with self.assertRaisesRegex(SourceHold, "uif_review_launcher_role_binding"):
            self._run_with_supervisor(role="missing-broker")
        self.assertEqual(self.calls, ["prepare"])
        self.assertFalse(self.fixture.directory.exists())

    async def test_registered_cancel_reconciles_original_without_another_start(self):
        result = self._run_with_supervisor(role="cancelled")
        f = self.fixture
        self.assertEqual((result["status"], result["child_tree_gone"]), ("cancelled", True))
        self.assertEqual(self.role_calls, ["start", "cancel"])
        self.assertEqual(f.backend._lease(f.spec.lease), (f.pool, "failed", "cancelled"))
        self.assertEqual((await f.backend.inspect(f.spec)).status, "failed")

    def test_registered_event_readback_must_match_imported_session(self):
        policy = self.attempt.preflight()
        self.fixture.review_receipt()
        outcome = dict(self.stopped, session_id="another-session", turn_completed=True, resolved_model=None)
        with self.assertRaisesRegex(SourceHold, "uif_review_launcher_event_readback"):
            self.attempt.qualify_result(outcome, policy, require_event_readback=True)


class LedgerReviewLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = self.f = await test_review.ledger_review_fixture(self)
        f = self.f
        resources = {"output_bytes": 65536, "cpu_millis": 1000, "memory_bytes": 4096}
        f.pool["resources"] = f.policy["resources"] = resources
        f.spec.policy.write_bytes(wire(f.policy))
        f.spec = replace(f.spec, policy_sha256=sha(f.spec.policy.read_bytes()), pool_binding_json=wire(f.pool))
        self.invocation = ReviewInvocation.for_spec(f.spec)
        self.settings = ReviewSettings("codex-chatgpt", "gpt-6.1-sol", "medium")
        self.attempt = ReviewAttempt(f.spec, settings=self.settings, backend=f.backend, validation=f.f.adapter)

    def test_cli_binds_canonical_task_and_genuine_invocation_uuid(self):
        invocation = self.invocation
        args = ["--runtime", str(invocation.runtime), *invocation.target_args(),
                "--native-review-policy", str(invocation.policy),
                "--native-review-policy-sha256", invocation.policy_sha256]
        parsed = ReviewInvocation.parse(args)
        self.assertEqual(parsed, invocation)
        parsed.require(self.f.spec)
        self.assertEqual(decode(parsed.task_json), self.f.spec.binding.task.to_record())
        with self.assertRaisesRegex(SourceHold, "invocation_target"):
            replace(parsed, invocation_id=self.f.spec.binding.dispatch.operation_id)

    async def test_registered_ledger_review_uses_read_only_broker_and_original_receipt(self):
        f = self.f
        result = ReviewLauncherTests._run_with_supervisor(self, role="succeeded")
        self.assertEqual(self.role_calls, ["start"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["task"], f.spec.binding.task.to_record())
        self.assertEqual(result["invocation_id"], f.spec.invocation_id)
        self.assertNotIn("issue", result)
        self.assertEqual((await f.backend.inspect(f.spec)).status, "succeeded")
        self.assertEqual((f.f.candidate / "value").read_text(), "one\n")

    def test_missing_ledger_broker_cannot_fall_back_or_admit_a_lease(self):
        with patch.object(role_broker, "LedgerReviewLaunchKey", None):
            with self.assertRaisesRegex(SourceHold, "ledger_role_unavailable"):
                ReviewLauncherTests._run_with_supervisor(self, role="succeeded")
        self.assertEqual(self.calls, ["prepare"])
        self.assertEqual(self.role_calls, [])
        self.assertIsNone(self.f.backend._lease(self.f.spec.lease))
        self.assertFalse(self.f.directory.exists())
