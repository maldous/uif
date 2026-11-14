"""Reuse white-room reconstruction at an authenticated ownership boundary.

There is no JSON-to-authority shortcut. An integrating owner supplies the fixed
input provider, authenticated Reader and owner-scoped checkpoint saver. Registry
admission/effect code calls this object while holding its ownership lock. This
module neither dispatches work nor grants execution from a comparator exit code.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
from typing import Callable

from .check import digest, evaluate
from .remote_evidence import Target, reconstruct


class RecoveryHold(ValueError):
    """A precise recovery prerequisite is absent, stale or inconsistent."""


@dataclass(frozen=True)
class CheckpointRead:
    """Current graph registration and read-only saver; never a historical replay.

    A nonempty thread/namespace is required even for absence verification. For a
    resume, metadata is the exact registered uif_recovery record, including graph,
    schema/code pins, accepted/pending stages and original operation references.
    """
    saver: object
    thread_id: str
    namespace: str
    checkpoint_id: str | None = None
    wire_version: int | None = None
    metadata: dict | None = None

    def verify(self, expected, disposition):
        if not isinstance(self.thread_id, str) or not self.thread_id or not isinstance(self.namespace, str):
            raise RecoveryHold('checkpoint_registration_missing')
        # Reading by old checkpoint ID would hide a newer superseding tuple.
        config = {'thread_id': self.thread_id, 'checkpoint_ns': self.namespace}
        record = self.saver.get_tuple({'configurable': config})
        if disposition == 'none':
            if record is not None or self.checkpoint_id is not None:
                raise RecoveryHold('checkpoint_absence_unverified')
            return {'disposition': 'none', 'absence_verified': True}
        if disposition != 'resume' or record is None or not self.checkpoint_id or not isinstance(self.metadata, dict):
            raise RecoveryHold('checkpoint_disposition_unqualified')
        origin_owner = getattr(expected, 'origin_owner', None)
        origin_generation = getattr(expected, 'origin_generation', None)
        transferring = origin_owner is not None or origin_generation is not None
        if transferring and (not isinstance(origin_owner, str) or not origin_owner
                             or type(origin_generation) is not int
                             or not 1 <= origin_generation < expected.generation):
            raise RecoveryHold('checkpoint_transfer_binding')
        # These predecessor fields are supplied by Registry from its fenced
        # handoff-pending claim, never from task JSON. Keep the original saver
        # metadata untouched while binding its use to the successor claim.
        allowed_owners = {(expected.owner, expected.generation)}
        if transferring:
            allowed_owners.add((origin_owner, origin_generation))
        selected_owner = (self.metadata.get('owner'), self.metadata.get('generation'))
        if type(selected_owner[1]) is not int or selected_owner not in allowed_owners:
            raise RecoveryHold('checkpoint_task_binding')
        bound = {
            'linear_workspace_id': expected.key.workspace_id,
            'linear_project_id': expected.key.project_id,
            'linear_issue_id': expected.key.issue_id,
            'owner': selected_owner[0], 'generation': selected_owner[1],
            'repository': expected.source['repository'],
            'source_base': expected.source['base'],
            'source_head': expected.source['head'],
            'source_tree': expected.source['tree'],
        }
        if any(self.metadata.get(k) != v for k, v in bound.items()):
            raise RecoveryHold('checkpoint_task_binding')
        for key in ('graph_sha256', 'schema_sha256', 'code_sha256', 'receipt_sha256'):
            value = self.metadata.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise RecoveryHold('checkpoint_registration_pin')
        completed, pending = self.metadata.get('completed'), self.metadata.get('pending')
        if (not isinstance(completed, list) or not isinstance(pending, list)
                or any(not isinstance(x, str) or not x for x in completed + pending)
                or len(set(completed + pending)) != len(completed + pending)
                or not isinstance(self.metadata.get('original_operations'), list)):
            raise RecoveryHold('checkpoint_stage_binding')
        if type(self.wire_version) is not int or self.wire_version < 1:
            raise RecoveryHold('checkpoint_wire_version')
        if (record.config['configurable'] != {**config, 'checkpoint_id': self.checkpoint_id}
                or record.checkpoint['id'] != self.checkpoint_id
                or type(record.checkpoint['v']) is not int
                or record.checkpoint['v'] != self.wire_version
                or digest(record.metadata.get('uif_recovery')) != digest(self.metadata)
                or record.pending_writes != []):
            raise RecoveryHold('checkpoint_incompatible_or_pending')
        return {'disposition': 'resume', 'checkpoint_id': self.checkpoint_id,
                'metadata_sha256': digest(self.metadata),
                'origin_owner': bound['owner'], 'origin_generation': bound['generation'],
                'target_owner': expected.owner, 'target_generation': expected.generation,
                'completed': list(completed), 'pending': list(pending)}


@dataclass(frozen=True)
class RecoveryInputs:
    """Privately acquired inputs from the actual owner and reviewed profile."""
    contract: dict
    evidence: dict
    target: Target
    reader: object
    checkpoint: CheckpointRead


class WhiteRoomVerifier:
    def __init__(self, inputs: Callable, clock=lambda: datetime.now(timezone.utc)):
        self.inputs = inputs
        self.clock = clock

    def __call__(self, expected):
        from uif.quota_handoff import RecoveryReceipt

        try:
            supplied = self.inputs(expected)
            if not isinstance(supplied, RecoveryInputs) or supplied.target.scope is None:
                raise RecoveryHold('reviewed_project_scope_missing')
            target = supplied.target
            target.validate()
            if (target.scope.workspace_id, target.scope.project_id, target.issue_uuid,
                    target.repository, target.selected_commit, target.profile) != (
                    expected.key.workspace_id, expected.key.project_id, expected.key.issue_id,
                    expected.source['repository'], expected.source['base'],
                    'foo' if expected.lane == 'local' else 'cloud'):
                raise RecoveryHold('reconstruction_target_mismatch')
            contract, evidence = copy.deepcopy(supplied.contract), copy.deepcopy(supplied.evidence)
            if target.contract_sha256 != digest(contract):
                raise RecoveryHold('declaration_drift')
            remote = reconstruct(target, supplied.reader, self.clock)
            if remote['status'] != 'observed':
                raise RecoveryHold('remote_reconstruction_inconclusive')
            if remote['work']['status_type'] not in {'unstarted', 'started', 'backlog'}:
                raise RecoveryHold('issue_not_eligible')
            if any(item['status_type'] != 'completed' for item in remote['dependencies']):
                raise RecoveryHold('dependency_incomplete')
            context = evidence['recovery']
            binding = {
                'repository': expected.source['repository'], 'issue': target.issue,
                'owner': expected.owner, 'ownership_epoch': expected.generation,
                'branch': expected.source['branch'],
                'candidate_head': expected.source['head'], 'candidate_tree': expected.source['tree'],
                'linear_workspace_id': expected.key.workspace_id,
                'linear_project_id': expected.key.project_id,
                'linear_issue_id': expected.key.issue_id,
                'accepted_commit': expected.source['base'],
            }
            if any(context.get(key) != value for key, value in binding.items()):
                raise RecoveryHold('recovery_context_mismatch')
            checkpoint = supplied.checkpoint.verify(expected, context['checkpoint']['disposition'])
            if checkpoint['disposition'] == 'resume':
                # This is a normalized target-resume observation after verifying
                # the immutable origin. It never writes the original checkpoint.
                evidence['recovery']['checkpoint'] = {
                    'disposition': 'resume', 'commit': expected.source['base'],
                    'issue': target.issue, 'owner': expected.owner,
                    'epoch': expected.generation, 'compatibility_verified': True,
                    'receipt_source': 'verified-latest-checkpoint:' + checkpoint['metadata_sha256'],
                }
            else:
                evidence['recovery']['checkpoint'] = {'disposition': 'none', 'absence_verified': True}
            # Replace only the access observations actually acquired this time.
            # All task-specific dependencies still require qualified declarations.
            evidence['observations'] = [item for item in evidence['observations']
                                        if item['id'] not in {'github-access', 'linear-access'}] + remote['observations']
            evidence['recovery']['remote_read_at'] = remote['observations'][0]['observed_at']
            result = evaluate(contract, evidence, target.profile, target.project, self.clock())
            if result['preflight_passed'] is not True:
                raise RecoveryHold('white_room_prerequisite_failed')
            report = {'white_room': result, 'remote': remote, 'checkpoint': checkpoint,
                      'evidence_ref': expected.evidence_ref}
            return RecoveryReceipt(report_sha256=digest(report), evidence_ref=expected.evidence_ref,
                                   checked_at=self.clock().timestamp())
        except RecoveryHold:
            raise
        except Exception:
            # Private backend errors and payloads do not cross this boundary.
            raise RecoveryHold('recovery_evidence_unavailable') from None
