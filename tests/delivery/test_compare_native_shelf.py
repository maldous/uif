import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/delivery'))
import compare_native_shelf as compare


class NativeShelfCompare(unittest.TestCase):
    def fixture(self):
        return dict(schema=compare.SCHEMA, core=compare.CORE,
                    python={'a.py':'1'*64,'b.py':'2'*64}, web_deps={'other/data':'3'*64})

    def test_missing_or_bad_pin_cannot_start_inventory(self):
        data = compare.encode(self.fixture())
        with patch.object(compare, '_capture_fixed') as capture:
            self.assertEqual(compare.compare_in_place(None, independently_retained_sha256='a'*64)['status'],
                             'held_expected_manifest_absent')
            with self.assertRaisesRegex(ValueError, 'manifest_pin_required'):
                compare.compare_in_place(data, independently_retained_sha256='f'*64)
            capture.assert_not_called()

    def test_exact_and_missing_extra_drift_without_member_names(self):
        expected = self.fixture()
        data = compare.encode(expected)
        pin = compare.hash_bytes(data)
        with patch.object(compare, '_capture_fixed', return_value=expected):
            result = compare.compare_in_place(data, independently_retained_sha256=pin)
        self.assertEqual(result['status'], 'exact_member_identity_match')
        self.assertEqual(result['production_acceptance'], 'not_evaluated')
        actual = dict(python={'a.py':'f'*64,'unexpected.py':'9'*64}, web_deps={'other/data':'3'*64})
        with patch.object(compare, '_capture_fixed', return_value=actual):
            result = compare.compare_in_place(data, independently_retained_sha256=pin)
        self.assertEqual(result['roles']['python'],
                         dict(expected=2, observed=2, missing=1, unexpected=1, drift=1))
        self.assertNotIn('unexpected.py', str(result))
        self.assertNotIn('a.py', str(result))
        self.assertEqual(result['status'], 'held_member_identity_mismatch')

    def test_real_fd_inventory_rejects_link_and_writable_member(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            member = root / 'member.py'
            member.write_bytes(b'raise AssertionError("not executed")')
            member.chmod(0o644)
            fd = os.open(root, compare.OPEN_DIR)
            try:
                self.assertEqual(compare.capture_role(fd, uid=os.geteuid()),
                                 {'member.py':compare.hash_bytes(member.read_bytes())})
                (root / 'link.py').symlink_to(member)
                with self.assertRaisesRegex(ValueError, 'inventory_member'):
                    compare.capture_role(fd, uid=os.geteuid())
                (root / 'link.py').unlink()
                member.chmod(0o666)
                with self.assertRaisesRegex(ValueError, 'inventory_member'):
                    compare.capture_role(fd, uid=os.geteuid())
            finally:
                os.close(fd)

    def test_final_fd_revalidation_detects_prior_member_overwritten_while_hashing_later(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            earlier, later = root / 'a.py', root / 'z.py'
            earlier.write_bytes(b'old')
            later.write_bytes(b'end')
            earlier.chmod(0o644)
            later.chmod(0o644)
            fd = os.open(root, compare.OPEN_DIR)
            directory_identity = compare.identity(os.fstat(fd))
            before = compare.identity(earlier.stat())
            original_identity = compare.identity
            original_sha = compare.sha256
            num = 0

            def coarse_identity(st):
                current = original_identity(st)
                if current[:2] == before[:2]:
                    return (*current[:-2], *before[-2:])
                return current

            def overwrite_during_later_hash():
                nonlocal num
                num += 1
                if num == 2:
                    earlier.write_bytes(b'new')  # same inode, size and directory entry
                return original_sha()
            try:
                ids = {}
                with patch.object(compare, 'identity', side_effect=coarse_identity):
                    with patch.object(compare, 'sha256', side_effect=overwrite_during_later_hash):
                        result = compare.capture_role(fd, uid=os.geteuid(), identities=ids)
                    self.assertEqual(result['a.py'], compare.hash_bytes(b'old'))
                    self.assertEqual(compare.identity(os.fstat(fd)), directory_identity)
                    self.assertEqual(compare.identity(earlier.stat()), before)
                    with self.assertRaisesRegex(ValueError, 'inventory_changed'):
                        compare._revalidate_role(fd, ids, result, uid=os.geteuid())
            finally:
                os.close(fd)

    def test_detached_historical_role_uses_compact_sorted_pairs_not_manifest_encoding(self):
        data = {'z.py': 'a'*64, 'a.py': 'b'*64}
        raw = compare.encode(data)
        pairs = ('[["a.py","' + 'b'*64 + '"],["z.py","' + 'a'*64 + '"]]').encode()
        prior = compare.HistoricalRoleReceipt('python', 2, compare.hash_bytes(pairs))
        self.assertEqual(compare.compare_to_historical_role('python', raw, prior=prior)['status'],
                         'historical_receipt_match')
        with self.assertRaisesRegex(ValueError, 'historical_role_receipt'):
            compare.compare_to_historical_role('web_deps', raw, prior=prior)
        invalid = compare.HistoricalRoleReceipt('python', 2, compare.hash_bytes(raw))
        self.assertEqual(compare.compare_to_historical_role('python', raw, prior=invalid)['status'],
                         'historical_receipt_differs')

    def test_historical_counts_cannot_infer_member_diff(self):
        raw = compare.encode({f'stokowski/file{i}.py': 'a'*64 for i in range(20)})
        prior = compare.HistoricalRoleReceipt('python', 23, 'b'*64)
        result = compare.compare_to_historical_role('python', raw, prior=prior)
        self.assertEqual(result, dict(role='python', historical_count=23, candidate_count=20,
                         status='historical_receipt_differs', production_acceptance='not_evaluated'))
