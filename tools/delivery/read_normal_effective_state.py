"""Bounded typed observation of normal Stokowski, never manager mutation."""

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import subprocess

from commissioned_normal_service import UNIT, check_normal_stage
from guarded_service_upgrade import hash_pin, require

BUSCTL = '/usr/bin/busctl'
BUS = 'org.freedesktop.systemd1'
OBJECT = '/org/freedesktop/systemd1/unit/stokowski_2dfoo_2eservice'
COMMAND_TYPE = 'a(sasbttttuii)'
FIELDS = (
    ('Unit', 'Id', 's'), ('Unit', 'NeedDaemonReload', 'b'),
    ('Unit', 'ActiveState', 's'), ('Unit', 'SubState', 's'),
    ('Unit', 'Job', '(uo)'), ('Unit', 'Conditions', 'a(sbbsi)'),
    ('Unit', 'Asserts', 'a(sbbsi)'), ('Service', 'ExecStart', COMMAND_TYPE),
    ('Service', 'MainPID', 'u'), ('Service', 'ControlPID', 'u'),
    ('Service', 'NRestarts', 'u'), ('Service', 'ExecMainStartTimestampMonotonic', 't'),
)


@dataclass(frozen=True)
class NormalEffectiveOriginal:
    """Independently retained outside-writer readback pins; not install authority."""
    stage: str
    expected_dropin_sha256: str
    observed_disk_dropin_sha256: str
    before_start_monotonic: int
    before_restarts: int
    conditions: tuple
    asserts: tuple


def _pairs(entries):
    result = {}
    for key, value in entries:
        require(key not in result, 'effective_manager_duplicate_response')
        result[key] = value
    return result


def _get(interface, name, signature):
    call = [BUSCTL, '--system', '--no-pager', '--json=short', '--timeout=5s', '--auto-start=no',
            'get-property', BUS, OBJECT, BUS + '.' + interface, name]
    raw = subprocess.check_output(call, stderr=subprocess.DEVNULL, timeout=7)
    require(type(raw) is bytes and 0 < len(raw) <= 32768, 'effective_manager_response_bound')
    value = json.loads(raw, object_pairs_hook=_pairs)
    require(type(value) is dict and value.keys() == {'type', 'data'} and value['type'] == signature,
            'effective_manager_property_signature')
    return value['data']


def _condition_shape(rows):
    require(type(rows) is list and len(rows) <= 64 and all(type(row) is list and len(row) == 5
            and tuple(map(type, row)) == (str, bool, bool, str, int) and row[4] in (-1, 0, 1)
            for row in rows), 'effective_manager_conditions_shape')
    return tuple(tuple(row[:4]) for row in rows)


def _state():
    values = {name: _get(iface, name, signature) for iface, name, signature in FIELDS}
    require(values['Id'] == UNIT and type(values['NeedDaemonReload']) is bool
            and all(type(values[k]) is str for k in ('ActiveState', 'SubState'))
            and all(type(values[k]) is int and values[k] >= 0 for k in
                    ('MainPID', 'ControlPID', 'NRestarts', 'ExecMainStartTimestampMonotonic')),
            'effective_manager_scalar_shape')
    job = values['Job']
    require(type(job) is list and len(job) == 2 and type(job[0]) is int and job[0] >= 0
            and type(job[1]) is str and job[1].startswith('/')
            and (job[0] != 0 or job[1] == '/'), 'effective_manager_job_shape')
    for column in ('Conditions', 'Asserts'):
        _condition_shape(values[column])
    exe = values['ExecStart']
    require(type(exe) is list and len(exe) <= 16 and all(type(row) is list and len(row) == 10
            and type(row[0]) is str and type(row[1]) is list
            and all(type(x) is str for x in row[1]) and type(row[2]) is bool
            and all(type(x) is int for x in row[3:]) for row in exe),
            'effective_manager_execstart_shape')
    return values


def valid_predicate_pins(rows):
    return (type(rows) is tuple and len(rows) <= 64
            and all(type(row) is tuple and tuple(map(type, row)) == (str, bool, bool, str)
                    for row in rows))


def validate_observation_original(original):
    """Check retained manager input types before any dependent filesystem effect."""
    require(type(original) is NormalEffectiveOriginal
            and hash_pin(original.expected_dropin_sha256) and hash_pin(original.observed_disk_dropin_sha256)
            and all(type(x) is int and x >= 0 for x in
                    (original.before_start_monotonic, original.before_restarts))
            and valid_predicate_pins(original.conditions) and valid_predicate_pins(original.asserts),
            'expected exact normal observation inputs')


def observe_normal_effective(original):
    """No load/start/reload, no path/argv output, no current installer conclusion."""
    validate_observation_original(original)
    stage = check_normal_stage(original.stage)

    def result(status):
        return {'status': status, 'unit': UNIT, 'native_startup_verified': False,
                'installation_accepted': False}
    if original.observed_disk_dropin_sha256 != original.expected_dropin_sha256:
        return result('disk_release_differs')
    try:
        first = _state()
        if _state() != first:
            return result('manager_observation_changed')
    except (OSError, ValueError, subprocess.SubprocessError):
        return result('manager_readback_unavailable')

    if first['NeedDaemonReload']:
        return result('disk_matches_manager_reload_needed')
    exe = str(PurePosixPath(stage) / 'venv/bin/python3.12')
    argv = [exe, '-I', '-S', '-B', str(PurePosixPath(stage) / 'launch.py')]
    commands = [(row[0], row[1], row[2]) for row in first['ExecStart']]
    if (commands != [(exe, argv, False)]
            or _condition_shape(first['Conditions']) != original.conditions
            or _condition_shape(first['Asserts']) != original.asserts):
        return result('disk_matches_effective_manager_differs')
    manager_state = (first['ActiveState'], first['SubState'])
    if (manager_state not in {('inactive', 'dead'), ('failed', 'failed')}
            or (first['MainPID'], first['ControlPID'], first['Job']) != (0, 0, [0, '/'])):
        return result('effective_normal_not_stopped')
    if (first['ExecMainStartTimestampMonotonic'], first['NRestarts']) != (
            original.before_start_monotonic, original.before_restarts):
        return result('effective_normal_start_history_changed')
    return result('effective_normal_stopped_same_original_observation')
