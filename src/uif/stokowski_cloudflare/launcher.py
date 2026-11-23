"""Protected launch binding for the native Stokowski controller."""

import asyncio
from contextvars import ContextVar
import json
from pathlib import Path
import re
import stat
import subprocess

from .budget import ProjectAccountRouter
from .client import WorkersAIClient
from .native import NativeTurnBinding, install_native_runner
from .turn import Attempt


def _protected_json(path: Path):
    for part in (*path.parents, path):
        info = part.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Workers AI owner policy must be root-controlled')
    return json.loads(path.read_text())


def configure(runner, orchestration, publication, *, policy: Path, database: Path):
    """Bind the provider to existing native custody before starting intake."""
    config = _protected_json(policy)
    accounts = config['accounts']
    projects = frozenset(config['projects'])
    if not projects <= accounts.keys():
        raise ValueError('Unknown native project in Workers AI policy')
    credentials = Path(config['credentials'])
    if not credentials.is_absolute():
        raise ValueError('Expected absolute Workers AI credentials directory')
    router = ProjectAccountRouter(database, accounts)
    reverse = {account: project for project, account in accounts.items()}

    def token(account):
        record = _protected_json(credentials / (reverse[account] + '.json'))
        if record['account_id'] != account or record['plan'] != 'free':
            raise ValueError('Unverified Workers AI account')
        return record['token']

    client = WorkersAIClient(router, token, gateways=config.get('gateways'))
    context = ContextVar('native_workers_ai_owner')
    original_worker = orchestration.Orchestrator._run_worker
    original_dispatch = orchestration.Orchestrator._dispatch

    def admissions():
        return _protected_json(policy)['admissions']

    def dispatch(owner, issue, attempt_num=None):
        if owner.project_name not in projects or issue.id not in admissions():
            owner._release_slot(issue.id)
            return None
        return original_dispatch(owner, issue, attempt_num)

    async def worker(owner, issue, attempt):
        context_token = context.set(owner)
        try:
            return await original_worker(owner, issue, attempt)
        finally:
            context.reset(context_token)

    def active_projects():
        owner = context.get()
        return {name for name in projects if owner.pool is not None and not owner.pool.is_paused(name)}

    def authorize(issue, attempt, workspace):
        owner = context.get()
        record = admissions().get(issue.id, {}).get(attempt.state_name)
        if (record is None or owner.running.get(issue.id) is not attempt
                or owner._tasks.get(issue.id) is not asyncio.current_task()
                or issue.id not in owner.claimed or record['project'] != owner.project_name
                or owner.project_name not in active_projects()
                or record['scope_sha256'] != publication.scope(issue)):
            raise ValueError('Workers AI attempt does not hold approved native custody')
        workspace = workspace.resolve(strict=True)
        if workspace != owner.cfg.workspace.resolved_root().resolve() / issue.identifier:
            raise ValueError('Workers AI workspace is outside native custody')

        def git(*args):
            return subprocess.check_output(['git', '-C', str(workspace), *args], timeout=10, text=True)

        if git('rev-parse', 'HEAD').strip() != record['source_head']:
            raise ValueError('Workers AI attempt source has moved')
        paths = tuple(filter(None, git('ls-files', '-z').split('\0')))
        roots = tuple(record['write_roots'])
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', root)
               or any(part in {'.', '..', '.git', '.stokowski'} for part in root.split('/'))
               for root in roots):
            raise ValueError('Invalid Workers AI write scope')
        return NativeTurnBinding(issue.id, workspace,
            Attempt(owner.project_name, record['attempt'], record.get('review_of'),
                    record.get('reviewer_project')), paths, roots)

    install_native_runner(runner, orchestration, mode='cloudflare', client=client,
                          authorize_turn=authorize, codex_available=lambda _: False,
                          active_projects=active_projects)
    orchestration.Orchestrator._dispatch = dispatch
    orchestration.Orchestrator._run_worker = worker
