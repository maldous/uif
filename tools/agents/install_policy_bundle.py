"""Install an instruction-only Foo startup pointer to a reviewed UIF checkout.

Dry run by default. This does not configure or start any service, model, account,
runtime, memory backend or execution policy. Run as the startup file's owner.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

# Static sibling import must not leave __pycache__ in the reviewed checkout.
sys.dont_write_bytecode = True
from check_policy import check, MANIFEST_PATH, POLICY_PATH, VERSION

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
MAX_STARTUP_BYTES = 1024 * 1024


class AdoptionError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _absolute(path):
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts
            or any(ord(c) < 32 or c == '`' for c in str(path))):
        raise AdoptionError('An absolute, unambiguous path is required')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise AdoptionError('Symlinked adoption paths are refused')
    return path


def _git(root, *arguments):
    # No shell, hooks, credential lookup, index refresh or configured fsmonitor.
    command = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-C', str(root), *arguments]
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise AdoptionError('Cannot verify the reviewed Git checkout')
    return result.stdout.decode('utf-8').strip()


def validate_bundle(bundle, source_commit):
    bundle = _absolute(bundle)
    if bundle != BUNDLE_ROOT:
        raise AdoptionError('Run the helper from the selected bundle checkout')
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise AdoptionError('A full reviewed source commit is required')
    if _git(bundle, 'rev-parse', '--show-toplevel') != str(bundle):
        raise AdoptionError('Bundle must be the exact Git checkout root')
    if _git(bundle, 'rev-parse', '--verify', 'HEAD') != source_commit:
        raise AdoptionError('Reviewed source commit does not match actual HEAD')
    if _git(bundle, 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=all'):
        raise AdoptionError('Reviewed source checkout must be clean')
    policy = (bundle / POLICY_PATH).read_bytes()
    errors = check(bundle, policy, 'maldous/uif')
    if errors:
        raise AdoptionError('Bundle conformance failed: ' + '; '.join(errors))
    manifest = json.loads((bundle / MANIFEST_PATH).read_text(encoding='utf-8'))
    return manifest['sha256']


def _open_owned(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1):
            raise AdoptionError('Startup and backup must be singly linked regular files owned by the caller')
        if metadata.st_size > MAX_STARTUP_BYTES:
            raise AdoptionError('Startup input is too large')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(MAX_STARTUP_BYTES + 1)
        if len(data) > MAX_STARTUP_BYTES:
            raise AdoptionError('Startup input is too large')
        return descriptor, metadata, data
    except BaseException:
        os.close(descriptor)
        raise


def startup_text(bundle, source_commit, policy_digest, original):
    try:
        previous = original.decode('utf-8')
    except UnicodeDecodeError:
        raise AdoptionError('Existing startup instructions must be UTF-8') from None
    prefix = (
        '# UIF execution environment startup\n\n'
        f'Read {bundle}/AGENTS.md and {bundle}/{MANIFEST_PATH} before UIF project work.\n'
        f'Use policy {VERSION}, SHA-256 {policy_digest}, reviewed source {source_commit}.\n'
        f'Load {bundle}/docs/agents/environment-profiles.json and the applicable project overlay;\n'
        'retain the five project roles and the unbound general Cloud role. Native Linear is the sole live intake.\n'
        'Preserve original operations, uncertain outcomes, current owners and all project-specific holds.\n'
        'Apply the selected environment and project white-room checks before dependent work.\n'
        'Preserve the current observed CIX control state; these instructions authorize no pause, restart or activation.\n'
        'This pointer installs instructions only; it does not start services or prove runtime readiness.\n\n'
        '## Retained earlier routing and holds\n\n'
        'The UIF bundle pointers above supersede historical UIF/OffMenu shared-bootstrap pointers below.\n'
        'Historical control-state assertions below are dated evidence, not instructions to pause or resume active CIX work.\n'
        'Current user directions, narrower applicable holds and unrelated USF routing retain their authority.\n\n'
    )
    return (prefix + previous + ('' if previous.endswith('\n') else '\n')).encode('utf-8')


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup(path, original):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        descriptor, _, retained = _open_owned(path)
        os.close(descriptor)
        if retained != original:
            raise AdoptionError('Existing preimage backup differs; it was not overwritten')
        return
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Retain an incomplete backup for reconciliation rather than overwrite it.
        raise
    _fsync_directory(path.parent)


def install(bundle, source_commit, startup, expected_preimage_sha256, *, apply=False):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_preimage_sha256):
        raise AdoptionError('A full current startup preimage SHA-256 is required')
    policy_digest = validate_bundle(bundle, source_commit)
    bundle, startup = _absolute(bundle), _absolute(startup)
    if startup == bundle or startup.is_relative_to(bundle):
        raise AdoptionError('Startup adoption must not modify the source checkout')
    descriptor, metadata, original = _open_owned(startup)
    temporary = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if digest(original) != expected_preimage_sha256:
            raise AdoptionError('Startup preimage SHA-256 mismatch; no replacement performed')
        proposed = startup_text(bundle, source_commit, policy_digest, original)
        backup = startup.with_name(startup.name + '.before-' + expected_preimage_sha256 + '.bak')
        result = {'status': 'dry-run', 'instruction_only': True,
                  'source_commit': source_commit, 'policy_version': VERSION,
                  'policy_sha256': policy_digest, 'startup': str(startup),
                  'preimage_sha256': expected_preimage_sha256,
                  'proposed_sha256': digest(proposed), 'backup': str(backup)}
        if not apply:
            return result
        _backup(backup, original)
        out_descriptor, temporary = tempfile.mkstemp(prefix='.' + startup.name + '.policy-', dir=startup.parent)
        with os.fdopen(out_descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(proposed)
            stream.flush()
            os.fsync(stream.fileno())
        if validate_bundle(bundle, source_commit) != policy_digest:
            raise AdoptionError('Reviewed bundle changed during adoption')
        # Detect a replaced path or changed bytes after preservation.
        current_descriptor, current_metadata, current = _open_owned(startup)
        os.close(current_descriptor)
        if ((current_metadata.st_dev, current_metadata.st_ino) != (metadata.st_dev, metadata.st_ino)
                or current != original):
            raise AdoptionError('Startup changed during adoption; no replacement performed')
        os.replace(temporary, startup)
        temporary = None
        _fsync_directory(startup.parent)
        readback_descriptor, readback_metadata, readback = _open_owned(startup)
        os.close(readback_descriptor)
        if readback != proposed or stat.S_IMODE(readback_metadata.st_mode) != stat.S_IMODE(metadata.st_mode):
            raise AdoptionError('Startup replacement requires readback reconciliation')
        result['status'] = 'installed'
        result['readback_sha256'] = digest(readback)
        return result
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--startup', type=Path, required=True)
    parser.add_argument('--expected-preimage-sha256', required=True)
    parser.add_argument('--apply', action='store_true', help='replace instructions after checks; default is dry run')
    args = parser.parse_args(argv)
    try:
        result = install(args.bundle, args.source_commit, args.startup,
                         args.expected_preimage_sha256, apply=args.apply)
    except (AdoptionError, OSError, subprocess.SubprocessError) as exc:
        # Errors contain controlled metadata only, never startup contents.
        print(json.dumps({'status': 'refused', 'instruction_only': True, 'reason': str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
