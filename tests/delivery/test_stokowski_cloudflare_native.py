from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

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
