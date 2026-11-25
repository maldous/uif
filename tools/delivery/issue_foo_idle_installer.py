#!/usr/bin/env python3
"""Issue only the fixed Foo idle installer's original admission pair."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace


LOADER = Path('/var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py')
SIGNERS = Path('/etc/uif-stokowski/activation_allowed_signers')
SSH_KEYGEN = '/usr/bin/ssh-keygen'
NAMESPACE = 'uif-foo-stokowski-idle-installer'
REQUEST = 'installer-request.json'
REVIEW_FIELDS = ('schema', 'unit', 'stage', 'head', 'author', 'reviewer', 'verdict',
                 'bundle_sha256', 'launcher_sha256', 'config_sha256', 'parked_unit_sha256')
WITNESS_FIELDS = ('schema', 'unit', 'parked_unit_sha256', 'observed_at',
                  'inflight', 'children_gone', 'unresolved_operation_ids')


def installed_loader():
    if os.getresuid() != (0, 0, 0):
        raise ValueError('protected Foo transport required')
    for parent in LOADER.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid or info.st_mode & 0o022:
            raise ValueError('installer loader directory is not protected')
    with os.fdopen(os.open(LOADER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as loader:
        info = os.fstat(loader.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError('installer loader is not protected')
        raw = loader.read(128_001)
    if not 0 < len(raw) <= 128_000:
        raise ValueError('installer loader exceeds its bound')
    core = SimpleNamespace(**runpy.run_path(str(LOADER)))
    pin = hashlib.sha256(raw).hexdigest()
    core.require(core.bytes_pin(LOADER) == pin, 'installer loader changed during read')
    return core, pin


def enrolled_operator(core, stream):
    raw = stream.read(core.MAX_RECORD + 1)
    lines = raw.strip().splitlines()
    core.require(len(raw) <= core.MAX_RECORD and len(lines) == 1, 'one enrolled operator is required')
    fields = lines[0].split(maxsplit=3)
    core.require(len(fields) >= 3 and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_.-]{0,63}', fields[0])
                 and fields[1].startswith((b'ssh-', b'ecdsa-', b'sk-')),
                 'an exact principal and existing public key are required')
    principal = fields[0].decode('ascii')
    account = pwd.getpwnam(principal)
    core.require(account.pw_name == principal and account.pw_uid > 0, 'enrolled Foo operator is not valid')
    stream.seek(0)
    return principal, account.pw_uid, core.digest(raw)


def snapshot(core, loader_pin, stream, operation, owner, uid, signers_pin):
    profile, stage = core.PROFILE_NAME, core.FIXED_STAGE
    target = core.PROFILES[profile]
    packet = core.PACKETS / stage
    review_raw = core.read_record(packet, 'review.json')
    review = core.record(review_raw, REVIEW_FIELDS)
    core.require((review['schema'], review['verdict'], review['unit'], review['stage'], review['head']) ==
                 ('uif.guarded-upgrade-review/1', 'accept', target.unit, stage, target.reviewed_head)
                 and all(type(review[key]) is str and re.fullmatch('[A-Za-z0-9_.-]{1,128}', review[key])
                         for key in ('author', 'reviewer'))
                 and review['author'].casefold() != review['reviewer'].casefold()
                 and all(core.hash_pin(review[key]) for key in ('bundle_sha256', 'launcher_sha256',
                                                               'config_sha256', 'parked_unit_sha256')),
                 'exact author-distinct installer review is required')
    unit_pin = core.stopped_unit_sha(target.unit)
    core.require(unit_pin == review['parked_unit_sha256'] and unit_pin != core.BOOTSTRAP_PRIOR
                 and core.bytes_pin(core.DROPIN) == core.digest(core.selector_dropin()),
                 'reviewed stopped idle bootstrap is required')
    command = core.selector_dropin().decode().split('ExecStart=\nExecStart=', 1)[1].strip().removeprefix('!')
    start = subprocess.check_output([core.SYSTEMCTL, '--system', '--no-pager', 'show',
                                     '--property=ExecStart', target.unit],
                                    env={'LC_ALL': 'C'}, text=True, timeout=4)
    core.require(f'argv[]={command} ;' in start and start.count('argv[]=') == 1,
                 'effective start does not match the reviewed bootstrap')
    witness_raw = core.read_record(packet, 'quiescence.json')
    witness = core.record(witness_raw, WITNESS_FIELDS)
    observed = witness['observed_at']
    now = time.time()
    core.require((witness['schema'], witness['unit'], witness['parked_unit_sha256']) ==
                 ('uif.guarded-upgrade-quiescence/1', target.unit, unit_pin)
                 and type(observed) is int and observed <= now < observed + 60
                 and type(witness['inflight']) is int and witness['inflight'] == 0
                 and witness['children_gone'] is True and witness['unresolved_operation_ids'] == [],
                 'fresh independently observed quiescence is required')
    custody = core.lock_identity(os.fstat(stream.fileno()))
    parked = core.plan_digest({'mode': 'parked', 'stage': core.PARKED_STAGE})
    idle = core.digest(core.sealed_selection(review))
    core.require(core.bytes_pin(core.SELECTOR) == parked
                 and core.no_marker(core.PARKED_STAGE) and core.no_marker(stage)
                 and core.lock_identity(core.LOCK.lstat()) == custody
                 and core.bytes_pin(LOADER) == loader_pin
                 and core.stopped_unit_sha(target.unit) == unit_pin,
                 'parked selection, loader or original host custody changed')
    return {
        'schema': 'uif.foo-idle-installer-request/1', 'action': 'issue_installer_admission',
        'operation_id': operation, 'profile': profile, 'unit': target.unit, 'stage': stage,
        'owner': owner, 'owner_uid': uid, 'signers_sha256': signers_pin,
        'installer_review_sha256': core.digest(review_raw),
        'quiescence_sha256': core.digest(witness_raw), 'bundle_sha256': review['bundle_sha256'],
        'bootstrapped_unit_sha256': unit_pin, 'parked_unit_preimage_sha256': core.BOOTSTRAP_PRIOR,
        'loader_sha256': loader_pin, 'lock_custody_sha256': core.plan_digest(custody),
        'parked_selection_sha256': parked, 'idle_selection_sha256': idle,
    }, observed + 60


def original(core, packet, name):
    try:
        return core.read_record(packet, name)
    except FileNotFoundError:
        return None


def ceremony(core, loader_pin, operation, *, issue):
    core.require(core.hash_pin(operation), 'an exact 64-hex installer operation is required')
    packet = core.PACKETS / core.FIXED_STAGE
    with core.selector_lock(shared=False) as stream, core.protected_open(SIGNERS, core.MAX_RECORD) as signers:
        owner, uid, signers_pin = enrolled_operator(core, signers)
        expected, deadline = snapshot(core, loader_pin, stream, operation, owner, uid, signers_pin)
        if not issue:
            return {**expected, 'issued_at': int(time.time()), 'expires_at': deadline}
        raw = core.read_record(packet, REQUEST)
        request = core.record(raw, (*expected, 'issued_at', 'expires_at'))
        issued, expires = request['issued_at'], request['expires_at']
        core.require(all(request[key] == value and type(request[key]) is type(value)
                         for key, value in expected.items())
                     and type(issued) is int and type(expires) is int
                     and issued <= time.time() < expires <= min(deadline, issued + 300),
                     'signed request does not match the current installer preflight')
        with core.protected_open(packet / (REQUEST + '.sig'), core.MAX_RECORD) as signature:
            fds = (signers.fileno(), signature.fileno())
            verified = subprocess.run(
                [SSH_KEYGEN, '-Y', 'verify', '-f', f'/proc/self/fd/{fds[0]}',
                 '-I', owner, '-n', NAMESPACE, '-s', f'/proc/self/fd/{fds[1]}'],
                input=raw, env={'LC_ALL': 'C'}, pass_fds=fds, timeout=4, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        core.require(verified.returncode == 0, 'installer owner signature refused')
        core.require(snapshot(core, loader_pin, stream, operation, owner, uid, signers_pin) ==
                     (expected, deadline) and core.bytes_pin(SIGNERS) == signers_pin
                     and time.time() < expires, 'installer authorization or quiescence changed')
        admission = core.encode({
            'schema': 'uif.guarded-upgrade-admission/1', 'profile': expected['profile'],
            'unit': expected['unit'], 'stage': expected['stage'], 'owner': owner, 'owner_uid': uid,
            'review_sha256': expected['installer_review_sha256'],
            'quiescence_sha256': expected['quiescence_sha256'], 'confirmation_sha256': core.digest(raw),
            'issued_at': issued, 'expires_at': expires,
        })
        prior_confirmation = original(core, packet, 'confirmation.json')
        prior_admission = original(core, packet, 'admission.json')
        core.require((prior_confirmation is None or prior_confirmation == raw)
                     and (prior_admission is None or (prior_confirmation == raw and prior_admission == admission)),
                     'original installer pair is different or incomplete; reconcile without replacement')
        if prior_confirmation is None:
            core.publish(packet / 'confirmation.json', raw, deadline=expires)
        if prior_admission is None:
            core.publish(packet / 'admission.json', admission, deadline=expires)
        core.require(original(core, packet, 'confirmation.json') == raw
                     and original(core, packet, 'admission.json') == admission,
                     'published installer pair requires reconciliation')
        return {'operation_id': operation, 'confirmation_sha256': core.digest(raw),
                'admission_sha256': core.digest(admission), 'expires_at': expires}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', required=True, choices=('prepare', 'issue'))
    parser.add_argument('--operation-id', required=True)
    args = parser.parse_args()
    try:
        core, pin = installed_loader()
        result = ceremony(core, pin, args.operation_id, issue=args.action == 'issue')
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        print('Installer ceremony refused. Check owner enrollment, exact inputs and original receipts.',
              file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
