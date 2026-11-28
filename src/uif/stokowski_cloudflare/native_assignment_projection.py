"""Pure candidate projection from inputs already verified by existing roles."""
from dataclasses import asdict
from pathlib import Path

from ..effect_authority import AuthorityIdentity, pinned_sha
from ..handoff_store import decode, require, wire
from .remote_authority import NativeAssignment, unstarted_transition


def project_clean_assignment(grant: NativeAssignment, identity: AuthorityIdentity, *,
                             review_of: dict[str, str], workspace_root: Path,
                             issue_identifier: str, observation: dict) -> bytes:
    """Return canonical candidate assignment fragment, not authorization.

    The existing owner must independently verify grant/identity custody, the
    exact matching review map and final-location observation. No I/O, issue
    claim, installation, or predecessor reconstruction is performed here.
    """
    entry = _workspace_entry(grant, identity, review_of, workspace_root, issue_identifier, observation)
    entry['workspace_binding']['original_claim_id'] = grant.claim(identity).request_id
    return wire({grant.scope.key.issue_id: entry})


def project_unstarted_assignment(grant: NativeAssignment, identity: AuthorityIdentity, *,
                                 installation_sha256: str, submitted_handoff: bytes,
                                 review_of: dict[str, str], workspace_root: Path,
                                 issue_identifier: str, observation: dict) -> bytes:
    """Produce a candidate for the existing owner from its retained recipient request.

    The owner supplies the submitted body, the commissioned realm and a
    final-location checkout observation under exclusive custody. The original
    still needs the recipient's authenticated readback.
    """
    entry = _workspace_entry(grant, identity, review_of, workspace_root, issue_identifier, observation)
    body = decode(submitted_handoff, limit=64_000)
    _, transition = unstarted_transition(grant, identity, installation_sha256, body)
    entry['workspace_binding']['original_transition'] = transition
    entry['recipient_original'] = {'installation_sha256': installation_sha256, 'handoff': body}
    return wire({grant.scope.key.issue_id: entry})


def _workspace_entry(grant, identity, review_of, workspace_root, issue_identifier, observation):
    require(type(grant) is NativeAssignment and type(identity) is AuthorityIdentity
            and grant.previous is None and grant.previous_completion_sha256 is None,
            'authority_clean_candidate_grant')
    require(type(review_of) is dict and all(
        r in grant.attempts and a in grant.attempts and r != a and a not in review_of
        for r, a in review_of.items()) and grant.attempts[0] not in review_of,
        'authority_clean_candidate_review')
    require(workspace_root.is_absolute() and '..' not in workspace_root.parts
            and type(issue_identifier) is str and issue_identifier not in ('', '.', '..')
            and Path(issue_identifier).name == issue_identifier,
            'authority_clean_candidate_workspace')
    target = workspace_root / issue_identifier
    require(type(observation) is dict and observation.keys() ==
            {'path', 'device', 'inode', 'git_config_sha256', 'prepared_tree_sha256'}
            and observation['path'] == str(target)
            and type(observation['device']) is int and observation['device'] >= 0
            and type(observation['inode']) is int and observation['inode'] > 0,
            'authority_clean_candidate_observation')
    pinned_sha(observation['git_config_sha256'])
    pinned_sha(observation['prepared_tree_sha256'])
    return {
        'scope_sha256': grant.scope_sha256, 'source': asdict(grant.source),
        'reconstruction_sha256': grant.reconstruction_sha256,
        'attempts': list(grant.attempts), 'review_of': dict(review_of),
        'workspace_binding': {field: observation[field] for field in ('path', 'device', 'inode')},
        'git_config_sha256': observation['git_config_sha256'],
        'prepared_tree_sha256': observation['prepared_tree_sha256'],
    }
