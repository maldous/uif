"""Protected launch binding for the native Stokowski controller."""

import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess

from .budget import PAID_ACCOUNT, PROJECTS, ProjectAccountRouter
from .client import MODEL, WorkersAIClient
from .intake import READY_STATES, intake_profile, intake_stages
from .native import NativeTurnBinding, install_native_runner
from .review import prepare_review
from .turn import Attempt


def _protected_json(path: Path):
    for part in (*path.parents, path):
        info = part.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Workers AI owner policy must be root-controlled')
    return json.loads(path.read_text())


def _codex_is_ready(attested_until, now):
    if not isinstance(attested_until, str) or not attested_until.endswith('Z'):
        return False
    try:
        expiry = datetime.fromisoformat(attested_until[:-1] + '+00:00')
    except ValueError:
        return False
    return expiry > now and shutil.which('codex') is not None


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
    mode = config.get('provider_mode', 'cloudflare')
    if mode not in {'cloudflare', 'codex', 'both'}:
        raise ValueError('Unknown Stokowski provider mode')
    accounts = config['accounts']
    projects = frozenset(config['projects'])
    if not projects <= PROJECTS or not projects <= accounts.keys():
        raise ValueError('Unknown native project in Workers AI policy')
    credentials = Path(config['credentials'])
    if not credentials.is_absolute():
        raise ValueError('Expected absolute Workers AI credentials directory')
    reverse = {account: project for project, account in accounts.items()}

    def token(account):
        name = reverse[account]
        if name == PAID_ACCOUNT:
            current = _protected_json(policy)
            if (current.get('paid_workers_ai_exclusive') is not True
                    or current['accounts'].get(PAID_ACCOUNT) != account):
                raise ValueError('Paid Workers AI requires exclusive ledger usage')
            plan = 'paid'
        else:
            plan = 'free'
        record = _protected_json(credentials / (name + '.json'))
        if record['account_id'] != account or record['plan'] != plan:
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
    client = WorkersAIClient(router, token, gateways=config.get('gateways'),
                             model=config.get('model', MODEL))
    context = ContextVar('native_workers_ai_owner')
    authorized = ContextVar('native_workers_ai_authorized', default=None)
    original_worker = orchestration.Orchestrator._run_worker
    original_dispatch = orchestration.Orchestrator._dispatch
    original_eligible = orchestration.Orchestrator._is_eligible
    original_start = orchestration.MultiOrchestrator.start
    logger = orchestration.logger

    def automatic_profile(current, owner, issue):
        if (mode != 'cloudflare' or current.get('provider_mode', 'cloudflare') != mode
                or owner.project_name not in current['projects']
                or issue.id in current['admissions']):
            return None
        return intake_profile(current, owner.project_name, issue)

    def admissions(owner, issue):
        current = _protected_json(policy)
        explicit = current['admissions'].get(issue.id)
        if explicit is not None:
            return explicit
        profile = automatic_profile(current, owner, issue)
        if profile is None:
            return {}
        return intake_stages(profile, owner.project_name, issue, publication.scope(issue),
                             owner._issue_state_runs.get(issue.id, 1))

    if mode == 'cloudflare' and config.get('project_intake'):
        original_entry = orchestration.Orchestrator._entry_state_for
        original_resolve = orchestration.Orchestrator._resolve_current_state

        def entry(owner, issue):
            profile = automatic_profile(_protected_json(policy), owner, issue)
            if profile is not None and orchestration.state_key(issue.state) in READY_STATES:
                return 'assess'
            return original_entry(owner, issue)

        async def resolve(owner, issue, *, comments=None):
            profile = automatic_profile(_protected_json(policy), owner, issue)
            if (profile is None or orchestration.state_key(issue.state) != 'active'
                    or issue.id in owner._issue_current_state or issue.id in owner.claimed
                    or owner._settlement_path(issue).exists()):
                return await original_resolve(owner, issue, comments=comments)
            if comments is None:
                comments = await owner._publication_comments(issue.id)
            state, run = await original_resolve(owner, issue, comments=comments)
            if orchestration.parse_latest_tracking(comments) is None:
                owner._issue_current_state[issue.id] = 'assess'
                return 'assess', run
            return state, run

        orchestration.Orchestrator._entry_state_for = entry
        orchestration.Orchestrator._resolve_current_state = resolve

    def scoped_workspace(owner, issue):
        base = owner.cfg.workspace.resolved_root().resolve()
        workspace = base / issue.identifier
        if workspace.parent != base or workspace.resolve(strict=True) != workspace:
            raise ValueError('Workers AI worktree is outside native custody')
        return workspace

    def checkout_source(workspace, record):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(workspace), *args],
                                           timeout=10, text=True).strip()

        if (git('remote', 'get-url', 'origin') != record['project_intake']['origin']
                or git('status', '--porcelain', '--untracked-files=all', '--', '.', ':!.stokowski')):
            raise ValueError('Project intake requires a clean native checkout of the approved origin')
        return dict(record, source_head=git('rev-parse', 'HEAD'),
                    branch=git('branch', '--show-current'))

    def automatic_stage_allowed(owner, issue, state, record, stages):
        if state == 'assess':
            linear_state = orchestration.state_key(issue.state)
            if any(not blocker.state or orchestration.state_key(blocker.state) not in {
                    orchestration.state_key(name) for name in owner.cfg.terminal_linear_states()
            } for blocker in getattr(issue, 'blocked_by', ())):
                return False
            return linear_state in READY_STATES or linear_state == 'active'
        try:
            workspace = scoped_workspace(owner, issue)
            run = owner._issue_state_runs.get(issue.id, 1)
            if state == 'investigate':
                prior = publication.read(workspace, Path('.stokowski/settlement.json'))
                return (prior.get('issue_id') == issue.id and prior.get('run') == run
                        and prior.get('scope_sha256') == record['scope_sha256']
                        and prior.get('state') == 'assess' and prior.get('phase') == 'complete'
                        and prior.get('event') == 'complete' and prior.get('target') == state
                        and prior.get('session_id') == stages['assess']['attempt'])
            if state == 'publish':
                prior = publication.read(workspace, publication.RECORD)
                binding = prior['binding']
                return (binding['issue_id'] == issue.id and binding['run'] == run
                        and binding['scope_sha256'] == record['scope_sha256']
                        and binding['kind'] == 'source-free'
                        and publication.full_accept(prior.get('accepted_review'), binding))
        except (OSError, ValueError, KeyError):
            return False
        return False

    def codex_available(_):
        current = _protected_json(policy)
        readiness = current.get('codex_ready_until', {})
        return (current.get('provider_mode') == 'both'
                and isinstance(readiness, dict)
                and _codex_is_ready(readiness.get(context.get().project_name),
                                    datetime.now(timezone.utc)))

    def available_projects(owner):
        return {name for name in projects if owner.pool is not None and not owner.pool.is_paused(name)}

    def current_admission(owner, issue, state, record):
        stages = admissions(owner, issue)
        author = record.get('automatic_author')
        if author is not None:
            return (state == 'independent-review' and state not in stages
                    and stages.get('investigate') == author)
        return stages.get(state) == record

    def dispatch_stage(owner, issue):
        state = owner._issue_current_state.get(issue.id) or owner._entry_state_for(issue)
        stage = owner._states_for(issue).get(state)
        if (orchestration.state_key(issue.state) in {'backlog', 'review', 'blocked'}
                and stage and stage.type == 'agent' and not stage.admission_review):
            state = owner._entry_state_for(issue)
            stage = owner._states_for(issue).get(state)
        return state, stage

    def eligible(owner, issue, **kwargs):
        if owner.project_name not in projects or not original_eligible(owner, issue, **kwargs):
            return False
        stages = admissions(owner, issue)
        assess = stages.get('assess', {})
        if 'project_intake' in assess:
            state, _ = dispatch_stage(owner, issue)
            if state == 'independent-review':
                return True
            return (state in stages
                    and automatic_stage_allowed(owner, issue, state, stages[state], stages))
        return any(record['project'] == owner.project_name
                   and record['scope_sha256'] == publication.scope(issue)
                   for record in stages.values())

    def dispatch(owner, issue, attempt_num=None):
        stages = admissions(owner, issue)
        if (owner.project_name not in projects or not stages or owner.pool is None
                or owner.pool.is_paused(owner.project_name)):
            owner._release_slot(issue.id)
            return None
        state, stage = dispatch_stage(owner, issue)
        record = stages.get(state)
        if (record and 'project_intake' in record
                and not automatic_stage_allowed(owner, issue, state, record, stages)):
            owner._release_slot(issue.id)
            return None
        if record is None and state == 'independent-review':
            author = stages.get('investigate')
            if (author and author.get('automatic_review') is True
                    and author['project'] == owner.project_name
                    and author['scope_sha256'] == publication.scope(issue)):
                reviewer = router.independent_reviewer(
                    owner.project_name, author['attempt'], available_projects(owner),
                    client.request_maximum)
                if reviewer is not None:
                    try:
                        workspace = scoped_workspace(owner, issue)
                        source = checkout_source(workspace, author) if 'project_intake' in author else author
                        record = prepare_review(workspace, issue,
                                                owner._issue_state_runs.get(issue.id, 1),
                                                source, reviewer, publication)
                        record['automatic_author'] = author
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        logger.warning('Automatic Workers AI review held: %s (%s)',
                                       issue.identifier, type(error).__name__)
        if record is None:
            retry = owner.retry_attempts.get(issue.id)
            if retry is not None and any(
                    item['project'] == owner.project_name
                    and item['scope_sha256'] == publication.scope(issue) for item in stages.values()):
                owner._schedule_retry(issue, attempt_num=retry.attempt, delay_ms=30_000,
                                      error='Waiting for owner-approved Workers AI stage')
            owner._release_slot(issue.id)
            return None
        if (record['project'] != owner.project_name
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
        previous = owner.running.get(issue.id)
        result = original_dispatch(owner, issue, attempt_num)
        current = owner.running.get(issue.id)
        if current is not None and current is not previous and record.get('automatic_author'):
            current.workers_ai_review = record
        return result

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
            issue, stage, attempt, record = binding
            issue_id = issue.id
            if (owner.running.get(issue_id) is not attempt or issue_id not in owner.claimed
                    or not current_admission(owner, issue, stage, record)):
                return set()
        return available_projects(owner)

    def authorize(issue, attempt, workspace):
        owner = context.get()
        stages = admissions(owner, issue)
        record = getattr(attempt, 'workers_ai_review', None)
        if record is None:
            record = stages.get(attempt.state_name)
        if record is None or record['project'] != owner.project_name:
            raise ValueError('Workers AI stage has no approved native binding')
        if not current_admission(owner, issue, attempt.state_name, record):
            raise ValueError('Workers AI stage authorization has changed')
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
        selected = record
        if 'project_intake' in record and attempt.state_name != 'independent-review':
            if not automatic_stage_allowed(owner, issue, attempt.state_name, record, stages):
                raise ValueError('Project intake continuation no longer has native authority')
            selected = checkout_source(workspace, record)

        def git(*args):
            return subprocess.check_output(['git', '-C', str(workspace), *args], timeout=10, text=True)

        head = git('rev-parse', 'HEAD').strip()
        branch = git('branch', '--show-current').strip()
        review_binding = None
        if attempt.state_name == 'independent-review':
            review_binding = publication.read(workspace, publication.BINDING)
            run = owner._issue_state_runs.get(issue.id, 1)
            if (review_binding['issue_id'] != issue.id or review_binding['run'] != run
                    or review_binding['state'] != attempt.state_name
                    or review_binding['scope_sha256'] != record['scope_sha256']):
                raise ValueError('Native review checkpoint does not bind the admitted issue and run')
        if review_binding and review_binding['kind'] == 'git':
            if (head != review_binding['candidate'] or branch != review_binding['branch']
                    or git('show', '-s', '--format=%P', 'HEAD').strip() != record['source_head']
                    or git('rev-parse', 'HEAD^{tree}').strip() != record['candidate_tree']):
                raise ValueError('Native review checkpoint differs from the admitted candidate tree')
        elif (head != selected['source_head'] or branch != selected['branch']
              or review_binding and (review_binding['kind'] != 'source-free'
                                     or review_binding['candidate'] != issue.id)):
            raise ValueError('Workers AI attempt source has moved')
        if (review_binding is not None
                and git('status', '--porcelain', '--untracked-files=all',
                        '--', '.', ':!.stokowski').strip()):
            raise ValueError('Workers AI review requires a clean candidate worktree')
        tracked = set(filter(None, git('ls-files', '-z').split('\0')))
        paths = tuple(record['read_paths'])
        if not set(paths) <= tracked:
            raise ValueError('Workers AI read scope includes an untracked file')
        prompt = record['prompt']
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Workers AI requires a scoped owner prompt')
        if attempt.state_name == 'assess':
            routing = {
                'issue': issue.identifier, 'state': attempt.state_name,
                'run': owner._issue_state_runs.get(issue.id, 1),
            }
            prompt += (
                '\nAssess readiness to start the bounded work. The owner instructions above '
                'form the authorized scope. An exclusion is not a missing input unless it '
                'prevents the bounded work itself. Do not claim implementation, tool use or '
                'tests at assessment. Use this final fenced JSON shape when approval is supported:\n'
                + json.dumps({
                    'summary': 'Explain why the bounded work can start.',
                    'verdict': 'approve',
                    'data_sources': [{'name': 'Owner-approved task scope',
                                      'how_verified': 'Describe the scope and its actual limits.'}],
                    'claims': [],
                    'verification': [],
                    'routing': dict(routing, actionable=True, ownership_clear=True,
                                    deferred=False, owner_pause=False, uncertain_effects=False,
                                    missing_inputs=[]),
                })
                + '\nFor a genuine dependency or unresolved outcome use verdict blocked or '
                'deferred, explain it, and retain the exact issue, state, and run in routing.'
            )
        if review_binding is not None:
            prompt += '\nNative review binding for the admitted candidate: ' + json.dumps(review_binding)
        if 'project_intake' in record and attempt.state_name == 'publish':
            acceptance = publication.read(workspace, publication.RECORD)
            prompt += '\nAccepted source-free native report: ' + json.dumps(acceptance['accepted_review'])
        roots = tuple(record['write_roots'])
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', root)
               or any(part in {'.', '..', '.git', '.stokowski'} for part in root.split('/'))
               for root in roots):
            raise ValueError('Invalid Workers AI write scope')
        authorized.set((issue.id, attempt.state_name, attempt, record))
        return NativeTurnBinding(issue.id, workspace,
            Attempt(owner.project_name, record['attempt'], record.get('review_of'),
                    record.get('reviewer_project')), paths, roots, prompt)

    install_native_runner(runner, orchestration, mode=mode, client=client,
                          authorize_turn=authorize, codex_available=codex_available,
                          active_projects=active_projects)
    orchestration.Orchestrator._dispatch = dispatch
    orchestration.Orchestrator._is_eligible = eligible
    orchestration.Orchestrator._run_worker = worker

    async def start(owner):
        return await _start_with_shutdown(owner, original_start)

    orchestration.MultiOrchestrator.start = start
