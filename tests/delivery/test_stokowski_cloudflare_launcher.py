import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.stokowski_cloudflare.launcher import _codex_is_ready, _start_with_shutdown, configure
from uif.stokowski_cloudflare.budget import ProjectAccountRouter


class LauncherTests(unittest.TestCase):
    def test_both_mode_requires_fresh_readiness_and_a_local_codex_executable(self):
        now = datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)
        with patch('uif.stokowski_cloudflare.launcher.shutil.which') as which:
            which.return_value = '/usr/bin/codex'
            self.assertFalse(_codex_is_ready(None, now))
            self.assertFalse(_codex_is_ready('2026-11-23T00:59:00Z', now))
            which.assert_not_called()
            self.assertTrue(_codex_is_ready('2026-11-23T01:03:00Z', now))
            which.return_value = None
            self.assertFalse(_codex_is_ready('2026-11-23T01:03:00Z', now))

    def test_installs_cloudflare_only_and_holds_unadmitted_issue_before_native_dispatch(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: True)
            _dispatch = Mock()
            _run_worker = AsyncMock()
            _release_slot = Mock()
            _is_eligible = Mock()

        original = Owner._dispatch
        codex = Mock(side_effect=AssertionError('Codex called'))
        runner = SimpleNamespace(run_turn=codex)
        orchestration = SimpleNamespace(run_turn=codex, Orchestrator=Owner,
                                        MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
                                        logger=Mock())
        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                            (digit * 32 for digit in 'abcde')))
        accounts['Paid'] = 'f' * 32
        config = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/uif-stokowski',
                      admissions={})
        records = {'owner.json': config,
                   'OffMenu.json': dict(account_id=accounts['OffMenu'], plan='free', token='test'),
                   'UIF.json': dict(account_id=accounts['UIF'], plan='paid', token='test'),
                   'Paid.json': dict(account_id=accounts['Paid'], plan='paid', token='test')}
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json',
                    side_effect=lambda path: records[path.name]) as read_policy,
              patch('uif.stokowski_cloudflare.launcher.WorkersAIClient') as client):
            configure(runner, orchestration, SimpleNamespace(), policy=Path('/etc/owner.json'),
                      database=Path(scratch)/'budget.sqlite')
            self.assertEqual(client.call_args.args[0].eligible_projects, {'OffMenu'})
            paid_token = client.call_args.args[1]
            with self.assertRaisesRegex(ValueError, 'exclusive ledger usage'):
                paid_token(accounts['Paid'])
            self.assertNotIn(Path('/etc/uif-stokowski/Paid.json'),
                             [call.args[0] for call in read_policy.call_args_list])
            config['paid_workers_ai_exclusive'] = True
            self.assertEqual(paid_token(accounts['Paid']), 'test')
            config['paid_workers_ai_exclusive'] = False
            with self.assertRaisesRegex(ValueError, 'exclusive ledger usage'):
                paid_token(accounts['Paid'])
            issue = SimpleNamespace(id='issue-id')
            self.assertIs(orchestration.run_turn, runner.run_turn)
            self.assertIsNot(runner.run_turn, codex)
            self.assertIsNone(Owner()._dispatch(issue))
            original.assert_not_called()
            Owner._release_slot.assert_called_once_with(issue.id)
            config['admissions'][issue.id] = {'investigate': {}}
            self.assertIsNone(Owner()._dispatch(issue))
            original.assert_not_called()
            self.assertEqual(Owner._release_slot.call_count, 2)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_intake_uses_native_custody_without_an_issue_admission(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)
            _is_eligible = Mock(return_value=True)
            _dispatch = Mock()
            _release_slot = Mock()
            _run_worker = AsyncMock()

            def _entry_state_for(self, issue):
                return 'investigate'

            async def _resolve_current_state(self, issue, *, comments=None):
                self._issue_current_state[issue.id] = 'investigate'
                return 'investigate', 1

            def _states_for(self, issue):
                return {'assess': SimpleNamespace(type='agent', admission_review=True),
                        'investigate': SimpleNamespace(type='agent', admission_review=False)}

        config = dict(accounts={name: digit * 32 for name, digit in
                                zip(('OffMenu', 'UIF', 'ANQR', 'CIX', 'CQ'), 'abcde')},
                      projects=['OffMenu'], credentials='/etc/uif-stokowski', admissions={},
                      project_intake={'OffMenu': {'origin': 'https://github.com/maldous/offmenu.git'}})
        orchestrator = SimpleNamespace(
            Orchestrator=Owner, MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
            logger=Mock(), state_key=lambda state: {'In Progress': 'active'}.get(state, state.lower()),
            parse_latest_tracking=lambda comments: comments or None)
        issue = SimpleNamespace(id='current-issue', identifier='UIF-10', title='Review test coverage',
                                description='Inspect the existing evidence.', state='Todo')
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json', return_value=config),
              patch('uif.stokowski_cloudflare.launcher.install_native_runner')):
            native_dispatch = Owner._dispatch
            configure(SimpleNamespace(), orchestrator, SimpleNamespace(scope=lambda _: 'current-scope'),
                      policy=Path('/etc/owner.json'), database=Path(scratch) / 'budget.sqlite')
            owner = Owner()
            owner._issue_current_state, owner._issue_state_runs, owner.running = {}, {}, {}
            owner.claimed = set()
            owner._settlement_path = lambda _: Path(scratch) / 'workspace'
            owner.cfg = SimpleNamespace(terminal_linear_states=lambda: ['Done', 'Canceled'])
            self.assertEqual(owner._entry_state_for(issue), 'assess')
            self.assertTrue(owner._is_eligible(issue))
            owner._dispatch(issue)
            native_dispatch.assert_called_once_with(owner, issue, None)

            issue.state = 'In Progress'
            self.assertEqual(await owner._resolve_current_state(issue, comments=['native history']),
                             ('investigate', 1))
            owner._issue_current_state.clear()
            self.assertEqual(await owner._resolve_current_state(issue, comments=[]), ('assess', 1))
            issue.blocked_by = [SimpleNamespace(state='In Progress')]
            self.assertFalse(owner._is_eligible(issue))

    async def test_native_dispatch_attaches_generated_review_and_preserves_owner_revocation(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)
            _is_eligible = Mock()
            _release_slot = Mock()

            def _dispatch(self, issue, attempt_num=None):
                attempt = SimpleNamespace(state_name='independent-review')
                self.running[issue.id] = attempt
                self._tasks[issue.id] = asyncio.create_task(self._run_worker(issue, attempt))

            async def _run_worker(self, issue, attempt):
                return binding.call_args.kwargs['authorize_turn'](issue, attempt, workspace)

        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                            (digit * 32 for digit in 'abcde')))
        issue = SimpleNamespace(id='issue', identifier='UIF-1', state='In Progress')
        author = dict(project='OffMenu', scope_sha256='scope', attempt='author',
                      source_head='head', branch='branch', automatic_review=True)
        review = dict(author, attempt='author-auto-review', review_of='author', reviewer_project='UIF',
                      read_paths=[], write_roots=[], prompt='Review evidence', automatic_author=author)
        stages = {'investigate': author}
        config = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/uif-stokowski',
                      admissions={issue.id: stages})
        native = dict(issue_id=issue.id, run=2, state='independent-review', scope_sha256='scope',
                      kind='source-free', candidate=issue.id)
        orchestration = SimpleNamespace(
            Orchestrator=Owner, MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
            logger=Mock(), state_key=str.lower)
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json', return_value=config),
              patch('uif.stokowski_cloudflare.launcher.install_native_runner') as binding,
              patch('uif.stokowski_cloudflare.launcher.prepare_review', return_value=review) as prepare,
              patch.object(ProjectAccountRouter, 'independent_reviewer', return_value='UIF'),
              patch('uif.stokowski_cloudflare.launcher.subprocess.check_output',
                    side_effect=['head', 'branch', '', ''])):
            root = Path(scratch).resolve()
            workspace = root / issue.identifier
            workspace.mkdir()
            publication = SimpleNamespace(scope=lambda _: 'scope', read=Mock(return_value=native),
                                          BINDING=Path('.stokowski/review-binding.json'))
            configure(SimpleNamespace(), orchestration, publication,
                      policy=Path('/etc/owner.json'), database=root / 'budget.sqlite')
            owner = Owner()
            owner.cfg = SimpleNamespace(workspace=SimpleNamespace(resolved_root=lambda: root))
            owner.running, owner._tasks, owner.claimed = {}, {}, {issue.id}
            owner._issue_state_runs = {issue.id: 2}
            owner._issue_current_state = {issue.id: 'independent-review'}
            owner._states_for = lambda _: {'independent-review': SimpleNamespace(type='agent')}
            owner.retry_attempts = {}

            owner._dispatch(issue)
            approved = await owner._tasks[issue.id]
            prepare.assert_called_once_with(workspace, issue, 2, author, 'UIF', publication)
            self.assertEqual(approved.attempt.id, 'author-auto-review')
            self.assertEqual(approved.attempt.reviewer_project, 'UIF')
            self.assertEqual(set(stages), {'investigate'})

            owner._dispatch(issue)
            del stages['investigate']
            with self.assertRaisesRegex(ValueError, 'stage authorization has changed'):
                await owner._tasks[issue.id]

    async def test_review_validates_the_native_checkpoint_against_the_owner_candidate_tree(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)
            _dispatch = Mock()
            _is_eligible = Mock()

            async def _run_worker(self, issue, attempt):
                self.running[issue.id] = attempt
                self._tasks[issue.id] = asyncio.current_task()
                authorize = install_binding.call_args.kwargs['authorize_turn']
                return authorize(issue, attempt, self.root / issue.identifier)

        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                            (digit * 32 for digit in 'abcde')))
        issue = SimpleNamespace(id='issue', identifier='UIF-1')
        attempt = SimpleNamespace(state_name='independent-review')
        record = dict(project='OffMenu', scope_sha256='scope', attempt='review',
                      source_head='base', branch='author', candidate_tree='admitted-tree',
                      review_of='author', reviewer_project='UIF', read_paths=[],
                      write_roots=[], prompt='Review candidate')
        native = dict(issue_id=issue.id, run=2, state=attempt.state_name, scope_sha256='scope',
                      kind='git', candidate='checkpoint', branch='native-review')
        config = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/uif-stokowski',
                      admissions={issue.id: {attempt.state_name: record}})
        orchestration = SimpleNamespace(
            Orchestrator=Owner, MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
            logger=Mock())
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json', return_value=config),
              patch('uif.stokowski_cloudflare.launcher.install_native_runner') as install_binding,
              patch('uif.stokowski_cloudflare.launcher.subprocess.check_output') as git):
            database = Path(scratch) / 'budget.sqlite'
            configure(SimpleNamespace(), orchestration,
                      SimpleNamespace(scope=lambda _: 'scope', read=Mock(return_value=native),
                                      BINDING=Path('.stokowski/review-binding.json')),
                      policy=Path('/etc/owner.json'), database=database)
            owner = Owner()
            owner.root = Path(scratch)
            (owner.root / issue.identifier).mkdir()
            owner.cfg = SimpleNamespace(workspace=SimpleNamespace(resolved_root=lambda: owner.root))
            owner.running, owner._tasks, owner.claimed = {}, {}, {issue.id}
            owner._issue_state_runs = {issue.id: 2}

            git.side_effect = ['checkpoint', 'native-review', 'base', 'admitted-tree', '', '']
            approved = await owner._run_worker(issue, attempt)
            self.assertEqual((approved.attempt.review_of, approved.attempt.reviewer_project),
                             ('author', 'UIF'))
            self.assertIn('"candidate": "checkpoint"', approved.scoped_prompt)

            git.side_effect = ['checkpoint', 'native-review', 'base', 'different-tree']
            with self.assertRaisesRegex(ValueError, 'admitted candidate tree'):
                await owner._run_worker(issue, attempt)

            git.side_effect = ['checkpoint', 'native-review', 'base', 'admitted-tree', ' M dirty']
            with self.assertRaisesRegex(ValueError, 'clean candidate worktree'):
                await owner._run_worker(issue, attempt)

    async def test_worker_clears_inherited_authorization_and_preserves_original_attempt(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)
            _dispatch = Mock()
            _release_slot = Mock()
            _is_eligible = Mock(return_value=True)

            async def _run_worker(self, issue, attempt):
                self.running[issue.id] = attempt
                self._tasks[issue.id] = asyncio.current_task()
                authorize = binding.call_args.kwargs['authorize_turn']
                approved = authorize(issue, attempt, self.root / issue.identifier)
                if attempt is first:
                    await asyncio.create_task(self._run_worker(issue, second))
                else:
                    self.assess_prompt = approved.scoped_prompt

        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                            (digit * 32 for digit in 'abcde')))
        issue = SimpleNamespace(id='id', identifier='UIF-1', state='Todo')
        first = SimpleNamespace(state_name='investigate')
        second = SimpleNamespace(state_name='assess')
        record = dict(project='OffMenu', scope_sha256='scope', attempt='first',
                      source_head='head', branch='branch', read_paths=[],
                      write_roots=[], prompt='Bound task')
        config = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/uif-stokowski',
                      admissions={issue.id: {'investigate': record,
                                             'assess': dict(record, attempt='second')}})
        runner = SimpleNamespace()
        orchestration = SimpleNamespace(
            Orchestrator=Owner, MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
            logger=Mock(), state_key=str.lower)
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json', return_value=config),
              patch('uif.stokowski_cloudflare.launcher.install_native_runner') as binding,
              patch('uif.stokowski_cloudflare.launcher.subprocess.check_output',
                    side_effect=['head', 'branch', '', 'head', 'branch', ''])):
            database = Path(scratch) / 'budget.sqlite'
            configure(runner, orchestration, SimpleNamespace(scope=lambda _: 'scope'),
                      policy=Path('/etc/owner.json'), database=database)
            owner = Owner()
            owner.root = Path(scratch)
            (owner.root / issue.identifier).mkdir()
            owner.cfg = SimpleNamespace(workspace=SimpleNamespace(resolved_root=lambda: owner.root))
            owner.running, owner._tasks, owner.claimed = {}, {}, {issue.id}
            owner._issue_state_runs = {issue.id: 2}
            await owner._run_worker(issue, first)
            self.assertIn('"issue": "UIF-1", "state": "assess", "run": 2', owner.assess_prompt)
            self.assertTrue(owner._is_eligible(issue))
            self.assertFalse(owner._is_eligible(SimpleNamespace(id='unadmitted')))

            router = ProjectAccountRouter(database, accounts)
            router.reserve_request('OffMenu', 'first', 'request', 1, {'OffMenu'},
                                   request_sha256=sha256(b'fixture').hexdigest())
            owner._issue_current_state = {issue.id: 'investigate'}
            owner._states_for = lambda _: {'investigate': SimpleNamespace(type='agent')}
            owner.retry_attempts = {issue.id: SimpleNamespace(error='previous')}
            owner._dispatch(issue)
            self.assertEqual(owner.retry_attempts[issue.id].error,
                             'Original Workers AI request retained for reconciliation')
            owner._release_slot.assert_called_once_with(issue.id)
            owner._issue_current_state[issue.id] = 'independent-review'
            owner._schedule_retry = Mock()
            owner.retry_attempts[issue.id].attempt = 0
            owner._dispatch(issue)
            owner._schedule_retry.assert_called_once_with(
                issue, attempt_num=0, delay_ms=30_000, error='Waiting for owner-approved Workers AI stage')

    async def test_sigterm_uses_and_awaits_native_stop(self):
        loop = asyncio.get_running_loop()
        handlers = {}
        stopped = asyncio.Event()
        drained = asyncio.Event()

        async def start(_):
            await stopped.wait()

        async def stop():
            stopped.set()
            await asyncio.sleep(0)
            drained.set()

        owner = SimpleNamespace(stop=AsyncMock(side_effect=stop))
        with (patch.object(loop, 'add_signal_handler', side_effect=handlers.__setitem__),
              patch.object(loop, 'remove_signal_handler') as remove):
            running = asyncio.create_task(_start_with_shutdown(owner, start))
            await asyncio.sleep(0)
            handlers[signal.SIGTERM]()
            await asyncio.wait_for(running, 1)
        owner.stop.assert_awaited_once()
        self.assertTrue(drained.is_set())
        self.assertEqual(remove.call_count, 2)
