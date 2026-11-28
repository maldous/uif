from contextlib import contextmanager
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import install_commissioned_normal_selection as install
from render_detached_normal_stage import render_detached_normal_stage

g = install.guard
s = install.staging


class FixedNormalSelection(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.selector = root/'normal'/'selection.json'
        self.dropin = root/'unit'/install.normal.DROPIN
        self.receipts = root/'receipts'
        for p in (self.selector.parent, self.dropin.parent, self.receipts):
            p.mkdir()
        old, old_selector, lock = self.dropin.with_name('legacy'), root/'guarded', root/'lock'
        old.write_bytes(g.selector_dropin())
        old_selector.write_bytes(b'old')
        old.chmod(0o644)
        old_selector.chmod(0o644)
        lock.write_bytes(b'lock')
        self.lock_held = False

        @contextmanager
        def held(*, shared):
            self.assertIs(shared, False)
            with lock.open('rb') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.lock_held = True
                try:
                    yield stream
                finally:
                    self.lock_held = False

        self.enterContext(patch.multiple(g, LOCK=lock, SELECTOR=old_selector, DROPIN=old,
            RECEIPTS=self.receipts, STAGES=root, protected_parents=lambda _: None,
            _private_webhook_root=lambda: True, selector_lock=held,
            bytes_pin=lambda path,*args: g.digest(install._snapshot(path, (0o400, 0o444, 0o555, 0o644)))))
        self.enterContext(patch.object(install, '_ROOT_UID', os.getuid()))
        old.write_bytes(g.selector_dropin())
        self.enterContext(patch.object(install.normal, 'SELECTION', str(self.selector)))
        # Existing renderer production path grammar remains unchanged; translate only
        # the selected fixed stage's tmp prefix so canonical rendered bytes can be used.
        check = install.normal.check_normal_stage
        self.leaf = root/'uif1313-normal-fixture'
        self.enterContext(patch.object(install.normal, 'check_normal_stage',
                         side_effect=lambda stage: stage if stage == str(self.leaf) else check(stage)))
        self.enterContext(patch.object(install.effective, 'check_normal_stage', install.normal.check_normal_stage))
        self.enterContext(patch.object(install.guarded, 'observe_retained_idle_start', return_value='consumed'))
        self.enterContext(patch.object(install.effective, '_get', side_effect=self.manager_property))
        values = {p: (p.encode(), 0o555 if p == g.INTERPRETER else 0o444) for p in s.REQUIRED}
        self.archive, projection = render_detached_normal_stage(values)
        self.staged = s.NormalRuntimeStageApproval('b'*64, self.leaf.name, projection['archive_sha256'],
            projection['members_sha256'], g.digest(old.read_bytes()), g.digest(b'old'))
        s.stage_normal_runtime(self.staged, self.archive)
        args = {key: g.digest(values[name][0]) for key, name in (
                 ('launcher_sha256','launch.py'), ('interpreter_sha256',g.INTERPRETER),
                 ('authority_config_sha256',g.IDLE_CONFIG))}
        self.bytes = install.normal.render_normal_selection(str(self.leaf), source_head='1'*40,
            source_tree='2'*40, release_sha256=self.staged.members_sha256, **args)
        self.unit = install.normal.render_normal_dropin(str(self.leaf))
        unit_pin = g.digest(self.unit)
        manager = install.effective.NormalEffectiveOriginal(str(self.leaf), unit_pin, unit_pin, 0, 0, (), ())
        self.original = install.transaction.SelectionOriginal('a'*64, self.staged, None, None,
            self.staged.prior_dropin_sha256, g.digest(self.bytes), unit_pin, manager)

    def manager_property(self, interface, name, signature):
        self.assertTrue(self.lock_held)
        exe = '/usr/bin/python3'
        args = [exe, '-I', '-S', '-B', str(g.TOOL_PATH), '--profile', 'uif-idle',
                '--stage', g.FIXED_STAGE, '--action', 'serve']
        # No reload is performed. Once the normal drop-in exists on fixture
        # disk, the manager still reports the old command and a pending reload.
        return {'Id': install.normal.UNIT, 'NeedDaemonReload': self.dropin.exists(),
                'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': 0, 'ControlPID': 0,
                'Job': [0, '/'], 'NRestarts': 0, 'ExecMainStartTimestampMonotonic': 0,
                'Conditions': [], 'Asserts': [], 'ExecStart': [[exe, args, False, 0, 0, 0, 0, 0, 0, 0]]}[name]

    def run_original(self):
        return install._install_from_trusted_owner(
            lambda: (self.original, self.archive, self.bytes, self.unit))

    def test_real_no_replace_pair_and_durable_same_original(self):
        got = self.run_original()
        self.assertFalse(got['manager']['installation_accepted'])
        before = [p.stat().st_ino for p in (self.selector, self.dropin, *sorted(self.receipts.iterdir()))]
        self.assertEqual(len(before), 4)
        self.assertEqual(self.run_original(), got)
        self.assertEqual(before, [p.stat().st_ino for p in
                                  (self.selector, self.dropin, *sorted(self.receipts.iterdir()))])

    def test_symlink_or_unexpected_mode_is_not_absent(self):
        outside = self.selector.parent/'outside'
        outside.write_bytes(b'untouched')
        self.selector.symlink_to(outside)
        with self.assertRaises(OSError):
            self.run_original()
        self.selector.unlink()
        self.selector.write_bytes(self.bytes)
        self.selector.chmod(0o666)
        with self.assertRaisesRegex(g.Held, 'custody'):
            self.run_original()
        self.assertEqual(outside.read_bytes(), b'untouched')
        self.assertFalse(self.dropin.exists())
        self.assertFalse(list(self.receipts.iterdir()))

    def test_noreplace_protects_outside_writer_and_changed_stage_blocks(self):
        self.leaf.joinpath('launch.py').chmod(0o644)
        with self.assertRaises(g.Held):
            self.run_original()
        self.leaf.joinpath('launch.py').chmod(0o444)
        publish = install._publish_once
        def insert(path, mode, write):
            if path == self.selector:
                self.selector.write_bytes(b'outside writer')
            return publish(path, mode, write)
        with patch.object(install, '_publish_once', side_effect=insert), self.assertRaises(FileExistsError):
            self.run_original()
        self.assertEqual(self.selector.read_bytes(), b'outside writer')
        self.assertFalse(self.dropin.exists())

    def test_untrusted_dispatch_and_hardlinked_leaf_refused(self):
        with patch.object(g, '_private_webhook_root', return_value=False), self.assertRaises(g.Held):
            install._install_from_trusted_owner(lambda: self.fail('untrusted resolver called'))
        other = self.selector.parent/'other'
        other.write_bytes(self.bytes)
        other.chmod(0o444)
        os.link(other, self.selector)
        with self.assertRaisesRegex(g.Held, 'custody'):
            self.run_original()
        self.assertFalse(self.dropin.exists())
        self.assertFalse(list(self.receipts.iterdir()))


    def test_intermediate_refuses_changed_guarded_conditions_or_asserts(self):
        expected = self.original
        with patch.object(install._FixedOwner, 'publish_normal_dropin',
                          side_effect=OSError('lost before drop-in')):
            with self.assertRaisesRegex(OSError, 'lost before drop-in'):
                self.run_original()
        before = self.selector.stat().st_ino
        for field in ('prior_guarded_conditions', 'prior_guarded_asserts'):
            self.original = replace(expected, **{field: (('Different', False, False, 'pin'),)})
            with self.assertRaisesRegex(g.Held, 'retained normal selection original differs'):
                self.run_original()
            self.assertEqual(self.selector.stat().st_ino, before)
            self.assertFalse(self.dropin.exists())
        self.original = expected
        self.run_original()
        self.assertEqual(self.selector.stat().st_ino, before)


    def test_retained_canonical_original_restores_types_and_refuses_other_bytes(self):
        txn = install.transaction
        intended = replace(self.original,
            prior_guarded_conditions=(('ConditionPathExists', False, False, '/fixed'),))
        raw = txn.encode_selection_original(intended)
        pin = g.digest(raw)
        self.assertEqual(txn.decode_selection_original(raw, pin), intended)
        self.assertEqual(txn.encode_selection_original(txn.decode_selection_original(raw, pin)), raw)
        changed = txn.encode_selection_original(self.original)
        with self.assertRaisesRegex(g.Held, 'byte pin differs'):
            txn.decode_selection_original(changed, pin)
        for alternate in (raw + b'\n', raw.replace(b'/2"', b'/1"'),
                          raw.replace(b'"prior_guarded_asserts":[]', b'"prior_guarded_asserts":null'),
                          raw.replace(b'"schema":', b'"schema":"duplicate","schema":')):
            with self.assertRaises(g.Held):
                txn.decode_selection_original(alternate, g.digest(alternate))
        self.assertFalse(list(self.receipts.iterdir()))
        self.assertFalse(self.selector.exists())

    def test_fixed_action_materializes_then_reconciles_verified_same_original(self):
        import shutil
        from unittest.mock import Mock
        shutil.rmtree(self.leaf)
        # Original and bytes come from a detached owner fixture. No installed
        # Windmill binding, host paths, or manager are used.
        retained = self.leaf.parent/'retained-original'
        wire = install.transaction.encode_selection_original(self.original)
        original_pin = g.digest(wire)
        retained.write_bytes(wire)
        retained.chmod(0o400)

        def resolve_retained():
            raw = install._snapshot(retained, (0o400,))
            return (install.transaction.decode_selection_original(raw, original_pin),
                    self.archive, self.bytes, self.unit)

        resolve = Mock(side_effect=resolve_retained)
        previous = install.transaction.commit_normal_selection

        def lose_ack(*args):
            committed = previous(*args)
            self.assertEqual(committed['status'], 'normal_pair_committed_on_disk')
            raise OSError('terminal reply lost')

        with patch.object(install.transaction, 'commit_normal_selection', side_effect=lose_ack):
            with self.assertRaisesRegex(OSError, 'terminal reply lost'):
                install._stage_and_select_from_trusted_owner(resolve)
        self.assertEqual(resolve.call_count, 1)
        selected = [self.leaf, self.selector, self.dropin, *sorted(self.receipts.iterdir())]
        before = [p.stat().st_ino for p in selected]
        result = install._stage_and_select_from_trusted_owner(resolve)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual([p.stat().st_ino for p in selected], before)
        self.assertEqual(result['operation_id'], self.original.operation_id)
        self.assertEqual(result['release_sha256'], self.staged.members_sha256)
        intent = self.receipts/(self.original.operation_id + '.normal-select-intent.json')
        receipt = self.receipts/(self.original.operation_id + '.normal-select-committed.json')
        self.assertEqual(intent.read_bytes(), wire)
        self.assertEqual(result['staging_receipt_sha256'], g.digest((self.leaf/s.RECEIPT).read_bytes()))
        self.assertEqual(result['selection_receipt_sha256'], g.digest(receipt.read_bytes()))
        self.assertEqual(result['manager_status'], 'disk_matches_manager_reload_needed')
        self.assertFalse(result['installation_accepted'])
        self.assertFalse(result['native_startup_verified'])
        self.assertNotIn('manager', result)

    def test_invalid_retained_manager_history_fails_before_staging(self):
        import shutil
        shutil.rmtree(self.leaf)
        for field in ('before_start_monotonic', 'before_restarts'):
            for invalid in (False, -1, '0'):
                with self.subTest(field=field, value=invalid):
                    manager = replace(self.original.effective_original, **{field: invalid})
                    wire = install.transaction.encode_selection_original(
                        replace(self.original, effective_original=manager))
                    restored = install.transaction.decode_selection_original(wire, g.digest(wire))
                    with self.assertRaisesRegex(g.Held, 'exact normal observation inputs'):
                        install._stage_and_select_from_trusted_owner(
                            lambda: (restored, self.archive, self.bytes, self.unit))
                    self.assertFalse(self.leaf.exists())
                    self.assertFalse(self.selector.exists())
                    self.assertFalse(self.dropin.exists())
                    self.assertFalse(list(self.receipts.iterdir()))

    def test_fixed_action_rejects_target_before_stage_creation(self):
        import shutil
        shutil.rmtree(self.leaf)
        with self.assertRaisesRegex(g.Held, 'target bytes changed'):
            install._stage_and_select_from_trusted_owner(
                lambda: (self.original, self.archive, self.bytes + b'x', self.unit))
        self.assertFalse(self.leaf.exists())
        self.assertFalse(self.selector.exists())
        self.assertFalse(self.dropin.exists())
        self.assertFalse(list(self.receipts.iterdir()))
