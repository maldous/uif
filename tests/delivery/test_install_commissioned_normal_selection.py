from contextlib import contextmanager
from dataclasses import replace
import fcntl
from io import BytesIO
import os
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import install_commissioned_normal_selection as install

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
        self.enterContext(patch.object(install.guarded, 'observe_guarded_preimage',
                                        side_effect=self.observe_guarded))
        self.enterContext(patch.object(install.effective, 'observe_normal_effective',
                                        side_effect=self.observe_normal))
        values = {p: (p.encode(), 0o555 if p == g.INTERPRETER else 0o444) for p in s.REQUIRED}
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tar:
            for name, (raw, mode) in values.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(raw), mode
                tar.addfile(info, BytesIO(raw))
        self.archive = buf.getvalue()
        self.staged = s.NormalRuntimeStageApproval('b'*64, self.leaf.name, g.digest(self.archive),
            g.plan_digest(s._project_member_map(values)), g.digest(old.read_bytes()), g.digest(b'old'))
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

    def observe_guarded(self, stream, prior, *, conditions, asserts):
        self.assertTrue(self.lock_held)
        self.assertEqual(prior.selector_sha256, self.staged.prior_selection_sha256)
        return True  # Actual retained/typed-manager proof tested in separate observer tests.

    def observe_normal(self, original):
        self.assertTrue(self.lock_held)
        self.assertEqual(self.selector.read_bytes(), self.bytes)
        self.assertEqual(self.dropin.read_bytes(), self.unit)
        return {'status': 'disk_matches_manager_reload_needed', 'installation_accepted': False}

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
