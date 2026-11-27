"""Read-only old guarded-generation assessment for normal selection.

Caller is the existing protected writer, inside its already-held exclusive
selector lock. This component never grants admission and never takes a lock.
"""
import guarded_service_upgrade as guard
import read_normal_effective_state as effective
from commit_commissioned_normal_selection import GuardedEffectivePrior


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
    status = guard.observe_retained_idle_start(locked_stream, prior.selector_sha256)
    guard.require(status in ('expired', 'consumed'), 'retained guarded start may still be actionable')
    guard.require(effective._state() == first
                  and guard.bytes_pin(guard.DROPIN) == prior.dropin_sha256,
                  'guarded preimage changed during bounded observation')
    return True
