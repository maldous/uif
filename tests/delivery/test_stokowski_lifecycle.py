"""Check the UIF workspace and reporting wiring at the upstream hook boundary."""

from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from uif.source import SourceHold, file_digest
from uif.stokowski import (
    UIF, LedgerAdmission, PolledIssue, Workspace, bind_uif_lifecycle,
)
import test_stokowski_control as control_fixtures


@dataclass
class Attempt:
    issue_id: str
    issue_identifier: str
    workspace_path: str
    status: str = "succeeded"
    attempt: int | None = None


class LifecycleTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.selected = PolledIssue("UIF-900001", "1765ee92-55d6-4012-a889-95e65555711d",
                                    UIF.project_id, UIF.project_slug)
        self.issue = SimpleNamespace(id=self.selected.issue_id, identifier=self.selected.identifier)
        self.coordinator_root = self.root / "coordinator" / "workspaces"
        self.workspace = self.root / "task" / "workspaces" / self.selected.identifier
        self.workspace.mkdir(parents=True)
        self.output = self.workspace / "unfinished"
        self.output.write_text("retain partial work")
        self.receipt = self.root / "task" / "dispatch-intent.json"
        self.receipt.write_text("retain uncertain operation")
        self.receipt.chmod(0o600)
        self.attempt = Attempt(self.issue.id, self.issue.identifier, str(self.workspace))
        self.reported = []
        self.acquired = []

        async def legacy(*args, **kwargs):
            self.fail("legacy lifecycle effect called")

        def acquire(issue, root, *, require_ledger):
            self.assertTrue(require_ledger)
            self.acquired.append((issue, root))
            return Workspace(self.workspace, issue.identifier, False)

        self.orchestrator = SimpleNamespace(ensure_workspace=legacy, remove_workspace=legacy)
        self.workspace_module = SimpleNamespace(ensure_workspace=legacy, remove_workspace=legacy,
                                                run_hook=legacy, WorkspaceResult=Workspace)
        self.owner = SimpleNamespace(_publish_run_report=legacy, _ensure_linear_client=legacy)
        self.boundary = SimpleNamespace(ensure_workspace=acquire, runtime_root=self.root)

    def bind(self, reporter):
        self.register = bind_uif_lifecycle(self.orchestrator, self.workspace_module, self.owner,
                           boundary=self.boundary, polled_issue=lambda identifier: self.selected,
                           report_attempt=reporter)

    async def report(self, binding):
        self.reported.append(binding)

    def register_attempt(self, attempt=None, receipt=None, operation="fixture-original-op"):
        receipt = receipt or self.receipt
        return self.register(self.issue, attempt or self.attempt, self.workspace,
                             operation_id=operation, receipt_path=receipt, receipt_sha256=file_digest(receipt))

    async def test_workspace_aliases_acquire_and_retain(self):
        self.bind(self.report)
        self.assertIs(self.orchestrator.ensure_workspace, self.workspace_module.ensure_workspace)
        self.assertIs(self.orchestrator.remove_workspace, self.workspace_module.remove_workspace)
        for module in (self.orchestrator, self.workspace_module):
            result = await module.ensure_workspace(self.coordinator_root, self.issue.identifier, {})
            self.assertEqual(result, Workspace(self.workspace, self.issue.identifier, False))
            await module.remove_workspace(self.workspace.parent, self.issue.identifier, {})
        self.assertEqual(self.acquired, [(self.selected, self.coordinator_root)] * 2)
        self.assertEqual(self.output.read_text(), "retain partial work")
        self.assertEqual(self.receipt.read_text(), "retain uncertain operation")
        with self.assertRaisesRegex(SourceHold, "uif_shell_hook_refused"):
            await self.workspace_module.run_hook("fixture shell hook", self.workspace, 1000, "fixture")

    async def test_report_uses_only_bound_platform_callback(self):
        self.bind(self.report)
        binding = self.register_attempt()
        await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(self.reported, [binding])
        self.assertEqual(self.attempt.status, "succeeded")

    async def test_report_identity_drift_and_foreign_project_hold(self):
        self.bind(self.report)
        self.register_attempt()
        self.attempt.issue_id = "different-attempt"
        with self.assertLogs("uif.stokowski", "WARNING"):
            await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(self.reported, [])
        self.attempt.issue_id = self.issue.id
        self.selected = replace(self.selected, project_slug="foreign")
        self.attempt.attempt = 4
        with self.assertLogs("uif.stokowski", "WARNING"):
            await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        with self.assertRaisesRegex(SourceHold, "uif_lifecycle_issue_binding"):
            await self.orchestrator.ensure_workspace(self.coordinator_root, self.issue.identifier, {})
        self.assertEqual(self.reported, [])
        self.assertEqual(self.acquired, [])

    async def test_report_failure_preserves_attempt_without_fallback(self):
        async def reporter(binding):
            self.reported.append(binding)
            raise OSError("fixture ambiguous report effect")

        self.bind(reporter)
        binding = self.register_attempt()
        with self.assertLogs("uif.stokowski", "WARNING") as log:
            await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(self.reported, [binding])
        self.assertNotIn("fixture ambiguous report effect", str(log.output))
        self.assertEqual(self.attempt.status, "succeeded")
        self.assertEqual(self.receipt.read_text(), "retain uncertain operation")

    async def test_distinct_and_late_attempts_keep_their_dispatch_receipts(self):
        self.bind(self.report)
        first = self.register_attempt()
        next_attempt = replace(self.attempt, attempt=2)
        next_receipt = self.receipt.with_name("other-dispatch-intent.json")
        next_receipt.write_text("next original operation")
        next_receipt.chmod(0o600)
        second = self.register_attempt(next_attempt, next_receipt, "fixture-second-op")
        with self.assertRaisesRegex(SourceHold, "uif_report_dispatch_rebind"):
            self.register_attempt(self.attempt, next_receipt, "fixture-second-op")
        self.selected = replace(self.selected, project_slug="now-unavailable")
        await self.owner._publish_run_report(self.issue, next_attempt, self.workspace, "coding")
        await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(self.reported, [second, first])
        self.assertEqual([row.operation_id for row in self.reported], ["fixture-second-op", "fixture-original-op"])
        self.assertEqual(self.receipt.read_text(), "retain uncertain operation")
        self.assertEqual(next_receipt.read_text(), "next original operation")

    async def test_missing_or_stale_receipt_refuses_reporting(self):
        self.bind(self.report)
        with self.assertLogs("uif.stokowski", "WARNING"):
            await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.register_attempt()
        unregistered = replace(self.attempt)
        with self.assertLogs("uif.stokowski", "WARNING"):
            await self.owner._publish_run_report(self.issue, unregistered, self.workspace, "coding")
        self.receipt.write_text("receipt changed after dispatch")
        with self.assertLogs("uif.stokowski", "WARNING"):
            await self.owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(self.reported, [])
        self.assertEqual(self.receipt.read_text(), "receipt changed after dispatch")

    def test_missing_report_boundary_refuses_before_binding(self):
        hooks = self.orchestrator.ensure_workspace, self.orchestrator.remove_workspace
        with self.assertRaisesRegex(SourceHold, "uif_report_boundary_required"):
            self.bind(None)
        self.assertEqual(hooks, (self.orchestrator.ensure_workspace, self.orchestrator.remove_workspace))


class LedgerWorkspaceTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = control_fixtures.SourceControlTests()
        self.addCleanup(self.source.doCleanups)
        self.source.setUp()
        self.source.acquire()
        self.boundary = self.source.boundary()
        self.context = self.boundary.qualify_context
        self.admission = LedgerAdmission(
            "fixture-store", "1" * 64, "fixture-row", "2" * 64, "implementation",
            "fixture-owner", "fixture-epoch", "fixture-original-op", "ready", "confirmed-absent", False,
        )
        self.coordinator = SimpleNamespace()
        workspace = SimpleNamespace(WorkspaceResult=Workspace)
        owner = SimpleNamespace()

        async def report(selected, location):
            self.fail("acquisition unexpectedly reported")

        bind_uif_lifecycle(self.coordinator, workspace, owner, boundary=self.boundary,
                           polled_issue=lambda identifier: self.source.issue, report_attempt=report)

    def qualify(self, issue):
        return replace(self.context(issue), ledger=self.admission)

    async def ensure(self):
        return await self.coordinator.ensure_workspace(
            self.source.coordinator / "workspaces", self.source.issue.identifier, {},
        )

    async def test_unqualified_or_held_ledger_work_never_reaches_source(self):
        cases = (
            (None, "uif_ledger_admission_required"),
            (replace(self.admission, original_operation_id=""), "uif_ledger_admission_binding"),
            (replace(self.admission, disposition="completed"), "uif_ledger_admission_held"),
            (replace(self.admission, disposition="superseded"), "uif_ledger_admission_held"),
            (replace(self.admission, phase="review"), "uif_ledger_admission_held"),
            (replace(self.admission, prior_effect="unknown"), "uif_original_operation_requires_reconciliation"),
            (replace(self.admission, combined=True), "uif_combined_counterpart_held"),
        )
        self.boundary.qualify_context = self.qualify
        for admission, reason in cases:
            with self.subTest(reason=reason):
                self.admission = admission
                with self.assertRaisesRegex(SourceHold, reason):
                    await self.ensure()
        self.assertFalse(any(call[0] == "selected" for call in self.source.calls))
        self.assertFalse((self.source.runtime / "workspaces" / self.source.issue.identifier).exists())

    async def test_eligible_workspace_keeps_original_binding_across_acquisition(self):
        self.boundary.qualify_context = self.qualify
        result = await self.ensure()
        self.assertTrue(result.created)
        self.assertEqual(result.path, self.source.runtime / "workspaces" / self.source.issue.identifier)

    async def test_original_operation_drift_refuses_workspace(self):
        first = self.admission

        def changed_context(issue):
            context = self.qualify(issue)
            self.admission = replace(first, original_operation_id="different-original-op")
            return context

        self.boundary.qualify_context = changed_context
        with self.assertRaisesRegex(SourceHold, "workspace_selection_changed"):
            await self.ensure()
        self.assertFalse((self.source.runtime / "workspaces" / self.source.issue.identifier).exists())
