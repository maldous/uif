"""Read-only white-room evidence comparison. Never grants execution authority."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from subprocess import SubprocessError

STATES = {'compliant', 'drifted', 'failed', 'blocked', 'unknown', 'not-applicable'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must have a timezone')
    return result

def index(items):
    result = {}
    for item in items:
        key = item['id']
        if not isinstance(key, str) or not key or key in result:
            raise ValueError('missing or duplicate identity')
        result[key] = item
    return result

def evaluate(contract, evidence, profile, project, now=None, *, checkout=None):
    """Compare caller-supplied evidence; freshness is not authenticity or a lease."""
    now = now or datetime.now(timezone.utc)
    for document in (contract, evidence):
        version = document['version']
        if type(version) is not int or version != 1:
            raise ValueError('unsupported version')
    if profile not in ('cloud', 'foo') or project not in contract['projects']:
        raise ValueError('unknown profile or project')
    repository = contract['projects'][project].get('repository')
    import re
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('unqualified repository mapping')
    requirements = index(contract['requirements'])
    for req in requirements.values():
        if type(req['max_age_seconds']) is not int or not 1 <= req['max_age_seconds'] <= 86400:
            raise ValueError('invalid freshness bound')
    observations = index(evidence['observations'])
    if set(observations) - set(requirements):
        raise ValueError('unknown observation identity')
    rows = []
    def row(key, state, reason):
        rows.append({'id': key, 'state': state, 'reason': reason})
    if evidence.get('contract_sha256') != digest(contract):
        row('contract-binding', 'drifted', 'evidence is not bound to this exact contract')
    if evidence.get('profile') != profile or evidence.get('project') != project:
        row('target-binding', 'drifted', 'evidence targets another executor or project')
    selected = contract['projects'][project]
    discovery = selected.get('discovery_complete', {})
    if not isinstance(discovery, dict) or any(type(value) is not bool for value in discovery.values()):
        raise ValueError('invalid discovery completion map')
    if discovery.get(profile) is not True:
        row('discovery', 'unknown', 'applicable dependency discovery is incomplete')
    for key, req in requirements.items():
        if profile not in req['profiles'] or project not in req['projects']:
            continue
        obs = observations.get(key)
        if not obs:
            row(key, 'unknown', 'no observation')
            continue
        state = obs.get('state')
        if state not in STATES:
            raise ValueError('invalid observation state')
        try:
            age = (now - timestamp(obs['observed_at'])).total_seconds()
        except (KeyError, ValueError, TypeError, AttributeError):
            row(key, 'unknown', 'missing or invalid observation time')
            continue
        if age < 0 or age > req['max_age_seconds']:
            row(key, 'unknown', 'future or expired evidence')
        elif not obs.get('source'):
            row(key, 'unknown', 'missing evidence source')
        elif state == 'not-applicable':
            row(key, 'blocked', 'required dependency cannot be waived by observation')
        elif state != 'compliant':
            row(key, state, 'dependency did not pass')
        elif req.get('expected') is None:
            row(key, 'unknown', 'declaration is not yet qualified')
        elif digest(obs.get('actual')) != digest(req['expected']):
            row(key, 'drifted', 'observed configuration differs from declaration')
        else:
            row(key, 'compliant', 'fresh supplied evidence matches declaration')
    # Omission never turns unknown checkpoint/ownership/effects into permission.
    context = evidence.get('recovery', {})
    if not isinstance(context, dict):
        raise ValueError('recovery evidence must be an object')
    if checkout is not None:
        selected_commit = context.get('selected_commit')
        registered = [item for item in checkout['worktrees'] if item['path'] == checkout['root']]
        if not selected_commit:
            row('checkout', 'unknown', 'evidence has no selected commit for the local checkout')
        elif (checkout['head'] != selected_commit or len(registered) != 1
              or registered[0]['head'] != checkout['head']
              or registered[0]['branch_ref'] != 'refs/heads/' + checkout['branch']):
            row('checkout', 'drifted', 'local HEAD or worktree registration differs from the selected source')
        else:
            row('checkout', 'compliant', 'local HEAD and worktree registration agree with the selected commit')
    needed = ('repository', 'accepted_commit', 'selected_commit', 'issue', 'owner',
              'ownership_epoch', 'authoritative_owner', 'authoritative_epoch',
              'remote_read_at', 'ownership_expires_at', 'workspace_isolated',
              'recoverable_work_preserved', 'pending_operations', 'checkpoint',
              'previous_owner_fenced', 'superseded', 'published_checkpoint')
    missing = [key for key in needed if key not in context]
    if missing:
        row('recovery', 'unknown', 'missing recovery fields: ' + ', '.join(missing))
    else:
        def require(ok, key, reason):
            if not ok:
                row(key, 'blocked', reason)
        require(context['repository'] == selected['repository'], 'repository', 'repository mismatch')
        import re
        valid_commit = isinstance(context['accepted_commit'], str) and bool(re.fullmatch('[a-f0-9]{40}', context['accepted_commit']))
        require(valid_commit and context['selected_commit'] == context['accepted_commit'], 'source', 'selected source is not current accepted source')
        valid_identity = all(isinstance(context[key], str) and context[key].strip()
                             for key in ('issue', 'owner'))
        epoch = context['ownership_epoch']
        require(valid_identity and type(epoch) is int and epoch >= 1, 'identity',
                'expected nonempty issue and owner strings and a positive integer epoch')
        require(context['owner'] == context['authoritative_owner'] and type(context['authoritative_epoch']) is int and context['ownership_epoch'] == context['authoritative_epoch'], 'owner', 'ownership does not match authoritative record')
        try:
            fresh = 0 <= (now - timestamp(context['remote_read_at'])).total_seconds() <= 300
            live = timestamp(context['ownership_expires_at']) > now
        except (ValueError, TypeError, AttributeError):
            fresh = live = False
        require(fresh and live, 'ownership-time', 'remote reconstruction stale or lease expired')
        for flag in ('workspace_isolated', 'recoverable_work_preserved', 'previous_owner_fenced'):
            require(context[flag] is True, flag, 'required recovery condition not established')
        require(context['superseded'] is False, 'superseded', 'old task was superseded or disposition unknown')
        require(context['pending_operations'] == [] and isinstance(context['pending_operations'], list), 'operations', 'reconcile original uncertain operations before replacement')
        checkpoint = context['checkpoint']
        if not isinstance(checkpoint, dict):
            require(False, 'checkpoint', 'checkpoint disposition must be explicit')
        elif checkpoint.get('disposition') == 'none':
            require(checkpoint.get('absence_verified') is True, 'checkpoint', 'absence must be verified')
        elif checkpoint.get('disposition') == 'resume':
            expected = {'commit': context['accepted_commit'], 'issue': context['issue'], 'owner': context['owner'], 'epoch': context['ownership_epoch']}
            require(all(digest(checkpoint.get(k)) == digest(v) for k, v in expected.items()) and checkpoint.get('compatibility_verified') is True and bool(checkpoint.get('receipt_source')), 'checkpoint', 'checkpoint identity/schema compatibility not verified')
        else:
            require(False, 'checkpoint', 'migration or supersession must be reconciled before resume')
        published = context['published_checkpoint']
        expected_publication = {k: context[k] for k in ('repository', 'issue', 'owner', 'ownership_epoch')}
        expected_publication['commit'] = context['accepted_commit']
        try:
            publication_fresh = 0 <= (now - timestamp(published['observed_at'])).total_seconds() <= 300
        except (KeyError, ValueError, TypeError, AttributeError):
            publication_fresh = False
        require(isinstance(published, dict) and published.get('remote_verified') is True and all(digest(published.get(k)) == digest(v) for k, v in expected_publication.items()) and bool(published.get('source')) and publication_fresh, 'publication', 'remote checkpoint identity or freshness not verified')
    return {'version': 1, 'profile': profile, 'project': project, 'contract_sha256': digest(contract),
            'evidence_sha256': digest(evidence), 'checked_at': now.isoformat(), 'checks': rows,
            'preflight_passed': bool(rows) and all(r['state'] == 'compliant' for r in rows),
            'execution_authorized': False, 'runtime_readiness_verified': False}

def reject_constant(value):
    raise ValueError('nonfinite JSON number')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--profile', choices=['cloud', 'foo'], required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--checkout', type=Path, help='compare the selected commit to this local Git checkout')
    args = parser.parse_args()
    try:
        contract = json.loads(args.contract.read_text(), parse_constant=reject_constant)
        evidence = json.loads(args.evidence.read_text(), parse_constant=reject_constant)
        checkout = None
        if args.checkout is not None:
            from inventory import inventory
            checkout = inventory(args.checkout)
        result = evaluate(contract, evidence, args.profile, args.project, checkout=checkout)
    except (ValueError, KeyError, TypeError, OSError, SubprocessError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'preflight_passed': False}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['preflight_passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
