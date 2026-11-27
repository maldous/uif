from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import stage_commissioned_normal_runtime as normal

g = normal.guard


class NormalRuntimeStageTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        old, selector = self.root/'old-drop-in', self.root/'old-selection'
        old.write_bytes(b'old drop-in')
        selector.write_bytes(b'old selector')
        self.old, self.selector = old, selector
        self.enterContext(patch.multiple(g, STAGES=self.root, SELECTOR=selector, DROPIN=old,
            protected_parents=lambda _: None, _private_webhook_root=lambda: True,
            selector_lock=lambda **_: nullcontext(), bytes_pin=lambda p,*_: g.digest(p.read_bytes())))
        self.leaf = self.root/'uif1313-normal-test'

    def fixture(self, *, exclude=None, extra=()):
        files = {n: n.encode() for n in normal.REQUIRED if n != exclude}
        files.update(extra)
        buf = BytesIO()
        members = {}
        with tarfile.open(fileobj=buf, mode='w') as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o555 if name == 'venv/bin/python3.12' else 0o444
                tar.addfile(info, BytesIO(data))
                members[name] = dict(bytes=len(data), mode=info.mode, sha256=g.digest(data))
        raw = buf.getvalue()
        pins = normal.NormalRuntimeStageApproval('a'*64, self.leaf.name, g.digest(raw),
            g.plan_digest(members), g.digest(self.old.read_bytes()), g.digest(self.selector.read_bytes()))
        return pins, raw

    def test_detached_stage_and_same_original_no_write_to_unit_or_selector(self):
        args = self.fixture()
        result = normal.stage_normal_runtime(*args)
        self.assertEqual(result['status'], 'normal_stage_materialized_not_selected')
        self.assertEqual(normal.stage_normal_runtime(*args), result)
        self.assertEqual((self.old.read_bytes(), self.selector.read_bytes()), (b'old drop-in', b'old selector'))
        (self.leaf/'launch.py').unlink()
        with self.assertRaisesRegex(g.Held, 'closure incomplete'):
            normal.stage_normal_runtime(*args)
        self.assertFalse((self.leaf/'launch.py').exists())

    def test_missing_modules_escaping_paths_and_old_preimage_refuse_before_writes(self):
        for params in ({'exclude': 'launch.py'}, {'extra': [('../bad', b'bad')]}):
            with self.assertRaises(g.Held):
                normal.stage_normal_runtime(*self.fixture(**params))
            self.assertFalse(self.leaf.exists())
        args = self.fixture()
        self.selector.write_bytes(b'changed elsewhere')
        with self.assertRaisesRegex(g.Held, 'selected runtime changed'):
            normal.stage_normal_runtime(*args)
        self.assertFalse(self.leaf.exists())

    def test_final_rename_interruption_or_lost_ack_never_partially_publishes(self):
        args = self.fixture()
        real = normal._rename_noreplace
        def interrupt(src, dst):
            if dst == self.leaf:
                raise OSError('before publish')
            return real(src, dst)
        with patch.object(normal, '_rename_noreplace', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'before publish'):
                normal.stage_normal_runtime(*args)
        self.assertFalse(self.leaf.exists())
        def lose_ack(src, dst):
            result = real(src, dst)
            if dst == self.leaf:
                raise OSError('lost ack')
            return result
        with patch.object(normal, '_rename_noreplace', side_effect=lose_ack):
            result = normal.stage_normal_runtime(*args)
        self.assertEqual(normal.stage_normal_runtime(*args), result)


    def test_visible_stage_requires_parent_durability_even_on_retry(self):
        args = self.fixture()
        rename = normal._rename_noreplace
        sync = normal._sync_parent
        touched = []
        def publish_before_ack(source, dest):
            if dest == self.leaf:
                source.rename(dest)  # Name visible, parent not yet synced.
                raise OSError('prior process stopped before parent sync')
            return rename(source, dest)
        def fail_stage_sync(path):
            if path == self.leaf:
                raise OSError('parent sync interrupted')
            return sync(path)
        with patch.object(normal, '_rename_noreplace', side_effect=publish_before_ack), \
                patch.object(normal, '_sync_parent', side_effect=fail_stage_sync):
            with self.assertRaisesRegex(OSError, 'parent sync interrupted'):
                normal.stage_normal_runtime(*args)
        self.assertTrue(self.leaf.is_dir())
        before = {p.relative_to(self.leaf):p.stat().st_ino for p in self.leaf.rglob('*')}
        with patch.object(normal, '_rename_noreplace', side_effect=AssertionError('must not rewrite')), \
                patch.object(normal, '_sync_parent', side_effect=lambda path: (touched.append(path), sync(path))):
            self.assertEqual(normal.stage_normal_runtime(*args)['status'],
                             'normal_stage_materialized_not_selected')
        self.assertEqual(touched, [self.leaf])
        self.assertEqual(before, {p.relative_to(self.leaf):p.stat().st_ino for p in self.leaf.rglob('*')})
        self.assertEqual((self.old.read_bytes(), self.selector.read_bytes()), (b'old drop-in', b'old selector'))


    def test_materializes_empty_regular_marker_but_not_empty_required_record(self):
        marker = 'venv/lib/python3.12/encodings/.empty-marker'
        args = self.fixture(extra=[(marker,b'')])
        first = normal.stage_normal_runtime(*args)
        self.assertEqual((self.leaf/marker).read_bytes(), b'')
        self.assertEqual(normal.stage_normal_runtime(*args), first)
        with self.assertRaisesRegex(g.Held, 'incomplete'):
            normal.stage_normal_runtime(*self.fixture(extra=[('launch.py',b'')]))
