"""New synthetic transformer contracts, never reads native records or services."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('owner_label_context', ROOT / 'tools/delivery/owner_label_context.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
FIXTURE = (b'class Synthetic:\n    def _management_context(self, labels):\n'
           + M.NEEDLE + b'\n        return sorted(label for label in labels if label not in machine_labels)\n')


class OwnerLabelContext(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(M, 'EXPECTED_SOURCE_SHA256', M.digest(FIXTURE)))

    def test_only_exact_pinned_expression_changes(self):
        after, receipt = M.render_native_source(FIXTURE)
        self.assertEqual(after, FIXTURE.replace(M.NEEDLE, M.REPLACEMENT))
        self.assertEqual(receipt['after_sha256'], M.digest(after))
        for key in ('native_installed', 'runtime_adopted', 'gate_cleared', 'claim_transferred', 'original_records_changed', 'human_context_changed'):
            self.assertIs(receipt[key], False)

    def context(self, raw, labels):
        # Only this credential-free synthetic fixture is executed, never input
        # selected by the CLI or installed native Python at import time.
        class Report:
            CLASSIFICATIONS = {'x': ('existing-machine-label', 'unused')}
            CONFIDENCE_LABELS = {}
        namespace = {'report_mod': Report}
        exec(compile(raw, '<synthetic-owner-label-context>', 'exec'), namespace)
        return namespace['Synthetic']()._management_context(labels)

    def test_seven_owner_values_and_existing_machine_label_are_neutral(self):
        after, _ = M.render_native_source(FIXTURE)
        before_labels = ['feature', 'existing-machine-label']
        self.assertEqual(self.context(FIXTURE, before_labels),
                         self.context(after, before_labels + sorted(M.INFORMATIONAL_OWNER_LABELS)))
        self.assertNotEqual(self.context(FIXTURE, ['Owner: UIF']), self.context(after, ['Owner: UIF']))

    def test_other_names_case_and_prefix_still_change_context(self):
        after, _ = M.render_native_source(FIXTURE)
        labels = ['Owner: Customer', 'owner: uif', 'Owner: UIF-extra', 'important']
        self.assertEqual(self.context(after, labels), sorted(labels))

    def test_wrong_pin_or_second_application_is_refused(self):
        after, _ = M.render_native_source(FIXTURE)
        for raw in (after, FIXTURE + b'# drift\n', 'not-bytes'):
            with self.subTest(raw=str(raw)[:20]), self.assertRaises(M.CorrectionError):
                M.render_native_source(raw)

    def test_ambiguous_or_wrong_function_is_refused_even_with_fixture_pin(self):
        for raw in (FIXTURE + FIXTURE, FIXTURE.replace(b'_management_context', b'other_context')):
            with self.subTest(raw=raw[:20]), patch.object(M, 'EXPECTED_SOURCE_SHA256', M.digest(raw)), self.assertRaises(M.CorrectionError):
                M.render_native_source(raw)

    def test_regular_bounded_nofollow_nonblock_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.py'
            path.write_bytes(FIXTURE)
            self.assertEqual(M.read_source(path), FIXTURE)
            alias = path.with_name('alias.py')
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                M.read_source(alias)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(M.CorrectionError):
                M.read_source(path)

    def test_emission_is_new_private_file_never_installed_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.py'
            M.emit_candidate(path, b'candidate')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                M.emit_candidate(path, b'changed')
            self.assertEqual(path.read_bytes(), b'candidate')


if __name__ == '__main__':
    unittest.main()
