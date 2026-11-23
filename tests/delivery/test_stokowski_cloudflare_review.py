from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from uif.stokowski_cloudflare.budget import ProjectAccountRouter
from uif.stokowski_cloudflare.review import prepare_review


class AutomaticReviewTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(TemporaryDirectory())
        self.workspace = Path(scratch).resolve()
        self.git('init', '-q', '-b', 'author')
        (self.workspace / 'allowed.py').write_text('value = 1\n')
        (self.workspace / 'private.py').write_text('private = 1\n')
        self.git('add', '--', 'allowed.py', 'private.py')
        self.git('-c', 'user.email=review@example.test', '-c', 'user.name=Review',
                 'commit', '-qm', 'fixture')
        self.issue = SimpleNamespace(id='issue')
        self.publication = SimpleNamespace(scope=lambda _: 'scope')
        self.author = dict(source_head=self.git('rev-parse', 'HEAD').strip(), branch='author',
                           attempt='author-attempt', read_paths=['allowed.py'], write_roots=['allowed.py'])
        self.settlement = dict(issue_id=self.issue.id, run=3, state='investigate',
                               scope_sha256='scope', session_id=self.author['attempt'],
                               phase='complete', report_posted=True, event='complete',
                               target='independent-review', report={'summary': 'Bounded author result'})
        (self.workspace / '.stokowski').mkdir()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.workspace), *args], text=True)

    def prepare(self):
        (self.workspace / '.stokowski' / 'settlement.json').write_text(json.dumps(self.settlement))
        return prepare_review(self.workspace, self.issue, 3, self.author, 'UIF', self.publication)

    def test_review_handoff_binds_source_free_settlement(self):
        record = self.prepare()
        self.assertEqual((record['review_of'], record['reviewer_project']), ('author-attempt', 'UIF'))
        self.assertNotIn('candidate_tree', record)
        self.assertEqual(record['read_paths'], [])
        self.assertEqual(record['write_roots'], [])
        self.assertIn('Bounded author result', record['prompt'])
        self.settlement['session_id'] = 'old-attempt'
        with self.assertRaisesRegex(ValueError, 'settlement is not ready'):
            self.prepare()

    def test_review_handoff_binds_diff_to_tree_and_preserves_index(self):
        (self.workspace / 'allowed.py').write_text('value = 2\n')
        record = self.prepare()
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')
        self.assertEqual(self.git('branch', '--show-current').strip(), 'author')
        self.assertEqual(self.git('show', record['candidate_tree'] + ':allowed.py'), 'value = 2\n')
        self.assertIn('+value = 2', record['prompt'])
        self.assertNotIn('private.py', record['prompt'])
        (self.workspace / 'private.py').write_text('private = 2\n')
        with self.assertRaisesRegex(ValueError, 'outside the author write scope'):
            self.prepare()

    def test_router_requires_settlement_and_a_distinct_unreserved_project_account(self):
        accounts = dict(zip(('OffMenu', 'UIF', 'ANQR', 'CIX', 'CQ'), 'abcde'))
        accounts = {name: digit * 32 for name, digit in accounts.items()}
        router = ProjectAccountRouter(self.workspace / 'budget.sqlite', accounts)
        active = {'OffMenu', 'ANQR', 'CIX', 'CQ'}
        args = ('OffMenu', 'author', active, 100)
        self.assertIsNone(router.independent_reviewer(*args))
        budget = router.reserve_request('OffMenu', 'author', 'request', 100, active,
                                        request_sha256=sha256(b'fixture').hexdigest())
        self.assertEqual(budget.account_id, accounts['OffMenu'])
        self.assertIsNone(router.independent_reviewer(*args))
        budget.settle('request', 1)
        self.assertEqual(router.independent_reviewer(*args), 'UIF')
        self.assertIsNone(router.independent_reviewer('OffMenu', 'author', active | {'UIF'}, 100))
        self.assertIsNone(router.independent_reviewer('UIF', 'author', active | {'UIF'}, 100))
