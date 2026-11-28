"""Fixed root-custodied native inventory comparison. Read-only; no CLI, no install."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = "uif.native-core-detached-file-map/1"
CORE = "native-core-20261113-a1"
CORE_PATH = Path("/opt/uif-stokowski") / CORE
ROLES = {"python": "python", "web_deps": "web-deps"}

NAME = re.compile(r"[A-Za-z0-9_@+.-]{1,128}")
MAX_ENTRIES = 5000
MAX_DEPTH = 30
MAX_BYTES = 512 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def hash_bytes(raw):
    return sha256(raw).hexdigest()


def identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def capture_role(dirfd, *, uid, identities=None):
    """Hash only ordinary stable, bounded files from an already owned descriptor."""
    out = {}
    entries = total = 0

    def walk(parent, prefix, depth):
        nonlocal entries, total
        start = os.fstat(parent)
        if identities is not None:
            identities[prefix] = identity(start)
        need(stat.S_ISDIR(start.st_mode) and start.st_uid == uid
             and not start.st_mode & 0o022 and depth <= MAX_DEPTH, "native_inventory_directory")
        for name in sorted(os.listdir(parent)):
            entries += 1
            need(entries <= MAX_ENTRIES and name not in (".", "..") and NAME.fullmatch(name),
                 "native_inventory_name")
            key = "/".join((*prefix, name))
            need(len(key) <= 1024, "native_inventory_path")
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(linked.st_mode):
                fd = os.open(name, OPEN_DIR, dir_fd=parent)
                try:
                    need(identity(os.fstat(fd)) == identity(linked), "native_inventory_directory_changed")
                    walk(fd, (*prefix, name), depth + 1)
                finally:
                    os.close(fd)
                need(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(linked),
                     "native_inventory_directory_changed")
                continue
            need(stat.S_ISREG(linked.st_mode) and linked.st_nlink == 1 and linked.st_uid == uid
                 and not linked.st_mode & 0o022 and 0 <= linked.st_size <= MAX_FILE,
                 "native_inventory_member")
            total += linked.st_size
            need(total <= MAX_BYTES, "native_inventory_size")
            fd = os.open(name, OPEN_FILE, dir_fd=parent)
            try:
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    need(identity(os.fstat(fd)) == identity(linked), "native_inventory_member_changed")
                    remaining = linked.st_size
                    digest = sha256()
                    while remaining:
                        data = stream.read(min(remaining, 1024 * 1024))
                        need(bool(data), "native_inventory_member_changed")
                        remaining -= len(data)
                        digest.update(data)
                    need(not stream.read(1) and identity(os.fstat(fd)) == identity(linked),
                         "native_inventory_member_changed")
            finally:
                os.close(fd)
            need(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(linked),
                 "native_inventory_member_changed")
            if identities is not None:
                identities[(*prefix, name)] = identity(linked)
            out[key] = digest.hexdigest()
        need(identity(os.fstat(parent)) == identity(start), "native_inventory_directory_changed")

    walk(dirfd, (), 0)
    return out




def _pairs(items):
    parsed = {}
    for key, value in items:
        need(key not in parsed, 'duplicate_manifest_path')
        parsed[key] = value
    return parsed


def expected_manifest(raw, pin):
    need(type(raw) is bytes and 0 < len(raw) <= 128000
         and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
         and hash_bytes(raw) == pin, 'independent_expected_manifest_pin_required')
    value = json.loads(raw, object_pairs_hook=_pairs)
    need(type(value) is dict and set(value) == {'schema', 'core', *ROLES}
         and (value['schema'], value['core']) == (SCHEMA, CORE)
         and encode(value) == raw, 'expected_native_manifest_format')
    for role in ROLES:
        validate_role_map(value[role])
    return value


def validate_role_map(members):
    need(type(members) is dict and 0 < len(members) <= MAX_ENTRIES,
         'expected_native_role_format')
    for key, digest in members.items():
        parts = key.split('/')
        need(len(key) <= 1024 and len(parts) <= MAX_DEPTH
             and all(p not in ('.', '..') and NAME.fullmatch(p) for p in parts)
             and type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest)
             and not any('/'.join(parts[:i]) in members for i in range(1, len(parts))),
             'expected_native_member_format')


@dataclass(frozen=True)
class HistoricalRoleReceipt:
    """Exact prior projected metadata; this is not an acceptance type."""
    role: str
    files: int
    sorted_pairs_sha256: str


def compare_to_historical_role(role, candidate_raw, *, prior):
    """Compare detached files against prior metadata without opening any shelf."""
    need(type(prior) is HistoricalRoleReceipt and role in ROLES and prior.role == role
         and type(prior.files) is int and 0 < prior.files <= MAX_ENTRIES
         and type(prior.sorted_pairs_sha256) is str
         and re.fullmatch('[0-9a-f]{64}', prior.sorted_pairs_sha256),
         'invalid_historical_role_receipt')
    need(type(candidate_raw) is bytes and 0 < len(candidate_raw) <= 128000,
         'invalid_detached_role_map')
    candidate = json.loads(candidate_raw, object_pairs_hook=_pairs)
    validate_role_map(candidate)
    need(candidate_raw == encode(candidate), 'invalid_detached_role_serialization')
    compact = json.dumps(sorted(candidate.items()), separators=(',', ':'), ensure_ascii=True).encode()
    matches = len(candidate) == prior.files and hash_bytes(compact) == prior.sorted_pairs_sha256
    return {'role': role, 'historical_count': prior.files, 'candidate_count': len(candidate),
            'status': 'historical_receipt_match' if matches else 'historical_receipt_differs',
            'production_acceptance': 'not_evaluated'}


def _revalidate_role(dirfd, identities, hashes, *, uid):
    """Recheck members through the retained descriptor, including their bytes."""
    current_identities = {}
    current_hashes = capture_role(dirfd, uid=uid, identities=current_identities)
    # An in-place, same-size edit can share the original filesystem timestamps.
    need(current_identities == identities and current_hashes == hashes,
         'installed_native_inventory_changed')


def _capture_fixed():
    # Keep ancestor FDs so the final readback detects exchanged path entries.
    chain = []
    role_handles = []
    fd = os.open('/', OPEN_DIR)
    try:
        for name in CORE_PATH.parts[1:]:
            linked = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(stat.S_ISDIR(linked.st_mode) and linked.st_uid == 0 and not linked.st_mode & 0o022,
                 'installed_native_root_custody')
            child = os.open(name, OPEN_DIR, dir_fd=fd)
            chain.append((fd, name, identity(linked)))
            fd = child
            need(identity(os.fstat(fd)) == identity(linked), 'installed_native_root_changed')
        out = {}
        for role, leaf in ROLES.items():
            linked = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            child = os.open(leaf, OPEN_DIR, dir_fd=fd)
            identities = {}
            role_handles.append((role, leaf, child, identity(linked), identities))
            need(identity(os.fstat(child)) == identity(linked), 'installed_native_role_changed')
            out[role] = capture_role(child, uid=0, identities=identities)
        for role, leaf, child, prior, identities in role_handles:
            _revalidate_role(child, identities, out[role], uid=0)
            need(identity(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == prior,
                 'installed_native_role_changed')
        for parent, name, prior in reversed(chain):
            need(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == prior,
                 'installed_native_root_changed')
        return out
    finally:
        for _, _, child, _, _ in role_handles:
            os.close(child)
        os.close(fd)
        for parent, _, _ in reversed(chain):
            os.close(parent)


def compare_in_place(expected_raw, *, independently_retained_sha256):
    """Trusted caller supplies existing acceptance custody, not a learned pin.

    This returns exact member identity agreement only. It cannot authenticate
    the independent manifest's issuer, approve a release or affect startup.
    The scan describes one bounded sequential observation, not an atomic
    snapshot. This helper neither acquires nor attests install writer exclusion.
    """
    if expected_raw is None or independently_retained_sha256 is None:
        return {'status': 'held_expected_manifest_absent', 'identity_only': True,
                'production_acceptance': 'not_evaluated'}
    expected = expected_manifest(expected_raw, independently_retained_sha256)
    try:
        observed = _capture_fixed()
    except (OSError, ValueError):
        return {'status': 'held_in_place_inventory_unavailable', 'identity_only': True,
                'production_acceptance': 'not_evaluated'}
    summary = {}
    for role in ROLES:
        want, got = expected[role], observed[role]
        common = want.keys() & got.keys()
        summary[role] = dict(expected=len(want), observed=len(got),
                             missing=len(want.keys() - got.keys()),
                             unexpected=len(got.keys() - want.keys()),
                             drift=sum(want[k] != got[k] for k in common))
    exact = not any(v[k] for v in summary.values() for k in ('missing', 'unexpected', 'drift'))
    return {'status': 'exact_member_identity_match' if exact else 'held_member_identity_mismatch',
            'roles': summary, 'observation': 'bounded_sequential_inventory',
            'identity_only': True, 'production_acceptance': 'not_evaluated'}
