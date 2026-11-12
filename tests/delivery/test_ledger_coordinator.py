"""Drive the coordinator with real local custody and substituted external effects."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import UUID

from test_dispatch import LEDGER_SUPERVISOR, put
from test_owner_runtime import OwnerRuntimeTests, ROW, SNAPSHOT
from test_publication import PublicationFixture
from test_review import ReviewTests
from test_validation import Windmill
from uif import supervisor
from uif.admission import _fingerprint
from uif.ledger import RowKey, ledger_task_target, read_snapshot
from uif.ledger_coordinator import CoordinatorBindings, OperationPlan, Progress, load_coordinator, main
from uif.native_publisher import inner_response
from uif.publication import GitReadback, Publisher, fingerprint
from uif.review import NativeReviewBackend, ReviewAdapter, ReviewSpec
from uif.source import SourceHold, file_digest, git
from uif.stokowski import UIF
from uif.task_identity import LedgerTaskKey
from uif.validation import StageBinding, ValidationSpec, decode, wire


CAPACITY = os.environ.get("UIF_NEUTRAL_POOL_SOURCE")
SUCCESSOR = "src-060-placement-decision"


def stopped_supervisor(capacity_path, *, delay=0):
    source = LEDGER_SUPERVISOR
    start = source.index("class FixturePool:")
    end = source.index("class FixtureBroker:")
    source = source[:start] + f"""capacity = supervisor.load_capacity({{'capacity': Path({str(capacity_path)!r})}})
capacity.host_snapshot = lambda path: dict(cpu_millis=4000, available_memory_bytes=2**40, available_disk_bytes=2**40)
capacity.host_pressure = lambda path, policy: None

""" + source[end:]
    start = source.index('        marker = root / "child-launch"')
    end = source.index("\n\nsupervisor._installed", start)
    return (source[:start] + r'''        import shutil
        (directory / "fixture-child").touch(exist_ok=False)
        returned = directory / "returned"
        returned.mkdir()
        workspace = returned / "workspace"
        shutil.copytree(directory / "projection", workspace)
        name = "other" if launch.key.task["row_id"] == "src-060-placement-decision" else "source"
        (workspace / name).write_text("disposable coding output\n")
        events = [{"type":"thread.started","thread_id":"fixture-author"},
                  {"type":"turn.completed","model":"fixture-model","usage":{"input_tokens":11,"output_tokens":2}}]
        (returned / "stdout.jsonl").write_bytes(b"".join(role_broker._wire(event)+b"\n" for event in events))
        (returned / "stderr.log").write_bytes(b"")
        manifest = returned / "manifest.json"
        from uif.source import file_digest
        manifest.write_bytes(role_broker._wire(dict(
            workspace_sha256=role_broker.projection_digest(workspace,custody_root=directory),
            artifacts={name:file_digest(returned/name) for name in ("stdout.jsonl","stderr.log")})))
        return role_broker.LaunchObservation("exited-clean",launch.digest,"d"*64,0,file_digest(manifest))
''' + source[end:]).replace("lambda _: FixtureCapacity", "lambda _: capacity").replace(
        "        import shutil\n", f"        import shutil, time\n        time.sleep({delay!r})\n")


class CoordinatorTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = owner = OwnerRuntimeTests()
        self.addCleanup(owner.doCleanups)
        owner.setUp()
        self.fixture = f = owner.fixture
        self.key = owner.prepare_ledger_inputs(state="reconciled-for-pickup", operation="opaque original / ledger")
        self.runtime = f.runtime
        f.policy["windmill"] = {"computation_subprocesses_per_job": 12}
        f.policy["symphony"]["memory_budget_bytes"] = 512 * 1024**2
        inputs = decode(Path(owner.entry["admission_inputs"]["path"]).read_bytes())
        inputs["policy_sha256"] = put(f.runtime / "capacity-policy.json", f.policy)
        owner.entry["admission_inputs"] = owner.ref("admission-inputs.json", inputs)
        owner.set_catalogue()
        self.supervisor = Path(supervisor.__file__).resolve()

    def test_cli_refuses_missing_malformed_or_unpinned_binding_source(self):
        f = self.fixture
        path = f.root / "bindings.py"
        args = ("--owner", self.owner.registration["path"], "--owner-sha256", self.owner.registration["sha256"],
                "--bindings", str(path), "--bindings-sha256")
        for content in (None, b"raise AssertionError('unpinned source ran')", b"bind_ledger_coordinator = 1"):
            with self.subTest(content=content):
                if content is not None:
                    path.write_bytes(content)
                    path.chmod(0o600)
                pin = file_digest(path) if content == b"bind_ledger_coordinator = 1" else "0" * 64
                self.assertEqual(main([*args, pin]), 2)
        self.assertFalse(self.owner.claim_file.exists())

    def test_invalid_binding_factory_cannot_admit_work(self):
        path = self.fixture.root / "bindings.py"
        path.write_text("def bind_ledger_coordinator(authority):\n    return None\n")
        path.chmod(0o600)
        with self.assertRaisesRegex(SourceHold, "coordinator_registration"):
            load_coordinator(self.owner.registration["path"], self.owner.registration["sha256"],
                             path, file_digest(path))
        self.assertFalse(self.owner.claim_file.exists())

    def setup_connected(self):
        authority = self.owner.authority()
        authority.pool_database_path.unlink()
        self.capacity = supervisor.load_capacity({"capacity": Path(CAPACITY)})
        self.pool = self.capacity.Pool(authority.pool_database_path.parent, self.fixture.policy)
        self.wm = Windmill()
        self.wm.drop_ack = True
        self.native = PublicationFixture()
        self.addCleanup(self.native.doCleanups)
        self.native.setUp()
        self.validation_input = self.review_input = self.publication_input = None
        self.calls = []
        self.children = 0
        self.bindings_source = self.fixture.root / "bindings.py"
        self.bindings_source.write_text(
            f"from {__name__} import fixture_bindings\n"
            "def bind_ledger_coordinator(authority):\n    return fixture_bindings(authority)\n")
        self.bindings_source.chmod(0o600)

    def coordinator(self):
        with patch(__name__ + ".fixture_bindings", self.bindings_for, create=True):
            return load_coordinator(self.owner.registration["path"], self.owner.registration["sha256"],
                                    self.bindings_source, file_digest(self.bindings_source))

    def bindings_for(self, authority):
        command = SimpleNamespace(call=self.publish_transport, closure_sha256=self.native.closure,
                                  paths=self.native.paths)
        publisher = Publisher(authority.publication_fence, command, GitReadback(self.native.bare))
        return CoordinatorBindings(
            self.supervisor, file_digest(self.supervisor), publisher,
            lambda key, runtime: self.plan(authority, key, runtime))

    def plan(self, authority, key, runtime):
        validation = authority.validation_adapter(runtime, self.wm, workspace="foo", script_path="u/uif/validation")
        worker = runtime / "fixture-review-worker"
        if not worker.exists():
            worker.write_text("#!/bin/sh\nexit 99\n")
            worker.chmod(0o700)
        backend = NativeReviewBackend(worker, executable_sha256=file_digest(worker),
                                      evidence_uid=os.getuid(), pool_database=authority.pool_database_path)
        review = ReviewAdapter(validation.journal, validation.fence, backend, validation=validation)
        return OperationPlan(validation, review, self.validation_spec,
                             lambda spec, proof: self.review_spec(backend, spec, proof),
                             lambda spec, vp, rp, outcome: self.publication_request(authority, spec, vp, rp, outcome))

    def validation_spec(self, report, outcome):
        if self.validation_input is None:
            workspace = report.workspace
            git(workspace, "add", "source")
            git(workspace, "-c", "user.name=UIF Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "disposable candidate")
            binding = StageBinding(report, workspace, self.fixture.source.head, git(workspace, "rev-parse", "HEAD"),
                                   git(workspace, "rev-parse", "HEAD^{tree}"), self.fixture.branch,
                                   outcome.worker_receipt_path, outcome.worker_receipt_sha256)
            args = wire(dict(action="run", job_id="fixture-validation", command="python -m unittest"))
            self.validation_input = ValidationSpec(binding, binding.key("validation"), str(UUID(int=88)),
                                                   "fixture-validation", args, "3" * 64, "4" * 64, True)
        return self.validation_input

    def finish_validation(self):
        spec = self.validation_input
        receipt = dict(job_id=spec.runner_job_id, status="succeeded", exit_code=0, child_tree_gone=True,
                       reconcile_required=False, runner_sha256=spec.runner_sha256,
                       capacity_policy_sha256=spec.capacity_policy_sha256, command_sha256=spec.command_sha256)
        self.wm.job = dict(id=spec.windmill_job_id, workspace_id="foo", script_path="u/uif/validation",
                           type="CompletedJob", running=False, success=True, args=decode(spec.args_json),
                           result=dict(job_id=spec.runner_job_id, job_succeeded=True, receipt=receipt))

    def review_spec(self, backend, validation, proof):
        if self.review_input is not None:
            return self.review_input
        b, task = validation.binding, validation.binding.task
        stopped = ReviewTests()
        stopped.f = SimpleNamespace(root=self.runtime)
        stopped.reviewer = "fixture-independent-reviewer"
        stopped.directory = self.runtime / "attempts" / ("review-" + task.key.workspace_key)
        files = decode(b.coding_receipt.read_bytes())["output_sha256"]
        stopped.source = dict(root=str(b.candidate), head=b.head, base=b.base, tree=b.tree, branch=b.branch,
                              outputs=files, files_sha256=fingerprint(files))
        stopped.pool = dict(owner=stopped.reviewer, mode="native-review-read-only", read_roots=[str(b.candidate)],
                            write_roots=[], head=b.head, branch=b.branch, source_sha256=stopped.source["files_sha256"],
                            task_sha256=fingerprint(task.to_record()), resources={})
        invocation, lease = str(UUID(int=89)), "fixture-review-lease"
        stopped.policy = dict(
            schema="native-readonly-review-v2", project_id=UIF.project_id, owner=task.owner, task=task.to_record(),
            workspace_key=task.key.workspace_key, invocation_id=invocation, lease=lease, author_owner=task.owner,
            reviewer_owner=stopped.reviewer, current_runtime=str(self.runtime),
            validation_identity=proof.operation_key, stage_identity=proof.operation_key,
            validation_receipt=dict(sha256=proof.receipt_sha256, windmill_job_id=proof.windmill_job_id,
                                    runner_job_id=proof.runner_job_id),
            unit_binding=dict(store_id=task.key.store_id, row_id=task.key.row_id, operation_id=task.original_operation_id,
                              owner=task.owner, epoch=task.epoch, dispatch_sha256=b.dispatch.receipt_sha256,
                              coding_sha256=b.coding_receipt_sha256),
            author_receipts=[dict(path=str(b.coding_receipt), sha256=b.coding_receipt_sha256)],
            excluded_owners=[task.owner], excluded_sessions=["fixture-author"], require_stopped_child=True,
            timeout_seconds=30, acceptance={}, resources={}, provider="codex-chatgpt", model="gpt-6.1-sol",
            effort="medium", source=stopped.source)
        policy = self.runtime / "review-policy.json"
        policy.write_bytes(wire(stopped.policy))
        stopped.spec = ReviewSpec(validation, proof.receipt_sha256, stopped.reviewer, lease, policy,
                                  stopped.directory, file_digest(policy), wire(stopped.pool), True,
                                  invocation_id=invocation)
        stopped.backend = backend
        # Only the stopped review worker is substituted. The coding worker
        # and coordinator use the retained Pool; the review observer reads it.
        def review_lease(lease_id, binding):
            with self.pool._transaction() as db:
                db.execute("INSERT INTO leases(id,binding,fingerprint,status,outcome,created) VALUES(?,?,?,?,?,?)",
                           (lease_id, wire(binding).decode(), fingerprint(binding), "finished", "succeeded", 0))

        with patch.object(stopped, "put_lease", side_effect=review_lease):
            stopped.review_receipt()
        self.review_input = stopped.spec
        return stopped.spec

    def publication_request(self, authority, spec, validation, review, outcome):
        if self.publication_input is not None:
            return self.publication_input
        b = spec.binding
        self.native.merge = git(b.candidate, "-c", "user.name=UIF Fixture", "-c", "user.email=fixture@example.invalid",
                                "commit-tree", b.tree, "-p", b.base, "-p", b.head, "-m", "disposable merge")
        self.candidate = b.candidate
        request = self.native.request
        source = dict(repository=UIF.repository, branch=b.branch, base=b.base, head=b.head, tree=b.tree,
                      target=UIF.accepted_branch, policy_sha256=request.policy_sha256)
        self.native.request = request = replace(
            request, admission=b.dispatch.ledger, issue=None, task=b.task,
            source_base=b.base, source_head=b.head, source_tree=b.tree, source_branch=b.branch,
            source_sha256=fingerprint(source), capacity_lease_id=outcome.lease_id,
            validation_operation_id=validation.operation_key, validation_job_id=validation.windmill_job_id,
            validation_receipt_sha256=validation.receipt_sha256, review_operation_id=review.operation_key,
            reviewer=review.reviewer, review_receipt_sha256=review.receipt_sha256)
        readiness = self.native.result()["receipt"]["body"]
        inner = {**request.envelope(observe=True)["request"], "mode": "observe"}
        response = inner_response(inner, "ready")
        readiness["intent"].update(request=inner, request_sha256=fingerprint(inner),
                                   invocation_id=request.readiness_invocation_id)
        readiness["terminal"].update(response=response, response_sha256=fingerprint(response))
        reference = self.owner.ref("original-ready.json", readiness)
        with authority.lock():
            catalogue, pin = authority.catalogue()
            catalogue["rows"][ROW]["publication_readiness"] = reference
            authority._replace_catalogue(catalogue, pin)
        self.native.request = self.publication_input = replace(request, readiness_receipt_sha256=reference["sha256"])
        return self.publication_input

    def publish_transport(self, envelope):
        self.calls.append(envelope["mode"])
        if envelope["mode"] == "invoke":
            git(self.native.bare, "fetch", "--quiet", str(self.candidate),
                f"+{self.native.merge}:refs/heads/develop")
            raise SourceHold("fixture lost publication acknowledgement")
        return self.native.result()

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_unregistered_row_plan_does_not_create_a_claim(self):
        self.setup_connected()
        coordinator = self.coordinator()
        coordinator.bindings = replace(coordinator.bindings, plan_for=lambda *_: None)
        with self.assertRaisesRegex(SourceHold, "stage_registration"):
            await coordinator.tick(asyncio.Event())
        self.assertFalse(self.owner.claim_file.exists())
        self.assertEqual(self.pool.status(), [])

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_row_local_preflight_hold_allows_an_independent_claim(self):
        self.setup_connected()
        self.prepare_successor()
        coordinator = self.coordinator()
        blocked, ready = [row.key.row_id for row in coordinator.intake.select_ready().eligible]
        resolve = coordinator.bindings.plan_for
        coordinator.bindings = replace(
            coordinator.bindings, plan_for=lambda key, runtime: None if key.row_id == blocked else resolve(key, runtime))
        before = coordinator.authority.catalogue()[0]["rows"][blocked]
        originals, _ = coordinator._schedule(asyncio.Event(), {})
        self.assertEqual([original.key.row_id for original in originals], [ready])
        rows = coordinator.authority.catalogue()[0]["rows"]
        self.assertEqual(rows[blocked], before)
        self.assertFalse((Path(before["runtime"]) / "current-admission.json").exists())
        self.assertEqual(rows[ready]["disposition"], "claimed")
        self.assertEqual(self.pool.status(), [])

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_missing_row_capacity_policy_allows_an_independent_claim(self):
        self.setup_connected()
        self.prepare_successor()
        coordinator = self.coordinator()
        selection = coordinator.intake.select_ready().eligible
        blocked, ready = [row.key.row_id for row in selection]
        before = coordinator.authority.catalogue()[0]["rows"]
        runtime = Path(before[blocked]["runtime"])
        policy = runtime / "capacity-policy.json"
        retained = policy.rename(runtime / "capacity-policy.unavailable")
        self.assertEqual(coordinator.intake.select_ready().eligible, selection)

        with patch.object(coordinator, "_pool_has_slot",
                          side_effect=SourceHold("uif_ledger_coordinator_pool_unavailable")):
            with self.assertRaisesRegex(SourceHold, "pool_unavailable"):
                coordinator._schedule(asyncio.Event(), {})
        self.assertEqual(coordinator.authority.catalogue()[0]["rows"], before)

        originals, _ = coordinator._schedule(asyncio.Event(), {})
        self.assertEqual([original.key.row_id for original in originals], [ready])
        rows = coordinator.authority.catalogue()[0]["rows"]
        self.assertEqual(rows[blocked], before[blocked])
        self.assertFalse(policy.exists())
        self.assertTrue(retained.is_file())
        self.assertFalse((runtime / "current-admission.json").exists())
        self.assertFalse((runtime / "attempts").exists())
        self.assertEqual(rows[ready]["disposition"], "claimed")
        self.assertEqual(self.pool.status(), [])

    @skipUnless(CAPACITY and os.geteuid() != 0, "requires the retained Pool and an unprivileged user")
    async def test_unreadable_row_capacity_policy_allows_an_independent_claim(self):
        self.setup_connected()
        self.prepare_successor()
        coordinator = self.coordinator()
        selection = coordinator.intake.select_ready().eligible
        blocked, ready = [row.key.row_id for row in selection]
        before = coordinator.authority.catalogue()[0]["rows"]
        runtime = Path(before[blocked]["runtime"])
        policy = runtime / "capacity-policy.json"
        policy.chmod(0)
        with self.assertRaises(PermissionError):
            policy.read_bytes()
        self.assertEqual(coordinator.intake.select_ready().eligible, selection)

        with patch.object(coordinator, "_pool_has_slot", side_effect=PermissionError("shared Pool unreadable")):
            with self.assertRaisesRegex(PermissionError, "shared Pool unreadable"):
                coordinator._schedule(asyncio.Event(), {})
        self.assertEqual(coordinator.authority.catalogue()[0]["rows"], before)

        originals, _ = coordinator._schedule(asyncio.Event(), {})
        self.assertEqual([original.key.row_id for original in originals], [ready])
        rows = coordinator.authority.catalogue()[0]["rows"]
        self.assertEqual(rows[blocked], before[blocked])
        self.assertEqual(policy.stat().st_mode & 0o777, 0)
        self.assertFalse((runtime / "current-admission.json").exists())
        self.assertFalse((runtime / "attempts").exists())
        self.assertEqual(rows[ready]["disposition"], "claimed")
        self.assertEqual(self.pool.status(), [])

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_partial_claim_holds_replacement_across_restart(self):
        self.setup_connected()
        self.prepare_successor()
        coordinator = self.coordinator()
        blocked, ready = [row.key.row_id for row in coordinator.intake.select_ready().eligible]
        rows = coordinator.authority.catalogue()[0]["rows"]
        with patch.object(coordinator.authority, "_replace_catalogue", side_effect=OSError("claim write interrupted")):
            with self.assertRaisesRegex(OSError, "claim write interrupted"):
                coordinator._schedule(asyncio.Event(), {})
        receipt = Path(rows[blocked]["runtime"]) / "current-admission.json"
        self.assertTrue(receipt.is_file())
        with self.assertRaisesRegex(SourceHold, "claim_unreconciled"):
            self.coordinator()._schedule(asyncio.Event(), {})
        self.assertFalse((Path(rows[ready]["runtime"]) / "current-admission.json").exists())
        self.assertEqual(coordinator.authority.catalogue()[0]["rows"], rows)
        self.assertEqual(self.pool.status(), [])

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_occupied_shared_pool_defers_new_claim(self):
        self.setup_connected()
        provider = decode(self.fixture.provider.read_bytes())
        host = dict(cpu_millis=4000, available_memory_bytes=2**40, available_disk_bytes=2**40)
        for index in range(2):
            self.pool.admit(f"other-{index}", dict(branch="feature/other", head="1" * 40, owner="other",
                                                  write_roots=[f"other-{index}"], task_sha256="2" * 64,
                                                  resources=dict(cpu_millis=100, memory_bytes=1048576,
                                                                 io_slots=1, output_bytes=1024)),
                            provider, host)
        self.assertEqual(await self.coordinator().tick(asyncio.Event()), Progress("idle"))
        self.assertFalse(self.owner.claim_file.exists())
        self.assertEqual(len(self.pool.status()), 2)

    def prepare_successor(self, *, disposition="ready"):
        f, authority = self.fixture, self.owner.authority()
        runtime = f.root / "successor"
        (runtime / "requests").mkdir(parents=True, mode=0o700)
        (runtime / "workspaces").mkdir(mode=0o700)
        runtime.chmod(0o700)
        key = LedgerTaskKey(authority.store_id, SUCCESSOR)
        source_sha = read_snapshot(SNAPSHOT)[SUCCESSOR].source_sha256
        target = ledger_task_target(key, source_sha)
        task = {name: value for name, value in f.manifest["tasks"][f.issue.identifier].items()
                if name != "issue_id"}
        task["allowed_paths"] = ["other"]
        manifest = {**f.manifest, "tasks": {key.workspace_key: {**task, "ledger_task": target}}}
        inputs = dict(
            source=str(f.checkout), inputs={}, manifest_sha256=put(runtime / "manifest.json", manifest),
            request_sha256=put(runtime / "requests" / (key.workspace_key + ".json"),
                               dict(prompt="fixture prompt", timeout_seconds=12, ledger_task=target)),
            policy_sha256=put(runtime / "capacity-policy.json", f.policy),
            installation_sha256=put(runtime / "installation.json", {}))
        lease = "rt-" + _fingerprint(dict(runtime=str(runtime), manifest_sha256=_fingerprint(manifest),
                                          ledger_task=key.workspace_key))
        effect = dict(schema="uif-original-effect/1", store_id=key.store_id, row_id=key.row_id,
                      owner=authority.owner, epoch=authority.epoch, source_sha256=source_sha,
                      runtime=str(runtime), lease_ids=[lease], state="confirmed-absent")
        entry = dict(runtime=str(runtime), source_sha256=source_sha, phase="implementation", disposition=disposition,
                     issue=None, counterparts=[], readiness=[], lease_ids=[lease],
                     effect=self.owner.ref("successor-effect.json", effect),
                     admission_inputs=self.owner.ref("successor-inputs.json", inputs),
                     selected_source=self.owner.entry["selected_source"],
                     provider_sha256=put(runtime / "provider-proof.json", decode(f.provider.read_bytes())))
        with authority.lock():
            catalogue, pin = authority.catalogue()
            catalogue["rows"][SUCCESSOR] = entry
            authority._replace_catalogue(catalogue, pin)

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_shutdown_drains_original_review_waiter_after_later_scans(self):
        self.setup_connected()
        coordinator = self.coordinator()
        stop = asyncio.Event()
        popen = subprocess.Popen

        def start(argv, **kwargs):
            if argv[:2] != [sys.executable, str(self.supervisor)]:
                return popen(argv, **kwargs)
            return popen([sys.executable, "-B", "-c", stopped_supervisor(Path(CAPACITY)),
                          str(self.supervisor.parents[1]), self.owner.registration["path"],
                          self.owner.registration["sha256"], *argv[1:]], **kwargs)

        with patch("uif.dispatch.subprocess.Popen", side_effect=start):
            self.assertEqual(await coordinator.tick(stop), Progress("validation_unknown", ROW))
        self.finish_validation()
        waiting, release, rescanned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resolve = coordinator.bindings.plan_for
        waiters = []

        class Child:
            async def wait(self):
                waiters.append(asyncio.current_task())
                waiting.set()
                await release.wait()
                return 0

        def resolve_plan(key, runtime):
            if waiting.is_set():
                rescanned.set()
            return resolve(key, runtime)

        coordinator.bindings = replace(coordinator.bindings, plan_for=resolve_plan)
        with patch.object(ReviewTests, "review_receipt", return_value=None), patch(
                "uif.review.asyncio.create_subprocess_exec", return_value=Child()) as spawn:
            run = asyncio.create_task(coordinator.run(stop, interval_seconds=0.01))
            try:
                await asyncio.wait_for(rescanned.wait(), 2)
                stop.set()
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(run), 0.05)
                self.assertEqual(len(waiters), 1)
                self.assertFalse(waiters[0].done())
                spawn.assert_awaited_once()
            finally:
                stop.set()
                release.set()
                await asyncio.wait_for(run, 2)
                await asyncio.gather(*waiters)
            self.assertEqual(await coordinator.tick(asyncio.Event()), Progress("review_unknown", ROW))
            spawn.assert_awaited_once()
        self.assertIsNone(coordinator.authority.read_completion(
            self.review_input.binding.task.original_operation_id))

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_shutdown_deadline_leaves_pending_review_untouched(self):
        self.setup_connected()
        coordinator = self.coordinator()
        stop, release = asyncio.Event(), asyncio.Event()
        stop.set()
        waiter = asyncio.create_task(release.wait())
        coordinator._review_waiters.add(waiter)
        try:
            with patch("uif.ledger_coordinator.REVIEW_DRAIN_SECONDS", 0.01):
                with self.assertRaisesRegex(SourceHold, "review_drain_timeout"):
                    await asyncio.wait_for(coordinator.run(stop), 1)
            self.assertFalse(waiter.done())
            self.assertFalse(self.owner.claim_file.exists())
        finally:
            release.set()
            await waiter

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_tick_drives_real_pool_recovery_and_distinct_refill(self):
        self.setup_connected()
        stop = asyncio.Event()
        popen = subprocess.Popen

        def start(argv, **kwargs):
            if argv[:2] != [sys.executable, str(self.supervisor)]:
                return popen(argv, **kwargs)
            self.children += 1
            if self.children == 2:
                stop.set()
            with (self.fixture.root / "worker-stderr").open("ab") as stderr:
                return popen([sys.executable, "-B", "-c", stopped_supervisor(Path(CAPACITY)),
                              str(self.supervisor.parents[1]), self.owner.registration["path"],
                              self.owner.registration["sha256"], *argv[1:]], **{**kwargs, "stderr": stderr})

        self.prepare_successor(disposition="held")
        first = self.coordinator()
        with first._admission_fence():
            with self.assertRaisesRegex(SourceHold, "coordinator_busy"):
                await self.coordinator().tick(stop)
        self.assertFalse(self.owner.claim_file.exists())
        with patch("uif.dispatch.subprocess.Popen", side_effect=start):
            self.assertEqual(await first.tick(stop), Progress("validation_unknown", ROW))
            self.assertEqual(self.owner.authority().catalogue()[0]["rows"][SUCCESSOR]["disposition"], "held")
            stop.set()
            self.assertEqual(await self.coordinator().tick(stop), Progress("stopped"))
            self.assertEqual([call[0] for call in self.wm.calls], ["get", "post"])
            stop.clear()
            self.finish_validation()
            with self.assertRaisesRegex(SourceHold, "lost publication acknowledgement"):
                await self.coordinator().tick(stop)
            authority = self.owner.authority()
            original = self.publication_input.original_operation_id
            self.assertIsNone(authority.read_completion(original))
            self.assertEqual(authority.catalogue()[0]["rows"][SUCCESSOR]["disposition"], "held")
            self.assertEqual(await self.coordinator().tick(stop), Progress("completed", ROW))
            self.assertEqual(self.owner.authority().read_completion(original).publication.head, self.native.merge)
            with authority.lock():
                catalogue, pin = authority.catalogue()
                catalogue["rows"][SUCCESSOR]["disposition"] = "ready"
                authority._replace_catalogue(catalogue, pin)
            await asyncio.wait_for(self.coordinator().run(stop, interval_seconds=0.01), 5)
            self.assertTrue(stop.is_set())
        rows = self.owner.authority().catalogue()[0]["rows"]
        self.assertEqual(rows[ROW]["disposition"], "completed")
        self.assertEqual(rows[SUCCESSOR]["disposition"], "claimed")
        successor = decode(Path(rows[SUCCESSOR]["claim"]["path"]).read_bytes())["ledger"]
        self.assertNotEqual(successor["original_operation_id"], original)
        self.assertEqual(self.children, 2)
        self.assertEqual(self.calls.count("invoke"), 1)
        self.assertEqual(sum(call[0] == "post" for call in self.wm.calls), 1)

    @skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the pinned retained Pool")
    async def test_two_independent_originals_hold_real_pool_leases_concurrently(self):
        self.setup_connected()
        self.prepare_successor()
        stop = asyncio.Event()
        popen = subprocess.Popen

        def start(argv, **kwargs):
            if argv[:2] != [sys.executable, str(self.supervisor)]:
                return popen(argv, **kwargs)
            self.children += 1
            with (self.fixture.root / "parallel-worker-stderr").open("ab") as stderr:
                return popen([sys.executable, "-B", "-c", stopped_supervisor(Path(CAPACITY), delay=0.8),
                              str(self.supervisor.parents[1]), self.owner.registration["path"],
                              self.owner.registration["sha256"], *argv[1:]], **{**kwargs, "stderr": stderr})

        async def observe_overlap():
            while True:
                running = [row for row in self.pool.status() if row["status"] == "running"]
                if len(running) == 2:
                    self.assertEqual({tuple(row["binding"]["write_roots"]) for row in running},
                                     {("source",), ("other",)})
                    stop.set()
                    return
                await asyncio.sleep(0.01)

        with patch("uif.dispatch.subprocess.Popen", side_effect=start):
            coordinator = asyncio.create_task(self.coordinator().run(stop, interval_seconds=0.01))
            try:
                await asyncio.wait_for(observe_overlap(), 5)
            finally:
                stop.set()
                await asyncio.wait_for(coordinator, 5)
        rows = self.owner.authority().catalogue()[0]["rows"]
        self.assertEqual(self.children, 2)
        self.assertEqual([row["status"] for row in self.pool.status()], ["finished", "finished"])
        self.assertNotEqual(rows[ROW]["coding_outcome"]["operation_id"], rows[SUCCESSOR]["coding_outcome"]["operation_id"])
        self.assertFalse(self.wm.calls)
        restarted = self.coordinator()
        originals, _ = restarted._schedule(asyncio.Event(), {})
        self.assertEqual({original.key.row_id for original in originals}, {ROW, SUCCESSOR})
        for original in originals:
            entry, runtime = restarted.authority.admission._entry(RowKey(original.key.store_id, original.key.row_id))
            task = restarted._claimed(original.key, entry, runtime)
            _, outcome = restarted._read_dispatch(task, entry["dispatch_intent"])
            self.assertEqual((outcome.operation_id, outcome.status), (task.original_operation_id, "succeeded"))
