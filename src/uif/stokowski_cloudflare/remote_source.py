"""Model tools for an issue's authenticated, revisioned candidate byte store.

Only the authority mutates candidate bytes. The selected local checkout is
never opened for write. Frozen artifacts retain both base and postimage for
independent review and the existing publisher.
"""
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from dataclasses import asdict
from difflib import unified_diff
from hashlib import sha1, sha256
import re

from ..effect_authority import Operation, pinned_sha
from ..handoff_store import Hold, digest, require
from .remote_authority import AuthorityUnavailable
from .turn import RepositoryToolError


MAX_FILE_BYTES = 256 * 1024


def safe_path(name):
    require(isinstance(name, str) and len(name) <= 256 and
            re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*', name)
            and not any(p.lower() in {'.git', '.stokowski', 'node_modules'} for p in name.split('/')),
            'authority_source_member_path')
    return name


def bytes_at(encoded, expected):
    if encoded is None:
        require(expected is None, 'authority_source_absence')
        return None
    require(isinstance(encoded, str) and len(encoded) <= ((MAX_FILE_BYTES + 2) // 3) * 4,
            'authority_source_member_size')
    try:
        raw = b64decode(encoded, validate=True)
    except (ValueError, Base64Error):
        raise Hold('authority_source_member_encoding') from None
    require(len(raw) <= MAX_FILE_BYTES and sha256(raw).hexdigest() == expected
            and b64encode(raw).decode('ascii') == encoded, 'authority_source_member_bytes')
    return raw


def git_blob(raw):
    return sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def pinned_members(installation, roots):
    """The source owner pins each supported edit target, including absence."""
    members = installation['members']
    require(isinstance(members, dict) and 1 <= len(members) <= 32, 'authority_source_members')
    pinned_sha(installation['public_source_receipt_sha256'])
    for name, pin in members.items():
        safe_path(name)
        require(type(pin.get('writable')) is bool and (not pin['writable'] or
                any(name == root or name.startswith(root + '/') for root in roots)),
                'authority_source_write_scope')
        require(pin.keys() == {'mode', 'base_sha256', 'base_git_blob', 'writable'}, 'authority_source_member_pin')
        if pin['mode'] is None:
            require(pin['base_sha256'] is None and pin['base_git_blob'] is None, 'authority_source_absence')
        else:
            require(pin['mode'] in {'100644', '100755'} and
                    re.fullmatch('[a-f0-9]{40}', pin['base_git_blob']), 'authority_source_member_mode')
            pinned_sha(pin['base_sha256'])
    return members


class SourceTools:
    """Produce and consume only the authority's current staged bytes."""

    def __init__(self, remote, current, issue_id, author, members, source_receipt):
        self.remote = remote
        self.current = current
        self.issue_id = issue_id
        self.author = author
        self.members = members
        self.source_receipt = source_receipt

    def _request(self, path, data, **kwargs):
        return self.remote.transport.request('/v1/source/' + path, data, **kwargs)

    def _claim(self):
        claim = self.current(self.issue_id)
        require(claim.attempt_id == self.author, 'authority_source_attempt')
        return claim

    def list(self, claim=None):
        claim = claim or self._claim()
        record = self._request('list', {'current': asdict(claim.lease), 'attempt_id': self.author})
        require(type(record['revision']) is int and record['revision'] >= 0
                and type(record['sealed']) is bool and record['public_source_receipt_sha256'] == self.source_receipt
                and record['paths'] == sorted(self.members)
                and record['writable_paths'] == sorted(p for p, pin in self.members.items() if pin['writable']),
                'authority_source_grant_changed')
        return record

    def read(self, name):
        if name not in self.members:
            raise RepositoryToolError('Path is outside the approved source members')
        claim = self._claim()
        record = self._request('read', {'current': asdict(claim.lease),
                                        'attempt_id': self.author, 'path': name})
        require(record['path'] == name and type(record['revision']) is int and record['revision'] >= 0,
                'authority_source_read_identity')
        body = bytes_at(record['body_base64'], record['sha256'])
        require(record['mode'] == (None if body is None else self.members[name]['mode'] or '100644'),
                'authority_source_read_mode')
        if record['revision'] == 0:
            require(record['sha256'] == self.members[name]['base_sha256'] and
                    (None if body is None else git_blob(body)) == self.members[name]['base_git_blob'],
                    'authority_source_preimage_pin')
        return record, body

    def tools(self):
        require(not self.list()['sealed'], 'authority_source_already_frozen')
        definitions = {
            'list_files': ('List exact admitted source members, including selected new files.', {}),
            'find_files': ('Find an admitted source path by substring.', {'query': 'string'}),
            'read_file': ('Read a bounded source file. For large files use read_range.', {'path': 'string'}),
            'read_range': ('Read up to 8000 characters at a zero-based character offset.',
                           {'path': 'string', 'offset': 'integer'}),
            'replace_text': ('Replace one unique text match in an existing admitted source member.',
                             {'path': 'string', 'old': 'string', 'replacement': 'string'}),
            'create_file': ('Create an exactly admitted absent UTF-8 source member.',
                            {'path': 'string', 'content': 'string'}),
        }
        tools = [{'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': {
            'type': 'object', 'additionalProperties': False, 'required': list(fields),
            'properties': {key: {'type': kind} for key, kind in fields.items()}}}}
            for name, (desc, fields) in definitions.items()]
        return tools, self

    def execute_original(self, name, args, remaining, *, request_id, ordinal, call_id):
        if name == 'list_files' and not args:
            return '\n'.join(sorted(self.members))
        if name == 'find_files' and set(args) == {'query'} and isinstance(args['query'], str) and args['query'].strip():
            return '\n'.join(sorted(p for p in self.members if args['query'].casefold() in p.casefold()))
        name_fields = {'read_file': {'path'}, 'read_range': {'path', 'offset'},
                       'replace_text': {'path', 'old', 'replacement'}, 'create_file': {'path', 'content'}}
        if name not in name_fields or args.keys() != name_fields[name] or not isinstance(args['path'], str):
            raise RepositoryToolError('Incorrect fields for the selected source tool')
        path = args['path']
        mutating = name in ('replace_text', 'create_file')
        call = {'attempt_id': self.author, 'provider_request_id': request_id,
                'tool_call_index': ordinal, 'tool_call_id': call_id}
        if mutating:
            if path not in self.members or not self.members[path]['writable']:
                raise RepositoryToolError('Path is outside the selected write scope')
            claim = self._claim()
            identity = {'current': asdict(claim.lease), **call}
            original_id = digest({'kind': 'uif.source-tool-original/1', **call})
            existing = self._request('receipt', identity, allow_absent=True)
            if existing is not None:
                return self._original(existing, path, original_id, claim, name, args)
        record, raw = self.read(path)
        try:
            base = raw.decode('utf-8') if raw is not None else None
        except UnicodeError:
            raise RepositoryToolError('The source member is not UTF-8 text') from None
        if name.startswith('read_'):
            if base is None:
                return 'This admitted source member is absent.'
            if name == 'read_file':
                if len(raw) > 24_000:
                    raise RepositoryToolError('Use read_range for this source member')
                return base
            offset = args['offset']
            if type(offset) is not int or not 0 <= offset <= len(base):
                raise RepositoryToolError('Offset is outside this source member')
            return base[offset:offset + 8000]
        claim = self._claim()
        if name == 'replace_text':
            old, replacement = args['old'], args['replacement']
            if base is None or not isinstance(old, str) or not isinstance(replacement, str) or not old or base.count(old) != 1:
                raise RepositoryToolError('Replacement requires one exact match in the current source member')
            text = base.replace(old, replacement, 1)
        else:
            if base is not None or self.members[path]['mode'] is not None or not isinstance(args['content'], str):
                raise RepositoryToolError('Creation requires an explicitly admitted absent member')
            text = args['content']
        post = text.encode('utf-8')
        if len(post) > MAX_FILE_BYTES or b'\0' in post or post == raw:
            raise RepositoryToolError('Source edit is unchanged, binary, or exceeds the member limit')
        fields = {**call, 'path': path, 'expected_revision': record['revision'],
                  'preimage_sha256': record['sha256'], 'postimage_sha256': sha256(post).hexdigest()}
        operation = Operation(claim.lease.fence, original_id, digest({'kind': 'uif.native-source-write/1', **fields}))
        request = {'operation': asdict(operation), **fields, 'body_base64': b64encode(post).decode('ascii')}
        try:
            result = self._request('write', request)
        except AuthorityUnavailable:
            receipt = self._request('receipt', identity)
            require(receipt['operation'] == asdict(operation), 'authority_unknown_source_write')
            result = receipt
        require(result['revision'] == record['revision'] + 1 and result['path'] == path
                and result['sha256'] == fields['postimage_sha256'], 'authority_source_write_receipt')
        return self._original(dict(result, operation=asdict(operation)), path, original_id, claim, name, args)

    def _original(self, result, path, original_id, claim, name, args):
        operation = result['operation']
        require(operation['fence'] == asdict(claim.lease.fence) and operation['original_id'] == original_id
                and result['path'] == path and type(result['revision']) is int and result['revision'] > 0,
                'authority_original_source_receipt')
        pin = {'kind': 'uif.native-source-receipt/1', 'operation': operation, 'revision': result['revision'],
               'path': path, 'sha256': pinned_sha(result['sha256']), 'tool_name': name, 'arguments_sha256': digest(args)}
        require(result['tool_name'] == name and result['arguments_sha256'] == digest(args)
                and result['receipt_sha256'] == digest(pin), 'authority_changed_source_tool_payload')
        return f"Stored exact candidate revision {result['revision']}, file SHA-256 {result['sha256']}."


def review_diff(artifact, members):
    """Produce a deterministic textual review from the authenticated file pairs."""
    changes = []
    paths = []
    for file in artifact['files']:
        name = safe_path(file['path'])
        require(name in members, 'authority_candidate_scope')
        original = bytes_at(file['base_base64'], file['preimage_sha256'])
        updated = bytes_at(file['body_base64'], file['postimage_sha256'])
        pin = members[name]
        require(pin['writable'] and file['base_git_blob'] == pin['base_git_blob']
                and file['base_mode'] == pin['mode'] and file['preimage_sha256'] == pin['base_sha256']
                and (git_blob(original) if original is not None else None) == pin['base_git_blob']
                and file['mode'] == (pin['mode'] or '100644') and updated is not None
                and updated != original, 'authority_candidate_member')
        before = original.decode().splitlines(keepends=True) if original is not None else []
        after = updated.decode().splitlines(keepends=True)
        diff = unified_diff(before, after, fromfile='/dev/null' if original is None else 'a/' + name,
                            tofile='b/' + name)
        # Preserve missing final newlines explicitly. Otherwise two changed
        # terminal lines can be concatenated in the review representation.
        rendered = []
        for line in diff:
            rendered.append(line)
            if not line.endswith('\n'):
                rendered.append('\n\\ No newline at end of file\n')
        changes.append(''.join(rendered))
        paths.append(name)
    require(paths and paths == sorted(set(paths)), 'authority_candidate_paths')
    result = '\n'.join(changes)
    require(len(result.encode()) <= 32_000, 'authority_review_diff_size')
    return result
