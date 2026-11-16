"""Bounded explicit future-clone profiles; dry-run by default, no workspace reset.

The caller retains actual host/configuration authority and sole-writer custody.
Preimage checks and a cooperative file lock are not a distributed ownership fence.
No hook, Git command, service operation, inference or publication is executed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone
import urllib.request
from dataclasses import dataclass

MAX_BYTES = 2 * 1024 * 1024
PROJECT = 'CIX'
REPOSITORY = 'cix-project/cix'
ORIGIN = 'https://github.com/cix-project/cix.git'
EXPECTED_HOOK_SHA256 = '9c6ba100a43720ba16d6e586ed2ddf3d82bd14b3f8fb0d436227e0e89fe5597c'
OLD_CLONE = 'clone --single-branch -- ' + ORIGIN + ' .'
NEW_CLONE = 'clone --branch develop --single-branch -- ' + ORIGIN + ' .'


@dataclass(frozen=True)
class SourceProfile:
    project: str
    repository: str
    origin: str
    hook_sha256: str
    source_branch: str
    expected_base: str
    corrected_base: str | None = None


PROFILES = {
    'cq-feature-bootstrap': SourceProfile('CQ', 'maldous/CircularQuay',
        'https://github.com/maldous/CircularQuay.git',
        'c5e13e48be4d26389110ebc069aa1f16fafb7ec46dda6e54f1a36e5602e02d55',
        'feature/initial-implementation', 'main'),
    'anqr-develop': SourceProfile('ANQR', 'maldous/anqr-v3',
        'https://github.com/maldous/anqr-v3.git',
        'f6b2bafee58ca78328730779815ddc065750fd37160307e25ec7c59d9c60abc1',
        'develop', 'develop'),
    'offmenu-develop': SourceProfile('OffMenu', 'maldous/offmenu',
        'https://github.com/maldous/offmenu.git',
        '4558f6998a3ea27c1690d507a59a21c205cff3a944a73ee364416508670c4be9',
        'develop', 'develop'),
}


def selected_profile(name):
    if name == 'cix':
        # Preserve the already reviewed default and its source qualification.
        return SourceProfile(PROJECT, REPOSITORY, ORIGIN, EXPECTED_HOOK_SHA256,
                             'develop', 'main', 'develop')
    if type(name) is not str or name not in PROFILES:
        raise MigrationError('Unknown explicit source profile')
    return PROFILES[name]


class MigrationError(ValueError):
    pass


class UncertainMigrationError(MigrationError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_spans(text):
    """Parse bounded JSON while retaining exact scalar byte-independent spans."""
    decoder = json.JSONDecoder(parse_constant=lambda _: (_ for _ in ()).throw(
        MigrationError('Nonfinite configuration value')))
    spans = {}

    def ws(index):
        while index < len(text) and text[index] in ' \t\r\n':
            index += 1
        return index

    def value(index, path=(), depth=0):
        if depth > 64:
            raise MigrationError('Configuration nesting exceeds bound')
        start = index = ws(index)
        if index >= len(text):
            raise MigrationError('Truncated configuration')
        if text[index] == '{':
            result, seen, index = {}, set(), ws(index + 1)
            if index < len(text) and text[index] == '}':
                index += 1
            else:
                while True:
                    key, end = decoder.raw_decode(text, index)
                    if type(key) is not str or key in seen:
                        raise MigrationError('Duplicate or invalid configuration key')
                    seen.add(key)
                    index = ws(end)
                    if index >= len(text) or text[index] != ':':
                        raise MigrationError('Invalid configuration object')
                    result[key], index = value(index + 1, path + (key,), depth + 1)
                    index = ws(index)
                    if index < len(text) and text[index] == '}':
                        index += 1
                        break
                    if index >= len(text) or text[index] != ',':
                        raise MigrationError('Invalid configuration object separator')
                    index = ws(index + 1)
        elif text[index] == '[':
            result, index = [], ws(index + 1)
            if index < len(text) and text[index] == ']':
                index += 1
            else:
                while True:
                    item, index = value(index, path + (len(result),), depth + 1)
                    result.append(item)
                    index = ws(index)
                    if index < len(text) and text[index] == ']':
                        index += 1
                        break
                    if index >= len(text) or text[index] != ',':
                        raise MigrationError('Invalid configuration array separator')
                    index = ws(index + 1)
        else:
            result, index = decoder.raw_decode(text, index)
        spans[path] = (start, index, result)
        return result, index

    result, end = value(0)
    if ws(end) != len(text):
        raise MigrationError('Trailing configuration content')
    return result, spans


def render(raw, profile='cix'):
    selected_source = selected_profile(profile)
    if len(raw) > MAX_BYTES:
        raise MigrationError('Configuration exceeds size bound')
    text = raw.decode('utf-8')
    config, spans = json_spans(text)
    projects = config.get('projects') if type(config) is dict else None
    if type(projects) is not list:
        raise MigrationError('Expected declared project list')
    selected = [i for i, project in enumerate(projects)
                if type(project) is dict and project.get('name') == selected_source.project]
    if len(selected) != 1:
        raise MigrationError('Exact selected project unavailable')
    index = selected[0]
    project = projects[index]
    delivery, hooks = project.get('delivery'), project.get('hooks')
    if (type(delivery) is not dict or delivery.get('repo') != selected_source.repository
            or delivery.get('origin') != selected_source.origin
            or delivery.get('base') != selected_source.expected_base
            or project.get('paused') is not True or type(hooks) is not dict):
        raise MigrationError('Expected paused project source binding differs')
    hook = hooks.get('after_create')
    old_clone = 'clone --single-branch -- ' + selected_source.origin + ' .'
    new_clone = ('clone --branch ' + selected_source.source_branch
                 + ' --single-branch -- ' + selected_source.origin + ' .')
    if (type(hook) is not str or digest(hook.encode()) != selected_source.hook_sha256
            or hook.count(old_clone) != 1):
        raise MigrationError('Original selected creation-hook pin differs')
    new_hook = hook.replace(old_clone, new_clone)
    edits = [(('projects', index, 'hooks', 'after_create'), new_hook)]
    if selected_source.corrected_base is not None:
        edits.append((('projects', index, 'delivery', 'base'), selected_source.corrected_base))
    output = text
    for path, replacement in sorted(edits, key=lambda item: spans[item[0]][0], reverse=True):
        start, end, _ = spans[path]
        output = output[:start] + json.dumps(replacement, ensure_ascii=False) + output[end:]
    corrected, _ = json_spans(output)
    expected = json.loads(json.dumps(config))
    if selected_source.corrected_base is not None:
        expected['projects'][index]['delivery']['base'] = selected_source.corrected_base
    expected['projects'][index]['hooks']['after_create'] = new_hook
    if corrected != expected:
        raise MigrationError('Unselected configuration fields changed')
    encoded = output.encode('utf-8')
    return encoded, dict(project=selected_source.project, repository=selected_source.repository,
                        source_profile=profile, source_branch=selected_source.source_branch,
                        before_sha256=digest(raw), after_sha256=digest(encoded),
                        old_base=delivery['base'],
                        new_base=selected_source.corrected_base or delivery['base'],
                        publication_base_changed=selected_source.corrected_base is not None,
                        old_hook_sha256=digest(hook.encode()),
                        new_hook_sha256=digest(new_hook.encode()),
                        changed_paths=['projects/' + selected_source.project + '/'
                                       + '/'.join(path[2:]) for path, _ in edits],
                        admission_changed=False, claims_transferred=False,
                        existing_workspaces_changed=False, runtime_restarted=False,
                        runtime_adoption_verified=False)


def read_state():
    with urllib.request.urlopen('http://127.0.0.1:4213/api/v1/state', timeout=5) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise MigrationError('Native state exceeds size bound')
    return json.loads(raw)


def check_state(state, now=None, project_name=PROJECT):
    now = now or datetime.now(timezone.utc)
    try:
        generated = datetime.fromisoformat(state['generated_at'].replace('Z', '+00:00'))
        age = (now - generated).total_seconds()
        projects = [p for p in state['projects'] if p.get('name') == project_name]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise MigrationError('Current native observation is invalid') from None
    if not 0 <= age <= 5 or len(projects) != 1:
        raise MigrationError('Current native observation is stale or ambiguous')
    project = projects[0]
    counts = project.get('counts')
    if (type(counts) is not dict or project.get('paused') is not True
            or any(type(counts.get(key)) is not int or counts[key] != 0
                   for key in ('running', 'settling', 'tracked_tasks', 'retrying'))):
        raise MigrationError('Selected project is not paused and quiescent')
    return state['generated_at']


def absolute(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise MigrationError('Absolute configuration path required')
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise MigrationError('Symlinked configuration paths refused')
    return path


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate(path, expected_preimage, *, apply=False, state_reader=read_state, profile='cix'):
    selected_source = selected_profile(profile)
    if type(expected_preimage) is not str or not re.fullmatch('[0-9a-f]{64}', expected_preimage):
        raise MigrationError('Exact preimage SHA-256 required')
    path = absolute(path)
    # Nonblocking admission prevents a replaced FIFO from hanging before fstat.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    temporary = None
    replaced = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_size > MAX_BYTES):
            raise MigrationError('Bounded singly linked configuration file required')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if digest(raw) != expected_preimage:
            raise MigrationError('Configuration preimage differs; reconcile original operation')
        output, result = render(raw, profile)
        result['native_observed_at'] = check_state(state_reader(), project_name=selected_source.project)
        result.update(status='dry-run', instruction_only=False,
                      scope='future-' + selected_source.project + '-source-selection-only')
        if not apply:
            return result
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise MigrationError('Applying requires the protected configuration owner')
        backup_dir = path.parent / '.uif-source-migrations'
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        bstat = backup_dir.lstat()
        if (not stat.S_ISDIR(bstat.st_mode) or backup_dir.is_symlink()
                or bstat.st_uid != os.geteuid() or stat.S_IMODE(bstat.st_mode) != 0o700):
            raise MigrationError('Private original configuration custody unavailable')
        backup = backup_dir / (expected_preimage + '.json')
        try:
            backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            bmeta = backup.lstat()
            if (not stat.S_ISREG(bmeta.st_mode) or bmeta.st_uid != os.geteuid()
                    or bmeta.st_nlink != 1 or stat.S_IMODE(bmeta.st_mode) != 0o600
                    or backup.read_bytes() != raw):
                raise MigrationError('Existing original backup differs; no overwrite')
        else:
            with os.fdopen(backup_fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(backup_dir)
        # Persist the backup directory's parent entry before replacing config.
        # A private backup file alone does not durably link a new directory.
        fsync_directory(path.parent)
        temp_fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.source-', dir=path.parent)
        with os.fdopen(temp_fd, 'wb') as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck the same source and current selected-project state immediately
        # before the bounded effect. The lock assumes the acknowledged sole writer.
        current = path.stat(follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
                or path.read_bytes() != raw):
            raise MigrationError('Configuration changed during preparation')
        check_state(state_reader(), project_name=selected_source.project)
        current = path.stat(follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
                or path.read_bytes() != raw):
            raise MigrationError('Configuration changed at the bounded effect boundary')
        os.replace(temporary, path)
        temporary, replaced = None, True
        fsync_directory(path.parent)
        if path.read_bytes() != output:
            raise UncertainMigrationError('Installed configuration needs original-operation reconciliation')
        result.update(status='applied', original_backup_sha256=digest(raw),
                      readback_sha256=digest(path.read_bytes()))
        return result
    except BaseException as error:
        if replaced and not isinstance(error, UncertainMigrationError):
            raise UncertainMigrationError('Configuration effect may have occurred; reconcile original operation') from error
        raise
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--expected-preimage-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--profile', choices=['cix', *PROFILES], default='cix')
    args = parser.parse_args(argv)
    try:
        result = migrate(args.config, args.expected_preimage_sha256, apply=args.apply, profile=args.profile)
    except UncertainMigrationError:
        print(json.dumps(dict(status='uncertain', original_operation_reconciliation_required=True)))
        return 1
    except (MigrationError, OSError, ValueError, UnicodeError):
        print(json.dumps(dict(status='refused', source_or_state_precondition_failed=True)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
