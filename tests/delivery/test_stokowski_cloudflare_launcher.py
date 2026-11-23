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
        with TemporaryDirectory() as scratch, patch(
            'uif.stokowski_cloudflare.launcher._protected_json', return_value=config
        ):
            configure(runner, orchestration, SimpleNamespace(), policy=Path('/etc/owner.json'),
                      database=Path(scratch)/'budget.sqlite')
            issue = SimpleNamespace(id='issue-id')
            self.assertIs(orchestration.run_turn, runner.run_turn)
            self.assertIsNot(runner.run_turn, codex)
            self.assertIsNone(Owner()._dispatch(issue))
            original.assert_not_called()
            Owner._release_slot.assert_called_once_with(issue.id)
