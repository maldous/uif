"""Bind Workers AI to native Stokowski's existing runner interface."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from .budget import BudgetRefused
from .client import MODEL, WorkersAIClient, WorkersAIRequestError
from .turn import Attempt, repository_tools, run_turn


@dataclass(frozen=True)
class NativeTurnBinding:
    issue_id: str
    workspace: Path
    attempt: Attempt
    tracked_paths: tuple[str, ...]
    write_roots: tuple[str, ...]


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
        if runner_type == 'codex' and mode == 'both' and codex_available(issue) is True:
            return await original(runner_type, claude_cfg, hooks_cfg, prompt, workspace_path,
                                  issue, attempt, on_event=on_event, on_pid=on_pid, env=env)
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

        tools, execute_tool = repository_tools(
            binding.workspace, binding.tracked_paths, binding.write_roots)
        stop = Event()
        attempt.turn_count += 1
        attempt.model = MODEL
        attempt.session_id = binding.attempt.id
        attempt.status = 'running'
        task = asyncio.create_task(asyncio.to_thread(
            run_turn, client, binding.attempt, prompt, tools=tools,
            execute_tool=execute_tool, active_projects=active_projects,
            cancelled=stop.is_set, timeout=min(600, claude_cfg.turn_timeout_ms // 1000)))
        try:
            attempt.result_text = await asyncio.shield(task)
            attempt.status = 'succeeded'
        except asyncio.CancelledError:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            attempt.status = 'cancelled'
            raise
        except (WorkersAIRequestError, BudgetRefused, OSError, ValueError) as error:
            attempt.status = 'failed'
            attempt.error = type(error).__name__
        finally:
            attempt.completed_at = datetime.now(timezone.utc)
        return attempt

    runner.run_turn = selected_turn
    orchestrator.run_turn = selected_turn
