#!/usr/bin/env python3
"""Offline Foo configuration qualification. No network, subprocess or real secrets."""
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config/windmill'
TRANSPORT = {'workspace': 'offmenu', 'path': 'u/admin/foo_root_jobs',
             'endpoint': 'https://windmill.aldous.info/api/mcp/w/offmenu/mcp',
             'ssh_account': 'offmenu-wm', 'protocol': 'foo-root-exec-v1'}


OBSERVED_DIGEST = '5cbecd6b800d48b8c4e009e3d0f9ef02009a184bdfc79d3b7646a4819f9049c9'
DISPOSITIONS_DIGEST = '11561e26b25b2aec4a1a482f801828f4db44b94f9cd4005c1f1059bafbeeb611'
REQUIRED_BLOCKERS = ['Approved source/config custody and tested restore absent', 'Deployed runner source/schema and host protocol pins unqualified', 'Native-job and all consumer reconciliation outstanding', 'New credential destination and persistent permissions require confirmation', 'Fresh-child identity, same-job status and terminal receipt not verified']


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((CONFIG / name).read_text())


def validate():
    """Validate checked-in identities and exhaustive baseline dispositions."""
    m, observed, dispositions = load('desired.json'), load('observed-2026-11-09.json'), load('dispositions.json')
    if object_digest(observed) != OBSERVED_DIGEST or object_digest(dispositions) != DISPOSITIONS_DIGEST:
        raise ValueError('fixed observation/evidence pin mismatch')
    if m['owner_repository'] != 'maldous/uif' or m['blockers'] != REQUIRED_BLOCKERS:
        raise ValueError('ownership or qualification gaps drift')
    if m['version'] != 1 or m['preserve_transport'] != TRANSPORT:
        raise ValueError('transport identity drift')
    if m['workspace'] != {'id': None, 'display_name': 'Foo', 'state': 'requires-explicit-target'}:
        raise ValueError('target identity drift')
    if m['credential'] != {'reference': 'u/admin/foo_ssh_key_20261027', 'destination': 'existing-offmenu-only', 'new_destination_authorized': False}:
        raise ValueError('credential destination drift')
    if m['runner'] != {'path': 'u/admin/foo_root_jobs', 'principal': 'foo-runner',
                       'permissions': ['execute:foo-root-jobs', 'read:own-job-status'],
                       'privilege_state': 'proposed-not-granted'}:
        raise ValueError('runner permissions drift')
    if m['dependencies'] != {'python': '3.12-standard-library', 'external_packages': []}:
        raise ValueError('dependency drift')
    if m['project_schedules'] or m['live_recreation_verified'] is not False:
        raise ValueError('live state claim or project schedule')
    if m['source']['mock_path'] != 'tools/windmill/white_room.py' or m['schema']['path'] != 'config/windmill/request.schema.json':
        raise ValueError('artifact path drift')
    if m['source']['mock_sha256'] != digest(Path(__file__)) or m['schema']['sha256'] != digest(CONFIG / 'request.schema.json'):
        raise ValueError('source/schema pin mismatch')
    if m['source']['deployed_runner_sha256'] is not None or m['source']['host_protocol_sha256'] is not None:
        raise ValueError('live source qualification needs independent review')
    baseline = observed['scripts'] + observed['resources'] + observed['other_items']
    keys = [(x['type'], x['path']) for x in baseline]
    mapped = [(x['type'], x['path']) for x in dispositions['items']]
    if len(keys) != len(set(keys)) or len(mapped) != len(set(mapped)) or set(keys) != set(mapped):
        raise ValueError('inventory disposition coverage mismatch')
    archive = dispositions['archive_overlay']['path']
    for row in dispositions['items']:
        expected = 'hold-preserve'
        if row['path'] in ('u/admin/foo_root_jobs', 'u/admin/foo_ssh_key_20261027'):
            expected = 'retain-current-transport'
        elif row['type'] == 'schedule':
            expected = 'retain-disabled'
        elif row['path'] == archive:
            expected = 'retain-archived'
        if row['delete_authorized'] is not False or row['disposition'] != expected:
            raise ValueError('unsafe disposition')
    return m


def validate_request(request):
    if not isinstance(request, dict) or request.get('action') not in ('submit', 'status'):
        raise ValueError('invalid request')
    expected = {'action', 'job_id', 'probe'} if request['action'] == 'submit' else {'action', 'job_id'}
    if set(request) != expected or not isinstance(request['job_id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', request['job_id']):
        raise ValueError('invalid request')
    if request['action'] == 'submit' and request['probe'] != 'identity':
        raise ValueError('invalid probe')


class MockCredentialResolver:
    """Opaque synthetic handle only: no environment, key store or provider reads."""
    def resolve(self, reference):
        if reference != 'u/admin/foo_ssh_key_20261027':
            raise ValueError('unknown credential reference')
        return object()


class MockSSHTransport:
    """Synthetic host protocol response, not an SSH implementation."""
    def __init__(self):
        self.calls = 0

    def identity(self, credential):
        if credential is None:
            raise ValueError('missing mock handle')
        self.calls += 1
        return {'host': 'foo', 'protocol': 'foo-root-exec-v1', 'state': 'succeeded'}


class WhiteRoom:
    """Local SQLite model with durable same-job status and fail-closed retry.

    Pending rows deliberately stay uncertain after interruption. This does not
    claim to atomically commit a remote effect or implement live fencing.
    """
    def __init__(self, database, resolver=None, transport=None):
        self.db = sqlite3.connect(database, timeout=5)
        self.resolver = resolver or MockCredentialResolver()
        self.transport = transport or MockSSHTransport()
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
        self.db.commit()

    def close(self):
        self.db.close()

    def handle(self, request):
        validate_request(request)
        job = request['job_id']
        if request['action'] == 'submit':
            # Acquire the sole local writer before checking/admitting this ID.
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                previous = self.db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()
                if previous is None:
                    self.db.execute('INSERT INTO jobs VALUES (?,?)', (job, 'uncertain'))
            if previous is None:
                try:
                    reply = self.transport.identity(self.resolver.resolve('u/admin/foo_ssh_key_20261027'))
                    if reply != {'host': 'foo', 'protocol': 'foo-root-exec-v1', 'state': 'succeeded'}:
                        raise ValueError('mock identity mismatch')
                    with self.db:
                        self.db.execute('UPDATE jobs SET state=? WHERE id=?', ('succeeded', job))
                except Exception:
                    # Never emit provider exceptions, arbitrary payloads or handles.
                    raise ValueError('mock operation uncertain; inspect same job') from None
        state = self.db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()
        return {'job_id': job, 'state': state[0] if state else 'unknown',
                'evidence_kind': 'synthetic-only', 'live_recreation_verified': False}


def plan():
    m = validate()
    return {'mode': 'plan-only', 'runtime_mutations': [], 'live_recreation_verified': False,
            'target': m['workspace'], 'preserve_transport': TRANSPORT, 'blockers': m['blockers'],
            'steps': [
                'Reconcile live native jobs and all consumers under one migration owner.',
                'Preserve approved source/config with hashes and prove restore without reading denied source.',
                'Qualify deployed source/schema and protocol pins; retain current transport.',
                'Obtain action-time approval for new credential destination and persistent access.',
                'Create isolated test workspace through authorized admin flow; inject credentials externally.',
                'Verify fresh-child identity, same-job status and terminal receipt independently.',
                'Switch verified consumers only with rollback to preserved offmenu transport available.',
                'Reconcile residual contents; request exact confirmation for any irreversible removal.'],
            'rollback': ['Keep old workspace, MCP endpoint, root script and credential binding intact.',
                         'Restore only from verified custody after reviewing uncertain operations.',
                         'Keep project schedules disabled; do not re-enable as automatic rollback.']}


def recreate_mock(directory):
    validate()
    # Exclusive creation prevents overwriting prior recovery evidence.
    directory.mkdir(parents=False, exist_ok=False)
    database = directory / 'synthetic-workspace.sqlite'
    room = WhiteRoom(database)
    try:
        room.handle({'action': 'submit', 'job_id': 'qualification-1', 'probe': 'identity'})
    finally:
        room.close()
    # A new client must recover the same durable receipt without resubmission.
    room = WhiteRoom(database)
    try:
        receipt = room.handle({'action': 'status', 'job_id': 'qualification-1'})
    finally:
        room.close()
    (directory / 'synthetic-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['validate', 'plan', 'recreate-mock'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'validate':
            validate()
            result = {'configuration_valid': True, 'live_recreation_verified': False}
        elif args.mode == 'plan':
            result = plan()
        else:
            if args.output is None:
                parser.error('--output is required for recreate-mock')
            result = recreate_mock(args.output)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        print(json.dumps({'error': 'offline qualification failed', 'live_recreation_verified': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
