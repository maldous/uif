"""Read-only old guarded-generation assessment for normal selection.

Caller is the existing protected writer, inside its already-held exclusive
selector lock. This component never grants admission and never takes a lock.
"""
import os
import pwd
import re
import stat
import subprocess
import time

import guarded_service_upgrade as guard
import read_normal_effective_state as effective
from commit_commissioned_normal_selection import GuardedEffectivePrior


# Assessment only: never change the byte-pinned legacy launch module.
def _retained_webhook_record(plan, approval):
    operation = plan['operation_id']
    guard.require(guard.hash_pin(operation) and type(approval) is dict and
            set(approval) == {'schema', 'job_id', 'preflight_sha256', 'issued_at',
                              'expires_at', *guard.WEBHOOK_PINS, *({'apply_authority'} if
                                  approval.get('apply_authority') == 'windmill' else set())}
            and approval['schema'] == guard.WEBHOOK_AUTH_SCHEMA
            and approval['preflight_sha256'] == guard.plan_digest(plan)
            and all(approval[key] == plan[key] for key in guard.WEBHOOK_PINS)
            and type(approval['issued_at']) is type(approval['expires_at']) is int
            and approval['issued_at'] < approval['expires_at'] <= approval['issued_at'] + 60,
            'invalid protected Windmill idle authorization')
    guard._webhook_job(approval['job_id'])
    guard.require(guard.read_record(guard.RECEIPTS, operation + '.windmill-start.json') == guard.encode(approval),
            'protected Windmill start record changed')
    return approval


def _retained_idle_original(custody):
    """Read common guarded-start bindings; never consume or grant a start."""
    selection = guard.read_record(guard.ROOT, 'selection.json')
    selected = guard.record(selection,
                      ('mode', 'stage', 'launcher_sha256', 'config_sha256', 'interpreter_sha256'))
    guard.require((selected['mode'], selected['stage']) == ('idle', guard.FIXED_STAGE), 'unit is parked')
    stage = guard.STAGES / guard.FIXED_STAGE
    for path, pin in (('launch.py', 'launcher_sha256'), (guard.IDLE_CONFIG, 'config_sha256'),
                      (guard.INTERPRETER, 'interpreter_sha256')):
        guard.require(guard.hash_pin(selected[pin]) and guard.bytes_pin(stage / path, guard.MAX_BUNDLE) == selected[pin],
                'selected stage pin drift')
    marker = (stage / guard.MARKER).lstat()
    guard.require(stat.S_ISREG(marker.st_mode) and marker.st_uid == 0 and marker.st_nlink == 1
            and not marker.st_mode & 0o022, 'protected activation marker unavailable')
    guard.require(marker.st_size > 0, 'signed start intent is missing')
    raw = guard.read_record(stage, guard.MARKER)
    approval = guard.record(raw, ('schema', 'operation_id', 'preflight_sha256', 'preflight', 'confirmation',
                            'selection_sha256', 'launch_before'))
    confirmation = approval['confirmation']
    plan = approval['preflight']
    webhook = approval['schema'] == guard.WEBHOOK_START_SCHEMA
    guard.require(all(guard.hash_pin(approval[key]) for key in ('operation_id', 'preflight_sha256'))
            and type(plan) is dict and guard.plan_digest(plan) == approval['preflight_sha256']
            and raw == guard.encode(approval), 'invalid start intent')
    if webhook:
        _retained_webhook_record(plan, confirmation)
    else:
        guard.require(type(confirmation) is dict
                and confirmation.keys() == {'confirmation_sha256', 'signers_sha256', 'issued_at', 'expires_at'}
                and all(guard.hash_pin(confirmation[key]) for key in ('confirmation_sha256', 'signers_sha256')),
                'invalid signed start intent')
    issued, expires, launch_before = (confirmation['issued_at'], confirmation['expires_at'],
                                      approval['launch_before'])
    guard.require((webhook or approval['schema'] == 'uif.foo-idle-start-intent/1')
            and approval['selection_sha256'] == guard.digest(selection)
            and all(type(value) is int for value in (issued, expires, launch_before))
            and issued < launch_before <= expires <= issued + 300,
            'signed start marker expired or no longer binds the selected stage')
    operation = approval['operation_id']
    unit = guard.PROFILES[guard.PROFILE_NAME].unit
    webhook_applied = webhook and confirmation.get('apply_authority') == 'windmill'
    host = guard.WindmillIdleHost() if webhook_applied else guard.LinuxIdleHost()
    admission = host.load_admission()
    if webhook_applied:
        operator, operator_bytes = host.operator, host.operator_bytes
    else:
        operator_bytes = guard.read_record(host.packet, 'admission.json')
        operator = guard.record(operator_bytes, guard.ADMISSION_FIELDS)
    principal, uid = operator['owner'], operator['owner_uid']
    guard.require((admission.operation_id, admission.unit, admission.stage, admission.idle_selection_sha256,
             admission.lock_custody_sha256) ==
            (operation, unit, guard.FIXED_STAGE, guard.digest(selection), guard.plan_digest(custody))
            and (plan['operation_id'], plan['unit'], plan['stage'], plan['selection_sha256'],
                 plan['lock_custody_sha256'], plan['unit_sha256']) ==
            (operation, unit, guard.FIXED_STAGE, guard.digest(selection), guard.plan_digest(custody), host.unit_pin)
            and (plan['signer_principal'], plan['original_owner_uid'], plan['original_admission_sha256'])
            == (principal, uid, guard.digest(operator_bytes))
            and type(uid) is int and uid > 0 and uid == host.uid and pwd.getpwuid(uid).pw_name == principal
            and guard.read_record(guard.RECEIPTS, operation + '.start-intent.json') == raw,
            'original signed intent or installed apply binding changed')
    guard.require(host.verify_admission(admission), 'installed bootstrap changed')
    original_intent, selected_receipt = guard.apply_records(admission)
    guard.require(host.receipts(operation, admission.prior_selection_sha256) ==
            (original_intent, selected_receipt, operation)
            and plan['selected_receipt_sha256'] == guard.plan_digest(selected_receipt),
            'original apply completion changed')
    unit_bytes = subprocess.check_output([guard.SYSTEMCTL, '--system', '--no-pager', 'cat', unit],
                                        env={'LC_ALL': 'C'}, timeout=4)
    review = guard.accepted_review(host.packet, guard.PROFILES[guard.PROFILE_NAME], guard.FIXED_STAGE, operator['review_sha256'])
    guard.require(guard.digest(unit_bytes) == host.unit_pin == review['parked_unit_sha256']
            and review['bundle_sha256'] == plan['bundle_sha256']
            and operator['review_sha256'] == plan['review_sha256']
            and guard.sealed_selection(review, allow_start_marker=True) == selection,
            'reviewed unit or sealed bundle changed before start')
    return selection, stage, raw, approval, confirmation, plan, host, principal, webhook


def _retained_idle_status(approval, consumed, now):
    """Conservatively classify a checked original, not authorize execution."""
    if consumed is not None:
        row = guard.record(consumed, ('schema', 'operation_id', 'intent_sha256', 'main_pid', 'invocation_id'))
        pid, invocation = row['main_pid'], row['invocation_id']
        guard.require(type(pid) is int and pid > 0 and type(invocation) is str
                and re.fullmatch(r'[0-9a-f]{32}', invocation) is not None
                and int(invocation, 16) != 0
                and consumed == guard.encode(guard.start_consumption(approval, pid, invocation)),
                'retained guarded start consumption ambiguous')
        return 'consumed'
    if now >= approval['launch_before']:
        return 'expired'
    # A future-dated or not-yet-consumed protected original is not harmless.
    return 'potentially_actionable'


def observe_retained_idle_start(locked_stream, expected_selection):
    """Read only while the existing caller holds the exclusive selector lock."""
    custody = guard.lock_identity(os.fstat(locked_stream.fileno()))
    guard.require(guard.lock_identity(guard.LOCK.lstat()) == custody, 'guarded selector lock custody changed')
    selection, stage, raw, approval, *_ = _retained_idle_original(custody)
    guard.require(guard.digest(selection) == expected_selection, 'guarded selection differs from prior')
    name = approval['operation_id'] + '.start-consumed.json'
    try:
        consumed = guard.read_record(guard.RECEIPTS, name)
    except FileNotFoundError:
        consumed = None
    status = _retained_idle_status(approval, consumed, time.time())
    guard.require(guard.lock_identity(guard.LOCK.lstat()) == custody and guard.read_record(guard.ROOT, 'selection.json') == selection
            and guard.read_record(stage, guard.MARKER) == raw, 'retained guarded start changed during observation')
    return status


def observe_guarded_preimage(locked_stream, prior, *, conditions, asserts):
    """Check bounded typed manager samples around the retained original read."""
    guard.require(type(prior) is GuardedEffectivePrior
                  and prior.unit == effective.UNIT
                  and prior.dropin_sha256 == guard.digest(guard.selector_dropin())
                  and guard.hash_pin(prior.selector_sha256)
                  and type(conditions) is type(asserts) is tuple,
                  'expected exact guarded effective preimage')
    guard.require(guard.bytes_pin(guard.DROPIN) == prior.dropin_sha256,
                  'guarded drop-in changed')
    first = effective._state()
    exe = '/usr/bin/python3'
    argv = [exe, '-I', '-S', '-B', str(guard.TOOL_PATH), '--profile', 'uif-idle',
            '--stage', guard.FIXED_STAGE, '--action', 'serve']
    guard.require(not first['NeedDaemonReload']
                  and (first['ActiveState'], first['SubState']) in
                      (('inactive', 'dead'), ('failed', 'failed'))
                  and (first['MainPID'], first['ControlPID'], first['Job']) == (0, 0, [0, '/'])
                  and (first['ExecMainStartTimestampMonotonic'], first['NRestarts']) ==
                      (prior.before_start_monotonic, prior.before_restarts)
                  and [(row[0], row[1], row[2]) for row in first['ExecStart']] == [(exe, argv, False)]
                  and effective._condition_shape(first['Conditions']) == conditions
                  and effective._condition_shape(first['Asserts']) == asserts,
                  'guarded effective manager preimage differs')
    status = observe_retained_idle_start(locked_stream, prior.selector_sha256)
    guard.require(status in ('expired', 'consumed'), 'retained guarded start may still be actionable')
    guard.require(effective._state() == first
                  and guard.bytes_pin(guard.DROPIN) == prior.dropin_sha256,
                  'guarded preimage changed during bounded observation')
    return True
