"""Filesystem-only fixture; never accesses Foo's protected directories."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/delivery'))
import install_fresh_idle_recovery_stage as install
import guarded_service_upgrade as guard


class RecoveryStage(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        root = Path(self.enterContext(TemporaryDirectory()))
        self.stage = root / 'stage'
        self.source = root / 'input'
        self.source.mkdir()
        self.old = root / 'existing-dropin'
        self.selection = root / 'selection'
        self.old.write_bytes(b'old')
        self.selection.write_bytes(b'selected')
        for n in install.MEMBERS:
            (self.source / n).write_text(n)
        for obj, folded in ((install, {'INPUT': self.source, 'STAGE': self.stage}),
                            (guard, {'DROPIN': self.old, 'SELECTOR': self.selection,
                                     '_private_webhook_root': lambda: True,
                                     'selector_lock': lambda **_: nullcontext(),
                                     'protected_parents': lambda p: None,
                                     'read_record': lambda p,n: (p/n).read_bytes(),
                                     'bytes_pin': lambda p: guard.digest(p.read_bytes())})):
            self.stack.enter_context(patch.multiple(obj, **folded))
        self.approval = install.RecoveryStageApproval('a'*64, guard.digest(b'old'),
            guard.digest(b'selected'), tuple(guard.digest(n.encode()) for n in install.MEMBERS))

    def test_files_are_published_once_and_old_unit_unchanged(self):
        result = install.stage(self.approval)
        inodes = {p.name: p.stat().st_ino for p in self.stage.iterdir()}
        self.assertEqual(install.stage(self.approval), result)
        self.assertEqual(inodes, {p.name: p.stat().st_ino for p in self.stage.iterdir()})
        self.assertEqual(result['status'], 'staged_not_activated')
        self.assertEqual(self.old.read_bytes(), b'old')
        self.assertEqual(self.selection.read_bytes(), b'selected')

    def test_bad_member_or_preimage_cannot_create_stage(self):
        (self.source / install.MEMBERS[0]).write_bytes(b'bad')
        with self.assertRaisesRegex(guard.Held, 'member mismatch'):
            install.stage(self.approval)
        self.assertFalse(self.stage.exists())
        self.old.write_bytes(b'changed')
        with self.assertRaisesRegex(guard.Held, 'preimage differs'):
            install.stage(self.approval)
        self.assertFalse(self.stage.exists())

    def test_tampered_completed_member_is_never_replaced(self):
        install.stage(self.approval)
        member = self.stage / install.MEMBERS[0]
        member.chmod(0o600)
        member.write_bytes(b'tampered')
        with self.assertRaisesRegex(guard.Held, 'published recovery closure differs'):
            install.stage(self.approval)
        self.assertEqual(member.read_bytes(), b'tampered')


    def test_interruption_before_publication_leaves_no_visible_stage(self):
        actual = install._rename_noreplace
        def interrupt(source, target):
            if target == self.stage:
                self.assertEqual(set(p.name for p in source.iterdir()),
                                 set(install.MEMBERS) | {'intent.json', 'receipt.json'})
                raise OSError('interrupted before publish')
            return actual(source, target)
        with patch.object(install, '_rename_noreplace', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                install.stage(self.approval)
        self.assertFalse(self.stage.exists())
        self.assertEqual(install.stage(self.approval)['status'], 'staged_not_activated')
        self.assertEqual((self.old.read_bytes(), self.selection.read_bytes()), (b'old', b'selected'))

    def test_lost_ack_after_publication_only_reconciles_full_exact_stage(self):
        actual = install._rename_noreplace
        def lose_ack(source, target):
            actual(source, target)
            if target == self.stage:
                raise OSError('lost ack')
        with patch.object(install, '_rename_noreplace', side_effect=lose_ack):
            result = install.stage(self.approval)
        self.assertEqual(install.stage(self.approval), result)
        self.assertEqual((self.old.read_bytes(), self.selection.read_bytes()), (b'old', b'selected'))
        (self.stage / install.MEMBERS[0]).unlink()
        with self.assertRaisesRegex(guard.Held, 'published recovery closure differs'):
            install.stage(self.approval)
        self.assertFalse((self.stage / install.MEMBERS[0]).exists())


    def test_reentry_after_interrupted_rename_syncs_parent_before_success(self):
        class ProcessInterrupted(BaseException):
            pass

        actual = install._rename_noreplace

        def interrupt(source, target):
            if target == self.stage:
                self.assertFalse(target.exists())
                source.rename(target)  # Deliberately no parent sync.
                raise ProcessInterrupted()
            return actual(source, target)

        with patch.object(install, '_rename_noreplace', side_effect=interrupt):
            with self.assertRaises(ProcessInterrupted):
                install.stage(self.approval)
        inodes = {p.name: p.stat().st_ino for p in self.stage.iterdir()}
        with patch.object(install, '_sync_parent', wraps=install._sync_parent) as sync, \
             patch.object(install, '_rename_noreplace', side_effect=AssertionError('must not republish')):
            self.assertEqual(install.stage(self.approval)['status'], 'staged_not_activated')
            sync.assert_called_once_with(self.stage)
        self.assertEqual(inodes, {p.name: p.stat().st_ino for p in self.stage.iterdir()})
        self.assertEqual((self.old.read_bytes(), self.selection.read_bytes()), (b'old', b'selected'))
