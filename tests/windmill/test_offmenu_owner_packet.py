# Detached test doubles. The trusted owner supplies the already loaded
# OffMenu modules in production; these tests never use any host inputs.
import json
import os
from pathlib import Path
import re
import sys
import types
import unittest
from dataclasses import make_dataclass
from hashlib import sha256
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import offmenu_native_release as r
    import offmenu_owner_packet as m
    import offmenu_verifier_v2_loader as loader


def review_record(raw):
    def unique(items):
        result = dict(items)
        if len(result) != len(items):
            raise ValueError("duplicate_record_key")
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError("record_object")
    return value


def shape(value, *keys):
    if set(value) != set(keys):
        raise ValueError("record_fields")
    return value


ingress = types.SimpleNamespace(
    STATE=Path("/var/lib/offmenu-publisher/upgrade-ingress"),
    SMALL=262144,
    OWNER=re.compile("[A-Za-z_][A-Za-z0-9_.-]{0,127}"),
    record=review_record,
    shape=shape,
    encode=lambda value: (json.dumps(value, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=True) + "\n").encode(),
    publisher_fence=Mock(),
    read=Mock(),
)


class Test(unittest.TestCase):

    def setUp(self):
        self.events = []
        self.original = 'd' * 64
        p = types.SimpleNamespace(_SPLIT_PACKET_ROOT=ingress.STATE, path_name=Mock(), OwnerPackageInputs=make_dataclass('Owner', ['head', 'tree', 'interpreter_sha256', 'node_sha256', 'interpreter_custody', 'node_custody']), PublisherPackageInput=make_dataclass('Publisher', ['head', 'tree']), PackageAcceptance=make_dataclass('Acceptance', ['owner', 'head', 'tree', 'review']))
        self.modules = loader.LoadedOffMenuV2(loader.PROFILE.release, ingress, p, types.SimpleNamespace(STATE='/var/lib/offmenu-publisher'))
        self.source = (r.OFFMENU_SOURCE, r.OFFMENU_TREE)
        self.review = dict(schema='offmenu.native-package-review/1', owner='owner', head=self.source[0], tree=self.source[1], author='author', reviewer='reviewer', verdict='accept')
        self.owner = p.OwnerPackageInputs(*self.source, 'a' * 64, 'b' * 64, dict(path='interpreter.json', sha256='c' * 64), dict(path='node.json', sha256='d' * 64))
        self.publisher = p.PublisherPackageInput(*self.source)
        self.pins = {name: 'e' * 64 for name in ('owner_current', 'owner_catalog', 'publisher_config')}

        def bind(o, k, fn):
            return self.enterContext(patch.object(o, k, side_effect=fn))
        bind(os, 'getresuid', lambda: (0, 0, 0))
        bind(os, 'close', lambda fd: None)
        self.lock = bind(m.fcntl, 'flock', lambda fd, flags: None)
        bind(os, 'fsync', lambda fd: self.events.append(('sync', fd)))
        self.mkdir = bind(os, 'mkdir', lambda *args, **kw: self.events.append(('mkdir', args, kw)))
        bind(os, 'fstat', lambda fd: types.SimpleNamespace(st_uid=0, st_mode=16832))
        self.open = bind(os, 'open', lambda name, *args, **kw: self.events.append(('open', name, args, kw)) or (43 if name == self.original else 44))
        self.write = bind(os, 'write', lambda fd, raw: self.events.append(('write', fd, bytes(raw))) or len(raw))
        bind(m, 'protected_directory', lambda path, *args: 45 if path.name == 'publisher-fence' else 46 if str(path) == '/var/lib/offmenu-publisher' else 42)
        bind(ingress, 'publisher_fence', lambda: ingress.STATE / 'publisher-fence')

        def missing(name, **kwargs):
            raise FileNotFoundError(name)
        self.statter = bind(os, 'stat', missing)
        bind(m, 'prepare_fixed_native_package', lambda *args, **kw: self.events.append(('prepare', args, kw)) or {'receipt': 'fixture'})

    def invoke(self, raw=None, **extra):
        raw = ingress.encode(self.review) if raw is None else raw
        return m.initialize_offmenu_owner_packet(self.modules, self.original, **dict(dict(owner_principal='owner', review_raw=raw, review_sha256=sha256(raw).hexdigest(), owner=self.owner, publisher=self.publisher, config_pins=self.pins), **extra))

    def test_exact_order(self):
        self.assertEqual(self.invoke(), {'receipt': 'fixture'})
        self.assertEqual([x[0] for x in self.events], ['mkdir', 'sync', 'open', 'open', 'write', 'sync', 'sync', 'prepare'])
        self.assertEqual(self.events[2], ('open', self.original, (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,), {'dir_fd': 42}))
        self.assertEqual(self.events[3], ('open', 'package-review.json', (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384), {'dir_fd': 43}))
        self.assertEqual(self.events[4], ('write', 44, ingress.encode(self.review)))
        self.assertEqual(self.events[5:7], [('sync', 44), ('sync', 43)])
        self.lock.assert_called_once_with(43, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)
        self.assertEqual(self.events[0][2], dict(mode=448, dir_fd=42))
        kwargs = self.events[-1][2]
        self.assertEqual(self.events[-1][1][1], self.original)
        self.assertEqual(kwargs['acceptance'].review['sha256'], sha256(ingress.encode(self.review)).hexdigest())
        self.assertIs(kwargs['owner'], self.owner)
        self.assertIs(kwargs['publisher'], self.publisher)

    def test_same_person_or_wrong_exact_review_refuses_before_effect(self):
        for field, value in (('reviewer', 'OWNER'), ('reviewer', 'AUTHOR'), ('verdict', 'hold'), ('head', 'a' * 40)):
            before = self.review.copy()
            self.review[field] = value
            with self.assertRaises(PermissionError):
                self.invoke()
            self.review = before
        with self.assertRaises(ValueError):
            self.invoke(review_sha256='d' * 64)
        with self.assertRaises(ValueError):
            self.invoke(b'{"owner":"x","owner":"y"}')
        self.assertEqual(self.events, [])

    def test_invalid_custody_does_not_consume_original(self):
        self.owner.node_custody = {'path': 'node.json', 'sha256': 'bad'}
        with self.assertRaisesRegex(ValueError, 'CUSTODY'):
            self.invoke()
        self.assertEqual(self.events, [])

    def test_retained_original_never_replays(self):
        self.mkdir.side_effect = FileExistsError('original')
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.open.assert_not_called()
        self.assertEqual(self.events, [])

    def test_partial_review_write_does_not_compose(self):
        self.write.side_effect = OSError('unknown')
        with self.assertRaises(OSError):
            self.invoke()
        self.assertEqual([x[0] for x in self.events], ['mkdir', 'sync', 'open', 'open'])

    def test_noncanonical_raw_review_retained_without_reencoding(self):
        raw = (json.dumps(self.review, indent=2) + '\n \n').encode()
        self.assertNotEqual(raw, ingress.encode(self.review))
        self.assertEqual(self.invoke(raw), {'receipt': 'fixture'})
        self.assertEqual(self.events[4], ('write', 44, raw))
        self.assertEqual(self.events[-1][2]['acceptance'].review['sha256'], sha256(raw).hexdigest())

    def observed(self, package_sha256='9' * 64, flags=()):
        raw = ingress.encode(self.review)
        p = self.modules.package
        info = types.SimpleNamespace(st_uid=0, st_mode=16832, st_dev=8, st_ino=19)
        manifest = dict(head=self.source[0], tree=self.source[1], owner='owner', review=dict(path='package-review.json', sha256=sha256(raw).hexdigest()), interpreter_custody=self.owner.interpreter_custody, node_custody=self.owner.node_custody, roles={'owner_config': {'files': {'current.json': {'sha256': self.pins['owner_current']}, 'catalog.json': {'sha256': self.pins['owner_catalog']}}}, 'publisher_config': {'files': {'config.json': {'sha256': self.pins['publisher_config']}}}})
        ac = p.PackageAcceptance('owner', *self.source, manifest['review'])
        p.read_package = Mock(return_value=(manifest, {}))
        p._owner_local_receipt = Mock(return_value={'receipt': 'verified'})
        if 'wrongconfig' in flags:
            manifest['roles']['publisher_config']['files']['config.json']['sha256'] = 'a' * 64
        if 'partial' in flags:
            p.read_package.side_effect = ValueError('partial')

        def read_stat(name, **kwargs):
            if kwargs.get('dir_fd') in (45, 46):
                if 'orphan_fence' in flags and kwargs['dir_fd'] == 45:
                    return info
                if 'orphan_canonical' in flags and kwargs['dir_fd'] == 46:
                    return info
                if 'unknown_fence' in flags:
                    raise PermissionError(name)
                raise FileNotFoundError(name)
            return info
        with patch.object(os, 'fstat', return_value=info), patch.object(os, 'stat', side_effect=read_stat), patch.object(ingress, 'read', return_value=raw):
            if 'missing' in flags:
                self.open.side_effect = FileNotFoundError()
            return m.observe_offmenu_owner_packet(self.modules, self.original, acceptance=ac, package_sha256=package_sha256, config_pins=self.pins, interpreter_custody=self.owner.interpreter_custody, node_custody=self.owner.node_custody)

    def test_readback_known_complete_only(self):
        self.assertEqual(self.observed(), {'state': 'prepared', 'receipt': {'receipt': 'verified'}})
        self.assertEqual(self.observed(None), {'state': 'reconcile'})
        self.assertEqual(self.observed(flags=('wrongconfig',)), {'state': 'reconcile'})
        self.assertEqual(self.observed(flags=('partial',)), {'state': 'reconcile'})
        self.assertEqual(self.observed(flags=('missing',)), {'state': 'not_started'})
        self.mkdir.assert_not_called()
        self.write.assert_not_called()

    def retained(self, entries=('package-review.json',), flags=()):
        from io import BytesIO
        raw = ingress.encode(self.review)
        p = self.modules.package
        ac = p.PackageAcceptance('owner', *self.source, dict(path='package-review.json', sha256=sha256(raw).hexdigest()))
        if 'changed' in flags:
            raw += b' '
        info = types.SimpleNamespace(st_uid=0, st_mode=16832, st_dev=8, st_ino=19, st_nlink=1)
        rev = types.SimpleNamespace(st_uid=0, st_mode=33152, st_nlink=1, st_size=len(raw))

        def live(name, **kwargs):
            if name == self.original and kwargs['dir_fd'] == 42:
                return info
            if 'effect' in flags:
                return info
            raise FileNotFoundError(name)
        self.open.side_effect = lambda name, *args, **kw: 43 if name == self.original else 44
        with patch.object(os, 'fstat', side_effect=lambda fd: rev if fd == 44 else info), patch.object(os, 'stat', side_effect=live), patch.object(os, 'listdir', return_value=list(entries)), patch.object(os, 'fdopen', side_effect=lambda *a, **kw: BytesIO(raw)), patch.object(ingress, 'publisher_fence', return_value=ingress.STATE / 'publisher-fence'):
            return m.resume_unstarted_offmenu_owner_package(self.modules, self.original, acceptance=ac, owner=self.owner, publisher=self.publisher, config_pins=self.pins)

    def test_resume_review_only_calls_same_original_without_rewriting_review(self):
        self.assertEqual(self.retained(), {'state': 'prepared', 'receipt': {'receipt': 'fixture'}})
        self.assertEqual(self.events[-1][1][1], self.original)
        self.assertEqual(self.events[:2], [('sync', 44), ('sync', 43)])
        self.mkdir.assert_not_called()
        self.write.assert_not_called()
        self.lock.assert_called_once_with(43, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)

    def test_pointer_fence_partial_and_tampered_review_never_resume(self):
        for key in ('package', 'request.json', 'windmill-release.intent', 'ingress-intent.json'):
            self.assertEqual(self.retained(('package-review.json', key)), {'state': 'reconcile'})
        for flag in ('effect', 'changed'):
            self.assertEqual(self.retained(flags=(flag,)), {'state': 'reconcile'})
        self.assertEqual(self.events, [])
        self.mkdir.assert_not_called()
        self.write.assert_not_called()

    def test_missing_packet_with_orphan_effect_or_unknown_never_restarts(self):
        for flag, effect_fd in (('orphan_fence', 45), ('orphan_canonical', 46), ('unknown_fence', None)):
            with self.subTest(flag=flag):
                self.open.side_effect = FileNotFoundError(self.original)

                def prior(name, **kwargs):
                    if flag == 'unknown_fence':
                        raise PermissionError(name)
                    if kwargs['dir_fd'] == effect_fd:
                        return object()
                    raise FileNotFoundError(name)
                self.statter.side_effect = prior
                with self.assertRaisesRegex(RuntimeError, 'ORIGINAL_REQUIRES_RECONCILIATION'):
                    self.invoke()
                self.open.assert_not_called()
                self.assertEqual(self.observed(flags=('missing', flag)), {'state': 'reconcile'})
        self.mkdir.assert_not_called()
        self.write.assert_not_called()
        self.assertEqual(self.events, [])

    def test_resume_exact_digest_of_invalid_review_is_not_acceptance(self):
        for field, value in (('reviewer', 'OWNER'), ('reviewer', 'AUTHOR'), ('verdict', 'hold'), ('tree', 'a' * 40)):
            with self.subTest(field=field, value=value):
                before = self.review.copy()
                self.review[field] = value
                self.assertEqual(self.retained(), {'state': 'reconcile'})
                self.review = before
        self.assertEqual(self.events, [])
        self.mkdir.assert_not_called()
        self.write.assert_not_called()
