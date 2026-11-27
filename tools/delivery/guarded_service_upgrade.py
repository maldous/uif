#!/usr/bin/env python3
"""Read-only operator preflight for a reviewed, fixed service upgrade."""

import argparse
import ctypes
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import fcntl
import grp
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
import tempfile
import time
from uuid import UUID


@dataclass(frozen=True)
class Profile:
    unit: str
    reviewed_head: str | None


@dataclass(frozen=True)
class ApplyAdmission:
    """Exact transaction pins supplied by the protected host admission adapter."""

    profile: str
    unit: str
    stage: str
    operation_id: str
    preflight_sha256: str
    lock_custody_sha256: str
    prior_selection_sha256: str
    idle_selection_sha256: str
    issued_at: int
    expires_at: int


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
FIXED_STAGE = 'uif1313-idle-a22fbb2'
PARKED_STAGE = 'uif1313-guarded-1f45bd56'
ROOT = PACKETS
TOOL_PATH = ROOT / 'guarded_service_upgrade.py'
SELECTOR = ROOT / 'selection.json'
LOCK = ROOT / 'selector.lock'
RECEIPTS = ROOT / 'receipts'
DROPIN = Path('/etc/systemd/system/stokowski-foo.service.d/60-uif1313-idle-selector.conf')
SYSTEMCTL = '/usr/bin/systemctl'
JOURNALCTL = '/usr/bin/journalctl'
SSH_KEYGEN = '/usr/bin/ssh-keygen'
ACTIVATION_SIGNERS = Path('/etc/uif-stokowski/activation_allowed_signers')
ACTIVATION_NAMESPACE = 'uif-foo-stokowski-idle-activation'
APPLY_NAMESPACE = 'uif-foo-stokowski-idle-apply'
INTERPRETER = 'venv/bin/python3.12'
BOOTSTRAP_PRIOR = '96c56b11f328c4d97df40e5d5ab705d4c85c6cd31c96cbacaca699ca05873502'
MARKER = 'ACTIVATION_AUTHORIZED'
PRELUDE_CAPABILITIES = {'cap_setgid': 6, 'cap_setuid': 7, 'cap_setpcap': 8}
PRELUDE_MASK = sum(1 << bit for bit in PRELUDE_CAPABILITIES.values())
PR_CAPBSET_DROP = 24
ADMISSION_FIELDS = ('schema', 'profile', 'unit', 'stage', 'owner', 'owner_uid', 'review_sha256',
                    'confirmation_sha256', 'quiescence_sha256', 'issued_at', 'expires_at')
INSTALLER_FIELDS = ('schema', 'action', 'operation_id', 'profile', 'unit', 'stage', 'owner',
                    'owner_uid', 'signers_sha256', 'installer_review_sha256', 'quiescence_sha256',
                    'bundle_sha256', 'bootstrapped_unit_sha256', 'parked_unit_preimage_sha256',
                    'loader_sha256', 'lock_custody_sha256', 'parked_selection_sha256',
                    'idle_selection_sha256', 'issued_at', 'expires_at')


def selector_dropin():
    marker = STAGES / FIXED_STAGE / MARKER
    return ('[Unit]\nConditionPathExists=\n'
            f'ConditionPathExists={marker}\n\n'
            '[Service]\nRestart=no\nNoNewPrivileges=yes\n'
            'CapabilityBoundingSet=\nCapabilityBoundingSet=CAP_SETGID CAP_SETUID CAP_SETPCAP\n'
            'ExecStart=\n'
            f'ExecStart=!/usr/bin/python3 -I -S -B {TOOL_PATH} --profile uif-idle'
            f' --stage {FIXED_STAGE} --action serve\n').encode()


class Held(ValueError):
    """A reviewed, safe-to-report preflight refusal."""


def require(condition, reason):
    if not condition:
        raise Held(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plan_digest(plan):
    return digest(encode(plan))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


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
    base = [SYSTEMCTL, '--no-pager']
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


def pinned_record(root, name, pin):
    raw = read_record(root, name + '.json')
    require(hash_pin(pin) and digest(raw) == pin, name + ' witness pin mismatch')
    return raw


def verify_installer_operation(packet, operator, operation):
    original = record(pinned_record(packet, 'confirmation', operator['confirmation_sha256']),
                      INSTALLER_FIELDS)
    require(hash_pin(operation) and (original['schema'], original['action'], original['operation_id']) ==
            ('uif.foo-idle-installer-request/1', 'issue_installer_admission', operation),
            'original installer operation does not match apply or start')
    fields = ('profile', 'unit', 'stage', 'owner', 'owner_uid', 'quiescence_sha256',
              'issued_at', 'expires_at')
    require(all(original[key] == operator[key] and type(original[key]) is type(operator[key])
                for key in fields)
            and original['installer_review_sha256'] == operator['review_sha256'],
            'original installer confirmation does not match its admission')


def accepted_review(root, profile, stage, pin):
    review = record(pinned_record(root, 'review', pin),
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
    return review


def preflight(profile_name, stage, *, sealed_stage=False, approved_operator_uid=None):
    return _preflight(profile_name, stage, sealed_stage=sealed_stage,
                      approved_operator_uid=approved_operator_uid)


def _preflight(profile_name, stage, *, sealed_stage, approved_operator_uid,
               webhook_admission=None):
    profile = PROFILES[profile_name]
    require(profile.reviewed_head is not None, 'legacy OffMenu retains its existing owner-only upgrade route')
    require(re.fullmatch('uif1313-idle-[a-z0-9][a-z0-9.-]{0,60}', stage) is not None,
            'invalid UIF idle stage')
    root = PACKETS / stage
    target = STAGES / stage
    before = time.time()
    admission = (record(read_record(root, 'admission.json'), ADMISSION_FIELDS)
                 if webhook_admission is None else webhook_admission)
    uid = os.getuid() if approved_operator_uid is None else approved_operator_uid
    caller_ok = (os.getuid() == os.geteuid() == uid if approved_operator_uid is None
                 else os.getuid() == os.geteuid() == 0)
    require(admission['schema'] == ('uif.guarded-upgrade-admission/1' if webhook_admission is None
                                     else WEBHOOK_APPLY_AUTH_SCHEMA)
            and (admission['profile'], admission['unit'], admission['stage']) ==
            (profile_name, profile.unit, stage)
            and caller_ok and type(uid) is int and uid > 0 and type(admission['owner_uid']) is int
            and admission['owner_uid'] == uid and admission['owner'] == pwd.getpwuid(uid).pw_name
            and type(admission['issued_at']) is int and type(admission['expires_at']) is int
            and admission['issued_at'] <= before < admission['expires_at'] <= admission['issued_at'] + 300,
            'operator, target or admission window mismatch')

    review = accepted_review(root, profile, stage, admission['review_sha256'])
    if webhook_admission is None:
        pinned_record(root, 'confirmation', admission['confirmation_sha256'])
    witness = record(pinned_record(root, 'quiescence', admission['quiescence_sha256']),
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
    if sealed_stage:
        info = target.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'sealed stage is not protected')
    else:
        require(not target.exists() and not target.is_symlink(), 'target stage already exists')
    after = time.time()
    require(before <= after < min(admission['expires_at'], witness['observed_at'] + 60),
            'admission or quiescence expired during preflight')
    return {'schema': 'uif.guarded-upgrade-preflight/1', 'mode': 'read_only',
            'profile': profile_name, 'unit': profile.unit, 'stage': stage, 'head': review['head'],
            'bundle_sha256': review['bundle_sha256'], 'parked_unit_sha256': review['parked_unit_sha256'],
            'launcher_sha256': review['launcher_sha256'], 'config_sha256': review['config_sha256'],
            'review_sha256': admission['review_sha256'], 'confirmation_sha256': admission['confirmation_sha256'],
            'quiescence_sha256': admission['quiescence_sha256'],
            'valid_until': min(admission['expires_at'], witness['observed_at'] + 60), 'apply_supported': False}


def apply_records(admission):
    intent = {'schema': 'uif.guarded-upgrade-intent/1', 'admission': asdict(admission)}
    selected = {'schema': 'uif.guarded-upgrade-selected/1', 'operation_id': admission.operation_id,
                'intent_sha256': plan_digest(intent), 'selection_sha256': admission.idle_selection_sha256}
    return intent, selected


def apply(profile_name, stage, *, admission, host):
    """Select an exact idle stage through a qualified host adapter. No default host or CLI.

    The adapter authenticates the external admission, holds all qualified
    selector writers, and implements durable create-only intent and completion.
    It exposes only the fixed unit's idle selection, never a start or marker.
    """
    profile = PROFILES[profile_name]
    require(profile.reviewed_head is not None, 'legacy OffMenu retains its existing owner-only upgrade route')
    require(re.fullmatch('uif1313-idle-[a-z0-9][a-z0-9.-]{0,60}', stage) is not None, 'invalid UIF idle stage')
    require(type(admission) is ApplyAdmission
            and (admission.profile, admission.unit, admission.stage) == (profile_name, profile.unit, stage)
            and all(hash_pin(value) for value in (admission.operation_id, admission.preflight_sha256,
                                                 admission.lock_custody_sha256, admission.prior_selection_sha256,
                                                 admission.idle_selection_sha256))
            and admission.prior_selection_sha256 != admission.idle_selection_sha256
            and type(admission.issued_at) is int and type(admission.expires_at) is int
            and 0 < admission.expires_at - admission.issued_at <= 300,
            'invalid apply admission')
    intent, result = apply_records(admission)
    with host.lock(profile.unit, admission.lock_custody_sha256) as locked:
        require(locked.verify_admission(admission) is True, 'trusted host admission unavailable')
        require(locked.verify_custody() is True, 'qualified selector lock unavailable')
        previous_intent, completion, prior_fence = locked.receipts(
            admission.operation_id, admission.prior_selection_sha256,
        )
        if previous_intent is not None or completion is not None or prior_fence is not None:
            require(previous_intent == intent and completion == result and prior_fence == admission.operation_id
                    and locked.selection_sha256() == admission.idle_selection_sha256,
                    'original upgrade effects require reconciliation')
            return completion

        def qualify():
            require(locked.verify_custody() is True, 'qualified selector lock unavailable')
            plan = locked.preflight(profile_name, stage)
            require(isinstance(plan, dict) and plan_digest(plan) == admission.preflight_sha256
                    and (plan.get('profile'), plan.get('unit'), plan.get('stage'), plan.get('head')) ==
                    (profile_name, profile.unit, stage, profile.reviewed_head)
                    and plan.get('schema') == 'uif.guarded-upgrade-preflight/1'
                    and plan.get('mode') == 'read_only',
                    'locked preflight differs from exact admission')
            require(locked.selection_sha256() == admission.prior_selection_sha256,
                    'parked selection changed')
            require(locked.verify_idle_stage(plan, admission.idle_selection_sha256) is True,
                    'sealed idle selection differs from exact admission')
            require(locked.verify_custody() is True, 'qualified selector lock unavailable')
            require(type(plan.get('valid_until')) is int
                    and admission.issued_at <= time.time() < min(plan['valid_until'], admission.expires_at),
                    'admission or quiescence expired under lock')
            return plan['valid_until']

        qualify()
        locked.create_intent(admission.operation_id, admission.prior_selection_sha256, intent)
        deadline = min(qualify(), admission.expires_at)
        locked.select_idle(stage, admission.prior_selection_sha256, admission.idle_selection_sha256, deadline)
        require(locked.verify_custody() is True and locked.selection_sha256() == admission.idle_selection_sha256,
                'selection readback requires reconciliation')
        require(time.time() < deadline,
                'selection occurred; completion requires reconciliation')
        locked.create_completion(admission.operation_id, result)
        return result


@contextmanager
def selector_lock(*, shared):
    with protected_open(LOCK, MAX_RECORD) as stream:
        require(stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o644, 'unexpected selector lock mode')
        fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield stream


def lock_identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink]


def publish(path, data, *, replace=False, deadline=None, mode=None):
    """Publish complete bytes on one filesystem and preserve existing receipts."""
    protected_parents(path)
    fd, temporary = tempfile.mkstemp(prefix='.uif-idle-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode if mode is not None else (0o644 if replace else 0o600))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if deadline is not None:
            require(time.time() < deadline, 'selection expired before atomic replacement')
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bytes_pin(path, maximum=MAX_RECORD):
    with protected_open(path, maximum) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def no_marker(stage):
    marker = STAGES / stage / MARKER
    return not marker.exists() and not marker.is_symlink()


def sealed_selection(plan, *, allow_start_marker=False):
    """Bind an already materialized root-controlled directory to the reviewed tar."""
    stage = STAGES / FIXED_STAGE
    protected_parents(stage / 'launch.py')
    pins = {}
    directories = set()
    with protected_open(PACKETS / FIXED_STAGE / 'bundle.tar', MAX_BUNDLE) as bundle:
        verify_bundle(bundle, plan, PROFILES['uif-idle'].unit, FIXED_STAGE)
        bundle.seek(0)
        with tarfile.open(fileobj=bundle, mode='r:') as archive:
            for item in archive:
                path = stage / item.name
                if item.isdir():
                    directories.add(item.name)
                    protected_parents(path / 'member')
                    require(stat.S_IMODE(path.lstat().st_mode) == item.mode & 0o777,
                            'sealed stage directory mode changed')
                else:
                    with archive.extractfile(item) as member:
                        pin = hashlib.file_digest(member, 'sha256').hexdigest()
                    require(bytes_pin(path, MAX_BUNDLE) == pin
                            and stat.S_IMODE(path.lstat().st_mode) == item.mode & 0o777,
                            'sealed stage bytes or mode changed')
                    pins[item.name] = pin
                directories.update(str(p) for p in PurePosixPath(item.name).parents if str(p) != '.')
    actual = set()
    for parent, dirs, files in os.walk(stage, followlinks=False):
        root = Path(parent)
        for name in (*dirs, *files):
            path = root / name
            if allow_start_marker and path == stage / MARKER and name in files:
                continue
            actual.add(str(path.relative_to(stage)))
            require(not path.is_symlink() and len(actual) <= 50_000, 'unexpected sealed stage member')
    require(actual == directories | pins.keys() and INTERPRETER in pins
            and bool((stage / INTERPRETER).lstat().st_mode & 0o111),
            'sealed stage is incomplete or contains extra members')
    return encode({'mode': 'idle', 'stage': FIXED_STAGE,
                   'launcher_sha256': pins['launch.py'], 'config_sha256': pins[IDLE_CONFIG],
                   'interpreter_sha256': pins[INTERPRETER]})


class LinuxIdleHost:
    """Fixed systemd service adapter. Its root-owned input is issued externally."""

    def __init__(self):
        self.packet = PACKETS / FIXED_STAGE
        self.fd = None
        self.candidate = None
        self.custody = None
        self.uid = None
        self.unit_pin = None
        self.admission = None

    @contextmanager
    def lock(self, unit, custody):
        require(unit == PROFILES['uif-idle'].unit, 'unexpected service target')
        with selector_lock(shared=False) as stream:
            self.fd, self.custody = stream.fileno(), custody
            try:
                yield self
            finally:
                self.fd = None

    def load_admission(self):
        require(os.getuid() == os.geteuid() == 0, 'protected loader requires root transport')
        packet = record(read_record(self.packet, 'linux-apply.json'),
                        ('schema', 'admission', 'operator_admission_sha256',
                         'bootstrap_prior_sha256', 'bootstrapped_unit_sha256'))
        require(packet['schema'] == 'uif.foo-idle-apply/1'
                and packet['bootstrap_prior_sha256'] == BOOTSTRAP_PRIOR
                and hash_pin(packet['bootstrapped_unit_sha256'])
                and packet['bootstrapped_unit_sha256'] != BOOTSTRAP_PRIOR, 'expected reviewed host bootstrap')
        operator = read_record(self.packet, 'admission.json')
        require(digest(operator) == packet['operator_admission_sha256'], 'operator admission drift')
        self.uid = json.loads(operator)['owner_uid']
        self.unit_pin = packet['bootstrapped_unit_sha256']
        return ApplyAdmission(**packet['admission'])

    def verify_admission(self, admission):
        verified = self.load_admission() == admission and bytes_pin(DROPIN) == digest(selector_dropin())
        if verified:
            operator = record(read_record(self.packet, 'admission.json'), ADMISSION_FIELDS)
            verify_installer_operation(self.packet, operator, admission.operation_id)
        self.admission = admission if verified else None
        return verified

    def verify_custody(self):
        if self.fd is None:
            return False
        opened, current = os.fstat(self.fd), LOCK.lstat()
        return lock_identity(opened) == lock_identity(current) and plan_digest(lock_identity(opened)) == self.custody

    def preflight(self, profile, stage):
        require(self.admission is not None and self.load_admission() == self.admission,
                'protected apply admission changed')
        value = preflight(profile, stage, sealed_stage=True, approved_operator_uid=self.uid)
        self.verify_inactive_bootstrap(PROFILES[profile].unit, value['parked_unit_sha256'])
        return value

    def verify_inactive_bootstrap(self, unit, unit_pin):
        command = selector_dropin().decode().split('ExecStart=\nExecStart=!', 1)[1].strip()
        effective = subprocess.check_output(
            [SYSTEMCTL, '--no-pager', 'show', '--property=ExecStart', unit],
            stderr=subprocess.DEVNULL, timeout=4, text=True,
        )
        require(unit_pin == self.unit_pin
                and f'argv[]={command} ;' in effective and effective.count('argv[]=') == 1
                and no_marker(PARKED_STAGE) and no_marker(FIXED_STAGE),
                'expected inactive bootstrapped unit with both markers absent')

    def selection_sha256(self):
        return bytes_pin(SELECTOR)

    def verify_idle_stage(self, plan, selection_sha256):
        self.candidate = sealed_selection(plan)
        return digest(self.candidate) == selection_sha256

    def receipts(self, operation, prior):
        def original(name):
            try:
                return json.loads(read_record(RECEIPTS, name))
            except FileNotFoundError:
                return None
        return (original(operation + '.intent.json'), original(operation + '.selected.json'),
                original(prior + '.from.json'))

    def create_intent(self, operation, prior, intent):
        publish(RECEIPTS / (prior + '.from.json'), encode(operation))
        publish(RECEIPTS / (operation + '.intent.json'), encode(intent))

    def select_idle(self, stage, prior, target, deadline):
        parked = digest(encode({'mode': 'parked', 'stage': PARKED_STAGE}))
        require(stage == FIXED_STAGE and self.verify_custody() and prior == parked
                and self.selection_sha256() == prior and digest(self.candidate) == target
                and no_marker(PARKED_STAGE) and no_marker(FIXED_STAGE), 'idle selection compare failed')
        publish(SELECTOR, self.candidate, replace=True, deadline=deadline)

    def create_completion(self, operation, result):
        publish(RECEIPTS / (operation + '.selected.json'), encode(result))


def verify_owner_confirmation(root, plan, owner, *, name, schema, action, namespace):
    require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', owner) is not None,
            'unsupported enrolled host-owner principal')
    raw = read_record(root, name + '.json')
    confirmation = record(raw, ('schema', 'action', 'operation_id', 'preflight_sha256',
                                'issued_at', 'expires_at'))
    issued, expires = confirmation['issued_at'], confirmation['expires_at']
    require((confirmation['schema'], confirmation['action'], confirmation['operation_id'],
             confirmation['preflight_sha256']) ==
            (schema, action, plan['operation_id'], plan_digest(plan))
            and type(issued) is int and type(expires) is int
            and issued <= time.time() < expires <= issued + 300,
            'owner confirmation does not bind the current operation and window')
    try:
        trusted_signers = protected_open(ACTIVATION_SIGNERS, MAX_RECORD)
    except FileNotFoundError as exc:
        raise Held('Foo host owner has not enrolled an activation signer') from exc
    with trusted_signers as signers, protected_open(
            root / (name + '.json.sig'), MAX_RECORD) as signature:
        signer_bytes = signers.read(MAX_RECORD + 1)
        signer_line = signer_bytes.strip().splitlines()
        require(len(signer_line) == 1 and len(signer_bytes) <= MAX_RECORD,
                'expected one enrolled activation signer')
        fields = signer_line[0].split(maxsplit=3)
        require(len(fields) >= 3 and fields[0] == owner.encode('ascii')
                and fields[1].startswith((b'ssh-', b'ecdsa-', b'sk-')),
                'enrolled signer does not name the original Foo operator exactly')
        signers.seek(0)
        fds = (signers.fileno(), signature.fileno())
        verified = subprocess.run(
            [SSH_KEYGEN, '-Y', 'verify', '-f', f'/proc/self/fd/{fds[0]}',
             '-I', owner, '-n', namespace, '-s', f'/proc/self/fd/{fds[1]}'],
            input=raw, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=fds, timeout=4, check=False,
        )
        require(verified.returncode == 0 and time.time() < expires,
                'owner signature or confirmation expiry refused')
        return {'confirmation_sha256': digest(raw),
                'signers_sha256': digest(signer_bytes),
                'issued_at': issued, 'expires_at': expires}


def verify_activation_confirmation(root, plan, owner, *, start=False):
    return verify_owner_confirmation(
        root, plan, owner, name='activation-confirmation',
        schema='uif.foo-idle-activation-confirmation/1',
        action='authorize_idle_marker_and_start' if start else 'authorize_idle_marker',
        namespace=ACTIVATION_NAMESPACE,
    )


def prepare_apply(operation_id, *, issue=False):
    """Prepare or issue the exact signed apply packet without changing the selector."""
    require(os.getuid() == os.geteuid() == 0 and hash_pin(operation_id),
            'expected protected transport and exact apply operation')
    profile = PROFILES[PROFILE_NAME]
    host = LinuxIdleHost()
    path = host.packet / 'linux-apply.json'
    with selector_lock(shared=False) as stream:
        custody = plan_digest(lock_identity(os.fstat(stream.fileno())))
        host.fd, host.custody = stream.fileno(), custody
        raw = read_record(host.packet, 'admission.json')
        operator = record(raw, ADMISSION_FIELDS)
        verify_installer_operation(host.packet, operator, operation_id)
        plan = preflight(PROFILE_NAME, FIXED_STAGE, sealed_stage=True,
                         approved_operator_uid=operator['owner_uid'])
        host.unit_pin = plan['parked_unit_sha256']
        require(host.unit_pin != BOOTSTRAP_PRIOR and bytes_pin(DROPIN) == digest(selector_dropin()),
                'reviewed host bootstrap is not installed')
        host.verify_inactive_bootstrap(profile.unit, host.unit_pin)
        prior = plan_digest({'mode': 'parked', 'stage': PARKED_STAGE})
        idle = digest(sealed_selection(plan))
        require(prior != idle and host.selection_sha256() == prior and host.verify_custody()
                and read_record(host.packet, 'admission.json') == raw
                and host.receipts(operation_id, prior) == (None, None, None),
                'parked selection, original receipts, or host custody changed')
        request = {'schema': 'uif.foo-idle-apply-preflight/1', 'operation_id': operation_id,
                   'signer_principal': operator['owner'], 'owner_uid': operator['owner_uid'],
                   'installer_preflight_sha256': plan_digest(plan), 'bundle_sha256': plan['bundle_sha256'],
                   'operator_admission_sha256': digest(raw), 'bootstrap_prior_sha256': BOOTSTRAP_PRIOR,
                   'bootstrapped_unit_sha256': host.unit_pin, 'lock_custody_sha256': custody,
                   'parked_selection_sha256': prior, 'idle_selection_sha256': idle,
                   'unit': profile.unit, 'stage': FIXED_STAGE, 'valid_until': plan['valid_until']}
        result = {'preflight': request, 'preflight_sha256': plan_digest(request)}
        if not issue:
            return result
        signed = verify_owner_confirmation(
            host.packet, request, operator['owner'], name='apply-confirmation',
            schema='uif.foo-idle-apply-confirmation/1', action='issue_linux_apply', namespace=APPLY_NAMESPACE,
        )
        deadline = min(plan['valid_until'], signed['expires_at'])
        issued = ApplyAdmission(PROFILE_NAME, profile.unit, FIXED_STAGE, operation_id,
                                plan_digest(plan), custody, prior, idle, signed['issued_at'], deadline)
        output = encode({'schema': 'uif.foo-idle-apply/1', 'admission': asdict(issued),
                         'operator_admission_sha256': digest(raw), 'bootstrap_prior_sha256': BOOTSTRAP_PRIOR,
                         'bootstrapped_unit_sha256': host.unit_pin})
        require(stopped_unit_sha(profile.unit) == host.unit_pin
                and host.selection_sha256() == prior and host.verify_custody()
                and read_record(host.packet, 'admission.json') == raw
                and preflight(PROFILE_NAME, FIXED_STAGE, sealed_stage=True,
                              approved_operator_uid=operator['owner_uid']) == plan
                and digest(sealed_selection(plan)) == idle
                and signed['issued_at'] <= time.time() < deadline,
                'signed apply issuance lost current quiescence or custody')
        host.verify_inactive_bootstrap(profile.unit, host.unit_pin)
        try:
            original = read_record(host.packet, path.name)
        except FileNotFoundError:
            publish(path, output, deadline=deadline)
        else:
            require(original == output, 'an original apply packet already exists')
        require(read_record(host.packet, path.name) == output, 'issued apply packet needs reconciliation')
        return {**result, 'operation_id': operation_id, 'apply_packet_sha256': digest(output),
                'confirmation_sha256': signed['confirmation_sha256']}


def start_quiescence(root, unit, unit_pin):
    raw = read_record(root, 'start-quiescence.json')
    witness = record(raw, ('schema', 'unit', 'parked_unit_sha256', 'observed_at',
                           'inflight', 'children_gone', 'unresolved_operation_ids'))
    when = witness['observed_at']
    require((witness['schema'], witness['unit'], witness['parked_unit_sha256']) ==
            ('uif.guarded-upgrade-quiescence/1', unit, unit_pin)
            and type(when) is int and when <= time.time() < when + 60
            and type(witness['inflight']) is int and witness['inflight'] == 0
            and witness['children_gone'] is True and witness['unresolved_operation_ids'] == [],
            'independent fresh start quiescence is required')
    return digest(raw), when + 60


@dataclass(frozen=True)
class FixedWebhookIdleStart:
    """Protected mounted controller configuration; no CLI or request loader."""
    operation_id: str
    source_head: str
    review_sha256: str
    bundle_sha256: str
    selection_sha256: str


WEBHOOK_APPLY_AUTH_SCHEMA = 'uif.foo-idle-private-windmill-apply-authorization/1'
WEBHOOK_APPLY_PACKET_SCHEMA = 'uif.foo-idle-private-windmill-apply/1'
WEBHOOK_PACKET = 'windmill-apply.json'
WEBHOOK_START_SCHEMA = 'uif.foo-idle-webhook-start-intent/1'
WEBHOOK_AUTH_SCHEMA = 'uif.foo-idle-private-windmill-authorization/1'
WEBHOOK_PINS = ('operation_id', 'source_head', 'review_sha256', 'bundle_sha256',
                'selection_sha256')


class WindmillIdleHost(LinuxIdleHost):
    """Protected only; never loads a signature-shaped record from Windmill."""

    def load_admission(self):
        require(_private_webhook_root(), 'private webhook requires protected root controller')
        raw = read_record(self.packet, WEBHOOK_PACKET)
        packet = record(raw, ('schema', 'authorization', 'admission', 'unit_sha256'))
        authorization = packet['authorization']
        claim = ApplyAdmission(**packet['admission'])
        review = accepted_review(self.packet, PROFILES[PROFILE_NAME], FIXED_STAGE,
                                 authorization['review_sha256'])
        require(packet['schema'] == WEBHOOK_APPLY_PACKET_SCHEMA
                and authorization['schema'] == WEBHOOK_APPLY_AUTH_SCHEMA
                and authorization['operation_id'] == claim.operation_id
                and authorization['owner_uid'] > 0
                and authorization["owner_uid"] == pwd.getpwnam("user").pw_uid
                and pwd.getpwuid(authorization['owner_uid']).pw_name == authorization['owner']
                and authorization['confirmation_sha256'] == plan_digest(
                    {key: value for key, value in authorization.items() if key != 'confirmation_sha256'})
                and authorization['bundle_sha256'] == review['bundle_sha256']
                and packet['unit_sha256'] == review['parked_unit_sha256']
                and packet['unit_sha256'] != BOOTSTRAP_PRIOR
                and claim.issued_at == authorization['issued_at']
                and claim.expires_at <= authorization['expires_at']
                and claim.idle_selection_sha256 == authorization['selection_sha256']
                and (claim.profile, claim.unit, claim.stage) ==
                    (PROFILE_NAME, PROFILES[PROFILE_NAME].unit, FIXED_STAGE)
                and raw == encode(packet),
                'original private webhook apply packet changed')
        _webhook_job(authorization['job_id'])
        self.uid = authorization['owner_uid']
        self.unit_pin = packet['unit_sha256']
        self.operator_bytes = encode(authorization)
        self.operator = authorization
        return claim

    def verify_admission(self, admission):
        verified = self.load_admission() == admission and bytes_pin(DROPIN) == digest(selector_dropin())
        self.admission = admission if verified else None
        return verified

    def preflight(self, profile, stage):
        require(self.admission is not None and self.load_admission() == self.admission,
                'protected Windmill apply admission changed')
        plan = _preflight(profile, stage, sealed_stage=True, approved_operator_uid=self.uid,
                          webhook_admission=self.operator)
        self.verify_inactive_bootstrap(PROFILES[profile].unit, plan['parked_unit_sha256'])
        return plan


def _apply_private_windmill(config, job_id):
    """Issue typed private admission then call the exact existing one-use apply."""
    require(type(config) is FixedWebhookIdleStart and _private_webhook_root(),
            'private webhook requires protected root controller')
    _webhook_job(job_id)
    host = WindmillIdleHost()
    profile = PROFILES[PROFILE_NAME]
    with selector_lock(shared=False) as stream:
        try:
            original = host.load_admission()
        except FileNotFoundError:
            original = None
        if original is not None:
            require(all(host.operator[key] == getattr(config, key) for key in WEBHOOK_PINS),
                    'mounted approval differs from original Windmill apply')
            claim = original
        else:
            review = accepted_review(host.packet, profile, FIXED_STAGE, config.review_sha256)
            unit_pin = stopped_unit_sha(profile.unit)
            require((review['head'], review['bundle_sha256'], review['parked_unit_sha256']) ==
                    (config.source_head, config.bundle_sha256, unit_pin)
                    and unit_pin != BOOTSTRAP_PRIOR and bytes_pin(DROPIN) == digest(selector_dropin()),
                    'reviewed bootstrap and exact Windmill review required')
            witness_raw = read_record(host.packet, 'quiescence.json')
            witness = json.loads(witness_raw)
            require(type(witness.get('observed_at')) is int, 'fresh exact quiescence is required')
            uid = pwd.getpwnam('user').pw_uid
            issued = int(time.time())
            deadline = witness['observed_at'] + 60
            require(uid > 0 and issued < deadline <= issued + 60, 'original Windmill apply witness expired')
            auth = {'schema': WEBHOOK_APPLY_AUTH_SCHEMA, 'job_id': job_id, **asdict(config),
                    'profile': PROFILE_NAME, 'unit': profile.unit, 'stage': FIXED_STAGE,
                    'owner': pwd.getpwuid(uid).pw_name, 'owner_uid': uid,
                    'quiescence_sha256': digest(witness_raw), 'issued_at': issued, 'expires_at': deadline}
            auth['confirmation_sha256'] = plan_digest(auth)
            host.unit_pin = unit_pin
            host.verify_inactive_bootstrap(profile.unit, unit_pin)
            plan = _preflight(PROFILE_NAME, FIXED_STAGE, sealed_stage=True, approved_operator_uid=uid,
                              webhook_admission=auth)
            prior = plan_digest({'mode': 'parked', 'stage': PARKED_STAGE})
            require(host.selection_sha256() == prior
                    and digest(sealed_selection(plan)) == config.selection_sha256,
                    'exact parked and selected Windmill state required')
            lock_pin = plan_digest(lock_identity(os.fstat(stream.fileno())))
            claim = ApplyAdmission(PROFILE_NAME, profile.unit, FIXED_STAGE, config.operation_id,
                                   plan_digest(plan), lock_pin, prior, config.selection_sha256,
                                   issued, plan['valid_until'])
            publish(host.packet / WEBHOOK_PACKET, encode({'schema': WEBHOOK_APPLY_PACKET_SCHEMA,
                    'authorization': auth, 'admission': asdict(claim), 'unit_sha256': unit_pin}),
                    deadline=claim.expires_at)
    return apply(PROFILE_NAME, FIXED_STAGE, admission=claim, host=host)


def _webhook_job(job_id):
    try:
        parsed = UUID(job_id) if type(job_id) is str and len(job_id) == 36 else None
    except ValueError:
        parsed = None
    require(parsed is not None and parsed.int and str(parsed) == job_id,
            'expected current private Windmill run ID')
    return job_id


def _private_webhook_root():
    return os.getresuid() == (0, 0, 0)


def _webhook_authorization(config, job_id, plan, expires_at):
    require(type(config) is FixedWebhookIdleStart and _private_webhook_root()
            and all(getattr(config, key) == plan[key] for key in WEBHOOK_PINS),
            'protected webhook configuration differs from original idle selection')
    issued = int(time.time())
    require(type(expires_at) is int and issued < expires_at <= issued + 60,
            'existing start witness expired or out of bounds')
    return {'schema': WEBHOOK_AUTH_SCHEMA, 'job_id': _webhook_job(job_id),
            'preflight_sha256': plan_digest(plan), 'issued_at': issued,
            'expires_at': expires_at, **asdict(config)}


def _verify_webhook_authorization(plan, approval, *, require_live=True):
    operation = plan['operation_id']
    require(hash_pin(operation) and type(approval) is dict and
            set(approval) == {'schema', 'job_id', 'preflight_sha256', 'issued_at',
                              'expires_at', *WEBHOOK_PINS, *({'apply_authority'} if
                                  approval.get('apply_authority') == 'windmill' else set())}
            and approval['schema'] == WEBHOOK_AUTH_SCHEMA
            and approval['preflight_sha256'] == plan_digest(plan)
            and all(approval[key] == plan[key] for key in WEBHOOK_PINS)
            and type(approval['issued_at']) is type(approval['expires_at']) is int
            and approval['issued_at'] < approval['expires_at'] <= approval['issued_at'] + 60
            and (not require_live or approval['issued_at'] <= time.time() < approval['expires_at']),
            'invalid protected Windmill idle authorization')
    _webhook_job(approval['job_id'])
    require(read_record(RECEIPTS, operation + '.windmill-start.json') == encode(approval),
            'protected Windmill start record changed')
    return approval


def bind_private_webhook_start(config, *, apply_first=False):
    """Mount solely behind the authenticated fixed Windmill action and private channel."""
    require(type(config) is FixedWebhookIdleStart and hash_pin(config.operation_id),
            'protected webhook controller not configured')
    def submit(job_id):
        _webhook_job(job_id)
        require(_private_webhook_root(), 'private webhook requires protected root controller')
        with selector_lock(shared=True):
            try:
                prior = read_record(RECEIPTS, config.operation_id + '.windmill-start.json')
            except FileNotFoundError:
                pass
            else:
                original = json.loads(prior)
                require(type(original) is dict and original.get('schema') == WEBHOOK_AUTH_SCHEMA
                        and all(original.get(key) == getattr(config, key) for key in WEBHOOK_PINS),
                        'original webhook decision does not match this mounted controller')
                try:
                    intent = json.loads(read_record(RECEIPTS, config.operation_id + '.start-intent.json'))
                    observed = record(read_record(RECEIPTS, config.operation_id + '.started.json'),
                                      ('schema', 'operation_id', 'intent_sha256', 'main_pid', 'invocation_id'))
                    consumed = read_record(RECEIPTS, config.operation_id + '.start-consumed.json')
                except FileNotFoundError as exc:
                    raise Held('original webhook start incomplete; reconcile without replay') from exc
                require(intent.get('schema') == WEBHOOK_START_SCHEMA and intent.get('confirmation') == original
                        and observed['schema'] == 'uif.foo-idle-start-observation/1'
                        and observed.get('operation_id') == config.operation_id
                        and observed.get('intent_sha256') == plan_digest(intent)
                        and consumed == encode(start_consumption(
                            intent, observed['main_pid'], observed['invocation_id'])),
                        'original webhook start observation disagrees with its consumed invocation')
                return observed
        if apply_first:
            _apply_private_windmill(config, job_id)
        return activation_preflight(config.operation_id, start=True,
                                    _webhook=(config, job_id), _webhook_applied=apply_first)
    return submit


def bind_private_webhook_idle(config):
    """Fixed mount behind authenticated Windmill; no caller operation or scope."""
    return bind_private_webhook_start(config, apply_first=True)


def activation_preflight(operation_id, *, verify_confirmation=False, start=False,
                         prepare_start=False, _webhook=None, _webhook_applied=False):
    """Describe one completed idle selection for a separate host-owner decision."""
    require(hash_pin(operation_id), 'expected exact original apply operation')
    profile = PROFILES[PROFILE_NAME]
    host = WindmillIdleHost() if _webhook_applied else LinuxIdleHost()
    admission = host.load_admission()
    require((admission.profile, admission.unit, admission.stage, admission.operation_id) ==
            (PROFILE_NAME, profile.unit, FIXED_STAGE, operation_id)
            and all(hash_pin(pin) for pin in (admission.lock_custody_sha256,
                                             admission.prior_selection_sha256,
                                             admission.idle_selection_sha256)),
            'original apply operation binding changed')
    with host.lock(profile.unit, admission.lock_custody_sha256):
        require(host.verify_admission(admission) and host.verify_custody(),
                'original host packet or selector lock changed')
        if _webhook_applied:
            require(_webhook is not None, 'private Windmill activation only')
            operator, packet = host.operator, host.operator_bytes
        else:
            packet = read_record(host.packet, 'admission.json')
            operator = record(packet, ADMISSION_FIELDS)
        uid = operator['owner_uid']
        require((operator['schema'], operator['profile'], operator['unit'], operator['stage']) ==
                (WEBHOOK_APPLY_AUTH_SCHEMA if _webhook_applied else 'uif.guarded-upgrade-admission/1',
                 PROFILE_NAME, profile.unit, FIXED_STAGE)
                and type(uid) is int and uid == host.uid and uid > 0
                and operator['owner'] == pwd.getpwuid(uid).pw_name,
                'original operator binding changed')
        review = accepted_review(host.packet, profile, FIXED_STAGE, operator['review_sha256'])
        if not _webhook_applied:
            pinned_record(host.packet, 'confirmation', operator['confirmation_sha256'])
        pinned_record(host.packet, 'quiescence', operator['quiescence_sha256'])
        intent, selected = apply_records(admission)
        require(host.receipts(operation_id, admission.prior_selection_sha256) ==
                (intent, selected, operation_id), 'original apply receipts are incomplete or disagree')
        unit_pin = stopped_unit_sha(profile.unit)
        require(unit_pin == review['parked_unit_sha256'], 'effective unit changed after apply')
        host.verify_inactive_bootstrap(profile.unit, unit_pin)
        require(host.verify_idle_stage(review, admission.idle_selection_sha256)
                and host.selection_sha256() == admission.idle_selection_sha256
                and stopped_unit_sha(profile.unit) == unit_pin
                and host.verify_admission(admission) and host.verify_custody()
                and (host.operator_bytes if _webhook_applied
                         else read_record(host.packet, 'admission.json')) == packet,
                'selected idle readback changed')
        host.verify_inactive_bootstrap(profile.unit, unit_pin)
        plan = {'schema': 'uif.foo-idle-activation-preflight/1', 'mode': 'read_only',
                'operation_id': operation_id, 'unit': profile.unit, 'stage': FIXED_STAGE,
                'original_owner_uid': uid, 'signer_principal': operator['owner'],
                'original_admission_sha256': digest(packet),
                'source_head': review['head'], 'review_sha256': operator['review_sha256'],
                'bundle_sha256': review['bundle_sha256'], 'unit_sha256': unit_pin,
                'selected_receipt_sha256': plan_digest(selected),
                'selection_sha256': admission.idle_selection_sha256,
                'lock_custody_sha256': admission.lock_custody_sha256}
        if start or prepare_start:
            witness_pin, witness_deadline = start_quiescence(host.packet, profile.unit, unit_pin)
            plan = {**plan, 'start_quiescence_sha256': witness_pin}
        if verify_confirmation or start:
            if _webhook is None:
                result = verify_activation_confirmation(host.packet, plan, operator['owner'], start=start)
            else:
                require(start and not verify_confirmation and not prepare_start,
                        'private webhook confined to one guarded idle start')
                result = _webhook_authorization(*_webhook, plan, witness_deadline)
            require(host.verify_custody() and host.selection_sha256() == admission.idle_selection_sha256
                    and stopped_unit_sha(profile.unit) == unit_pin,
                    'selected idle readback changed during confirmation verification')
            host.verify_inactive_bootstrap(profile.unit, unit_pin)
            require(time.time() < result['expires_at'], 'activation confirmation expired')
            if not start:
                return {'preflight': plan, 'preflight_sha256': plan_digest(plan),
                        'verified_confirmation': result}
            if _webhook_applied:
                result = {**result, 'apply_authority': 'windmill'}
            deadline = min(result['expires_at'], witness_deadline)
            require(start_quiescence(host.packet, profile.unit, unit_pin) == (witness_pin, witness_deadline),
                    'start quiescence witness changed')
            restart = subprocess.check_output([SYSTEMCTL, '--no-pager', 'show', '--property=Restart',
                                               profile.unit], timeout=4, text=True)
            require(restart.strip() == 'Restart=no' and time.time() < deadline,
                    'one-start policy or activation deadline refused')
            intent_path = RECEIPTS / (operation_id + '.start-intent.json')
            marker = STAGES / FIXED_STAGE / MARKER
            start_intent = {'schema': WEBHOOK_START_SCHEMA if _webhook else 'uif.foo-idle-start-intent/1',
                            'operation_id': operation_id,
                            'preflight_sha256': plan_digest(plan), 'preflight': plan, 'confirmation': result,
                            'selection_sha256': admission.idle_selection_sha256, 'launch_before': deadline}
            if _webhook is not None:
                publish(RECEIPTS / (operation_id + '.windmill-start.json'), encode(result),
                        deadline=deadline)
            publish(intent_path, encode(start_intent), deadline=deadline)
            publish(marker, encode(start_intent), deadline=deadline, mode=0o644)
            require(bytes_pin(marker) == plan_digest(start_intent) and host.verify_custody(),
                    'activation marker needs reconciliation')
        else:
            return plan

    # ExecStart consumes the intent under its own exclusive lock.
    require(time.time() < deadline, 'start window expired after marker; reconcile original intent')
    subprocess.run([SYSTEMCTL, '--no-pager', '--job-mode=fail', 'start', profile.unit],
                   timeout=15, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid, invocation = verify_started_idle(profile.unit)
    consumed = start_consumption(start_intent, pid, invocation)
    require(read_record(RECEIPTS, operation_id + '.start-consumed.json') == encode(consumed),
            'running process does not match the original start consumption')
    receipt = {'schema': 'uif.foo-idle-start-observation/1', 'operation_id': operation_id,
               'intent_sha256': plan_digest(start_intent), 'main_pid': pid, 'invocation_id': invocation}
    publish(RECEIPTS / (operation_id + '.started.json'), encode(receipt))
    return receipt


def verify_started_idle(unit):
    def state():
        current = subprocess.check_output([SYSTEMCTL, '--no-pager', 'show',
                                          '--property=ActiveState,SubState,MainPID,InvocationID', unit],
                                         timeout=4, text=True)
        return dict(line.split('=', 1) for line in current.splitlines())

    original = state()
    pid = int(original.get('MainPID', '0'))
    invocation = original.get('InvocationID', '')
    require(original.get('ActiveState') == 'active' and original.get('SubState') == 'running' and pid > 0
            and re.fullmatch('[0-9a-f]{32}', invocation) is not None,
            'original start has unverified process readback')
    idle_messages = {
        'Stokowski idle: free_route_unavailable; admissions and effects closed',
        'Stokowski idle: free_quota_unavailable; admissions and effects closed',
    }
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        journal = subprocess.check_output(
            [JOURNALCTL, '--no-pager', '--output=cat', '--lines=16', f'_SYSTEMD_UNIT={unit}',
             f'_SYSTEMD_INVOCATION_ID={invocation}', f'_PID={pid}'],
            timeout=4, text=True, stderr=subprocess.DEVNULL,
        )
        require(state() == original, 'original idle process changed during readback')
        if idle_messages.intersection(journal.splitlines()):
            return pid, invocation
        time.sleep(0.25)
    raise Held('original start did not prove the guarded idle hold')


def trusted_start_process():
    """Read the system manager and kernel, without accepting an invocation from the caller."""
    require(os.getresuid() == (0, 0, 0) and os.getppid() == 1,
            'idle start edge must be launched by the system manager')
    unit = PROFILES[PROFILE_NAME].unit
    properties = ('Id,User,Group,SupplementaryGroups,Type,Restart,NoNewPrivileges,'
                  'CapabilityBoundingSet,AmbientCapabilities,'
                  'MainPID,InvocationID,ControlGroup,ActiveState')
    output = subprocess.check_output(
        [SYSTEMCTL, '--system', '--no-pager', 'show', '--property=' + properties, unit],
        env={'LC_ALL': 'C'}, timeout=4, text=True,
    )
    observed = dict(line.split('=', 1) for line in output.splitlines())
    pid = os.getpid()
    invocation, cgroup = observed['InvocationID'], observed['ControlGroup']
    require((observed['Id'], observed['User'], observed['Group'], observed['SupplementaryGroups'],
             observed['Type'], observed['Restart'], observed['NoNewPrivileges'], observed['MainPID'])
            == (unit, 'user', 'user', '', 'simple', 'no', 'yes', str(pid))
            and set(observed['CapabilityBoundingSet'].lower().split()) == PRELUDE_CAPABILITIES.keys()
            and not observed['AmbientCapabilities'].strip()
            and observed['ActiveState'] in ('activating', 'active')
            and re.fullmatch('[0-9a-f]{32}', invocation) is not None
            and invocation != '0' * 32 and cgroup.startswith('/') and cgroup != '/'
            and Path('/proc/self/cgroup').read_text().splitlines() == ['0::' + cgroup],
            'system manager and kernel did not attest this idle invocation')
    return pid, invocation


def start_consumption(intent, pid, invocation):
    return {'schema': 'uif.foo-idle-start-consumption/1', 'operation_id': intent['operation_id'],
            'intent_sha256': plan_digest(intent), 'main_pid': pid, 'invocation_id': invocation}


def kernel_security_status():
    return dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())


def drop_prelude_bound():
    status = kernel_security_status()
    require(status['NoNewPrivs'].strip() == '1'
            and all(int(status[name], 16) == PRELUDE_MASK for name in ('CapBnd', 'CapPrm', 'CapEff'))
            and all(int(status[name], 16) == 0 for name in ('CapInh', 'CapAmb')),
            'idle prelude does not have the exact capability set')
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
    prctl.restype = ctypes.c_int
    for capability in PRELUDE_CAPABILITIES.values():
        if prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, 'idle prelude could not drop the capability bound')
    require(int(kernel_security_status()['CapBnd'], 16) == 0, 'idle prelude capability bound remains')


def drop_idle_privileges():
    account = pwd.getpwnam('user')
    uid, gid = account.pw_uid, account.pw_gid
    require(uid > 0 and gid > 0 and gid == grp.getgrnam('user').gr_gid,
            'unexpected service account')
    groups = set(os.getgrouplist(account.pw_name, gid))
    drop_prelude_bound()
    os.initgroups(account.pw_name, gid)
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    status = kernel_security_status()
    require(os.getresuid() == (uid, uid, uid) and os.getresgid() == (gid, gid, gid)
            and set(os.getgroups()) == groups and status['NoNewPrivs'].strip() == '1'
            and all(int(status[name], 16) == 0
                    for name in ('CapBnd', 'CapPrm', 'CapEff', 'CapAmb', 'CapInh')),
            'service privileges did not drop')
    os.environ.update(HOME=account.pw_dir, USER=account.pw_name,
                      LOGNAME=account.pw_name, SHELL=account.pw_shell)


def _retained_idle_original(custody, *, require_live):
    """Read common guarded-start bindings; never consume or grant a start."""
    selection = read_record(ROOT, 'selection.json')
    selected = record(selection,
                      ('mode', 'stage', 'launcher_sha256', 'config_sha256', 'interpreter_sha256'))
    require((selected['mode'], selected['stage']) == ('idle', FIXED_STAGE), 'unit is parked')
    stage = STAGES / FIXED_STAGE
    for path, pin in (('launch.py', 'launcher_sha256'), (IDLE_CONFIG, 'config_sha256'),
                      (INTERPRETER, 'interpreter_sha256')):
        require(hash_pin(selected[pin]) and bytes_pin(stage / path, MAX_BUNDLE) == selected[pin],
                'selected stage pin drift')
    marker = (stage / MARKER).lstat()
    require(stat.S_ISREG(marker.st_mode) and marker.st_uid == 0 and marker.st_nlink == 1
            and not marker.st_mode & 0o022, 'protected activation marker unavailable')
    require(marker.st_size > 0, 'signed start intent is missing')
    raw = read_record(stage, MARKER)
    approval = record(raw, ('schema', 'operation_id', 'preflight_sha256', 'preflight', 'confirmation',
                            'selection_sha256', 'launch_before'))
    confirmation = approval['confirmation']
    plan = approval['preflight']
    webhook = approval['schema'] == WEBHOOK_START_SCHEMA
    require(all(hash_pin(approval[key]) for key in ('operation_id', 'preflight_sha256'))
            and type(plan) is dict and plan_digest(plan) == approval['preflight_sha256']
            and raw == encode(approval), 'invalid start intent')
    if webhook:
        _verify_webhook_authorization(plan, confirmation, require_live=require_live)
    else:
        require(type(confirmation) is dict
                and confirmation.keys() == {'confirmation_sha256', 'signers_sha256', 'issued_at', 'expires_at'}
                and all(hash_pin(confirmation[key]) for key in ('confirmation_sha256', 'signers_sha256')),
                'invalid signed start intent')
    issued, expires, launch_before = (confirmation['issued_at'], confirmation['expires_at'],
                                      approval['launch_before'])
    require((webhook or approval['schema'] == 'uif.foo-idle-start-intent/1')
            and approval['selection_sha256'] == digest(selection)
            and all(type(value) is int for value in (issued, expires, launch_before))
            and issued < launch_before <= expires <= issued + 300
            and (not require_live or issued <= time.time() < launch_before),
            'signed start marker expired or no longer binds the selected stage')
    operation = approval['operation_id']
    unit = PROFILES[PROFILE_NAME].unit
    webhook_applied = webhook and confirmation.get('apply_authority') == 'windmill'
    host = WindmillIdleHost() if webhook_applied else LinuxIdleHost()
    admission = host.load_admission()
    if webhook_applied:
        operator, operator_bytes = host.operator, host.operator_bytes
    else:
        operator_bytes = read_record(host.packet, 'admission.json')
        operator = record(operator_bytes, ADMISSION_FIELDS)
    principal, uid = operator['owner'], operator['owner_uid']
    require((admission.operation_id, admission.unit, admission.stage, admission.idle_selection_sha256,
             admission.lock_custody_sha256) ==
            (operation, unit, FIXED_STAGE, digest(selection), plan_digest(custody))
            and (plan['operation_id'], plan['unit'], plan['stage'], plan['selection_sha256'],
                 plan['lock_custody_sha256'], plan['unit_sha256']) ==
            (operation, unit, FIXED_STAGE, digest(selection), plan_digest(custody), host.unit_pin)
            and (plan['signer_principal'], plan['original_owner_uid'], plan['original_admission_sha256'])
            == (principal, uid, digest(operator_bytes))
            and type(uid) is int and uid > 0 and uid == host.uid and pwd.getpwuid(uid).pw_name == principal
            and read_record(RECEIPTS, operation + '.start-intent.json') == raw,
            'original signed intent or installed apply binding changed')
    require(host.verify_admission(admission), 'installed bootstrap changed')
    original_intent, selected_receipt = apply_records(admission)
    require(host.receipts(operation, admission.prior_selection_sha256) ==
            (original_intent, selected_receipt, operation)
            and plan['selected_receipt_sha256'] == plan_digest(selected_receipt),
            'original apply completion changed')
    unit_bytes = subprocess.check_output([SYSTEMCTL, '--system', '--no-pager', 'cat', unit],
                                        env={'LC_ALL': 'C'}, timeout=4)
    review = accepted_review(host.packet, PROFILES[PROFILE_NAME], FIXED_STAGE, operator['review_sha256'])
    require(digest(unit_bytes) == host.unit_pin == review['parked_unit_sha256']
            and review['bundle_sha256'] == plan['bundle_sha256']
            and operator['review_sha256'] == plan['review_sha256']
            and sealed_selection(review, allow_start_marker=True) == selection,
            'reviewed unit or sealed bundle changed before start')
    return selection, stage, raw, approval, confirmation, plan, host, principal, webhook


def _retained_idle_status(approval, consumed, now):
    """Conservatively classify a checked original, not authorize execution."""
    if consumed is not None:
        row = record(consumed, ('schema', 'operation_id', 'intent_sha256', 'main_pid', 'invocation_id'))
        pid, invocation = row['main_pid'], row['invocation_id']
        require(type(pid) is int and pid > 0 and type(invocation) is str
                and re.fullmatch(r'[0-9a-f]{32}', invocation) is not None
                and int(invocation, 16) != 0
                and consumed == encode(start_consumption(approval, pid, invocation)),
                'retained guarded start consumption ambiguous')
        return 'consumed'
    if now >= approval['launch_before']:
        return 'expired'
    # A future-dated or not-yet-consumed protected original is not harmless.
    return 'potentially_actionable'


def observe_retained_idle_start(locked_stream, expected_selection):
    """Read only while the existing caller holds the exclusive selector lock."""
    custody = lock_identity(os.fstat(locked_stream.fileno()))
    require(lock_identity(LOCK.lstat()) == custody, 'guarded selector lock custody changed')
    selection, stage, raw, approval, *_ = _retained_idle_original(custody, require_live=False)
    require(digest(selection) == expected_selection, 'guarded selection differs from prior')
    name = approval['operation_id'] + '.start-consumed.json'
    try:
        consumed = read_record(RECEIPTS, name)
    except FileNotFoundError:
        consumed = None
    status = _retained_idle_status(approval, consumed, time.time())
    require(lock_identity(LOCK.lstat()) == custody and read_record(ROOT, 'selection.json') == selection
            and read_record(stage, MARKER) == raw, 'retained guarded start changed during observation')
    return status


def serve_idle_stage():
    """Consume one signed start at the privileged systemd edge, then exec unprivileged."""
    pid, invocation = trusted_start_process()
    with selector_lock(shared=False) as stream:
        custody = lock_identity(os.fstat(stream.fileno()))
        selection, stage, raw, approval, confirmation, plan, host, principal, webhook = (
            _retained_idle_original(custody, require_live=True))
        operation = approval['operation_id']
        unit = PROFILES[PROFILE_NAME].unit
        expires, launch_before = confirmation['expires_at'], approval['launch_before']
        witness_pin, witness_deadline = start_quiescence(host.packet, unit, host.unit_pin)
        require(witness_pin == plan['start_quiescence_sha256']
                and launch_before == min(expires, witness_deadline)
                and (_verify_webhook_authorization(plan, confirmation) if webhook
                     else verify_activation_confirmation(host.packet, plan, principal, start=True)) == confirmation
                and trusted_start_process() == (pid, invocation)
                and lock_identity(LOCK.lstat()) == custody,
                'start authorization, invocation or lock changed')
        consumed = start_consumption(approval, pid, invocation)
        consumed_path = RECEIPTS / (operation + '.start-consumed.json')
        try:
            publish(consumed_path, encode(consumed), deadline=launch_before)
        except FileExistsError as exc:
            raise Held('original start already consumed; reconcile its receipt') from exc
        require(read_record(RECEIPTS, consumed_path.name) == encode(consumed),
                'original start consumption needs reconciliation')
        # Keep the live-service fence, and verify custody after lock conversion.
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        require(lock_identity(LOCK.lstat()) == custody and read_record(ROOT, 'selection.json') == selection
                and read_record(stage, MARKER) == raw and time.time() < launch_before,
                'start state changed after consumption; reconcile the original receipt')
        os.set_inheritable(stream.fileno(), True)
        executable = str(stage / INTERPRETER)
        drop_idle_privileges()
        require(time.time() < launch_before, 'start expired after consumption')
        os.execv(executable, [executable, '-I', '-S', '-B', str(stage / 'launch.py')])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=PROFILES)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--action', choices=('preflight', 'sealed-preflight', 'apply',
                                             'prepare-apply', 'issue-apply', 'activation-preflight',
                                             'verify-activation-confirmation', 'prepare-start', 'start-idle', 'serve'),
                        default='preflight')
    parser.add_argument('--operation-id')
    args = parser.parse_args()
    try:
        activation_checks = ('activation-preflight', 'verify-activation-confirmation',
                             'prepare-start', 'start-idle')
        apply_checks = ('prepare-apply', 'issue-apply')
        require((args.operation_id is not None) == (args.action in (*activation_checks, *apply_checks)),
                'operation id is required only for signed workflow actions')
        if args.action == 'preflight':
            result = preflight(args.profile, args.stage)
        else:
            require((args.profile, args.stage) == ('uif-idle', FIXED_STAGE), 'unexpected service target')
            if args.action == 'serve':
                serve_idle_stage()
                return 1
            if args.action in apply_checks:
                result = prepare_apply(args.operation_id, issue=args.action == 'issue-apply')
            elif args.action in activation_checks:
                result = activation_preflight(
                    args.operation_id, verify_confirmation=args.action == 'verify-activation-confirmation',
                    start=args.action == 'start-idle', prepare_start=args.action == 'prepare-start',
                )
                if args.action in ('activation-preflight', 'prepare-start'):
                    result = {'preflight': result, 'preflight_sha256': plan_digest(result)}
            elif args.action == 'sealed-preflight':
                with selector_lock(shared=True) as stream:
                    plan = preflight(args.profile, args.stage, sealed_stage=True)
                    result = {'preflight': plan, 'preflight_sha256': plan_digest(plan),
                              'idle_selection_sha256': digest(sealed_selection(plan)),
                              'prior_selection_sha256': bytes_pin(SELECTOR),
                              'lock_custody_sha256': plan_digest(lock_identity(os.fstat(stream.fileno())))}
            else:
                host = LinuxIdleHost()
                result = apply(args.profile, args.stage, admission=host.load_admission(), host=host)
    except Held as exc:
        print(f'Guarded upgrade held: {exc}.', file=sys.stderr)
        if args.action in ('apply', 'issue-apply', 'start-idle'):
            print('Reconcile the original receipts and selector before retrying.', file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        print('Guarded upgrade refused. Inspect the original receipts before retrying.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
