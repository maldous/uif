from hashlib import sha256
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from uif.effect_authority import SourcePin
from uif.handoff_store import Hold
from uif.stokowski_cloudflare.native_workspace_preparation import (
    _copy_pinned_bundle, _git, place_prepared_workspace, prepare_detached_workspace,
)


class NativeWorkspacePreparationTest(unittest.TestCase):
    def test_bundle_materialization_is_private_create_only_and_source_pinned(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            origin = root / 'origin'
            origin.mkdir()
            _git(origin, 'init', '--template=', '-b', 'develop')
            (origin / 'file').write_text('exact\n')
            _git(origin, 'add', 'file')
            _git(origin, '-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture',
                 'commit', '-m', 'fixture')
            head, tree = [_git(origin, 'rev-parse', name).decode().strip()
                          for name in ('HEAD', 'HEAD^{tree}')]
            source = SourcePin('maldous/offmenu', 'develop', head, head, tree)
            bundle = root / 'origin.bundle'
            _git(origin, 'bundle', 'create', str(bundle), 'develop')
            pin = sha256(bundle.read_bytes()).hexdigest()
            stages = root / 'staging'
            stages.mkdir(mode=0o700)
            existing = stages / 'UIF-1'
            existing.mkdir()
            before = (origin / 'file').read_bytes()
            with self.assertRaisesRegex(Hold, 'bundle_digest'):
                prepare_detached_workspace(stages, bundle, 'a'*64, source)
            failed, = set(stages.iterdir()) - {existing}
            self.assertEqual((failed / '.uif-input.bundle').read_bytes(), bundle.read_bytes())
            self.assertFalse((failed / '.git').exists())
            alias = root / 'alias.bundle'
            alias.symlink_to(bundle)
            with self.assertRaises(OSError) as blocked:
                prepare_detached_workspace(stages, alias, pin, source)
            self.assertEqual(blocked.exception.errno, errno.ELOOP)
            oversized = root / 'oversized.bundle'
            with oversized.open('wb') as stream:
                stream.truncate((512 * 1024 * 1024) + 1)
            with self.assertRaisesRegex(Hold, 'bundle_type_or_size'):
                prepare_detached_workspace(stages, oversized, pin, source)
            fifo = root / 'fifo.bundle'
            os.mkfifo(fifo)
            with self.assertRaisesRegex(Hold, 'bundle_type_or_size'):
                prepare_detached_workspace(stages, fifo, pin, source)
            self.assertEqual(list(existing.iterdir()), [])
            first, observed = prepare_detached_workspace(stages, bundle, pin, source)
            second, other = prepare_detached_workspace(stages, bundle, pin, source)
            self.assertNotEqual(first, second)
            self.assertNotEqual(observed['inode'], other['inode'])
            self.assertEqual((first / 'file').read_bytes(), before)
            self.assertEqual((origin / 'file').read_bytes(), before)
            self.assertTrue(existing.is_dir())
            self.assertFalse((first / '.git/objects/info/alternates').exists())
            self.assertFalse((first / '.git/hooks').exists())
            with self.assertRaises(OSError) as collision:
                place_prepared_workspace(stages, first, 'UIF-1', source, observed)
            self.assertEqual(collision.exception.errno, errno.EEXIST)
            self.assertTrue(first.is_dir())
            self.assertEqual(list(existing.iterdir()), [])
            with self.assertRaisesRegex(Hold, 'observation'):
                place_prepared_workspace(stages, first, 'UIF-2', source, observed | {'inode': other['inode']})
            final = place_prepared_workspace(stages, first, 'UIF-2', source, observed)
            self.assertEqual(final, observed | {'path': str(stages / 'UIF-2')})
            self.assertFalse(first.exists())
            self.assertTrue(second.is_dir())
            self.assertEqual((stages / 'UIF-2/file').read_bytes(), before)

            # A lost sync acknowledgement must not replay the rename or undo
            # the exact target. In retained custody the owner can observe it.
            with patch('uif.stokowski_cloudflare.native_workspace_preparation.os.fsync',
                       side_effect=OSError(errno.EIO, 'uncertain sync')):
                with self.assertRaises(OSError) as lost:
                    place_prepared_workspace(stages, second, 'UIF-3', source, other)
            self.assertEqual(lost.exception.errno, errno.EIO)
            self.assertFalse(second.exists())
            target = stages / 'UIF-3'
            self.assertEqual((target / 'file').read_bytes(), before)
            with self.assertRaises(FileNotFoundError):
                place_prepared_workspace(stages, second, 'UIF-3', source, other)
            from uif.stokowski_cloudflare.native_composition import observe_clean_assignment_workspace
            self.assertEqual(observe_clean_assignment_workspace(target, source, other['git_config_sha256']),
                             other | {'path': str(target)})




    def test_input_descriptor_closed_if_stream_setup_fails(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / 'input'
            path.write_bytes(b'input')
            opened = []
            original = os.open

            def track(*args, **kwargs):
                fd = original(*args, **kwargs)
                opened.append(fd)
                return fd

            with patch('uif.stokowski_cloudflare.native_workspace_preparation.os.open', side_effect=track):
                with patch('uif.stokowski_cloudflare.native_workspace_preparation.os.fdopen',
                           side_effect=OSError('stream setup failed')):
                    with self.assertRaisesRegex(OSError, 'stream setup failed'):
                        _copy_pinned_bundle(path, Path(parent) / 'copy', '0'*64)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError) as e:
                os.fstat(opened[0])
            self.assertEqual(e.exception.errno, errno.EBADF)
