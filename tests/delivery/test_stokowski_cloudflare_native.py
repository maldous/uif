import asyncio
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.stokowski_cloudflare.budget import BudgetRefused
from uif.stokowski_cloudflare.native import NativeTurnBinding, install_native_runner
from uif.stokowski_cloudflare.turn import Attempt


class NativeRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.workspace = Path(work.name)
        (self.workspace/'fixture.txt').write_text('old')
        self.issue = SimpleNamespace(id='issue-uuid')
        self.original = AsyncMock(side_effect=AssertionError('Codex called'))
        self.runner = SimpleNamespace(run_turn=self.original)
        self.orchestrator = SimpleNamespace(run_turn=self.original)
        self.available = Mock(return_value=False)
        self.attempt = SimpleNamespace(turn_count=0, state_name='investigate')
        self.config = SimpleNamespace(permission_mode='default', turn_timeout_ms=5000)
        self.binding = NativeTurnBinding(
            self.issue.id, self.workspace, Attempt('OffMenu', 'author'),
            ('fixture.txt',), ('fixture.txt',), 'Scoped synthetic prompt')

    def install(self, mode):
        install_native_runner(self.runner, self.orchestrator, mode=mode, client=Mock(),
                              authorize_turn=lambda *_: self.binding,
                              codex_available=self.available,
                              active_projects=lambda: {'OffMenu'})

    async def run_native(self):
        return await self.orchestrator.run_turn(
            'codex', self.config, None, 'Fix fixture', self.workspace, self.issue, self.attempt)

    async def test_cloudflare_only_replaces_native_runner_and_skips_codex(self):
        self.install('cloudflare')

        def edit(client, binding, prompt, *, execute_tool, **kwargs):
            self.assertEqual(prompt, 'Scoped synthetic prompt')
            execute_tool('replace_text', {'path': 'fixture.txt', 'old': 'old',
                                          'replacement': 'new'}, 1)
            return 'Patch prepared'

        with patch('uif.stokowski_cloudflare.native.run_turn', side_effect=edit):
            result = await self.run_native()
        self.assertEqual((result.status, result.session_id, result.result_text),
                         ('succeeded', 'author', 'Patch prepared'))
        self.assertEqual((self.workspace/'fixture.txt').read_text(), 'new')
        self.available.assert_not_called()
        self.original.assert_not_awaited()

    async def test_review_requires_separate_readonly_binding(self):
        self.install('both')
        self.config.permission_mode = 'plan'
        self.attempt.state_name = 'independent-review'
        with self.assertRaisesRegex(ValueError, 'independent account binding'):
            await self.run_native()

        self.binding = NativeTurnBinding(
            self.issue.id, self.workspace, Attempt('OffMenu', 'review', 'author', 'UIF'),
            ('fixture.txt',), ())
        with patch('uif.stokowski_cloudflare.native.run_turn', return_value='Accepted') as run:
            result = await self.run_native()
        self.assertEqual((result.status, result.session_id), ('succeeded', 'review'))
        self.assertEqual(run.call_args.args[1].reviewer_project, 'UIF')
        self.original.assert_not_awaited()

    async def test_both_mode_uses_cloudflare_when_codex_is_unavailable(self):
        self.install('both')
        with patch('uif.stokowski_cloudflare.native.run_turn', return_value='Cloudflare result'):
            result = await self.run_native()
        self.assertEqual(result.result_text, 'Cloudflare result')
        self.available.assert_called_once_with(self.issue)
        self.original.assert_not_awaited()

    async def test_both_mode_uses_only_the_authorized_codex_runner_when_ready(self):
        self.available.return_value = True
        self.original.side_effect = None
        self.original.return_value = 'Codex result'
        self.install('both')
        with patch('uif.stokowski_cloudflare.native.run_turn') as cloudflare:
            self.assertEqual(await self.run_native(), 'Codex result')
        cloudflare.assert_not_called()
        self.assertEqual(self.original.call_args.args[3], 'Scoped synthetic prompt')

    async def test_bare_cloudflare_report_keeps_its_fields_in_the_native_report_envelope(self):
        self.install('cloudflare')
        bare = '{"summary":"No source receipt","verdict":"blocked"}'
        with patch('uif.stokowski_cloudflare.native.run_turn', return_value=bare):
            result = await self.run_native()
        self.assertEqual(result.result_text, '```json\n' + bare + '\n```')

    async def test_restricted_turn_authorizes_only_its_actual_executor_and_original_result(self):
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        release = Event()
        copied_marker = ContextVar('fixture-owner-marker')
        copied_marker.set('visible-in-child-context')
        entry = {}
        executions = []

        def prepare(run_once, require_turn_thread):
            self.assertIs(asyncio.current_task(), selected)
            self.assertEqual(self.attempt.turn_count, 0)

            def wrapped():
                require_turn_thread()
                executions.append(current_thread())
                run_once()
                with self.assertRaisesRegex(BudgetRefused, 'thread is unavailable'):
                    require_turn_thread()
                with self.assertRaisesRegex(BudgetRefused, 'already invoked'):
                    run_once()
                return 'Substituted result'

            entry['call'] = wrapped
            return wrapped

        self.binding = replace(self.binding, write_roots=(), prepare_turn_thread=prepare)
        self.available.return_value = True
        self.install('both')
        owner_thread = current_thread()

        def run(client, _attempt, prompt, *, tools, execute_tool, **kwargs):
            self.assertEqual(copied_marker.get(), 'visible-in-child-context')
            self.assertEqual((tools, execute_tool), ([], None))
            self.assertEqual(prompt, 'Scoped synthetic prompt')
            self.assertIsNot(current_thread(), owner_thread)
            self.assertIs(current_thread(), executions[0])
            loop.call_soon_threadsafe(ready.set)
            self.assertTrue(release.wait(2))
            return 'Original result'

        def substitute():
            self.assertEqual(copied_marker.get(), 'visible-in-child-context')
            with self.assertRaisesRegex(BudgetRefused, 'thread is unavailable'):
                entry['call']()

        async def copied_task():
            substitute()
            await asyncio.to_thread(substitute)

        with (patch('uif.stokowski_cloudflare.native.run_turn', side_effect=run) as original,
              patch('uif.stokowski_cloudflare.native.repository_tools') as local_tools):
            selected = asyncio.create_task(self.run_native())
            try:
                await asyncio.wait_for(ready.wait(), 2)
                await asyncio.create_task(copied_task())
            finally:
                release.set()
                result = await selected
            original.assert_called_once()
            local_tools.assert_not_called()
        self.assertEqual(len(executions), 1)
        self.assertEqual((result.status, result.result_text), ('succeeded', 'Original result'))
        self.available.assert_not_called()
        self.original.assert_not_awaited()
        await asyncio.to_thread(substitute)

    async def test_restricted_turn_drains_its_original_despite_repeated_cancellation(self):
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        release = Event()
        gate = {}

        def prepare(run_once, require_turn_thread):
            def wrapped():
                require_turn_thread()
                try:
                    return run_once()
                finally:
                    gate['finished'] = True
            gate['validate'] = require_turn_thread
            return wrapped

        def run(*args, cancelled, **kwargs):
            gate['stop'] = cancelled
            loop.call_soon_threadsafe(ready.set)
            self.assertTrue(release.wait(2))
            with self.assertRaisesRegex(BudgetRefused, 'thread is unavailable'):
                gate['validate']()
            self.assertTrue(cancelled())
            return 'Drained result'

        self.binding = replace(self.binding, write_roots=(), prepare_turn_thread=prepare)
        self.install('cloudflare')
        with patch('uif.stokowski_cloudflare.native.run_turn', side_effect=run) as original:
            selected = asyncio.create_task(self.run_native())
            try:
                await asyncio.wait_for(ready.wait(), 2)
                selected.cancel('original cancellation')
                async with asyncio.timeout(2):
                    while not gate['stop']():
                        await asyncio.sleep(0)
                for message in ('second cancellation', 'third cancellation'):
                    selected.cancel(message)
                    done, pending = await asyncio.wait({selected}, timeout=.01)
                    self.assertEqual((done, pending), (set(), {selected}))
                    self.assertEqual(self.attempt.status, 'running')
                    self.assertFalse(gate.get('finished', False))
                    self.assertFalse(hasattr(self.attempt, 'completed_at'))
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError) as cancel:
                    await selected
            original.assert_called_once()
        self.assertEqual(str(cancel.exception), 'original cancellation')
        self.assertTrue(gate['finished'])
        self.assertEqual(self.attempt.status, 'cancelled')
        self.assertIsNotNone(self.attempt.completed_at)
        self.assertFalse(hasattr(self.attempt, 'result_text'))

    async def test_restricted_turn_cannot_fall_back_to_local_write_tools(self):
        prepare = Mock()
        self.binding = replace(self.binding, prepare_turn_thread=prepare)
        self.install('cloudflare')
        with patch('uif.stokowski_cloudflare.native.repository_tools') as local_tools:
            with self.assertRaisesRegex(BudgetRefused, 'requires a remote source receiver'):
                await self.run_native()
            local_tools.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual((self.workspace/'fixture.txt').read_text(), 'old')
