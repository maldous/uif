"""Bind Workers AI to native Stokowski's existing runner interface."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from inspect import iscoroutinefunction
import json
import logging
from pathlib import Path
from threading import Event, current_thread
from typing import Callable

from .budget import BudgetRefused
from .client import WorkersAIClient, WorkersAIRequestError
from .turn import Attempt, repository_tools, run_turn

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NativeTurnBinding:
    issue_id: str
    workspace: Path
    attempt: Attempt
    tracked_paths: tuple[str, ...]
    write_roots: tuple[str, ...]
    scoped_prompt: str | None = None
    structured_report: bool = False
    remote_tools: Callable | None = None
    finish_original: Callable | None = None
    one_shot_report: bool = False
    prepare_turn_thread: Callable[[Callable[[], str], Callable[[], None]], Callable[[], str]] | None = None


def _native_report(result):
    """Wrap a bare report object in the native controller's existing format."""
    try:
        report = json.loads(result)
    except json.JSONDecodeError:
        return result
    if isinstance(report, dict) and ('summary' in report or 'claims' in report):
        return f'```json\n{result}\n```'
    return result


def _prepare_bound_turn(prepare, turn, stopped):
    """Delegate only this exact turn to the thread entered by the runner."""
    if not callable(prepare) or iscoroutinefunction(prepare):
        raise ValueError('Bound Workers AI turn requires a synchronous factory')
    thread = None
    started = False
    invoked = False
    missing = object()
    result = missing

    def require_turn_thread():
        if current_thread() is not thread or stopped():
            raise BudgetRefused('Bound Workers AI turn thread is unavailable')

    def run_once():
        nonlocal invoked, result, thread
        if invoked:
            raise BudgetRefused('Bound Workers AI turn was already invoked')
        require_turn_thread()
        invoked = True
        try:
            result = turn()
            return result
        finally:
            thread = None

    prepared = prepare(run_once, require_turn_thread)
    if not callable(prepared) or iscoroutinefunction(prepared):
        raise ValueError('Bound Workers AI turn requires a synchronous invocation')

    def invoke():
        nonlocal started, thread
        if started:
            raise BudgetRefused('Bound Workers AI turn thread was already entered')
        started = True
        thread = current_thread()
        try:
            require_turn_thread()
            prepared()
            if result is missing:
                raise BudgetRefused('Bound Workers AI turn did not return its original result')
            return result
        finally:
            thread = None

    return invoke


def install_native_runner(runner, orchestrator, *, mode, client: WorkersAIClient,
                          authorize_turn, codex_available, active_projects):
    """Install at native launch, after the owner has bound its adapters.

    authorize_turn resolves the existing issue, workspace, source, and stage
    under the runtime's authority. It must supply the distinct reviewer
    identity and author attempt for review work.
    """
    if mode not in {'cloudflare', 'both', 'codex'}:
        raise ValueError('Unknown Stokowski provider mode')
    if mode == 'codex':
        return
    original = runner.run_turn
    if orchestrator.run_turn is not original:
        raise ValueError('Unexpected native Stokowski runner binding')

    async def selected_turn(runner_type, claude_cfg, hooks_cfg, prompt, workspace_path,
                            issue, attempt, on_event=None, on_pid=None, env=None):
        if runner_type not in {'codex', 'cloudflare'}:
            raise ValueError('Unexpected runner in the Cloudflare workflow')
        binding = authorize_turn(issue, attempt, Path(workspace_path))
        if (not isinstance(binding, NativeTurnBinding) or binding.issue_id != issue.id
                or binding.workspace != Path(workspace_path).resolve(strict=True)):
            raise ValueError('Native Cloudflare attempt is not bound to its admitted workspace')
        read_only = claude_cfg.permission_mode == 'plan'
        review = attempt.state_name == 'independent-review'
        if ((read_only and binding.write_roots) or
                (review and (not read_only or not binding.attempt.review_of
                             or not binding.attempt.reviewer_project))):
            raise ValueError('Cloudflare review requires a read-only independent account binding')
        if not review and binding.attempt.review_of:
            raise ValueError('Author workflow cannot consume a review binding')
        if binding.attempt.project not in active_projects():
            raise BudgetRefused('Native project is paused')
        prepare = binding.prepare_turn_thread
        if (prepare is None and runner_type == 'codex' and mode == 'both' and not review
                and codex_available(issue) is True):
            return await original(runner_type, claude_cfg, hooks_cfg, binding.scoped_prompt or prompt,
                                  workspace_path, issue, attempt, on_event=on_event, on_pid=on_pid,
                                  env=env)

        if binding.remote_tools is None:
            if prepare is None:
                tools, execute_tool = repository_tools(
                    binding.workspace, binding.tracked_paths, binding.write_roots)
            elif binding.write_roots:
                raise BudgetRefused('Bound Workers AI write scope requires a remote source receiver')
            else:
                tools, execute_tool = [], None
        else:
            tools, execute_tool = binding.remote_tools()
        stop = Event()
        turn = partial(run_turn, client, binding.attempt, binding.scoped_prompt or prompt, tools=tools,
                       execute_tool=execute_tool, active_projects=active_projects,
                       cancelled=stop.is_set, timeout=min(600, claude_cfg.turn_timeout_ms // 1000),
                       structured_report=binding.structured_report, finish_original=binding.finish_original,
                       one_shot_report=binding.one_shot_report)
        if prepare is not None:
            turn = _prepare_bound_turn(prepare, turn, stop.is_set)
        attempt.turn_count += 1
        attempt.model = client.model
        attempt.session_id = binding.attempt.id
        attempt.status = 'running'
        logger.info('Workers AI turn started issue=%s stage=%s model=%s',
                    getattr(issue, 'identifier', issue.id), attempt.state_name, client.model)
        task = asyncio.create_task(asyncio.to_thread(turn))
        try:
            attempt.result_text = _native_report(await asyncio.shield(task))
            attempt.status = 'succeeded'
        except asyncio.CancelledError:
            stop.set()
            # Cancelling the executor awaitable cannot stop its actual thread.
            # Keep ownership and shield the same drain even if cancelled again.
            drain = asyncio.gather(task, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
            attempt.status = 'cancelled'
            raise
        except (WorkersAIRequestError, BudgetRefused) as error:
            attempt.status = 'failed'
            attempt.error = str(error)
        except (OSError, ValueError) as error:
            attempt.status = 'failed'
            attempt.error = type(error).__name__
        finally:
            attempt.completed_at = datetime.now(timezone.utc)
        return attempt

    runner.run_turn = selected_turn
    orchestrator.run_turn = selected_turn
