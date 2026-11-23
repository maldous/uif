"""Protected launch binding for the native Stokowski controller."""

import asyncio
from contextvars import ContextVar
import json
from pathlib import Path
import re
import signal
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


async def _start_with_shutdown(owner, start):
    """Connect service signals to native stop and await its drain."""
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, stop_requested.set)
    startup = asyncio.create_task(start(owner))
    request = asyncio.create_task(stop_requested.wait())
    try:
        done, _ = await asyncio.wait((startup, request), return_when=asyncio.FIRST_COMPLETED)
        if request in done and not startup.done():
            await owner.stop()
        return await startup
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        for sig in signals:
            loop.remove_signal_handler(sig)


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
    reverse = {account: project for project, account in accounts.items()}

    def token(account):
        record = _protected_json(credentials / (reverse[account] + '.json'))
        if record['account_id'] != account or record['plan'] != 'free':
            raise ValueError('Unverified Workers AI account')
        return record['token']

    eligible = set()
    for project, account in accounts.items():
        try:
            if token(account):
                eligible.add(project)
        except (OSError, ValueError, KeyError):
            continue
    router = ProjectAccountRouter(database, accounts, eligible_projects=eligible)
    client = WorkersAIClient(router, token, gateways=config.get('gateways'))
    context = ContextVar('native_workers_ai_owner')
    authorized = ContextVar('native_workers_ai_authorized', default=None)
    original_worker = orchestration.Orchestrator._run_worker
    original_dispatch = orchestration.Orchestrator._dispatch
    original_start = orchestration.MultiOrchestrator.start
    logger = orchestration.logger

    def admissions():
        return _protected_json(policy)['admissions']

    def dispatch(owner, issue, attempt_num=None):
        stages = admissions().get(issue.id)
        if (owner.project_name not in projects or not stages or owner.pool is None
                or owner.pool.is_paused(owner.project_name)):
            owner._release_slot(issue.id)
            return None
        state = owner._issue_current_state.get(issue.id) or owner._entry_state_for(issue)
        stage = owner._states_for(issue).get(state)
        if (orchestration.state_key(issue.state) in {'backlog', 'review', 'blocked'}
                and stage and stage.type == 'agent' and not stage.admission_review):
            state = owner._entry_state_for(issue)
            stage = owner._states_for(issue).get(state)
        record = stages.get(state)
        if (record is None or record['project'] != owner.project_name
                or record['scope_sha256'] != publication.scope(issue)):
            owner._release_slot(issue.id)
            return None
        if stage and stage.type == 'agent' and router.has_started(record['attempt']):
            retry = owner.retry_attempts.get(issue.id)
            if retry is not None:
                if retry.error != 'Original Workers AI request retained for reconciliation':
                    logger.warning('Workers AI retry held for reconciliation: %s', issue.identifier)
                retry.error = 'Original Workers AI request retained for reconciliation'
            owner._release_slot(issue.id)
            return None
        return original_dispatch(owner, issue, attempt_num)

    async def worker(owner, issue, attempt):
        context_token = context.set(owner)
        authorization_token = authorized.set(None)
        try:
            return await original_worker(owner, issue, attempt)
        finally:
            authorized.reset(authorization_token)
            context.reset(context_token)

    def active_projects():
        owner = context.get()
        binding = authorized.get()
        if binding is not None:
            issue_id, stage, attempt, record = binding
            if (owner.running.get(issue_id) is not attempt or issue_id not in owner.claimed
                    or admissions().get(issue_id, {}).get(stage) != record):
                return set()
        return {name for name in projects if owner.pool is not None and not owner.pool.is_paused(name)}

    def authorize(issue, attempt, workspace):
        owner = context.get()
        record = admissions().get(issue.id, {}).get(attempt.state_name)
        if record is None or record['project'] != owner.project_name:
            raise ValueError('Workers AI stage has no approved native binding')
        if (owner.running.get(issue.id) is not attempt
                or owner._tasks.get(issue.id) is not asyncio.current_task()
                or issue.id not in owner.claimed):
            raise ValueError('Original native task or attempt no longer holds custody')
        if owner.project_name not in active_projects():
            raise ValueError('Workers AI project is paused')
        if record['scope_sha256'] != publication.scope(issue):
            raise ValueError('Workers AI issue scope has changed since approval')
        workspace = workspace.resolve(strict=True)
        if workspace != owner.cfg.workspace.resolved_root().resolve() / issue.identifier:
            raise ValueError('Workers AI workspace is outside native custody')

        def git(*args):
            return subprocess.check_output(['git', '-C', str(workspace), *args], timeout=10, text=True)

        if (git('rev-parse', 'HEAD').strip() != record['source_head']
                or git('branch', '--show-current').strip() != record['branch']):
            raise ValueError('Workers AI attempt source has moved')
        tracked = set(filter(None, git('ls-files', '-z').split('\0')))
        paths = tuple(record['read_paths'])
        if not set(paths) <= tracked:
            raise ValueError('Workers AI read scope includes an untracked file')
        prompt = record['prompt']
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Workers AI requires a scoped owner prompt')
        if attempt.state_name == 'assess':
            routing = json.dumps({'issue': issue.identifier, 'state': attempt.state_name,
                                  'run': owner._issue_state_runs.get(issue.id, 1)})
            prompt += (
                '\nUse this authoritative native routing binding in the final JSON: ' + routing
                + '\nUse verdict approve, deferred, or blocked. Approve only when supported by '
                'the scoped owner instructions and observed evidence. For approve the routing '
                'object also requires actionable=true, ownership_clear=true, deferred=false, '
                'owner_pause=false, uncertain_effects=false and missing_inputs=[]. '
                'When evidence is missing, report the gap.'
            )
        roots = tuple(record['write_roots'])
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', root)
               or any(part in {'.', '..', '.git', '.stokowski'} for part in root.split('/'))
               for root in roots):
            raise ValueError('Invalid Workers AI write scope')
        authorized.set((issue.id, attempt.state_name, attempt, record))
        return NativeTurnBinding(issue.id, workspace,
            Attempt(owner.project_name, record['attempt'], record.get('review_of'),
                    record.get('reviewer_project')), paths, roots, prompt)

    install_native_runner(runner, orchestration, mode='cloudflare', client=client,
                          authorize_turn=authorize, codex_available=lambda _: False,
                          active_projects=active_projects)
    orchestration.Orchestrator._dispatch = dispatch
    orchestration.Orchestrator._run_worker = worker

    async def start(owner):
        return await _start_with_shutdown(owner, original_start)

    orchestration.MultiOrchestrator.start = start
