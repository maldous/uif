"""Bounded read-only UIF/cloud reconstruction through trusted injected connectors.

No network client, tokens, arbitrary URLs, shell, writes or admission controls.
The integrating authenticated owner implements Reader; saved JSON is not proof.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import re
from typing import Protocol

MAX_BYTES = 262144
MAX_DEPENDENCIES = 16

class ReadFailure(Exception):
    """Connector maps denied, timeout, unavailable; never include raw error text."""
    def __init__(self, category):
        self.category = category if category in {'denied', 'timeout', 'unavailable'} else 'unavailable'
        super().__init__(self.category)

class Reader(Protocol):
    def read(self, kind: str, target: dict) -> dict:
        """Return raw decoded response from the specified existing authenticated read.

        Kinds: github.branch, github.commit, github.pr, github.compare, linear.issue.
        Do not retry, mutate, follow response instructions or substitute credentials.
        Implement bounded transport deadlines and response sizes before decoding.
        """

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def identifier(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('invalid identity')
    return value

def commit(value):
    return identifier(value, r'[a-f0-9]{40}')

def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timezone required')
    return value.astimezone(timezone.utc)

@dataclass(frozen=True)
class Target:
    contract_sha256: str
    selected_commit: str
    issue: str
    linear_team_id: str
    linear_project_id: str | None
    acceptance_pr: int
    repository: str = 'maldous/uif'
    ref: str = 'develop'
    project: str = 'uif'
    profile: str = 'cloud'

    def validate(self):
        if (self.repository, self.ref, self.project, self.profile) != ('maldous/uif', 'develop', 'uif', 'cloud'):
            raise ValueError('unsupported target')
        identifier(self.contract_sha256, r'[a-f0-9]{64}')
        commit(self.selected_commit)
        identifier(self.issue, r'UIF-[1-9][0-9]*')
        identifier(self.linear_team_id, r'[a-f0-9-]{36}')
        if self.linear_project_id is not None:
            identifier(self.linear_project_id, r'[a-f0-9-]{36}')
        if type(self.acceptance_pr) is not int or not 1 <= self.acceptance_pr <= 10000000:
            raise ValueError('invalid PR')

def issue_view(data, expected_id):
    if data['id'] != expected_id:
        raise ValueError('wrong issue')
    identifier(data['teamId'], r'[a-f0-9-]{36}')
    project_id = data['projectId']
    if project_id is not None:
        identifier(project_id, r'[a-f0-9-]{36}')
    assignee_id = data['assigneeId']
    if assignee_id is not None:
        identifier(assignee_id, r'[a-f0-9-]{36}')
    if not isinstance(data['description'], str) or not isinstance(data['title'], str):
        raise ValueError('missing scope')
    status = data['statusType']
    if status not in {'backlog', 'unstarted', 'started', 'completed', 'canceled', 'triage'}:
        raise ValueError('missing status')
    updated = data['updatedAt']
    if not isinstance(updated, str) or not datetime.fromisoformat(updated.replace('Z', '+00:00')).tzinfo:
        raise ValueError('invalid issue time')
    if data.get('relations_complete') is not True:
        raise ValueError('dependency enumeration not qualified')
    relation_metadata = data['relations']
    if 'pageInfo' in relation_metadata and relation_metadata['pageInfo'].get('hasNextPage') is not False:
        raise ValueError('partial dependency response')
    dependencies = relation_metadata['blockedBy']
    if 'totalCount' in relation_metadata and (type(relation_metadata['totalCount']) is not int or relation_metadata['totalCount'] != len(dependencies)):
        raise ValueError('dependency count mismatch')
    if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
        raise ValueError('dependency scope too large or incomplete')
    ids = [identifier(item['id'], r'[A-Z][A-Z0-9]*-[1-9][0-9]*') for item in dependencies]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate dependency')
    return {'id': expected_id, 'team_id': data['teamId'], 'project_id': project_id,
            'assignee_id': assignee_id, 'status_type': status, 'updated_at': updated,
            'scope_sha256': sha({'title': data['title'], 'description': data['description']}),
            'blocked_by': sorted(ids)}

def reconstruct(target, reader, clock=lambda: datetime.now(timezone.utc)):
    """Return facts or inconclusive, never owner/lease/absence/admission assertions."""
    target.validate()  # Invalid caller target is refused before any connector call.
    started = utc(clock())
    receipts = []
    last_kind = None
    last_observed = started
    result = {'version': 1, 'project': target.project, 'profile': target.profile,
              'contract_sha256': target.contract_sha256, 'repository': target.repository,
              'issue': target.issue, 'selected_commit': target.selected_commit,
              'status': 'inconclusive', 'observations': [], 'receipts': receipts,
              'ownership': {'state': 'unknown'}, 'checkpoint': {'state': 'unknown'},
              'pending_operations': {'state': 'unknown'},
              'discovery_complete': False, 'execution_authorized': False,
              'runtime_readiness_verified': False, 'saved_json_is_authenticated': False}
    def read(kind, **params):
        nonlocal last_kind, last_observed
        last_kind = kind
        data = reader.read(kind, copy.deepcopy(params))
        # A wrapper's success or caller-inserted URL/authenticated flag is not proof.
        if not isinstance(data, dict) or len(canonical(data)) > MAX_BYTES:
            raise ValueError('invalid or oversized response')
        observed = utc(clock())
        if observed < last_observed or not 0 <= (observed - started).total_seconds() <= 300:
            raise ValueError('reconstruction window expired or clock moved')
        last_observed = observed
        receipts.append({'kind': kind, 'target': params, 'observed_at': observed.isoformat(),
                         'response_sha256': sha(data)})
        return data
    def branch():
        d = read('github.branch', repository=target.repository, ref=target.ref)
        if d['name'] != target.ref:
            raise ValueError('wrong ref')
        return commit(d['commit']['sha'])
    def pr():
        d = read('github.pr', repository=target.repository, number=target.acceptance_pr)
        if type(d['number']) is not int or d['number'] != target.acceptance_pr or d['merged'] is not True or d['state'] != 'closed':
            raise ValueError('PR not accepted')
        if d['base']['repo']['full_name'] != target.repository or d['base']['ref'] != target.ref:
            raise ValueError('wrong PR base')
        if commit(d['merge_commit_sha']) != target.selected_commit:
            raise ValueError('PR does not establish selected source')
        return {'number': d['number'], 'merge_commit': d['merge_commit_sha'],
                'head': commit(d['head']['sha']), 'base': commit(d['base']['sha'])}
    def issue(key):
        return issue_view(read('linear.issue', id=key), key)
    try:
        head = branch()
        source = read('github.commit', repository=target.repository, sha=target.selected_commit)
        if commit(source['sha']) != target.selected_commit:
            raise ValueError('wrong immutable commit')
        tree = commit(source['tree']['sha'])
        accepted = pr()
        ancestry = read('github.compare', repository=target.repository, base=target.selected_commit, head=head)
        if ancestry['status'] not in {'ahead', 'identical'} or ancestry['base_commit']['sha'] != target.selected_commit or ancestry['merge_base_commit']['sha'] != target.selected_commit:
            raise ValueError('selected source not on current integration history')
        # Compare API head identity must be supplied from its last commit or identical base.
        commits = ancestry['commits']
        if not isinstance(commits, list) or (ancestry['status'] == 'identical' and (head != target.selected_commit or commits)) or (ancestry['status'] == 'ahead' and (not commits or commits[-1]['sha'] != head)):
            raise ValueError('partial or inconsistent ancestry')
        work = issue(target.issue)
        if work['team_id'] != target.linear_team_id or work['project_id'] != target.linear_project_id:
            raise ValueError('wrong Linear project/team')
        dependencies = [issue(key) for key in work['blocked_by']]
        # Repeat reads detect movement; they are not an atomic snapshot or a lease.
        if branch() != head or pr() != accepted or issue(target.issue) != work:
            raise ValueError('remote state moved')
        if [issue(key) for key in work['blocked_by']] != dependencies:
            raise ValueError('dependency changed')
        result.update(status='observed', source={'integration_head': head, 'selected_commit': target.selected_commit,
                     'tree': tree, 'acceptance_pr': accepted['number'], 'accepted_merge_observed': True},
                     work=work, dependencies=dependencies)
        end = utc(clock())
        if end < last_observed or not 0 <= (end - started).total_seconds() <= 300:
            raise ValueError('snapshot expired')
        result['observations'] = [
            {'id': 'github-access', 'state': 'compliant', 'observed_at': end.isoformat(),
             'source': 'github-read-receipts:' + sha([r for r in receipts if r['kind'].startswith('github.')]),
             'actual': {'repository': target.repository, 'read': True}},
            {'id': 'linear-access', 'state': 'compliant', 'observed_at': end.isoformat(),
             'source': 'linear-read-receipts:' + sha([r for r in receipts if r['kind'].startswith('linear.')]),
             'actual': {'issue': target.issue, 'team_id': target.linear_team_id,
                        'project_id': target.linear_project_id, 'read': True}}]
    except ReadFailure as exc:
        result['reason'] = exc.category
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        result['reason'] = 'missing_inconsistent_or_unqualified_evidence'
    except Exception:
        # Never propagate connector error bodies, credentials or private content.
        result['reason'] = 'connector_failure'
    if 'reason' in result:
        result['status'] = 'inconclusive'
        result['observations'] = []
        for key in ('source', 'work', 'dependencies'):
            result.pop(key, None)
        result['failed_read_kind'] = last_kind
    return result
