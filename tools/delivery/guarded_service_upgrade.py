#!/usr/bin/env python3
"""Read-only operator preflight for a reviewed, fixed service upgrade."""

import argparse
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import fcntl
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
ADMISSION_FIELDS = ('schema', 'profile', 'unit', 'stage', 'owner', 'owner_uid', 'review_sha256',
                    'confirmation_sha256', 'quiescence_sha256', 'issued_at', 'expires_at')


def selector_dropin():
    marker = STAGES / FIXED_STAGE / MARKER
    return ('[Unit]\nConditionPathExists=\n'
            f'ConditionPathExists={marker}\n\n'
            '[Service]\nRestart=no\nExecStart=\n'
            f'ExecStart=/usr/bin/python3 -I -S -B {TOOL_PATH} --profile uif-idle'
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
    profile = PROFILES[profile_name]
    require(profile.reviewed_head is not None, 'legacy OffMenu retains its existing owner-only upgrade route')
    require(re.fullmatch('uif1313-idle-[a-z0-9][a-z0-9.-]{0,60}', stage) is not None,
            'invalid UIF idle stage')
    root = PACKETS / stage
    target = STAGES / stage
    before = time.time()
    admission = record(read_record(root, 'admission.json'), ADMISSION_FIELDS)
    uid = os.getuid() if approved_operator_uid is None else approved_operator_uid
    caller_ok = (os.getuid() == os.geteuid() == uid if approved_operator_uid is None
                 else os.getuid() == os.geteuid() == 0)
    require(admission['schema'] == 'uif.guarded-upgrade-admission/1'
            and (admission['profile'], admission['unit'], admission['stage']) ==
            (profile_name, profile.unit, stage)
            and caller_ok and type(uid) is int and uid > 0 and type(admission['owner_uid']) is int
            and admission['owner_uid'] == uid and admission['owner'] == pwd.getpwuid(uid).pw_name
            and type(admission['issued_at']) is int and type(admission['expires_at']) is int
            and admission['issued_at'] <= before < admission['expires_at'] <= admission['issued_at'] + 300,
            'operator, target or admission window mismatch')

    review = accepted_review(root, profile, stage, admission['review_sha256'])
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


def sealed_selection(plan):
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
        command = selector_dropin().decode().split('ExecStart=\nExecStart=', 1)[1].strip()
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


def activation_preflight(operation_id, *, verify_confirmation=False, start=False, prepare_start=False):
    """Describe one completed idle selection for a separate host-owner decision."""
    require(hash_pin(operation_id), 'expected exact original apply operation')
    profile = PROFILES[PROFILE_NAME]
    host = LinuxIdleHost()
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
        packet = read_record(host.packet, 'admission.json')
        operator = record(packet, ADMISSION_FIELDS)
        uid = operator['owner_uid']
        require((operator['schema'], operator['profile'], operator['unit'], operator['stage']) ==
                ('uif.guarded-upgrade-admission/1', PROFILE_NAME, profile.unit, FIXED_STAGE)
                and type(uid) is int and uid == host.uid and uid > 0
                and operator['owner'] == pwd.getpwuid(uid).pw_name,
                'original operator binding changed')
        review = accepted_review(host.packet, profile, FIXED_STAGE, operator['review_sha256'])
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
                and read_record(host.packet, 'admission.json') == packet,
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
            result = verify_activation_confirmation(host.packet, plan, operator['owner'], start=start)
            require(host.verify_custody() and host.selection_sha256() == admission.idle_selection_sha256
                    and stopped_unit_sha(profile.unit) == unit_pin,
                    'selected idle readback changed during confirmation verification')
            host.verify_inactive_bootstrap(profile.unit, unit_pin)
            require(time.time() < result['expires_at'], 'activation confirmation expired')
            if not start:
                return {'preflight': plan, 'preflight_sha256': plan_digest(plan),
                        'verified_confirmation': result}
            deadline = min(result['expires_at'], witness_deadline)
            require(start_quiescence(host.packet, profile.unit, unit_pin) == (witness_pin, witness_deadline),
                    'start quiescence witness changed')
            restart = subprocess.check_output([SYSTEMCTL, '--no-pager', 'show', '--property=Restart',
                                               profile.unit], timeout=4, text=True)
            require(restart.strip() == 'Restart=no' and time.time() < deadline,
                    'one-start policy or activation deadline refused')
            intent_path = RECEIPTS / (operation_id + '.start-intent.json')
            marker = STAGES / FIXED_STAGE / MARKER
            start_intent = {'schema': 'uif.foo-idle-start-intent/1', 'operation_id': operation_id,
                            'preflight_sha256': plan_digest(plan), 'confirmation': result,
                            'selection_sha256': admission.idle_selection_sha256, 'launch_before': deadline}
            publish(intent_path, encode(start_intent), deadline=deadline)
            publish(marker, encode(start_intent), deadline=deadline, mode=0o644)
            require(bytes_pin(marker) == plan_digest(start_intent) and host.verify_custody(),
                    'activation marker needs reconciliation')
        else:
            return plan

    # ExecStart acquires a shared lock, so release the exclusive lock before dispatch.
    require(time.time() < deadline, 'start window expired after marker; reconcile original intent')
    subprocess.run([SYSTEMCTL, '--no-pager', '--job-mode=fail', 'start', profile.unit],
                   timeout=15, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = verify_started_idle(profile.unit)
    receipt = {'schema': 'uif.foo-idle-start-observation/1', 'operation_id': operation_id,
               'intent_sha256': plan_digest(start_intent), 'main_pid': pid}
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
            return pid
        time.sleep(0.25)
    raise Held('original start did not prove the guarded idle hold')


def serve_idle_stage():
    """systemd's one guarded ExecStart. Its shared lock survives exec and stop."""
    require(os.getuid() == os.geteuid() == pwd.getpwnam('user').pw_uid != 0, 'unexpected service user')
    with selector_lock(shared=True) as stream:
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
        approval = record(raw, ('schema', 'operation_id', 'preflight_sha256', 'confirmation',
                                'selection_sha256', 'launch_before'))
        confirmation = approval['confirmation']
        require(type(confirmation) is dict
                and confirmation.keys() == {'confirmation_sha256', 'signers_sha256', 'issued_at', 'expires_at'}
                and all(hash_pin(confirmation[key]) for key in ('confirmation_sha256', 'signers_sha256'))
                and all(hash_pin(approval[key]) for key in ('operation_id', 'preflight_sha256'))
                and raw == encode(approval),
                'invalid signed start intent')
        issued, expires, launch_before = (confirmation['issued_at'], confirmation['expires_at'],
                                          approval['launch_before'])
        require(approval['schema'] == 'uif.foo-idle-start-intent/1'
                and approval['selection_sha256'] == digest(selection)
                and all(type(value) is int for value in (issued, expires, launch_before))
                and issued <= time.time() < launch_before <= expires <= issued + 300,
                'signed start marker expired or no longer binds the selected stage')
        require(lock_identity(os.fstat(stream.fileno())) == lock_identity(LOCK.lstat()),
                'selector lock was replaced')
        os.set_inheritable(stream.fileno(), True)
        executable = str(stage / INTERPRETER)
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
