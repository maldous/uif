"""Offline conformance for an environment-owned UIF instruction bundle.

Checks declared source consistency only: no installed-tool, runtime admission,
memory enforcement, authorization or publication claims. Product repositories
do not need copies of this operational bundle.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

POLICY_PATH = 'docs/agents/uif-worker-policy.md'
MANIFEST_PATH = 'docs/agents/uif-worker-policy.json'
PROFILES_PATH = 'docs/agents/environment-profiles.json'
VERSION = '2.0.0'
ROLES = {'uif', 'anqr', 'offmenu', 'circularquay', 'cix', 'cloud'}
EXECUTORS = {'cloud', 'foo_codex', 'foo_mcp_user', 'foo_mcp_root'}
MAX_INPUT_BYTES = 1024 * 1024


class InputError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError('Duplicate JSON field')
        result[key] = value
    return result


def _read(root, name):
    """Read bounded regular files; reject escaping and symlinked references."""
    if (not isinstance(name, str) or not name or '\\' in name
            or any(ord(c) < 32 for c in name)):
        raise InputError(f'Nonportable instruction path: {name!r}')
    relative = PurePosixPath(name)
    if (relative.is_absolute() or relative.as_posix() != name
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise InputError(f'Nonportable instruction path: {name}')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise InputError(f'Nonportable instruction path: {name}')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise InputError(f'Unreadable instruction path: {name}')
    with path.open('rb') as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise InputError(f'Instruction input too large: {name}')
    return data


def _json(data):
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          InputError('Nonfinite JSON value')))


def _strings(value):
    return (isinstance(value, list)
            and all(isinstance(item, str) and item.strip() == item and item
                    for item in value)
            and len(value) == len(set(value)))


def _implementation(root, status, evidence, subject, label):
    """Bind optional qualification evidence without asserting live readiness.

    Receipt references are opaque. This never fetches them, establishes their
    authenticity/freshness, or treats a source report as runtime authority.
    """
    if status not in ('unimplemented', 'unqualified', 'qualified'):
        return [f'Invalid {label} declaration']
    if evidence is None:
        return [f'Missing {subject} qualification evidence'] if status == 'qualified' else []
    try:
        if (not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256'}
                or not isinstance(evidence['sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', evidence['sha256'])):
            return [f'Invalid {subject} qualification binding']
        raw = _read(root, evidence['path'])
        if hashlib.sha256(raw).hexdigest() != evidence['sha256']:
            return [f'{subject} qualification hash mismatch']
        report = _json(raw)
        if not isinstance(report, dict):
            return [f'Invalid {subject} qualification record']
        observed = report.get('observed_at')
        observed = datetime.fromisoformat(observed.replace('Z', '+00:00')) if isinstance(observed, str) else None
        references = report.get('receipt_refs')
        if (report.get('subject') != subject or report.get('status') != status
                or not isinstance(report.get('source_commit'), str)
                or not re.fullmatch(r'[0-9a-f]{40}', report['source_commit'])
                or observed is None or observed.utcoffset() is None
                or not _strings(references) or not 1 <= len(references) <= 128
                or any(len(ref) > 2048 or any(ord(c) < 32 for c in ref) for ref in references)):
            return [f'Invalid {subject} qualification record']
    except (OSError, ValueError, TypeError, RecursionError):
        return [f'Unreadable {subject} qualification evidence']
    return []


def _profiles(data, manifest, capabilities, selected_role, project_repository, root):
    errors = []
    if not isinstance(data, dict):
        return ['Environment profiles must be an object']
    fixed = {
        'schema': 'uif-environment-profiles-v1',
        'policy': {'id': 'uif-worker-policy', 'version': VERSION,
                   'path': POLICY_PATH, 'manifest': MANIFEST_PATH},
        'inventory': {'capabilities': manifest.get('capabilities'),
                      'memory': manifest.get('memory')},
    }
    for key, value in fixed.items():
        if data.get(key) != value:
            errors.append(f'Invalid environment profiles {key}')
    if type(data.get('local_session_ceiling')) is not int or data['local_session_ceiling'] != 64:
        errors.append('Invalid local session ceiling')
    intake = data.get('intake')
    if (not isinstance(intake, dict)
            or intake.get('kind') != 'linear-only'
            or intake.get('registry_is_queue') is not False
            or intake.get('identity') != ['workspace_uuid', 'project_uuid', 'issue_uuid']
            or intake.get('ledger_ids') != 'provenance-only'):
        errors.append('Invalid Linear-only intake declaration')
    if (not isinstance(intake, dict)
            or intake.get('claim_authority') != 'off-foo-control-plane'):
        errors.append('Invalid shared off-Foo authority declaration')
    if isinstance(intake, dict):
        errors.extend(_implementation(root, intake.get('authority_implementation'),
                                      intake.get('authority_qualification'), 'off-foo-authority',
                                      'shared off-Foo authority'))
    continuity = data.get('morgan_continuity')
    if (not isinstance(continuity, dict)
            or continuity.get('lifecycle') != 'durable-logical-role'
            or continuity.get('conversation_required') is not False
            or continuity.get('replacement') != 'fenced-reconstruction'
            or continuity.get('wake') != 'independent-scheduled-or-event-trigger'):
        errors.append('Invalid durable Morgan lifecycle declaration')
    if isinstance(continuity, dict):
        errors.extend(_implementation(root, continuity.get('implementation'), continuity.get('qualification'),
                                      'morgan-lifecycle', 'durable Morgan lifecycle'))
    outage = data.get('foo_outage')
    if (not isinstance(outage, dict)
            or type(outage.get('sustained_grace_seconds')) is not int
            or outage['sustained_grace_seconds'] <= 0
            or type(outage.get('qualified_probes_required')) is not int
            or outage['qualified_probes_required'] < 2
            or outage.get('configurable') is not True
            or outage.get('healthy_probe_resets_grace') is not True
            or outage.get('brief_glitches') != 'retry-original-operation'
            or outage.get('authority_must_remain_reachable') is not True):
        errors.append('Invalid sustained Foo outage declaration')
    inference = data.get('inference_continuity')
    if (not isinstance(inference, dict)
            or inference.get('capacity') != 'existing-authorized-only'
            or inference.get('new_provider_fallback') is not False
            or inference.get('when_unavailable') != 'preserve-and-resume'):
        errors.append('Invalid existing inference capacity declaration')
    if data.get('resource_accounting') != 'shared-across-local-sessions-and-mcp-jobs':
        errors.append('Missing shared host resource accounting')
    roles = data.get('logical_roles')
    if not isinstance(roles, dict) or set(roles) != ROLES:
        errors.append('Invalid logical role set')
        roles = roles if isinstance(roles, dict) else {}
    namespaces, repositories = set(), set()
    for key, role in roles.items():
        if not isinstance(role, dict):
            errors.append(f'Invalid logical role: {key}')
            continue
        bindings = role.get('repository_binding')
        if not _strings(bindings) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
                                         for repo in (bindings or []) if isinstance(repo, str)):
            errors.append(f'Invalid repository bindings: {key}')
            bindings = []
        for repository in bindings:
            if repository in repositories:
                errors.append(f'Repository assigned to multiple roles: {repository}')
            repositories.add(repository)
        if key == 'cloud':
            if bindings or role.get('baseline') is not None or role.get('product_instructions') is not None:
                errors.append('General Cloud role must have no repository binding')
        else:
            baseline = role.get('baseline')
            if (not bindings or not isinstance(baseline, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', baseline)
                    or '..' in baseline or '//' in baseline
                    or role.get('product_instructions') != 'AGENTS.md'):
                errors.append(f'Incomplete project role declaration: {key}')
            elif baseline not in ('main', 'develop') and not role.get('baseline_exception'):
                errors.append(f'Nonstandard baseline needs an explicit exception: {key}')
        if role.get('operational_policy') != 'environment-owned':
            errors.append(f'Operational policy must remain environment-owned: {key}')
        memory = role.get('memory')
        if (not isinstance(memory, dict) or memory.get('namespace') != f'uif/roles/{key}'
                or memory.get('shared') != 'uif/shared' or memory.get('private_data') is not False):
            errors.append(f'Invalid memory isolation declaration: {key}')
        else:
            if memory['namespace'] in namespaces:
                errors.append('Duplicate private memory namespace')
            namespaces.add(memory['namespace'])
    executors = data.get('executor_profiles')
    if not isinstance(executors, dict) or set(executors) != EXECUTORS:
        errors.append('Invalid executor profile set')
        executors = executors if isinstance(executors, dict) else {}
    for key, profile in executors.items():
        if not isinstance(profile, dict) or not isinstance(profile.get('capabilities'), dict):
            errors.append(f'Invalid executor capabilities: {key}')
            continue
        capability = profile['capabilities']
        required, optional = capability.get('required'), capability.get('optional')
        if not _strings(required) or not required or not _strings(optional):
            errors.append(f'Invalid executor capability lists: {key}')
            continue
        if set(required) & set(optional):
            errors.append(f'Required and optional capabilities overlap: {key}')
        for name in required + optional:
            if '`' + name + '`' not in capabilities:
                errors.append(f'Capability absent from inventory: {name}')
        if key in ('foo_mcp_user', 'foo_mcp_root'):
            if profile.get('provides_reasoning') is not False or profile.get('provides_session_control') is not False:
                errors.append(f'Deterministic transport cannot declare reasoning or session control: {key}')
        if key == 'foo_mcp_root' and profile.get('repository_commands_as') != 'verified-checkout-owner':
            errors.append('Root transport must preserve checkout ownership')
        if key == 'foo_codex' and (type(profile.get('session_ceiling')) is not int or profile['session_ceiling'] != 64):
            errors.append('Invalid Foo Codex session ceiling')
        if key == 'cloud' and profile.get('foo_filesystem_shared') is not False:
            errors.append('Cloud environment must not declare a shared Foo filesystem')
    if selected_role is not None:
        if selected_role not in ROLES or selected_role not in roles:
            errors.append('Unknown selected role')
        elif project_repository is not None:
            selected = roles[selected_role]
            if not isinstance(selected, dict) or project_repository not in selected.get('repository_binding', []):
                errors.append('Selected repository does not belong to selected role')
    elif project_repository is not None:
        errors.append('A selected repository requires --role')
    return errors


def check(root, expected_policy, expected_repository=None, *, role=None, project_repository=None):
    """Preserve the Python API while checking the current environment bundle.

    expected_repository identifies the bundle checkout, not a consuming product.
    Historical 1.x copies are stale. Selecting a role never reads product files.
    """
    root = Path(root).resolve()
    errors = []
    try:
        manifest = _json(_read(root, MANIFEST_PATH))
        if not isinstance(manifest, dict):
            return ['Manifest must be an object']
        policy = _read(root, POLICY_PATH)
        agents = _read(root, 'AGENTS.md')
        fixed = {'schema': 'uif-worker-policy-manifest-v2',
                 'policy_id': 'uif-worker-policy', 'version': VERSION,
                 'policy_path': POLICY_PATH, 'maintenance_repository': 'maldous/uif',
                 'maintenance_path': POLICY_PATH, 'distribution': 'environment-owned',
                 'environment_profiles': PROFILES_PATH,
                 'capabilities': 'docs/agents/skills-and-plugins.md',
                 'memory': 'docs/agents/memory-routing.md',
                 'startup_template': 'docs/agents/startup-template.md'}
        for key, value in fixed.items():
            if manifest.get(key) != value:
                errors.append(f'Invalid {key}')
        if expected_repository and manifest.get('repository') != expected_repository:
            errors.append('Repository identity mismatch')
        if hashlib.sha256(policy).hexdigest() != manifest.get('sha256'):
            errors.append('Policy hash mismatch')
        if policy != expected_policy:
            errors.append('Policy differs from selected UIF source')
        if f'Policy ID: uif-worker-policy. Version: {VERSION}.' not in policy.decode('utf-8'):
            errors.append('Policy header version mismatch')
        agents_text = agents.decode('utf-8')
        if POLICY_PATH not in agents_text or MANIFEST_PATH not in agents_text:
            errors.append('Missing root startup references')
        documents = {'AGENTS.md': agents, POLICY_PATH: policy}
        for key in ('capabilities', 'memory', 'startup_template'):
            documents[fixed[key]] = _read(root, fixed[key])
        for name, document in documents.items():
            document.decode('utf-8')
            if b'\r' in document or not document.endswith(b'\n'):
                errors.append(f'Instructions require LF and final newline: {name}')
        profiles = _json(_read(root, PROFILES_PATH))
        errors.extend(_profiles(profiles, manifest, documents[fixed['capabilities']].decode('utf-8'),
                                role, project_repository, root))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        errors.append(f'Unreadable instruction input: {exc}')
    return errors


def check_reference(bundle_root, reference, *, role=None):
    """Validate an optional product's routing reference, not a policy copy.

    No product command, Git operation or memory/tool access is performed.
    Historical policy paths remain provenance; they are not loaded as active.
    """
    errors = []
    try:
        reference = Path(reference)
        value = _json(_read(reference.parent.resolve(), reference.name))
        if not isinstance(value, dict):
            return ['Policy reference must be an object']
        fixed = {'schema': 'uif-worker-policy-reference-v1',
                 'policy_id': 'uif-worker-policy', 'required_policy_version': VERSION,
                 'distribution': 'environment-owned-overlay',
                 'maintenance_repository': 'maldous/uif', 'maintenance_path': POLICY_PATH,
                 'manifest_path': MANIFEST_PATH,
                 'installed_digest_source': 'selected execution environment manifest'}
        for key, expected in fixed.items():
            if value.get(key) != expected:
                errors.append(f'Invalid policy reference {key}')
        if 'sha256' in value:
            errors.append('A routing reference cannot assert an installed policy digest')
        profiles = _json(_read(Path(bundle_root).resolve(), PROFILES_PATH))
        selected_role = value.get('environment_profile')
        selected = profiles.get('logical_roles', {}).get(selected_role)
        if not isinstance(selected, dict) or selected_role == 'cloud':
            errors.append('Invalid policy reference project role')
        elif value.get('repository') not in selected.get('repository_binding', []):
            errors.append('Policy reference repository does not belong to its role')
        if role is not None and selected_role != role:
            errors.append('Policy reference differs from selected role')
        if 'repository_surface' in value and value['repository_surface'] != value.get('repository'):
            errors.append('Policy reference repository surface mismatch')
        for key in ('historical_policy_path', 'historical_manifest_path'):
            name = value.get(key)
            if name is not None and (not isinstance(name, str) or not name
                    or '\\' in name or PurePosixPath(name).is_absolute()
                    or PurePosixPath(name).as_posix() != name
                    or any(part in ('', '.', '..') for part in name.split('/'))):
                errors.append(f'Nonportable policy reference {key}')
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        errors.append(f'Unreadable policy reference: {exc}')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repositories', nargs='*', help='bundle_repository=local_checkout (legacy CLI spelling)')
    parser.add_argument('--bundle', type=Path, help='environment-owned UIF bundle checkout')
    parser.add_argument('--role', choices=sorted(ROLES))
    parser.add_argument('--project-repository', help='selected repository identity; no product files are read')
    parser.add_argument('--reference', type=Path, action='append', default=[],
                        help='optional product routing-reference JSON; no policy copy is required')
    args = parser.parse_args(argv)
    if args.bundle and args.repositories:
        parser.error('Use --bundle or repository=checkout, not both')
    if not args.bundle and not args.repositories:
        parser.error('Supply --bundle or repository=checkout')
    if args.reference and not args.bundle:
        parser.error('--reference requires --bundle')
    source = Path(__file__).resolve().parents[2] / POLICY_PATH
    try:
        expected = source.read_bytes()
    except OSError as exc:
        parser.error(f'Cannot read selected UIF policy: {exc}')
    targets = [('environment bundle', args.bundle)] if args.bundle else []
    for item in args.repositories:
        repository, separator, path = item.partition('=')
        if not separator or not repository or not path:
            parser.error('Use repository=local_checkout')
        targets.append((repository, path))
    errors = []
    for repository, path in targets:
        identity = None if args.bundle else repository
        errors.extend(f'{repository}: {error}' for error in check(
            path, expected, identity, role=args.role, project_repository=args.project_repository))
    for reference in args.reference:
        errors.extend(f'{reference}: {error}' for error in check_reference(args.bundle, reference, role=args.role))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'PASS: {len(targets)} environment policy bundle(s), version {VERSION}. '
          'Declared source conformance only; no installation, runtime or publication proof.')


if __name__ == '__main__':
    main()
