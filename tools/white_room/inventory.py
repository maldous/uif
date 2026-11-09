"""Bounded, read-only Git inventory; never fetch, reset, clean or read file bodies."""
import argparse
import json
import os
import subprocess
import selectors
import time
from datetime import datetime, timezone
from pathlib import Path

DECLARATIONS = {'AGENTS.md', 'pyproject.toml', 'package.json', 'package-lock.json',
                'uv.lock', 'requirements.txt', 'Cargo.toml', 'Cargo.lock', 'go.mod',
                'go.sum', 'mix.exs', 'mix.lock', 'compose.yaml', 'docker-compose.yml'}

def inventory(root):
    root = Path(root).resolve(strict=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    def git(*args, allowed=(0,)):
        argv = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull,
                '-c', 'core.untrackedCache=false', '-C', str(root), *args]
        with subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            chunks = bytearray()
            deadline = time.monotonic() + 15
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError('inventory timeout')
                        if not selector.select(remaining):
                            raise ValueError('inventory timeout')
                        chunk = os.read(proc.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        chunks.extend(chunk)
                        if len(chunks) > 4 * 1024 * 1024:
                            raise ValueError('inventory output budget exceeded')
                    if proc.wait(timeout=max(0.01, deadline - time.monotonic())) not in allowed:
                        raise ValueError('git inventory failed')
                except BaseException:
                    proc.kill()
                    proc.wait()
                    raise
            return chunks.decode('utf-8', errors='strict')
    top = Path(git('rev-parse', '--show-toplevel').strip()).resolve()
    if top != root:
        raise ValueError('select exact checkout root')
    if git('config', '--name-only', '--get-regexp', r'^(extensions\.partialclone|remote\..*\.promisor)$', allowed=(0, 1)).strip():
        raise ValueError('partial/promisor checkout requires separate qualification')
    files = git('ls-tree', '-r', '--name-only', '-z', 'HEAD').split('\0')
    declarations = [p for p in files if p and (Path(p).name in DECLARATIONS or
                     p.startswith(('config/', '.github/', '.agents/')) or p.endswith('.service'))]
    return {'version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'root': str(root), 'head': git('rev-parse', 'HEAD').strip(),
            'branch': git('symbolic-ref', '--quiet', '--short', 'HEAD').strip(),
            'dirty': None, 'dirty_state': 'unknown',
            'local_refs': git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads/').splitlines(),
            'declaration_paths': declarations, 'remote_verified': False,
            'discovery_complete': False,
            'limitations': ['No remote authentication or freshness inferred from tracking refs.',
                           'No worktree status/diff, file bodies, environment values, credentials or data read.',
                           'Worktrees, unpushed work and external services require further reconciliation.']}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    args = parser.parse_args()
    try:
        print(json.dumps(inventory(args.root), indent=2))
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({'error': 'inventory_failed', 'discovery_complete': False}))
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
