import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('migration_audit', ROOT / 'tools/agents/audit_linear_migration.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MigrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            '- **source-one**: First action.\n'
            '- **CIX-507:second**: Second action.\n'
            '## 11 nonadditive references\n'
            '- **rollup**: Historical nonadditive context.\n'
        )

    def issue(self, identity, primary, archival=''):
        return {'id': identity, 'description': module.IMPORT_MARKER + '\n' + primary
                + '\n' + module.ANCILLARY_HEADING + '\n' + archival}

    def test_complete_primary_coverage_and_linear_inline_issue_format(self):
        issue = self.issue('UIF-116', '* **source-one**: Accepted.\n'
                           '* <issue id="uuid" label="CIX-507" />**:second**: Retained.')
        result = module.audit(self.source, [issue])
        self.assertTrue(result['scope_accounting_complete'])
        self.assertEqual(result['primary_unique_action_count'], 2)
        self.assertEqual(result['source_nonadditive_reference_count'], 1)
        self.assertEqual(result['receiving_issue_count'], 1)

    def test_archival_whole_readme_cannot_hide_missing_primary_import(self):
        issue = self.issue('UIF-116', '* **source-one**: Accepted.', self.source)
        result = module.audit(self.source, [issue])
        self.assertFalse(result['scope_accounting_complete'])
        self.assertEqual(result['missing_action_ids'], ['CIX-507:second'])

    def test_repeated_original_and_later_wording_is_one_unique_scope(self):
        issue = self.issue('UIF-116', '* **source-one**: Original.\n'
                           '* **source-one**: Later disposition.\n'
                           '* **CIX-507:second**: Retained.')
        result = module.audit(self.source, [issue])
        self.assertEqual(result['primary_unique_action_count'], 2)
        self.assertEqual(result['primary_scope_occurrences'], 3)
        self.assertEqual(result['multiple_primary_occurrences'],
                         [{'source_id': 'source-one', 'issues': ['UIF-116', 'UIF-116']}])

    def test_abbreviated_and_duplicate_readbacks_refused(self):
        with self.assertRaisesRegex(ValueError, 'full description'):
            module.audit(self.source, [{'id': 'UIF-1'}])
        issue = self.issue('UIF-116', '')
        with self.assertRaisesRegex(ValueError, 'Duplicate issue'):
            module.audit(self.source, [issue, issue])
        issue['description'] += module.IMPORT_MARKER
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            module.audit(self.source, [issue])

    def test_missing_boundary_and_duplicate_source_ids_refused(self):
        with self.assertRaisesRegex(ValueError, 'boundary'):
            module.audit('- **source-one**: First.\n', [])
        with self.assertRaisesRegex(ValueError, 'unique'):
            module.audit('- **source-one**: Duplicate.\n' + self.source, [])

    def test_issues_without_import_marker_do_not_establish_coverage(self):
        result = module.audit(self.source, [{'id': 'UIF-116', 'description': self.source}])
        self.assertEqual(result['primary_unique_action_count'], 0)
        self.assertFalse(result['scope_accounting_complete'])


if __name__ == '__main__':
    unittest.main()
