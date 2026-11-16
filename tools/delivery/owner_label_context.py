"""Prepare one pinned native context correction; never installs or restarts it.

Seven informational coordinating-owner labels must not alter native management
context. Other labels, comments, scope, state, dependencies and custody retain
their existing meaning. A prepared candidate is not an adopted runtime fix.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat

MAX_BYTES = 1024 * 1024
EXPECTED_SOURCE_SHA256 = '7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea'
INFORMATIONAL_OWNER_LABELS = frozenset({
    'Owner: UIF', 'Owner: Cloud', 'Owner: ANQR', 'Owner: OffMenu',
    'Owner: CircularQuay', 'Owner: CIX', 'Owner: Morgan',
})
NEEDLE = b'        machine_labels = {name for name, _ in (*report_mod.CLASSIFICATIONS.values(), *report_mod.CONFIDENCE_LABELS.values())}'
REPLACEMENT = NEEDLE + b' | {\n' + b''.join(
    ('            ' + repr(name) + ',\n').encode()
    for name in sorted(INFORMATIONAL_OWNER_LABELS)
) + b'        }'


class CorrectionError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def render_native_source(raw):
    if (type(raw) is not bytes or len(raw) > MAX_BYTES
            or digest(raw) != EXPECTED_SOURCE_SHA256 or raw.count(NEEDLE) != 1):
        raise CorrectionError('Exact bounded original native source required')
    try:
        before = ast.parse(raw)
        selected = [n for n in ast.walk(before)
                    if isinstance(n, ast.FunctionDef) and n.name == '_management_context']
        if len(selected) != 1:
            raise CorrectionError('Unique original management context required')
        assignments = [n for n in selected[0].body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'machine_labels' for t in n.targets)]
        if len(assignments) != 1 or not isinstance(assignments[0].value, ast.SetComp):
            raise CorrectionError('Original machine-label expression differs')
        after = raw.replace(NEEDLE, REPLACEMENT)
        ast.parse(after)
    except (SyntaxError, UnicodeError, RecursionError) as error:
        raise CorrectionError('Native Python source is invalid') from error
    return after, dict(before_sha256=digest(raw), after_sha256=digest(after),
                       changed_function='_management_context',
                       ignored_informational_labels=sorted(INFORMATIONAL_OWNER_LABELS),
                       unrelated_labels_ignored=False, human_context_changed=False,
                       original_records_changed=False, candidate_prepared=True,
                       native_installed=False, runtime_adopted=False,
                       gate_cleared=False, claim_transferred=False)


def read_source(path):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise CorrectionError('Bounded regular native source required')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CorrectionError('Native source exceeds size bound')
    return raw


def emit_candidate(path, raw):
    # A separate new file only: never overwrite the installed source or history.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--emit-candidate', type=Path)
    args = parser.parse_args(argv)
    try:
        raw, result = render_native_source(read_source(args.source))
        if args.emit_candidate is not None:
            emit_candidate(args.emit_candidate, raw)
            result['candidate_file_sha256'] = digest(raw)
    except (CorrectionError, OSError, ValueError):
        print(json.dumps(dict(status='refused', installed_source_changed=False)))
        return 2
    print(json.dumps(dict(status='prepared', **result), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
