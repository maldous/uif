"""Materialize exact retained normal-runtime archive; never select or start it."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import os
import re
import shutil
import stat
import tarfile
from tempfile import mkdtemp

import guarded_service_upgrade as guard
from install_guarded_idle_bootstrap import _publish_once, _rename_noreplace, _sync_parent

MAX_ARCHIVE = 192 * 1024 * 1024
MAX_FILES = 10000
RECEIPT = '.uif-normal-stage-original.json'
SITE = 'venv/lib/python3.12/site-packages/uif/stokowski_cloudflare/'
REQUIRED = {'launch.py', 'venv/bin/python3.12', 'selector-v1/remote-authority.json',
            'selector-v1/workers-ai-parked.json',
            *(SITE + module + '.py' for module in ('launcher', 'native_composition', 'remote_authority',
                                                   'remote_source', 'native', 'turn'))}


@dataclass(frozen=True)
class NormalRuntimeStageApproval:
    """Inputs from independently accepted fixed owner preparation, not caller parameters."""
    operation_id: str
    stage_leaf: str
    archive_sha256: str
    members_sha256: str
    prior_dropin_sha256: str
    prior_selection_sha256: str


def _members(archive, approved):
    guard.require(type(archive) is bytes and 0 < len(archive) <= MAX_ARCHIVE
                  and guard.digest(archive) == approved.archive_sha256, 'normal stage archive differs')
    values = {}
    total = 0
    with tarfile.open(fileobj=BytesIO(archive), mode='r:') as source:
        for member in source:
            name = member.name
            leaf = PurePosixPath(name)
            guard.require(0 < len(name) <= 512 and all(re.fullmatch(r'[A-Za-z0-9_@+.-]+', p)
                          and p not in ('.', '..') for p in leaf.parts)
                          and not leaf.is_absolute() and leaf.as_posix() == name
                          and name != RECEIPT and name not in values and len(values) < MAX_FILES
                          and member.isreg() and not member.issparse()
                          and member.mode in (0o444, 0o555) and 0 < member.size <= MAX_ARCHIVE,
                          'unexpected normal stage archive member')
            total += member.size
            guard.require(total <= MAX_ARCHIVE, 'oversized normal stage membership')
            with source.extractfile(member) as stream:
                raw = stream.read(member.size + 1)
            guard.require(len(raw) == member.size, 'truncated normal stage member')
            values[name] = (raw, member.mode)
    for name in values:
        guard.require(not any(str(parent) in values or str(parent) == RECEIPT
                              for parent in PurePosixPath(name).parents),
                      'normal stage member conflicts with directory')
    projected = {k: {'bytes': len(raw), 'mode': mode, 'sha256': sha256(raw).hexdigest()}
                 for k, (raw, mode) in values.items()}
    guard.require(REQUIRED <= set(values) and values['venv/bin/python3.12'][1] == 0o555
                  and guard.plan_digest(projected) == approved.members_sha256,
                  'incomplete or different independently accepted normal stage')
    return values


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _verify_directory(root, expected):
    """Verify exact protected closed stage without altering any published member."""
    guard.protected_parents(root)
    uid = os.geteuid()
    root_info = root.lstat()
    guard.require(stat.S_ISDIR(root_info.st_mode) and root_info.st_uid == uid
                  and stat.S_IMODE(root_info.st_mode) in (0o700, 0o755), 'normal stage directory custody')
    names, directories = set(), set()
    required_dirs = {str(d) for k in expected for d in PurePosixPath(k).parents if str(d) != '.'}
    for folder, subdirs, files in os.walk(root, followlinks=False):
        base = Path(folder)
        for name in subdirs:
            child = base / name
            relative = child.relative_to(root).as_posix()
            info = child.lstat()
            guard.require(relative in required_dirs and stat.S_ISDIR(info.st_mode)
                          and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o755,
                          'normal stage directory unexpected or unprotected')
            directories.add(relative)
        for name in files:
            child = base / name
            relative = child.relative_to(root).as_posix()
            guard.require(relative in expected, 'normal stage member unexpected')
            raw, mode = expected[relative]
            info = child.lstat()
            guard.require(stat.S_ISREG(info.st_mode) and info.st_uid == uid
                          and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1
                          and info.st_size == len(raw)
                          and guard.bytes_pin(child, len(raw)) == guard.digest(raw)
                          and _identity(child.lstat()) == _identity(info), 'normal stage member differs from same original')
            names.add(relative)
    guard.require(names == set(expected) and directories == required_dirs, 'normal stage closure incomplete')


def stage_normal_runtime(approved, archive):
    """Existing protected maintenance owner only. No CLI or writable selector."""
    guard.require(type(approved) is NormalRuntimeStageApproval and guard._private_webhook_root()
                  and type(approved.stage_leaf) is str
                  and re.fullmatch(r'uif1313-normal-[a-z0-9][a-z0-9.-]{1,65}', approved.stage_leaf)
                  and all(guard.hash_pin(v) for k, v in vars(approved).items() if k != 'stage_leaf'),
                  'expected protected exact normal stage approval')
    expected = _members(archive, approved)
    receipt = guard.encode({'schema': 'uif.foo-normal-runtime-staged/1', **vars(approved)})
    expected[RECEIPT] = (receipt, 0o400)
    stage = guard.STAGES / approved.stage_leaf
    with guard.selector_lock(shared=False):
        def preimage():
            guard.require(guard.bytes_pin(guard.DROPIN) == approved.prior_dropin_sha256
                          and guard.bytes_pin(guard.SELECTOR) == approved.prior_selection_sha256,
                          'selected runtime changed before normal stage materialization')
        preimage()
        if not stage.exists() and not stage.is_symlink():
            guard.protected_parents(stage)
            temporary = Path(mkdtemp(prefix='.normal-stage-', dir=stage.parent))
            try:
                for name, (raw, mode) in sorted(expected.items()):
                    leaf = temporary / name
                    leaf.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    _publish_once(leaf, mode, lambda out, raw=raw: out.write(raw))
                # Seal and sync every metadata level before one final publish.
                for folder, _, _ in os.walk(temporary, topdown=False):
                    path = Path(folder)
                    path.chmod(0o755)
                    _sync_parent(path / '.metadata-sync')
                _verify_directory(temporary, expected)
                preimage()
                try:
                    _rename_noreplace(temporary, stage)
                except OSError:
                    if not stage.exists() and not stage.is_symlink():
                        raise
                    _verify_directory(stage, expected)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        _verify_directory(stage, expected)
        # The prior operation may have stopped between rename and parent fsync.
        # Exact same-original readback always durably commits the directory entry.
        _sync_parent(stage)
        preimage()
        return {'status': 'normal_stage_materialized_not_selected',
                'receipt_sha256': guard.digest(receipt), 'release_sha256': approved.members_sha256}
