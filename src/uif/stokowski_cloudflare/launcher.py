"""Protected launch binding for the native Stokowski controller."""

import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from inspect import getclosurevars, getsource
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import time

from .account_admission import FreeRoute
from .budget import BudgetRefused, PAID_ACCOUNT, PROJECTS, ProjectAccountRouter
from .client import MODEL, WorkersAIClient
from .intake import READY_STATES, intake_profile, intake_stages
from .native import NativeTurnBinding, install_native_runner
from .review import prepare_review
from .turn import Attempt


@dataclass
class _RecipientDispatch:
    owner: object
    issue: object
    record: dict
    claim: object
    attempt: object = None
    started: bool = False
    stage: str = 'assess'


def _recipient_native_prologue(orchestration, dispatch, worker, authority):
    """Qualify the native prologue and the reconciliation it relies on."""
    native = orchestration.Orchestrator
    reconcile = getattr(native, '_reconcile', None)
    terminal = getattr(native, '_observe_terminal', None)
    original_reconcile = getattr(reconcile, '__wrapped__', None)
    workspace = getattr(orchestration, 'ensure_workspace', None)
    globals_ = getattr(workspace, '__globals__', {})
    result = globals_.get('WorkspaceResult')
    reviewed = (
        (dispatch, 'da63ce2fe67723fe9e362cd8eb9c4eed5d69b7267ffffc42e625228c2392cc29'),
        (worker, '47a49c4930cf8a0faf670b6def7326f4f0982de7b8f9b548045899753a0c6482'),
        (original_reconcile, 'd6b4da1c014f6442e8805b7f82834c356696697490a60908a7fbabccfbd7af2d'),
        (terminal, '7a4d04b5824ea78596adc52a2eb9ada05aa9b05c464fba47fc59c5ba642672e9'),
        (workspace, '647eacc835b9c1b2698c6990cc3168ef35db379a1263f969ea1f02192f80c6be'),
        (result, '336dbd12bfcdc495bf9e7840f6a738ba8a64f88a5a2008c229b2cff5e8fbf9b3'),
    )
    if any(getattr(value, '__globals__', None) is not vars(orchestration)
           for value in (dispatch, worker, original_reconcile, terminal)):
        return None
    try:
        # The same native file also guards retained workspaces at startup
        # and at terminal transitions, outside the running worker.
        source = Path(worker.__code__.co_filename)
        if (source != Path(orchestration.__file__) or sha256(source.read_bytes()).hexdigest()
                != '59b063610dea19c18b736d3949607712f4c3ad0d8187c9956e5d6019b29d0352'):
            return None
        # getsource(function) unwraps decorators. Review the actual composition
        # shim and verify its closed-over native callable before unwrapping it.
        if (sha256(getsource(reconcile.__code__).encode()).hexdigest()
                != '2e3d003b006b5259e9ee6793c3815cfdaf665768003863e6060c64347d085ba3'
                or getclosurevars(reconcile).nonlocals != {
                    'composition': authority, 'orchestration': orchestration,
                    'original_reconcile': original_reconcile,
                }):
            return None
        if all(not hasattr(value, '__wrapped__') and sha256(getsource(value).encode()).hexdigest() == pin
               for value, pin in reviewed):
            return workspace, result, reconcile, terminal
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None


def _idle_notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.settimeout(0.1)
                notify.sendto(message.encode(), address)
        except OSError:
            pass  # The same hold is also recorded in the service journal.


def wait_for_normal_authority(verify):
    """Keep the commissioned process online; retry only its read-only challenge."""
    reported = False
    while verify() is not True:
        if not reported:
            status = 'Stokowski blocked: authority_unavailable; admissions and effects closed'
            print(status, flush=True)
            _idle_notify(f'STATUS={status}')
            reported = True
        time.sleep(5)
    if reported:
        _idle_notify('STATUS=Stokowski authority reachable; checking normal admission')


def serve_supervised_idle(reason):
    """Keep the guarded service PID alive without constructing a scheduler."""
    if reason not in ('free_route_unavailable', 'free_quota_unavailable'):
        raise ValueError('Unknown stopped idle reason')
    return _serve_idle(f'Stokowski idle: {reason}; admissions and effects closed')


_COMMISSIONED_IDLE_POLL_SECONDS = 5


def serve_commissioned_no_route_idle(verify):
    """Supervise an authenticated mount without an installed provider route."""
    return _serve_idle('Stokowski commissioned idle: free_route_unavailable; '
                       'verified mounted route required; admissions and effects closed', verify=verify)


def _serve_idle(status, *, verify=None):
    async def wait_for_stop():
        loop = asyncio.get_running_loop()
        stopped = asyncio.Event()
        signals = (signal.SIGTERM, signal.SIGINT)
        for sig in signals:
            loop.add_signal_handler(sig, stopped.set)
        try:
            print(status, flush=True)
            _idle_notify(f'READY=1\nSTATUS={status}')
            if verify is None:
                await stopped.wait()
            else:
                last_status = status
                while not stopped.is_set():
                    try:
                        await asyncio.wait_for(stopped.wait(), _COMMISSIONED_IDLE_POLL_SECONDS)
                    except TimeoutError:
                        mounted = await asyncio.to_thread(verify)
                        current = (status if mounted is True else
                                   'Stokowski blocked: authority_unavailable; admissions and effects closed')
                        if current != last_status and not stopped.is_set():
                            print(current, flush=True)
                            _idle_notify(f'STATUS={current}')
                            last_status = current
            _idle_notify('STOPPING=1\nSTATUS=Stokowski idle shutdown')
            print('Stokowski idle shutdown', flush=True)
        finally:
            for sig in signals:
                loop.remove_signal_handler(sig)

    asyncio.run(wait_for_stop())


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
    installed_signals = []
    tasks = []
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_requested.set)
            installed_signals.append(sig)
        startup = asyncio.create_task(start(owner))
        tasks.append(startup)
        request = asyncio.create_task(stop_requested.wait())
        tasks.append(request)
        done, _ = await asyncio.wait((startup, request), return_when=asyncio.FIRST_COMPLETED)
        if request in done and not startup.done():
            await owner.stop()
        return await startup
    finally:
        try:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for sig in reversed(installed_signals):
                loop.remove_signal_handler(sig)


from ..provider_discovery import NormalProviderDiscovery


def configure(runner, orchestration, publication, *, policy: Path, database: Path,
              read_account_recovery=None, read_account_usage=None,
              open_fenced_connection=None, allows_native_dispatch=None, authority_lifecycle=None,
              provider_discovery=None):
    """Bind native custody and an optional trusted, cached account evidence reader."""
    if provider_discovery is not None and type(provider_discovery) is not NormalProviderDiscovery:
        raise ValueError('Expected installed normal provider discovery')
    if read_account_recovery is not None and not callable(read_account_recovery):
        raise ValueError('Expected a Workers AI account recovery reader')
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
    strict_config = config.get('workers_ai_strict_free_routes')
    strict_routes = None
    if 'workers_ai_strict_free_routes' in config:
        if (mode != 'cloudflare' or type(strict_config) is not dict or not strict_config
                or not strict_config.keys() <= PROJECTS
                or not strict_config.keys() <= accounts.keys()
                or any(type(epoch) is not str or not re.fullmatch(r'[a-f0-9]{64}', epoch)
                       for epoch in strict_config.values())):
            raise ValueError('Strict Workers AI requires Cloudflare Free routes and credential epochs')
        gateways = config.get('gateways') or {}
        strict_routes = {
            name: FreeRoute(accounts[name], config.get('model', MODEL),
                            gateways.get(accounts[name]), epoch)
            for name, epoch in strict_config.items()
        }

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

    eligible = set(strict_routes) if strict_routes is not None else set()
    if strict_routes is None:
        for project, account in accounts.items():
            try:
                if token(account):
                    eligible.add(project)
            except (OSError, ValueError, KeyError):
                continue
    router = ProjectAccountRouter(database, accounts, eligible_projects=eligible,
                                  strict_routes=strict_routes)
    client = WorkersAIClient(router, token, gateways=config.get('gateways'),
                             model=config.get('model', MODEL),
                             read_account_usage=read_account_usage,
                             open_fenced_connection=open_fenced_connection)

    if authority_lifecycle is not None:
        from .native_composition import NativeComposition
        if (type(authority_lifecycle) is not NativeComposition or mode != 'cloudflare'
                or projects != {'OffMenu'} or strict_routes != authority_lifecycle.remote.routes):
            raise ValueError('Remote authority requires the exact protected OffMenu route')

    def native_allowed(owner, issue, *, selecting=False):
        if strict_routes is None:
            return True
        if not client.strict_ports_ready or not callable(allows_native_dispatch):
            return False
        try:
            current = _protected_json(policy)
            if (current.get('workers_ai_strict_free_routes') != strict_config
                    or current.get('provider_mode', 'cloudflare') != mode
                    or current.get('accounts') != accounts
                    or owner.project_name not in (current.get('projects') or [])
                    or current.get('model', MODEL) != client.model
                    or (current.get('gateways') or {}) != dict(client.gateways)):
                return False
            if authority_lifecycle is not None:
                return selecting or authority_lifecycle.current(issue.id) is not None
            return allows_native_dispatch(owner.project_name, issue.id) is True
        except (OSError, ValueError):
            return False
        except Exception:
            # A broken trust adapter is a denial. Do not expose its detail or
            # let diagnostics prevent the revoked worker's local cleanup.
            try:
                logger.warning('Workers AI native authority check unavailable; holding dispatch')
            except Exception:
                pass
            return False

    context = ContextVar('native_workers_ai_owner')
    authorized = ContextVar('native_workers_ai_authorized', default=None)
    original_worker = orchestration.Orchestrator._run_worker
    original_dispatch = orchestration.Orchestrator._dispatch
    original_eligible = orchestration.Orchestrator._is_eligible
    original_start = orchestration.MultiOrchestrator.start
    logger = orchestration.logger
    recipient = ContextVar('native_recipient_dispatch', default=None)
    recipient_prologue = None
    if (authority_lifecycle is not None and 'prepare_turn_thread' in NativeTurnBinding.__dataclass_fields__
            and all(callable(getattr(authority_lifecycle, name, None)) for name in (
                'recipient_assignment', 'recipient_first_entry', 'recipient_workspace_for_prologue',
                'recipient_task_active', 'verify_recipient_workspace', 'prepare_recipient_turn_thread',
                'recipient_assessment_finisher',
            ))):
        recipient_prologue = _recipient_native_prologue(
            orchestration, original_dispatch, original_worker, authority_lifecycle)
    successor_worker_ready = (recipient_prologue is not None and mode == 'cloudflare'
        and strict_routes is not None and all(callable(getattr(authority_lifecycle, name, None)) for name in (
            'successor_assignment', 'successor_first_entry', 'successor_workspace_for_prologue',
            'successor_task_active', 'verify_successor_workspace', 'prepare_successor_turn_thread',
            'successor_investigation_finisher',
        )))
    if recipient_prologue is not None:
        original_workspace, workspace_result, _, _ = recipient_prologue

        async def existing_recipient_workspace(workspace_root, issue_identifier, hooks):
            selected = recipient.get()
            if selected is None:
                return await original_workspace(workspace_root, issue_identifier, hooks)
            if (selected.attempt is None or issue_identifier != selected.issue.identifier
                    or not selected_stage_allowed(selected.owner, selected.issue, selected.record, selected.stage)):
                raise ValueError('Recovered native workspace input changed')
            workspace_for_prologue = (authority_lifecycle.successor_workspace_for_prologue
                                      if selected.stage == 'investigate'
                                      else authority_lifecycle.recipient_workspace_for_prologue)
            path = workspace_for_prologue(
                selected.owner, selected.issue, selected.record['scope_sha256'], selected.attempt)
            if path != workspace_root / issue_identifier:
                raise ValueError('Recovered native workspace path changed')
            return workspace_result(path, issue_identifier, False)

        orchestration.ensure_workspace = existing_recipient_workspace

    def explicit_admissions(current, owner, issue):
        stages = current['admissions'].get(issue.id)
        if not stages:
            return {}
        scope = publication.scope(issue)
        return {state: record for state, record in stages.items()
                if record.get('project') == owner.project_name and record.get('scope_sha256') == scope}

    def automatic_profile(current, owner, issue):
        if (mode != 'cloudflare' or current.get('provider_mode', 'cloudflare') != mode
                or owner.project_name not in current['projects']
                or explicit_admissions(current, owner, issue)):
            return None
        return intake_profile(current, owner.project_name, issue)

    def admissions(owner, issue):
        current = _protected_json(policy)
        explicit = explicit_admissions(current, owner, issue)
        if explicit:
            return explicit
        profile = automatic_profile(current, owner, issue)
        if profile is None:
            return {}
        return intake_stages(profile, owner.project_name, issue, publication.scope(issue),
                             owner._issue_state_runs.get(issue.id, 1))

    if mode == 'cloudflare' and config.get('project_intake'):
        original_entry = orchestration.Orchestrator._entry_state_for
        original_resolve = orchestration.Orchestrator._resolve_current_state
        original_enter_gate = orchestration.Orchestrator._enter_gate

        def entry(owner, issue):
            profile = automatic_profile(_protected_json(policy), owner, issue)
            if profile is not None and orchestration.state_key(issue.state) in READY_STATES:
                return 'assess'
            return original_entry(owner, issue)

        async def resolve(owner, issue, *, comments=None):
            profile = automatic_profile(_protected_json(policy), owner, issue)
            linear_state = orchestration.state_key(issue.state)
            if (profile is None or linear_state not in {'active', 'blocked', 'review'}
                    or issue.id in owner._issue_current_state or issue.id in owner.claimed):
                return await original_resolve(owner, issue, comments=comments)
            if comments is None:
                comments = await owner._publication_comments(issue.id)
            unclaimed_checkout = not owner._settlement_path(issue).exists()
            state, run = await original_resolve(owner, issue, comments=comments)
            tracking = orchestration.parse_latest_tracking(comments)
            if (state == 'assess' and tracking and tracking.get('type') == 'state'
                    and tracking.get('state') == state and tracking.get('run') == run
                    and restore_completed_successor(owner, issue, run) is True):
                return 'investigate', run
            if (linear_state == 'active' and unclaimed_checkout and tracking is None):
                owner._issue_current_state[issue.id] = 'assess'
                return 'assess', run
            if (linear_state in {'blocked', 'review'} and state == 'assess' and tracking
                    and tracking.get('type') == 'state' and tracking.get('state') == 'assess'
                    and tracking.get('run') == run):
                owner._announced_states.add((issue.id, state, run))
            return state, run

        async def enter_gate(owner, issue, state, **kwargs):
            if (owner.project_name in config['project_intake']
                    and kwargs.get('assessed_context') is None):
                # Native reread drops the projection when the gate carries no
                # assessed snapshot. Hash the same inputs at gate creation.
                owner._dependency_observations.pop(issue.id, None)
            return await original_enter_gate(owner, issue, state, **kwargs)

        orchestration.Orchestrator._entry_state_for = entry
        orchestration.Orchestrator._resolve_current_state = resolve
        orchestration.Orchestrator._enter_gate = enter_gate

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
        approved = set(record['read_paths'])
        roots = tuple(record.get('read_roots', ()))
        if roots:
            for name in git('ls-files', '-z').split('\0'):
                if (not any(name.startswith(root + '/') for root in roots)
                        or any(part.startswith('.') for part in name.split('/'))):
                    continue
                path = workspace / name
                if (path.suffix in {'.py', '.js', '.mjs', '.ts', '.tsx', '.html', '.css', '.md'}
                        and not path.is_symlink() and path.is_file() and path.stat().st_size <= 24_000):
                    approved.add(name)
        return dict(record, source_head=git('rev-parse', 'HEAD'), branch=git('branch', '--show-current'),
                    read_paths=sorted(approved))

    def author_source(workspace, author, *, establish=False):
        if not author.get('write_roots'):
            return checkout_source(workspace, author)
        path = Path('.stokowski') / 'workers-ai-source' / (author['attempt'] + '.json')
        contract = sha256(json.dumps(author, sort_keys=True).encode()).hexdigest()
        if establish:
            current = checkout_source(workspace, author)
            source = {key: current[key] for key in ('source_head', 'branch', 'read_paths')}
            if not (workspace / path).exists():
                publication.save(workspace, path, dict(source, contract=contract))
        bound = publication.read(workspace, path)
        if bound.pop('contract', None) != contract:
            raise ValueError('Original Workers AI author source binding changed')
        if establish and bound != source:
            raise ValueError('Original Workers AI source has moved')
        return dict(author, **{key: bound[key] for key in ('source_head', 'branch', 'read_paths')})

    def automatic_stage_allowed(owner, issue, state, record, stages):
        if state == 'assess':
            linear_state = orchestration.state_key(issue.state)
            if any(not blocker.state or orchestration.state_key(blocker.state) not in {
                    orchestration.state_key(name) for name in owner.cfg.terminal_linear_states()
            } for blocker in getattr(issue, 'blocked_by', ())):
                return False
            return (linear_state in READY_STATES or linear_state == 'active'
                    or linear_state in {'blocked', 'review'} and (
                        issue.id, state, owner._issue_state_runs.get(issue.id, 1)
                    ) in owner._announced_states)
        try:
            workspace = scoped_workspace(owner, issue)
            run = owner._issue_state_runs.get(issue.id, 1)
            if state == 'investigate':
                if (authority_lifecycle is not None and record.get('write_roots')
                        and not authority_lifecycle.supports_source(issue.id, record)):
                    return False
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

    def restore_completed_successor(owner, issue, run):
        """Return true if restored, false for a held marked edge, or None if unrelated."""
        def still_assess():
            return (owner.project_name == 'OffMenu' and orchestration.state_key(issue.state) == 'active'
                    and owner._issue_current_state.get(issue.id) == 'assess'
                    and owner._issue_state_runs.get(issue.id) == run
                    and all(issue.id not in records for records in (
                        owner.running, owner._tasks, owner.claimed, owner.retry_attempts, owner._pending_gates)))

        assignment = getattr(authority_lifecycle, 'assignment', None)
        if not callable(assignment) or type(run) is not int or run < 1 or not still_assess():
            return None
        stages = admissions(owner, issue)
        investigate = stages.get('investigate')
        if not investigate or not automatic_stage_allowed(owner, issue, 'investigate', investigate, stages):
            return None
        try:
            grant = assignment(owner.project_name, issue.id, investigate['scope_sha256'])
            if type(getattr(grant, 'first_intent_sha256', None)) is not str:
                return None
            if successor_worker_ready:
                claim = authority_lifecycle.successor_assignment(
                    owner, issue, investigate['scope_sha256'], 'investigate', run)
                if (getattr(claim, 'attempt_id', None) == investigate['attempt']
                        and publication.scope(issue) == investigate['scope_sha256']
                        and assignment(owner.project_name, issue.id, investigate['scope_sha256']) == grant
                        and current_admission(owner, issue, 'investigate', investigate) and still_assess()):
                    # Only memory changes. Native dispatch rechecks the same
                    # protected workspace and consumed unentered successor.
                    owner._issue_current_state[issue.id] = 'investigate'
                    return True
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            pass
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

    def recipient_stage_allowed(owner, issue, record):
        stage = owner._states_for(issue).get('assess')
        return (recipient_prologue is not None
                and getattr(getattr(owner, '_reconcile', None), '__func__', None) is recipient_prologue[2]
                and getattr(getattr(owner, '_observe_terminal', None), '__func__', None) is recipient_prologue[3]
                and owner.project_name == 'OffMenu' and owner._issue_current_state.get(issue.id) == 'assess'
                and record and 'project_intake' in record and not record['read_paths'] and not record['write_roots']
                and stage is not None and stage.type == 'agent' and stage.admission_review is True
                and not stage.publication and stage.session == 'fresh' and stage.permission_mode == 'plan'
                and stage.runner in {'codex', 'cloudflare'} and not getattr(stage.hooks, 'on_stage_enter', None))

    def successor_stage_qualification(owner, issue, run):
        try:
            stage = owner._states_for(issue).get('investigate')
            record = admissions(owner, issue).get('investigate')
            if (not successor_worker_ready
                    or getattr(getattr(owner, '_reconcile', None), '__func__', None) is not recipient_prologue[2]
                    or getattr(getattr(owner, '_observe_terminal', None), '__func__', None) is not recipient_prologue[3]
                    or owner.project_name != 'OffMenu' or orchestration.state_key(issue.state) != 'active'
                    or owner.pool is None or owner.pool.is_paused(owner.project_name)
                    or not native_allowed(owner, issue, selecting=True)
                    or type(run) is not int or run < 1 or owner._issue_state_runs.get(issue.id, 1) != run
                    or not record or 'project_intake' not in record
                    or record.get('read_paths') or record.get('read_roots') or record.get('write_roots')
                    or stage is None or stage.type != 'agent' or stage.admission_review
                    or stage.publication or stage.session != 'fresh' or stage.permission_mode != 'plan'
                    or stage.runner not in {'codex', 'cloudflare'} or getattr(stage.hooks, 'on_stage_enter', None)):
                return False
            workspace = scoped_workspace(owner, issue)
            # The pinned native worker always checks both inputs before the
            # turn. This bounded route admits no Git repoint or npm bootstrap.
            return (not os.path.lexists(workspace / publication.WORKING_SOURCE)
                    and not (os.path.lexists(workspace / '.node-version')
                             and os.path.lexists(workspace / 'package.json')))
        except (OSError, ValueError, KeyError, AttributeError):
            return False

    if authority_lifecycle is not None:
        authority_lifecycle._successor_native_worker_qualified = successor_worker_ready
        authority_lifecycle._successor_stage_qualification = successor_stage_qualification

    def selected_stage_allowed(owner, issue, record, state):
        if state == 'investigate':
            return (owner._issue_current_state.get(issue.id) == state
                    and successor_stage_qualification(owner, issue, owner._issue_state_runs.get(issue.id, 1)))
        return recipient_stage_allowed(owner, issue, record)

    def select_native(owner, issue, state, stage, record, *, retry=None, attempt_num=None):
        if authority_lifecycle is None:
            return True, None
        try:
            scope = publication.scope(issue)
            if state == 'investigate':
                assignment = getattr(authority_lifecycle, 'assignment', None)
                grant = assignment(owner.project_name, issue.id, scope) if callable(assignment) else None
                if type(getattr(grant, 'first_intent_sha256', None)) is str:
                    current_retry = owner.retry_attempts.get(issue.id)
                    retry_attempt = getattr(retry, 'attempt', None)
                    if ((attempt_num is not None and (type(attempt_num) is not int or attempt_num != 0))
                            or retry is not current_retry
                            or (retry is not None and (type(retry_attempt) is not int or retry_attempt != 0))
                            or (issue.id in owner.claimed and retry is None)
                            or issue.id in owner.running or issue.id in owner._pending_gates
                            or asyncio.get_running_loop().get_task_factory() is not None
                            or not selected_stage_allowed(owner, issue, record, state)):
                        return False, None
                    claim = authority_lifecycle.successor_assignment(
                        owner, issue, scope, state, owner._issue_state_runs.get(issue.id, 1))
                    if claim is not None and claim.attempt_id == record['attempt']:
                        return True, _RecipientDispatch(owner, issue, record, claim, stage=state)
                    return False, None  # Never substitute the ordinary claim path for this original.
            if authority_lifecycle.selected(owner.project_name, issue, scope):
                return True, None
            if (recipient_prologue is None or state != 'assess' or stage is None
                    or issue.id in owner.retry_attempts or issue.id in owner.running
                    or issue.id in owner.claimed or issue.id in owner._pending_gates
                    or asyncio.get_running_loop().get_task_factory() is not None
                    or not recipient_stage_allowed(owner, issue, record)):
                return False, None
            claim = authority_lifecycle.recipient_assignment(
                owner, issue, scope, state, owner._issue_state_runs.get(issue.id, 1))
            if claim is not None and claim.attempt_id == record['attempt']:
                return True, _RecipientDispatch(owner, issue, record, claim)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            pass
        return False, None

    def eligible(owner, issue, **kwargs):
        if (owner.project_name not in projects or not native_allowed(owner, issue, selecting=True)
                or not original_eligible(owner, issue, **kwargs)):
            return False
        if restore_completed_successor(owner, issue, owner._issue_state_runs.get(issue.id)) is False:
            return False
        stages = admissions(owner, issue)
        assess = stages.get('assess', {})
        if 'project_intake' in assess:
            state, stage = dispatch_stage(owner, issue)
            allowed, _ = select_native(owner, issue, state, stage, stages.get(state),
                                       retry=kwargs.get('retry_entry'))
            if not allowed:
                return False
            if state == 'independent-review':
                return True
            return (state in stages
                    and automatic_stage_allowed(owner, issue, state, stages[state], stages))
        allowed, _ = select_native(owner, issue, None, None, None)
        return allowed and any(record['project'] == owner.project_name
                               and record['scope_sha256'] == publication.scope(issue)
                               for record in stages.values())

    def dispatch(owner, issue, attempt_num=None):
        if (owner.project_name not in projects or owner.pool is None
                or owner.pool.is_paused(owner.project_name) or not native_allowed(owner, issue, selecting=True)):
            owner._release_slot(issue.id)
            return None
        stages = admissions(owner, issue)
        if not stages:
            owner._release_slot(issue.id)
            return None
        state, stage = dispatch_stage(owner, issue)
        record = stages.get(state)
        retry = owner.retry_attempts.get(issue.id) if attempt_num == 0 else None
        allowed, selected_recipient = select_native(owner, issue, state, stage, record,
                                                    retry=retry, attempt_num=attempt_num)
        if (not allowed or record and 'project_intake' in record
                and not automatic_stage_allowed(owner, issue, state, record, stages)):
            owner._release_slot(issue.id)
            return None
        if read_account_recovery is not None and stage and stage.type == 'agent':
            try:
                router.refresh_holds(read_account_recovery)
            except (OSError, sqlite3.Error, ValueError) as error:
                # Existing durable holds still apply. A missed poll cannot
                # reopen them or suspend an unrelated account.
                logger.warning('Workers AI account recovery unavailable: %s', type(error).__name__)
        if record is None and state == 'independent-review':
            author = stages.get('investigate')
            if (author and author.get('automatic_review') is True
                    and author['project'] == owner.project_name
                    and author['scope_sha256'] == publication.scope(issue)):
                try:
                    reviewer = router.independent_reviewer(
                        owner.project_name, author['attempt'], available_projects(owner),
                        client.request_maximum, **client.reservation_options(90 if authority_lifecycle is not None else 600))
                except (OSError, sqlite3.Error, BudgetRefused) as error:
                    logger.warning('Workers AI review budget unavailable: %s', type(error).__name__)
                    reviewer = None
                if reviewer is not None:
                    try:
                        review_identity = dict(attempt=author['attempt'] + '-auto-review',
                                               review_of=author['attempt'], reviewer_project=reviewer)
                        prepare_binding = (authority_lifecycle.bind_dispatch(
                            owner, issue, author['scope_sha256'], review_identity)
                            if authority_lifecycle is not None else nullcontext())
                        # Preparing the original review can write to the
                        # isolated Git object store. Bind the review attempt
                        # before preparation as well as before native launch.
                        with prepare_binding:
                            workspace = scoped_workspace(owner, issue)
                            source = author_source(workspace, author) if 'project_intake' in author else author
                            record = prepare_review(workspace, issue,
                                                    owner._issue_state_runs.get(issue.id, 1),
                                                    source, reviewer, publication)
                            if authority_lifecycle is not None:
                                record = authority_lifecycle.prepare_source_review(issue.id, author, record)
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
                or record['scope_sha256'] != publication.scope(issue)
                or authority_lifecycle is not None and record.get('write_roots')
                   and not authority_lifecycle.supports_source(issue.id, record)):
            owner._release_slot(issue.id)
            return None
        if stage and stage.type == 'agent':
            try:
                started = router.has_started(record['attempt'])
                has_capacity = started or mode != 'cloudflare'
                if not has_capacity:
                    options = client.reservation_options(90 if authority_lifecycle is not None else 600)
                    if authority_lifecycle is not None:
                        name = record.get('reviewer_project') or owner.project_name
                        options['usage_snapshots'] = {k: v for k, v in options['usage_snapshots'].items() if k == name}
                    has_capacity = router.can_start(owner.project_name, available_projects(owner),
                        client.request_maximum, reviewer_project=record.get('reviewer_project'), **options)
            except (OSError, sqlite3.Error, BudgetRefused) as error:
                logger.warning('Workers AI budget unavailable: %s', type(error).__name__)
                owner._release_slot(issue.id)
                return None
            if started:
                retry = owner.retry_attempts.get(issue.id)
                if retry is not None:
                    if retry.error != 'Original Workers AI request retained for reconciliation':
                        logger.warning('Workers AI retry held for reconciliation: %s', issue.identifier)
                    retry.error = 'Original Workers AI request retained for reconciliation'
            if started or not has_capacity:
                reason = 'original_reconciliation_pending' if started else 'free_route_or_quota_unavailable'
                _idle_notify(f'STATUS=Stokowski blocked: {reason}; no new agent claim')
                owner._release_slot(issue.id)
                return None
        previous = owner.running.get(issue.id)
        binding = (authority_lifecycle.bind_dispatch(owner, issue, record['scope_sha256'], record, stage=state)
                   if authority_lifecycle is not None and selected_recipient is None else nullcontext())
        # Enter before native dispatch consumes the retry or acquires custody.
        # The task it creates inherits the bound original via contextvars.
        recipient_token = recipient.set(selected_recipient)
        try:
            with binding:
                if authority_lifecycle is not None and record.get('write_roots'):
                    source = authority_lifecycle.source_receiver(issue.id, record)
                    if source.list()['sealed']:
                        raise ValueError('Original source candidate already checkpointed')
                result = original_dispatch(owner, issue, attempt_num)
                if selected_recipient is not None:
                    current = owner.running.get(issue.id)
                    task = owner._tasks.get(issue.id)
                    if current is not None and current is not previous and isinstance(task, asyncio.Task):
                        current._uif_retained_recipient = True
                        selected_recipient.attempt = current

                        def release_unentered(done):
                            if not selected_recipient.started:
                                retire_unentered_worker(owner, issue, current, done)

                        task.add_done_callback(release_unentered)
                elif authority_lifecycle is not None:
                    authority_lifecycle.attach_fresh_assessment_winner(owner, issue, previous)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            if authority_lifecycle is None or owner.running.get(issue.id) is not previous:
                raise
            _idle_notify('STATUS=Stokowski blocked: native_admission_unavailable; no new agent claim')
            logger.warning('Native authority admission held for %s (%s)', issue.identifier, type(error).__name__)
            owner._release_slot(issue.id)
            return None
        finally:
            recipient.reset(recipient_token)
        current = owner.running.get(issue.id)
        if current is not None and current is not previous and record.get('automatic_author'):
            current.workers_ai_review = record
        return result

    async def worker(owner, issue, attempt):
        selected = recipient.get()
        if selected is not None:
            bound = False
            try:
                if (selected.owner is not owner or selected.issue is not issue or selected.attempt is not attempt
                        or owner.running.get(issue.id) is not attempt or attempt.state_name != selected.stage
                        or owner._tasks.get(issue.id) is not asyncio.current_task()
                        or attempt.session_id is not None or not native_allowed(owner, issue, selecting=True)
                        or not selected_stage_allowed(owner, issue, selected.record, selected.stage)
                        or not current_admission(owner, issue, attempt.state_name, selected.record)):
                    raise ValueError('Recovered native task registration changed')
                attempt.session_id = selected.claim.attempt_id
                first_entry = (authority_lifecycle.successor_first_entry if selected.stage == 'investigate'
                               else authority_lifecycle.recipient_first_entry)
                with first_entry(
                        owner, issue, selected.record['scope_sha256'], attempt, expected=selected.claim):
                    bound = True
                    return await run_native_worker(owner, issue, attempt)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                if bound:
                    raise
                logger.warning('Recovered native entry held for %s (%s)', issue.identifier, type(error).__name__)
                return None
        binding = (authority_lifecycle.fresh_assessment_worker(owner, issue, attempt)
                   if authority_lifecycle is not None else nullcontext())
        with binding:
            return await run_native_worker(owner, issue, attempt)

    def retire_unentered_worker(owner, issue, attempt, task):
        issue_id = issue.id
        if (owner.running.get(issue_id) is attempt and owner._tasks.get(issue_id) is task
                and not getattr(attempt, 'settlement_record', None)):
            owner.running.pop(issue_id)
            owner._tasks.pop(issue_id)
            owner._release_slot(issue_id)
            if issue_id not in owner.retry_attempts and issue_id not in owner._pending_gates:
                owner.claimed.discard(issue_id)

    async def run_native_worker(owner, issue, attempt):
        if not native_allowed(owner, issue):
            # Native dispatch already registered this task and acquired its
            # local slot. It installs no independent done/cleanup callback.
            # The native worker/exit path can publish or retry; do not enter
            # it under revoked authority or discard an unresolved original.
            retire_unentered_worker(owner, issue, attempt, asyncio.current_task())
            return None
        context_token = context.set(owner)
        authorization_token = authorized.set(None)
        try:
            selected = recipient.get()
            if selected is not None:
                selected.started = True
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
                    or not native_allowed(owner, issue) or not current_admission(owner, issue, stage, record)):
                return set()
        return available_projects(owner)

    def authorize(issue, attempt, workspace):
        owner = context.get()
        if not native_allowed(owner, issue):
            raise BudgetRefused('Exclusive native Workers AI dispatch is unavailable')
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
        selected_recipient = recipient.get()
        successor = selected_recipient is not None and selected_recipient.stage == 'investigate'
        if selected_recipient is not None:
            task_active = (authority_lifecycle.successor_task_active if successor
                           else authority_lifecycle.recipient_task_active)
            verify_workspace = (authority_lifecycle.verify_successor_workspace if successor
                                else authority_lifecycle.verify_recipient_workspace)
            if (record != selected_recipient.record or attempt is not selected_recipient.attempt
                    or attempt.state_name != selected_recipient.stage
                    or not selected_stage_allowed(owner, issue, record, selected_recipient.stage)
                    or not task_active(issue.id, record['attempt'])
                    or verify_workspace(
                        owner, issue, record['scope_sha256'], record['attempt']) != workspace):
                raise ValueError('Original authority recipient workspace is unavailable')
        selected = record
        if 'project_intake' in record and attempt.state_name != 'independent-review':
            if not automatic_stage_allowed(owner, issue, attempt.state_name, record, stages):
                raise ValueError('Project intake continuation no longer has native authority')
            if successor:
                source = selected_recipient.claim.lease.fence.source
                selected = dict(record, source_head=source.head, branch=source.branch)
            else:
                selected = (author_source(workspace, record, establish=True) if attempt.state_name == 'investigate'
                            else checkout_source(workspace, record))

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
        paths = tuple(selected['read_paths'])
        if not set(paths) <= tracked:
            raise ValueError('Workers AI read scope includes an untracked file')
        prompt = record['prompt']
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Workers AI requires a scoped owner prompt')
        prompt += ('\nController-verified current worktree source: ' +
                   json.dumps({'head': head, 'branch': branch}) +
                   '. Use this source identity for current observations. '
                   'Treat other source identities in issue history as provenance.\n')
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
        if successor:
            prompt += ('\nThis bounded investigation uses the approved issue statement. '
                       'No source tools or test runner are available. Report any additional evidence '
                       'needed to deliver the requested result.\n')
        if 'project_intake' in record and attempt.state_name == 'publish':
            acceptance = publication.read(workspace, publication.RECORD)
            prompt += '\nAccepted source-free native report: ' + json.dumps(acceptance['accepted_review'])
        if authority_lifecycle is not None:
            if review_binding is not None:
                authority_lifecycle.review_candidate(issue.id, record, prompt)
            elif selected_recipient is None:
                grant = authority_lifecycle.assignment(owner.project_name, issue.id, record['scope_sha256'])
                if grant is None:
                    raise ValueError('Original authority source assignment is unavailable')
                authority_lifecycle.verify_workspace(owner, issue, grant)
        roots = tuple(record['write_roots'])
        remote_tools = None
        if authority_lifecycle is not None and roots:
            remote_tools = authority_lifecycle.source_receiver(issue.id, record).tools
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', root)
               or any(part in {'.', '..', '.git', '.stokowski'} for part in root.split('/'))
               for root in roots):
            raise ValueError('Invalid Workers AI write scope')
        fresh_finisher = (authority_lifecycle.fresh_assessment_finisher(
            issue, record, owner._issue_state_runs.get(issue.id, 1))
            if authority_lifecycle is not None and selected_recipient is None
            and attempt.state_name == 'assess' else None)
        finish_original = fresh_finisher
        if selected_recipient is not None:
            finisher = (authority_lifecycle.successor_investigation_finisher if successor
                        else authority_lifecycle.recipient_assessment_finisher)
            finish_original = finisher(issue, record, owner._issue_state_runs.get(issue.id, 1))
            if not callable(finish_original):
                raise ValueError('Original authority recipient stage evidence is unavailable')
        if remote_tools is not None:
            finish_original = authority_lifecycle.retain_source_checkpoint(issue.id, record)
        elif authority_lifecycle is not None and record.get('source_candidate_sha256'):
            finish_original = authority_lifecycle.retain_final_review
        authorized.set((issue, attempt.state_name, attempt, record))
        thread_binding = {}
        if selected_recipient is not None:
            thread_binding['prepare_turn_thread'] = (authority_lifecycle.prepare_successor_turn_thread if successor
                                                     else authority_lifecycle.prepare_recipient_turn_thread)
        return NativeTurnBinding(issue.id, workspace,
            Attempt(owner.project_name, record['attempt'], record.get('review_of'),
                    record.get('reviewer_project')), paths, roots, prompt,
            structured_report='project_intake' in record or 'automatic_author' in record,
            remote_tools=remote_tools, finish_original=finish_original,
            one_shot_report=selected_recipient is not None or fresh_finisher is not None, **thread_binding)

    install_native_runner(runner, orchestration, mode=mode, client=client,
                          authorize_turn=authorize, codex_available=codex_available,
                          active_projects=active_projects)
    orchestration.Orchestrator._dispatch = dispatch
    orchestration.Orchestrator._is_eligible = eligible
    orchestration.Orchestrator._run_worker = worker

    async def start(owner):
        if provider_discovery is None:
            return await _start_with_shutdown(owner, original_start)
        # The advisory hook exists only in this configured service lifecycle.
        runtime = orchestration.MultiOrchestrator
        hooks = {
            'uif_select_provider': staticmethod(provider_discovery.select),
            'uif_provider_roster': staticmethod(provider_discovery.roster),
        }
        if any(name in vars(runtime) for name in hooks):
            raise RuntimeError('Provider discovery launcher already installed')
        for name, hook in hooks.items():
            setattr(runtime, name, hook)
        try:
            roster = provider_discovery.roster()
            logger.info('Stokowski provider roster configured: %s',
                        tuple(route_id for route_id, _ in roster['route_readers'])
                        + tuple(item['route_id'] for item in roster['metadata_candidates']))
            return await _start_with_shutdown(
                owner, lambda _: provider_discovery.run(lambda: original_start(owner)))
        finally:
            for name, hook in hooks.items():
                if vars(runtime).get(name) is hook:
                    delattr(runtime, name)

    orchestration.MultiOrchestrator.start = start
