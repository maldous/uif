"""Pinned Windmill REST recreation adapter; transport and secret binding injected.

No CLI live mode, implicit auth, credential copying, source execution or deletion.
Callers must separately authorize administrative writes and credential placement.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

SCRIPT = 'u/admin/foo_root_jobs'
CREDENTIAL = 'u/admin/foo_ssh_key_20261027'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_pinned(root, entry):
    if set(entry) != {'path', 'sha256'} or not re.fullmatch('[0-9a-f]{64}', entry['sha256'] or ''):
        raise ValueError('missing artifact pin')
    root = Path(root).resolve()
    candidate = (root / entry['path']).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError('artifact escapes approved bundle')
    raw = candidate.read_bytes()
    if sha256(raw) != entry['sha256']:
        raise ValueError('artifact pin mismatch')
    return raw.decode('utf-8')


def payload(root, bundle):
    """A metadata inventory or mock cannot stand in for reviewed deployed source."""
    if set(bundle) != {'version', 'source', 'schema', 'lock', 'path', 'language',
                       'summary', 'credential_reference', 'source_kind'}:
        raise ValueError('invalid bundle metadata')
    if bundle['version'] != 1 or bundle['source_kind'] != 'reviewed-windmill-export':
        raise ValueError('reviewed source export required')
    if bundle['path'] != SCRIPT or bundle['language'] != 'python3' or bundle['credential_reference'] != CREDENTIAL:
        raise ValueError('runner identity drift')
    if not isinstance(bundle['summary'], str) or not bundle['summary'].strip():
        raise ValueError('summary required')
    source = read_pinned(root, bundle['source'])
    schema = json.loads(read_pinned(root, bundle['schema']))
    lock = read_pinned(root, bundle['lock'])
    if not source.strip() or not isinstance(schema, dict) or schema.get('type') != 'object' or not schema.get('properties'):
        raise ValueError('source and argument schema required')
    # This fixed runner imports paramiko and wmill; an empty lock is never resolved.
    if not lock.strip():
        raise ValueError('resolved dependency lock required')
    return {'path': SCRIPT, 'summary': bundle['summary'], 'content': source,
            'language': 'python3', 'schema': schema, 'lock': lock,
            'deployment_message': 'UIF pinned recreation; no automatic execution'}


def validate_target(target):
    if not isinstance(target, str) or not re.fullmatch('[a-z][a-z0-9_-]{2,63}', target) or target in ('foo', 'offmenu', 'admins'):
        raise ValueError('explicit isolated target required; preserved workspaces forbidden')


def recreate(root, bundle, target, transport, credential_binding, journal):
    """Use documented /api operations with an injected authenticated transport.

    transport.request(method, path, body) -> (HTTP status, decoded body).
    credential_binding(target, reference) verifies separately authorized external
    injection and returns exactly True; this adapter never accepts secret bytes.
    Journal is exclusively created. Any uncertain write blocks automatic replay.
    The adapter does not execute the runner or assert live host qualification.
    """
    validate_target(target)
    body = payload(root, bundle)  # Validate everything before side effects.
    identity = {'target': target, 'source_sha256': bundle['source']['sha256'],
                'schema_sha256': bundle['schema']['sha256'], 'lock_sha256': bundle['lock']['sha256'],
                'state': 'pending', 'live_recreation_verified': False,
                'permissions_verified': False, 'host_protocol_verified': False}
    journal = Path(journal)
    # An existing operation must be reconciled by its owner, never silently replayed.
    with journal.open('x') as stream:
        json.dump(identity, stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())

    def request(method, path, data, expected):
        status, response = transport.request(method, path, data)
        if status != expected:
            raise ValueError('unexpected Windmill response')
        return response

    try:
        exists = request('POST', '/api/workspaces/exists', {'id': target}, 200)
        if exists is not False:
            raise ValueError('target not verified absent')
        request('POST', '/api/workspaces/create', {'id': target, 'name': 'Foo'}, 201)
        if credential_binding(target, CREDENTIAL) is not True:
            raise ValueError('external credential binding not verified')
        prefix = '/api/w/' + quote(target, safe='')
        exists = request('GET', prefix + '/variables/exists/' + CREDENTIAL, None, 200)
        if exists is not True:
            raise ValueError('required external credential unavailable')
        new_hash = request('POST', prefix + '/scripts/create', body, 201)
        if not isinstance(new_hash, str) or not re.fullmatch('[0-9a-f]{16}', new_hash):
            raise ValueError('invalid deployed script hash')
        deployed = request('GET', prefix + '/scripts/get/p/' + SCRIPT, None, 200)
        if not isinstance(deployed, dict) or any(deployed.get(k) != body[k] for k in ('path', 'content', 'language', 'schema', 'lock')):
            raise ValueError('deployed source/schema/lock mismatch')
        if deployed.get('hash') != new_hash or deployed.get('lock_error_logs'):
            raise ValueError('deployment identity or dependency lock not verified')
        identity.update(state='script-copy-verified', deployed_hash=new_hash)
    except Exception:
        # Do not print arbitrary server bodies, exceptions, source or credentials.
        # The original durable pending journal is sufficient to prohibit replay.
        raise ValueError('recreation incomplete or uncertain; reconcile original journal, no automatic retry') from None
    # Preserve original pending journal as custody; receipt is a separate create.
    with Path(str(journal) + '.receipt.json').open('x') as stream:
        json.dump(identity, stream, indent=2)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    return identity
