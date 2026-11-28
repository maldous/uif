"""Exercise the exact staged native prologue with detached authority ports."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from uif.handoff_store import Hold
from uif.stokowski_cloudflare import launcher
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native_composition import NativeComposition, _COMMENT_SLOTS, install_sinks
from test_native_remote_authority import ISSUE, RECORD, ROUTE


RECONCILE_PREIMAGES = {
    '228523e5a9e78ed5ad3ea78c8fe8f37093e5ce2cf5460068215ffb655028fa96',
    '3b8a3bfd6291bb96560b5ff2b3f9b2af5ef7aa61eb3bcb81f75b1e89fef3ba38',
    'afdc9c589570edfb24d62ecb0e8caf8a3e5fe045230dc93e6225b33a2ccfe431',
}
RECONCILE_POSTIMAGE = '59b063610dea19c18b736d3949607712f4c3ad0d8187c9956e5d6019b29d0352'


class RecipientLauncherPrologueTest(IsolatedAsyncioTestCase):
    def setUp(self):
        staged = os.environ.get('UIF_PINNED_RECIPIENT_NATIVE')
        if staged is None:
            self.skipTest('select exact staged native dependencies with UIF_PINNED_RECIPIENT_NATIVE')
        from stokowski import orchestrator, publication, runner
        from stokowski.config import ClaudeConfig, HooksConfig, StateConfig, WorkspaceConfig
        from stokowski.models import Issue

        source = Path(orchestrator.__file__)
        self.assertEqual(source.parent, Path(staged))
        self.native_sha = sha256(source.read_bytes()).hexdigest()
        self.assertIn(self.native_sha, RECONCILE_PREIMAGES | {RECONCILE_POSTIMAGE})
        self.orchestrator, self.runner, self.publication = orchestrator, runner, publication
        owner = self.owner = orchestrator.Orchestrator.__new__(orchestrator.Orchestrator)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.issue = Issue(ISSUE, 'OFF-1', 'Synthetic assessment', state='Backlog')
        self.path = self.root / self.issue.identifier
        self.path.mkdir()
        env = os.environ.copy()
        env.pop('GIT_CONFIG_COUNT', None)
        subprocess.run(['git', 'init', '-q', '-b', 'develop', str(self.path)], env=env, check=True)
        subprocess.run(['git', '-C', str(self.path), 'remote', 'add', 'origin',
                        'https://github.com/maldous/offmenu.git'], env=env, check=True)
        subprocess.run(['git', '-C', str(self.path), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-q', '--allow-empty',
                        '-m', 'synthetic workspace'], env=env, check=True)
        # Native git calls inherit the harness; limit this test's override to its Git environment.
        self.enterContext(patch.dict(os.environ, clear=True, values=env))
        self.clone_marker = self.root / 'unauthorized-clone'
        self.stage = StateConfig(name='assess', type='agent', runner='codex', session='fresh',
                                 permission_mode='plan', admission_review=True)
        config = SimpleNamespace(
            workspace=WorkspaceConfig(root=str(self.root)),
            active_linear_states=lambda: ['Backlog'], terminal_linear_states=lambda: ['Done'],
            hooks=HooksConfig(after_create=f'touch {self.clone_marker}'), claude=ClaudeConfig(),
            agent_env=lambda: {}, tracker=SimpleNamespace(project_slug='offmenu'),
            linear_states=SimpleNamespace(todo='Todo', active='In Progress'),
        )
        owner.workflow = SimpleNamespace(config=config)
        owner.project_name = 'OffMenu'
        owner.pool = SimpleNamespace(is_paused=lambda _: False)
        owner.running, owner._tasks, owner.retry_attempts = {}, {}, {}
        owner._retry_timers = {}
        owner.claimed, owner.completed = set(), set()
        owner._issue_current_state, owner._issue_state_runs = {ISSUE: 'assess'}, {ISSUE: 1}
        owner._pending_gates = {}
        owner._states_for = lambda _: {'assess': self.stage}
        owner._release_slot = Mock()
        owner._release_slot.assert_not_called()
        owner._render_prompt_async = AsyncMock(return_value='Fixture does not use a provider')
        owner._publish_run_report = AsyncMock()
        owner._announce_state = AsyncMock()
        owner._on_agent_event, owner._on_child_pid = Mock(), Mock()

        async def retire(issue, attempt):
            owner.running.pop(issue.id)
            owner._tasks.pop(issue.id)
            owner.claimed.discard(issue.id)
            owner._release_slot(issue.id)

        owner._on_worker_exit = AsyncMock(side_effect=retire)
        self.fetch_issue = AsyncMock(return_value=self.issue)
        owner._ensure_linear_client = lambda: SimpleNamespace(fetch_issue=self.fetch_issue)
        self.config = dict(
            accounts={'OffMenu': ROUTE.account_id}, projects=['OffMenu'], credentials='/etc/unused',
            gateways={ROUTE.account_id: ROUTE.gateway_id}, model=ROUTE.model,
            workers_ai_strict_free_routes={'OffMenu': ROUTE.credential_epoch_sha256},
            admissions={}, project_intake={'OffMenu': {'origin': 'https://github.com/maldous/offmenu.git'}},
        )
        scope = publication.scope(self.issue)
        record = intake_stages(self.config['project_intake']['OffMenu'], 'OffMenu', self.issue, scope, 1)['assess']
        self.claim = replace(RECORD, attempt_id=record['attempt'])
        remote = SimpleNamespace(routes={'OffMenu': ROUTE})
        lifecycle = self.lifecycle = NativeComposition(remote, self.root / 'unused')
        lifecycle.selected = Mock(return_value=False)
        lifecycle.recipient_assignment = Mock(return_value=self.claim)
        lifecycle.current = Mock(side_effect=self.read_current)
        lifecycle.recipient_task_active = Mock(side_effect=lambda *args: self.active)
        lifecycle.verify_recipient_workspace = Mock(return_value=self.path)
        lifecycle.recipient_workspace_for_prologue = Mock(side_effect=self.prologue)
        lifecycle.recipient_first_entry = self.enter_recipient
        lifecycle.prepare_recipient_turn_thread = Mock()
        lifecycle.recipient_assessment_finisher = Mock(return_value=Mock())
        lifecycle.bind_dispatch = Mock(side_effect=AssertionError('Ordinary dispatch entered'))
        lifecycle.fresh_assessment_worker = Mock(side_effect=AssertionError('Fresh assessment entered'))
        self.active = False
        self.entry_error = None
        self.entries = []
        self.bindings = []
        # Restore class and module hooks after installing the exact production launcher.
        for name in ('_dispatch', '_is_eligible', '_run_worker', '_entry_state_for',
                     '_resolve_current_state', '_resume_settlement', '_reconcile', *_COMMENT_SLOTS):
            self.enterContext(patch.object(orchestrator.Orchestrator, name, getattr(orchestrator.Orchestrator, name)))
        for module, name in ((orchestrator, 'ensure_workspace'), (orchestrator, 'run_turn'),
                             (orchestrator, 'LinearClient'),
                             (orchestrator.MultiOrchestrator, 'start')):
            self.enterContext(patch.object(module, name, getattr(module, name)))

    def read_current(self, issue_id):
        self.assertEqual(issue_id, ISSUE)
        self.assertTrue(self.active)
        return self.claim

    @contextmanager
    def enter_recipient(self, owner, issue, scope, attempt, *, expected):
        self.assertIs(expected, self.claim)
        self.assertIs(owner._tasks[issue.id], asyncio.current_task())
        self.assertIs(owner.running[issue.id], attempt)
        self.assertIn(issue.id, owner.claimed)
        self.assertEqual(attempt.session_id, self.claim.attempt_id)
        self.assertIs(attempt._uif_retained_recipient, True)
        self.entries.append(attempt)
        if self.entry_error is not None:
            self.lifecycle.recipient_assignment.side_effect = Hold('Original entry already attempted')
            raise self.entry_error
        self.active = True
        try:
            yield self.claim
        finally:
            self.active = False

    def prologue(self, owner, issue, scope, attempt):
        self.assertTrue(self.active)
        self.assertIs(owner._tasks[issue.id], asyncio.current_task())
        self.assertIs(owner.running[issue.id], attempt)
        self.fetch_issue.assert_awaited_once()
        if not self.path.is_dir():
            raise Hold('Staged workspace moved after current issue read')
        return self.path

    def configure(self):
        install_sinks(self.orchestrator, self.lifecycle)

        def bind_runner(runner, orchestration, *, authorize_turn, **unused):
            async def capture(*, issue, attempt, workspace_path, **_):
                binding = authorize_turn(issue, attempt, workspace_path)
                self.bindings.append(binding)
                attempt.status = 'canceled'
                return attempt
            orchestration.run_turn = capture

        # Fixture joins the native prologue to the launcher and checks which
        # provider arguments are selected. It executes no model or provider call.
        ports = Mock(can_start=Mock(return_value=True), has_started=Mock(return_value=False))
        with (patch.object(launcher, '_protected_json', return_value=self.config),
              patch.object(launcher, 'ProjectAccountRouter', return_value=ports),
              patch.object(launcher, 'WorkersAIClient', return_value=SimpleNamespace(
                  strict_ports_ready=True, model=ROUTE.model, gateways=self.config['gateways'],
                  request_maximum=1, reservation_options=lambda _: {'usage_snapshots': {}})),
              patch.object(launcher, 'install_native_runner', side_effect=bind_runner)):
            launcher.configure(self.runner, self.orchestrator, self.publication,
                               policy=self.root / 'fixture-policy', database=self.root / 'fixture-budget',
                               authority_lifecycle=self.lifecycle, allows_native_dispatch=lambda *_: True)
        self.enterContext(patch.object(launcher, '_protected_json', return_value=self.config))

    def require_turn_binding(self):
        if 'prepare_turn_thread' not in launcher.NativeTurnBinding.__dataclass_fields__:
            self.skipTest('compose the independently reviewed native turn thread adapter')

    def require_reconcile_patch(self):
        self.require_turn_binding()
        if self.native_sha != RECONCILE_POSTIMAGE:
            self.skipTest('apply the reviewed native reconciliation patch')

    def prepare_successor(self):
        self.require_reconcile_patch()
        self.issue.state = 'In Progress'
        self.owner.workflow.config.active_linear_states = lambda: ['In Progress']
        self.owner._issue_current_state[ISSUE] = 'investigate'
        self.stage = replace(self.stage, name='investigate', admission_review=False)
        self.owner._states_for = lambda _: {'investigate': self.stage}
        scope = self.publication.scope(self.issue)
        stages = intake_stages(self.config['project_intake']['OffMenu'], 'OffMenu', self.issue, scope, 1)
        head = subprocess.check_output(['git', '-C', str(self.path), 'rev-parse', 'HEAD'], text=True).strip()
        tree = subprocess.check_output(['git', '-C', str(self.path), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
        source = replace(self.claim.lease.fence.source, branch='develop', base=head, head=head, tree=tree)
        self.claim = replace(self.claim, attempt_id=stages['investigate']['attempt'],
                             lease=replace(self.claim.lease, fence=replace(self.claim.lease.fence, source=source)))
        self.publication.save(self.path, Path('.stokowski/settlement.json'), {
            'issue_id': ISSUE, 'run': 1, 'scope_sha256': scope, 'state': 'assess',
            'phase': 'complete', 'event': 'complete', 'target': 'investigate',
            'session_id': stages['assess']['attempt'],
        })
        lifecycle = self.lifecycle
        lifecycle.assignment = Mock(return_value=SimpleNamespace(first_intent_sha256='a' * 64))
        lifecycle.selected.return_value = True  # An unsafe fallback would choose ordinary dispatch.
        lifecycle.successor_assignment = Mock(return_value=self.claim)
        lifecycle.successor_first_entry = self.enter_recipient
        lifecycle.successor_workspace_for_prologue = Mock(side_effect=self.prologue)
        lifecycle.successor_task_active = Mock(side_effect=lambda *args: self.active)
        lifecycle.verify_successor_workspace = Mock(return_value=self.path)
        lifecycle.prepare_successor_turn_thread = Mock()
        lifecycle.successor_investigation_finisher = Mock(return_value=Mock())
        self.configure()
        return lifecycle

    async def test_exact_consumed_successor_uses_native_investigation_without_source_tools(self):
        native = self.prepare_successor()
        self.assertTrue(native._successor_native_worker_qualified)
        self.assertTrue(native._successor_stage_qualification(self.owner, self.issue, 1))
        self.owner._issue_current_state[ISSUE] = 'assess'
        self.assertTrue(native._successor_stage_qualification(self.owner, self.issue, 1))
        self.owner._issue_current_state[ISSUE] = 'investigate'

        self.assertTrue(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        await self.owner._tasks[ISSUE]
        self.assertEqual(len(self.entries), 1)
        self.assertEqual(self.entries[0].state_name, 'investigate')
        (binding,) = self.bindings
        self.assertEqual(binding.attempt.id, self.claim.attempt_id)
        self.assertEqual((binding.tracked_paths, binding.write_roots, binding.remote_tools), ((), (), None))
        self.assertTrue(binding.one_shot_report)
        self.assertIs(binding.finish_original, native.successor_investigation_finisher.return_value)
        self.assertIs(binding.prepare_turn_thread, native.prepare_successor_turn_thread)
        self.owner._announce_state.assert_awaited_once_with(self.issue, 'investigate', 1)
        native.recipient_assignment.assert_not_called()
        native.recipient_assessment_finisher.assert_not_called()
        native.bind_dispatch.assert_not_called()
        self.assertFalse(self.clone_marker.exists())

    async def test_cold_active_resolver_restores_only_a_verified_unentered_successor(self):
        native = self.prepare_successor()
        from stokowski.tracking import make_state_comment

        stages = {'assess': replace(self.stage, name='assess', admission_review=True),
                  'investigate': self.stage}
        self.owner._states_for = lambda _: stages
        self.owner._entry_state_for = lambda _: 'assess'
        self.owner._adopt_workflow_from_tracking = lambda *_: None
        comments = [{'body': make_state_comment('assess', 1)}]
        settlement = self.path / '.stokowski/settlement.json'
        frozen = settlement.read_bytes()

        async def restart():
            self.owner._issue_current_state.clear()
            self.owner._issue_state_runs.clear()
            return await self.owner._resolve_current_state(self.issue, comments=comments)

        self.assertEqual(await restart(), ('investigate', 1))
        self.assertEqual(self.owner._issue_current_state, {ISSUE: 'investigate'})
        native.successor_assignment.assert_called_once_with(
            self.owner, self.issue, self.publication.scope(self.issue), 'investigate', 1)
        self.assertEqual(settlement.read_bytes(), frozen)
        self.assertEqual(self.owner._tasks, {})
        self.owner._announce_state.assert_not_awaited()
        self.fetch_issue.assert_not_awaited()

        native.successor_assignment.side_effect = Hold('Consumed entry absence is unverified')
        self.assertEqual(await restart(), ('assess', 1))
        self.assertEqual(self.owner._issue_current_state, {ISSUE: 'assess'})
        self.assertEqual(settlement.read_bytes(), frozen)
        self.assertFalse(self.owner._is_eligible(self.issue))
        self.assertEqual(self.owner._issue_current_state, {ISSUE: 'assess'})
        native.successor_assignment.side_effect = None
        self.assertTrue(self.owner._is_eligible(self.issue))  # No restart or state unset.
        self.assertEqual(self.owner._issue_current_state, {ISSUE: 'investigate'})
        self.assertEqual(settlement.read_bytes(), frozen)
        self.owner._announce_state.assert_not_awaited()
        self.fetch_issue.assert_not_awaited()
        title = self.issue.title

        def scope_changed(*_):
            self.issue.title = 'Changed while observing successor'
            return self.claim

        native.successor_assignment.side_effect = scope_changed
        self.assertEqual(await restart(), ('assess', 1))
        self.assertEqual(self.owner._issue_current_state, {ISSUE: 'assess'})
        self.issue.title = title
        native.successor_assignment.reset_mock(side_effect=True)
        settlement.unlink()
        self.assertEqual(await restart(), ('assess', 1))
        native.successor_assignment.assert_not_called()
        native.recipient_assignment.assert_not_called()
        native.bind_dispatch.assert_not_called()
        self.assertEqual(self.owner._tasks, {})

    async def test_successor_with_bootstrap_or_unverified_original_does_not_dispatch(self):
        native = self.prepare_successor()
        node, package = self.path / '.node-version', self.path / 'package.json'
        node.write_text('24.16.0\n')
        package.write_text('{"packageManager":"npm@11.9.0"}\n')
        self.assertFalse(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        self.assertEqual(self.owner._tasks, {})
        native.successor_assignment.assert_not_called()
        node.unlink()
        package.unlink()

        self.owner._dispatch(self.issue, attempt_num=1)
        self.assertEqual(self.owner._tasks, {})
        native.successor_assignment.assert_not_called()
        native.successor_assignment.side_effect = Hold('Original successor entry already attempted')
        self.assertFalse(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        native.bind_dispatch.assert_not_called()
        self.assertEqual(self.owner._tasks, {})
        self.assertEqual(self.entries, [])

    async def test_successor_lost_entry_holds_without_another_worker_or_assessment(self):
        native = self.prepare_successor()
        first_entries = []

        def lost(owner, issue, scope, attempt, *, expected):
            first_entries.append(attempt)
            self.assertIs(owner._tasks[ISSUE], asyncio.current_task())
            self.assertIs(expected, self.claim)
            native.successor_assignment.side_effect = Hold('Original successor entry already attempted')
            raise Hold('Original successor acknowledgement lost')

        native.successor_first_entry = lost
        self.owner._dispatch(self.issue)
        await self.owner._tasks[ISSUE]
        self.assertFalse(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        self.assertEqual(len(first_entries), 1)
        self.assertEqual(self.owner._tasks, {})
        self.assertFalse(self.bindings)
        self.fetch_issue.assert_not_awaited()
        native.recipient_assignment.assert_not_called()
        native.bind_dispatch.assert_not_called()

    async def test_pins_native_prologue_and_uses_same_task_before_workspace_and_turn(self):
        self.require_reconcile_patch()
        self.configure()
        self.assertTrue(self.owner._is_eligible(self.issue))
        self.assertEqual(self.entries, [])
        self.assertFalse((self.path / '.stokowski').exists())
        self.owner._dispatch(self.issue)
        task = self.owner._tasks[ISSUE]
        await task
        self.assertEqual(len(self.entries), 1)
        self.assertFalse(self.active)
        self.lifecycle.recipient_workspace_for_prologue.assert_called_once()
        self.lifecycle.verify_recipient_workspace.assert_called_once()
        (binding,) = self.bindings
        self.assertEqual(binding.attempt.id, self.claim.attempt_id)
        self.assertEqual(binding.tracked_paths, ())
        self.assertEqual(binding.write_roots, ())
        self.assertTrue(binding.one_shot_report)
        self.assertIs(binding.finish_original, self.lifecycle.recipient_assessment_finisher.return_value)
        self.assertIs(binding.prepare_turn_thread, self.lifecycle.prepare_recipient_turn_thread)
        self.assertTrue((self.path / '.stokowski' / 'artifacts').is_dir())
        self.assertFalse(self.clone_marker.exists())

    async def test_removed_staged_path_does_not_trigger_native_clone_hook(self):
        self.require_reconcile_patch()
        self.configure()
        moved = self.root / 'original-only'

        async def read_and_move(*_):
            self.path.rename(moved)
            return self.issue

        self.fetch_issue.side_effect = read_and_move
        self.owner._dispatch(self.issue)
        await self.owner._tasks[ISSUE]
        self.assertFalse(self.path.exists())
        self.assertFalse(self.clone_marker.exists())
        self.assertFalse((moved / '.stokowski').exists())
        self.assertFalse(self.bindings)
        self.owner._publish_run_report.assert_not_awaited()

    async def test_lost_original_and_parked_retry_do_not_schedule_a_new_native_task(self):
        self.require_reconcile_patch()
        self.configure()
        parked = object()
        self.owner.retry_attempts[ISSUE] = parked
        self.assertFalse(self.owner._is_eligible(self.issue, retry_entry=parked))
        self.owner._dispatch(self.issue)
        self.assertIs(self.owner.retry_attempts.pop(ISSUE), parked)
        self.assertEqual(self.owner._tasks, {})
        self.lifecycle.recipient_assignment.assert_not_called()
        self.entry_error = Hold('Lost original recipient acknowledgment')
        self.owner._dispatch(self.issue)
        task = self.owner._tasks[ISSUE]
        await task
        for _ in range(2):
            self.assertFalse(self.owner._is_eligible(self.issue))
            self.owner._dispatch(self.issue)
            self.assertEqual(self.owner._tasks, {})
        self.assertEqual(len(self.entries), 1)
        self.assertEqual(self.owner.running, {})
        self.assertNotIn(ISSUE, self.owner.claimed)
        self.fetch_issue.assert_not_awaited()

    async def test_unreviewed_workspace_callable_does_not_select_recipient(self):
        self.require_reconcile_patch()
        original = self.orchestrator.ensure_workspace
        replacement = AsyncMock()
        with patch.object(self.orchestrator, 'ensure_workspace', replacement):
            self.configure()
            self.assertFalse(self.owner._is_eligible(self.issue))
            self.owner._dispatch(self.issue)
        self.assertIs(self.orchestrator.ensure_workspace, original)
        self.assertFalse(self.entries)
        self.assertEqual(self.owner._tasks, {})
        self.lifecycle.recipient_assignment.assert_not_called()

    async def test_cancellation_before_entry_releases_only_its_registered_slot(self):
        self.require_reconcile_patch()
        self.configure()
        self.owner._dispatch(self.issue)
        task = self.owner._tasks[ISSUE]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.owner._release_slot.assert_called_once_with(ISSUE)
        self.assertNotIn(ISSUE, self.owner.running)
        self.assertNotIn(ISSUE, self.owner._tasks)
        self.assertNotIn(ISSUE, self.owner.claimed)
        self.assertEqual(self.entries, [])
        self.fetch_issue.assert_not_awaited()
        self.assertFalse((self.path / '.stokowski').exists())

    async def test_earlier_native_reconciliation_holds_recipient_selection(self):
        self.require_turn_binding()
        if self.native_sha not in RECONCILE_PREIMAGES:
            self.skipTest('select an exact native reconciliation preimage')
        self.configure()
        self.assertFalse(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        self.assertFalse(self.owner._tasks)
        self.lifecycle.recipient_assignment.assert_not_called()

    async def test_reconciler_replacement_after_dispatch_stops_original_before_entry(self):
        self.require_reconcile_patch()
        self.configure()
        self.assertTrue(self.owner._is_eligible(self.issue))
        self.owner._dispatch(self.issue)
        task = self.owner._tasks[ISSUE]
        with patch.object(self.owner, '_reconcile', AsyncMock()):
            await task
        self.assertEqual(self.entries, [])
        self.fetch_issue.assert_not_awaited()
        self.owner._release_slot.assert_called_once_with(ISSUE)
