"""Offline instruction-distribution checker; not runtime admission/enforcement."""
import argparse
import hashlib
import json
from pathlib import Path

POLICY_PATH = 'docs/agents/uif-worker-policy.md'
MANIFEST_PATH = 'docs/agents/uif-worker-policy.json'


def check(root, expected_policy, expected_repository=None):
    root = Path(root).resolve()
    errors = []
    try:
        for name in (POLICY_PATH, MANIFEST_PATH, 'AGENTS.md'):
            path = root / name
            if not path.resolve().is_relative_to(root) or path.is_symlink():
                return [f'Nonportable instruction path: {name}']
        manifest = json.loads((root / MANIFEST_PATH).read_text())
        if not isinstance(manifest, dict):
            return ['Manifest must be an object']
        policy = (root / POLICY_PATH).read_bytes()
        agents = (root / 'AGENTS.md').read_text()
        fixed = {'schema': 'uif-worker-policy-manifest-v1',
                 'policy_id': 'uif-worker-policy', 'version': '1.2.4',
                 'policy_path': POLICY_PATH, 'maintenance_repository': 'maldous/uif',
                 'maintenance_path': POLICY_PATH, 'distribution': 'reviewed-local-copy'}
        for key, value in fixed.items():
            if manifest.get(key) != value:
                errors.append(f'Invalid {key}')
        if expected_repository and manifest.get('repository') != expected_repository:
            errors.append('Repository identity mismatch')
        if hashlib.sha256(policy).hexdigest() != manifest.get('sha256'):
            errors.append('Policy hash mismatch')
        if policy != expected_policy:
            errors.append('Policy differs from selected UIF source')
        if POLICY_PATH not in agents or MANIFEST_PATH not in agents:
            errors.append('Missing root startup references')
        for data in (policy, (root / 'AGENTS.md').read_bytes()):
            if b'\r' in data or not data.endswith(b'\n'):
                errors.append('Instructions require LF and final newline')
    except (OSError, ValueError) as exc:
        errors.append(f'Unreadable instruction input: {exc}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repositories', nargs='+', help='repository=local_checkout')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2] / POLICY_PATH
    expected = source.read_bytes()
    errors = []
    for item in args.repositories:
        repository, separator, path = item.partition('=')
        if not separator or not repository or not path:
            parser.error('Use repository=local_checkout')
        errors.extend(f'{repository}: {error}' for error in check(path, expected, repository))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'PASS: {len(args.repositories)} local policy copies. No runtime or publication proof.')


if __name__ == '__main__':
    main()
