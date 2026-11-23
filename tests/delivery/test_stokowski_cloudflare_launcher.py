from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.stokowski_cloudflare.launcher import configure


class LauncherTests(unittest.TestCase):
    def test_installs_cloudflare_only_and_holds_unadmitted_issue_before_native_dispatch(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: True)
            _dispatch = Mock()
            _run_worker = AsyncMock()
            _release_slot = Mock()

        original = Owner._dispatch
        codex = Mock(side_effect=AssertionError('Codex called'))
        runner = SimpleNamespace(run_turn=codex)
        orchestration = SimpleNamespace(run_turn=codex, Orchestrator=Owner)
        accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                            (digit * 32 for digit in 'abcde')))
        config = dict(accounts=accounts, projects=['OffMenu'], credentials='/etc/uif-stokowski',
                      admissions={})
        records = {'owner.json': config,
                   'OffMenu.json': dict(account_id=accounts['OffMenu'], plan='free', token='test'),
                   'UIF.json': dict(account_id=accounts['UIF'], plan='paid', token='test')}
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json',
                    side_effect=lambda path: records[path.name]),
              patch('uif.stokowski_cloudflare.launcher.WorkersAIClient') as client):
            configure(runner, orchestration, SimpleNamespace(), policy=Path('/etc/owner.json'),
                      database=Path(scratch)/'budget.sqlite')
            self.assertEqual(client.call_args.args[0].eligible_projects, {'OffMenu'})
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
