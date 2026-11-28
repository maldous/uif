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
from uif.stokowski_cloudflare.native_composition import NativeComposition
from test_native_remote_authority import ISSUE, RECORD, ROUTE


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
        self.assertEqual(sha256(source.read_bytes()).hexdigest(),
                         '228523e5a9e78ed5ad3ea78c8fe8f37093e5ce2cf5460068215ffb655028fa96')
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
                     '_resolve_current_state', '_enter_gate'):
            self.enterContext(patch.object(orchestrator.Orchestrator, name, getattr(orchestrator.Orchestrator, name)))
        for module, name in ((orchestrator, 'ensure_workspace'), (orchestrator, 'run_turn'),
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

    async def test_pins_native_prologue_and_uses_same_task_before_workspace_and_turn(self):
        self.require_turn_binding()
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
        self.require_turn_binding()
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
        self.require_turn_binding()
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
        self.require_turn_binding()
        original = self.orchestrator.ensure_workspace
        replacement = AsyncMock()
        with patch.object(self.orchestrator, 'ensure_workspace', replacement):
            self.assertIsNone(launcher._recipient_native_prologue(
                self.orchestrator, self.orchestrator.Orchestrator._dispatch,
                self.orchestrator.Orchestrator._run_worker))
            self.configure()
            self.assertFalse(self.owner._is_eligible(self.issue))
            self.owner._dispatch(self.issue)
        self.assertIs(self.orchestrator.ensure_workspace, original)
        self.assertFalse(self.entries)
        self.assertEqual(self.owner._tasks, {})
        self.lifecycle.recipient_assignment.assert_not_called()

    async def test_cancellation_before_entry_releases_only_its_registered_slot(self):
        self.require_turn_binding()
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
