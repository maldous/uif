#!/usr/bin/env python3
"""Read-only operator preflight for a reviewed, fixed service upgrade."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import sys
import tarfile
import time


@dataclass(frozen=True)
class Profile:
    unit: str
    reviewed_head: str | None


PROFILES = {
    'uif-idle': Profile('stokowski-foo.service', 'a22fbb270fb5e76e2498f049a3080229db05b568'),
    'offmenu-legacy': Profile('offmenu-stokowski-current.service', None),
}
PACKETS = Path('/var/lib/uif-stokowski/idle-upgrade')
STAGES = Path('/opt/uif-stokowski')
MAX_RECORD = 128_000
MAX_BUNDLE = 1024**3
IDLE_CONFIG = 'selector-v1/remote-authority.json'
PROFILE_NAME = 'uif-idle'
HOST = 'https://uif-shared-authority.uif-black-brook-8df3.workers.dev'
ACCESS_FILE = '/etc/uif-stokowski-native/access.json'
STOPPED = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
           'MainPID': '0', 'ControlPID': '0', 'Job': '', 'NeedDaemonReload': 'no'}


class Held(ValueError):
    """A reviewed, safe-to-report preflight refusal."""


def require(condition, reason):
    if not condition:
        raise Held(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(raw, fields):
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), 'duplicate record field')
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and value.keys() == set(fields), 'unexpected record fields')
    return value


def hash_pin(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def protected_parents(path):
    for directory in path.parents:
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'path parent is not protected')


def protected_open(path, maximum):
    protected_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= maximum,
                'packet file is not protected or exceeds its bound')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def read_record(root, name):
    with protected_open(root / name, MAX_RECORD) as stream:
        raw = stream.read(MAX_RECORD + 1)
    require(len(raw) <= MAX_RECORD, 'record exceeds its bound')
    return raw


def stopped_unit_sha(unit):
    base = ['/usr/bin/systemctl', '--no-pager']
    config = subprocess.check_output([*base, 'cat', unit], stderr=subprocess.DEVNULL, timeout=4)
    status = subprocess.check_output([*base, 'show', '--property=' + ','.join(STOPPED), unit],
                                     stderr=subprocess.DEVNULL, timeout=4, text=True)
    require(dict(line.split('=', 1) for line in status.splitlines()) == STOPPED,
            'unit is not stopped and idle in systemd')
    return digest(config)


def verify_bundle(stream, review, unit, stage):
    require(hashlib.file_digest(stream, 'sha256').hexdigest() == review['bundle_sha256'],
            'bundle pin mismatch')
    stream.seek(0)
    wanted = {'launch.py': review['launcher_sha256'], IDLE_CONFIG: review['config_sha256']}
    found = {}
    seen = set()
    with tarfile.open(fileobj=stream, mode='r:') as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            require(len(seen) < 50_000 and str(path) == item.name and not path.is_absolute()
                    and '..' not in path.parts and path.name != 'ACTIVATION_AUTHORIZED'
                    and item.name not in seen and (item.isfile() or item.isdir()),
                    'unexpected bundle member')
            seen.add(item.name)
            if item.name not in wanted:
                continue
            require(item.isfile() and 0 < item.size <= MAX_RECORD, 'invalid reviewed member')
            with archive.extractfile(item) as file:
                raw = file.read(MAX_RECORD + 1)
            require(digest(raw) == wanted[item.name], 'reviewed member pin mismatch')
            found[item.name] = raw
    require(found.keys() == wanted.keys(), 'missing reviewed member')
    cfg = record(found[IDLE_CONFIG], ('schema', 'unit', 'stage', 'origin', 'access_credential',
                                     'native_identity', 'coordinator_epoch', 'hold_reason'))
    require(cfg['schema'] == 'uif.stokowski-remote-idle/1'
            and (cfg['unit'], cfg['stage']) == (unit, stage)
            and cfg['origin'] == HOST and cfg['access_credential'] == ACCESS_FILE
            and cfg['hold_reason'] in ('free_route_unavailable', 'free_quota_unavailable')
            and type(cfg['coordinator_epoch']) is int and cfg['coordinator_epoch'] > 0,
            'bundle is not the fixed idle installation')
    identity = cfg['native_identity']
    require(type(identity) is dict and identity.keys() ==
            {'authority_id', 'session_id', 'deployment_sha256', 'policy_sha256'}
            and all(hash_pin(identity[k]) for k in ('deployment_sha256', 'policy_sha256'))
            and all(type(identity[k]) is str and re.fullmatch('[A-Za-z0-9_.-]{1,128}', identity[k])
                    for k in ('authority_id', 'session_id')), 'invalid native identity shape')


def preflight(profile_name, stage):
    profile = PROFILES[profile_name]
    require(profile.reviewed_head is not None, 'legacy OffMenu retains its existing owner-only upgrade route')
    require(re.fullmatch('uif1313-idle-[a-z0-9][a-z0-9.-]{0,60}', stage) is not None,
            'invalid UIF idle stage')
    root = PACKETS / stage
    target = STAGES / stage
    before = time.time()
    admission = record(read_record(root, 'admission.json'),
                       ('schema', 'profile', 'unit', 'stage', 'owner', 'owner_uid', 'review_sha256',
                        'confirmation_sha256', 'quiescence_sha256', 'issued_at', 'expires_at'))
    uid = os.getuid()
    require(admission['schema'] == 'uif.guarded-upgrade-admission/1'
            and (admission['profile'], admission['unit'], admission['stage']) ==
            (profile_name, profile.unit, stage)
            and 0 < uid == os.geteuid() and type(admission['owner_uid']) is int
            and admission['owner_uid'] == uid and admission['owner'] == pwd.getpwuid(uid).pw_name
            and type(admission['issued_at']) is int and type(admission['expires_at']) is int
            and admission['issued_at'] <= before < admission['expires_at'] <= admission['issued_at'] + 300,
            'operator, target or admission window mismatch')

    def pinned(name, pin):
        raw = read_record(root, name + '.json')
        require(hash_pin(pin) and digest(raw) == pin, name + ' witness pin mismatch')
        return raw

    review = record(pinned('review', admission['review_sha256']),
                    ('schema', 'unit', 'stage', 'head', 'author', 'reviewer', 'verdict',
                     'bundle_sha256', 'launcher_sha256', 'config_sha256', 'parked_unit_sha256'))
    require(review['schema'] == 'uif.guarded-upgrade-review/1' and review['verdict'] == 'accept'
            and (review['unit'], review['stage'], review['head']) == (profile.unit, stage, profile.reviewed_head)
            and all(type(review[k]) is str and re.fullmatch('[A-Za-z0-9_.-]{1,128}', review[k])
                    for k in ('author', 'reviewer'))
            and review['author'].casefold() != review['reviewer'].casefold()
            and all(hash_pin(review[k]) for k in ('bundle_sha256', 'launcher_sha256',
                                                 'config_sha256', 'parked_unit_sha256')),
            'exact independent acceptance is required')
    pinned('confirmation', admission['confirmation_sha256'])
    witness = record(pinned('quiescence', admission['quiescence_sha256']),
                     ('schema', 'unit', 'parked_unit_sha256', 'observed_at', 'inflight',
                      'children_gone', 'unresolved_operation_ids'))
    require(witness['schema'] == 'uif.guarded-upgrade-quiescence/1'
            and witness['unit'] == profile.unit and witness['parked_unit_sha256'] == review['parked_unit_sha256']
            and type(witness['observed_at']) is int and before - 60 < witness['observed_at'] <= before
            and type(witness['inflight']) is int and witness['inflight'] == 0
            and witness['children_gone'] is True and witness['unresolved_operation_ids'] == [],
            'fresh exact quiescence is required')
    require(stopped_unit_sha(profile.unit) == review['parked_unit_sha256'], 'parked unit pin mismatch')
    with protected_open(root / 'bundle.tar', MAX_BUNDLE) as stream:
        verify_bundle(stream, review, profile.unit, stage)
    require(stopped_unit_sha(profile.unit) == review['parked_unit_sha256'], 'parked unit changed')
    protected_parents(target)
    require(not target.exists() and not target.is_symlink(), 'target stage already exists')
    after = time.time()
    require(before <= after < min(admission['expires_at'], witness['observed_at'] + 60),
            'admission or quiescence expired during preflight')
    return {'schema': 'uif.guarded-upgrade-preflight/1', 'mode': 'read_only',
            'profile': profile_name, 'unit': profile.unit, 'stage': stage, 'head': review['head'],
            'bundle_sha256': review['bundle_sha256'], 'parked_unit_sha256': review['parked_unit_sha256'],
            'review_sha256': admission['review_sha256'], 'apply_supported': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=PROFILES)
    parser.add_argument('--stage', required=True)
    args = parser.parse_args()
    try:
        result = preflight(args.profile, args.stage)
    except Held as exc:
        print(f'Guarded upgrade preflight held: {exc}. No changes made.', file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        print('Guarded upgrade preflight refused; no changes made.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
