"""Detached projection of a verified fresh assess terminal into the native phase."""
from hashlib import sha256
import os
import stat

from ..effect_authority import Lease
from ..handoff_store import decode, require, wire
from .fresh_assessment_dispatch import observe_fresh_assessment_dispatch
from .fresh_assessment_terminal import observe_fresh_assessment_terminal
from .native_composition import _clock_file_identity, _publish_private_original


def project_fresh_assessment_phase(composition, owner, issue, run, scope_sha256, lease: Lease, *,
                                   raw_report: bytes, rendered_body: str, attestation_request_id: str,
                                   verify_first_dispatch_applicability, verify_original_prerequisites):
    """Write once for one independently verified fresh original, never replace.

    An exclusive approved coordinator and exact fresh launch/provenance are
    prerequisites carried by the existing trusted callbacks. There is no
    runtime wiring or transition, and no replacement/repair for an existing
    phase, even when bytes would match.
    """
    def read_dispatch():
        return observe_fresh_assessment_dispatch(
            composition, owner, issue, run, scope_sha256, lease,
            verify_first_dispatch_applicability=verify_first_dispatch_applicability)

    original = read_dispatch()
    def read_terminal():
        return observe_fresh_assessment_terminal(
            composition, issue, run, scope_sha256, lease, raw_report=raw_report,
            rendered_body=rendered_body, attestation_request_id=attestation_request_id,
            verify_original_prerequisites=verify_original_prerequisites)

    terminal = read_terminal()
    outcome = decode(terminal, limit=8192)
    require(outcome['issue_id'] == issue.id and outcome['run'] == run
            and outcome['scope_sha256'] == scope_sha256 and outcome['previous'] == original.attempt_id
            and outcome['state'] == 'assess' and outcome['successor_state'] == 'investigate',
            'assessment_phase_terminal_binding')
    grant = composition.assignment(owner.project_name, issue.id, scope_sha256)
    workspace = composition.verify_workspace(owner, issue, grant)
    raw = wire({
        'issue_id': issue.id, 'run': run, 'scope_sha256': scope_sha256,
        'state': 'assess', 'session_id': original.attempt_id,
        'phase': 'complete', 'event': 'complete', 'target': 'investigate', 'report_posted': True,
    })
    require(read_dispatch() == original and composition.assignment(owner.project_name, issue.id, scope_sha256) == grant
            and composition.verify_workspace(owner, issue, grant) == workspace, 'assessment_phase_custody_changed')
    binding = dict(composition.installation()['assignments'][issue.id]['workspace_binding'])
    require(binding['path'] == str(workspace), 'assessment_phase_workspace_custody')
    parent = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(parent)
        stable_parent = _clock_file_identity(before)[:5]
        require((before.st_dev, before.st_ino) == (binding['device'], binding['inode'])
                and before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o700
                and stable_parent == _clock_file_identity(workspace.lstat())[:5],
                'assessment_phase_workspace_custody')
        require(composition.remote.clock().timestamp() < lease.expires_at, 'assessment_phase_expired')
        try:
            os.mkdir('.stokowski', mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        fd = os.open('.stokowski', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            phase = os.fstat(fd)
            stable_phase = _clock_file_identity(phase)[:5]
            require(phase.st_uid == os.geteuid() and stat.S_IMODE(phase.st_mode) == 0o700,
                    'assessment_phase_directory_custody')

            def require_same_directories():
                require(stable_parent == _clock_file_identity(workspace.lstat())[:5]
                        and stable_phase == _clock_file_identity(
                            os.stat('.stokowski', dir_fd=parent, follow_symlinks=False))[:5],
                        'assessment_phase_workspace_changed')

            def check_before_publish():
                # The temporary file is durable but not visible as settlement.
                require(composition.remote.clock().timestamp() < lease.expires_at,
                        'assessment_phase_expired')
                require(read_dispatch() == original and read_terminal() == terminal
                        and composition.assignment(owner.project_name, issue.id, scope_sha256) == grant
                        and composition.installation()['assignments'][issue.id]['workspace_binding'] == binding,
                        'assessment_phase_custody_changed')
                require_same_directories()
                require(composition.remote.clock().timestamp() < lease.expires_at,
                        'assessment_phase_expired')

            require_same_directories()
            try:
                os.stat('settlement.json', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                require(False, 'assessment_phase_already_exists')
            _publish_private_original(fd, 'settlement.json', raw, prefix='.fresh-assessment-phase-',
                                      before_publish=check_before_publish)
            require_same_directories()
        finally:
            os.close(fd)
        os.fsync(parent)
        require(stable_parent == _clock_file_identity(workspace.lstat())[:5],
                'assessment_phase_workspace_changed')
    finally:
        os.close(parent)
    return sha256(terminal).hexdigest()
