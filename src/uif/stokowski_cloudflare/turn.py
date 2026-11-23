"""Model/tool loop for a protected, attempt-bound Stokowski tool executor."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .client import WorkersAIClient, WorkersAIRequestError


MAX_TURN_REQUESTS = 24
FINAL_REPORT = (
    'Return the final report as one JSON object with summary and verdict, and '
    'the applicable native routing fields. Preserve the actions actually taken, '
    'available evidence and any remaining gaps. No further tools are available.'
)


@dataclass(frozen=True)
class Attempt:
    project: str
    id: str
    review_of: str | None = None
    reviewer_project: str | None = None


class RepositoryToolError(WorkersAIRequestError):
    """A rejected bounded tool operation that the model can correct."""


def repository_tools(workspace, tracked_paths, write_roots):
    """Expose only bounded text operations on the admitted checkout."""
    workspace = Path(workspace).resolve(strict=True)
    tracked = frozenset(tracked_paths)
    definitions = {
        'list_files': ('List available tracked files. Call with an empty JSON object.', ()),
        'find_files': ('Find approved tracked files by path substring.', ('query',)),
        'read_file': ('Read one of the available tracked files by relative path.', ('path',)),
    } if tracked else {}
    if write_roots:
        definitions['replace_text'] = (
            'Replace one unique text match in a writable tracked file.',
            ('path', 'old', 'replacement'),
        )
        definitions['verify_diff'] = (
            'Run Git whitespace and conflict-marker checks on the candidate. This is not a functional test.',
            (),
        )
    tools = [
        {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': {
            'type': 'object', 'properties': {key: {'type': 'string'} for key in keys},
            'required': list(keys), 'additionalProperties': False}}}
        for name, (description, keys) in definitions.items()
    ]

    def execute(name, arguments, remaining):
        _, keys = definitions[name]
        if set(arguments) != set(keys) or not all(
                isinstance(value, str) for value in arguments.values()):
            expected = ', '.join(keys)
            raise RepositoryToolError(
                f'Expected string fields {expected}' if keys else 'Expected an empty JSON object')
        if name == 'list_files':
            return '\n'.join(sorted(tracked))[:8000]
        if name == 'find_files':
            query = arguments['query'].strip().casefold()
            if not query:
                raise RepositoryToolError('Supply a nonempty path substring')
            return '\n'.join(sorted(path for path in tracked if query in path.casefold()))[:8000]
        if name == 'verify_diff':
            try:
                result = subprocess.run(
                    ['git', '-C', str(workspace), 'diff', '--no-ext-diff', '--no-textconv',
                     '--check', 'HEAD', '--', '.', ':!.stokowski'],
                    capture_output=True, timeout=min(10, remaining), check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RepositoryToolError('Git diff check could not run') from error
            if result.returncode:
                return 'Git diff check failed.\n' + result.stdout[:8000].decode(errors='replace')
            return 'Git diff check passed. Functional tests were not run.'
        relative = arguments['path']
        path = workspace / relative
        if relative not in tracked or path.resolve(strict=True) != path or not path.is_file():
            raise RepositoryToolError('Workspace file is outside the tracked scope')
        stat = path.stat()
        if stat.st_nlink != 1 or stat.st_size > 24_000:
            raise RepositoryToolError('Workspace file exceeds the tool size limit')
        content = path.read_text(encoding='utf-8')
        if name == 'read_file':
            return content
        if not any(relative == root or relative.startswith(root + '/') for root in write_roots):
            raise RepositoryToolError('Workspace file is outside the write scope')
        old = arguments['old']
        if not old or content.count(old) != 1:
            raise RepositoryToolError('Edit requires one unambiguous match')
        updated = content.replace(old, arguments['replacement'], 1)
        if len(updated.encode()) > 24_000:
            raise RepositoryToolError('Updated file exceeds the tool size limit')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            staged = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), stat.st_mode & 0o777)
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)
        return 'Updated tracked file'

    return tools, execute


def run_turn(client: WorkersAIClient, attempt: Attempt, prompt: str, *,
             tools: list[dict], execute_tool, active_projects, cancelled, timeout: int,
             structured_report=False) -> str:
    """Execute only the tools that the protected owner bound to this attempt.

    The executor enforces workspace scope and applies the attempt's separate
    review or implementation authority. Tool arguments never select it.
    """
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('Invalid turn timeout')
    tool_names = {tool['function']['name'] for tool in tools}
    messages = [{'role': 'user', 'content': prompt}]
    report_only = False
    deadline = time.monotonic() + timeout
    for ordinal in range(1, MAX_TURN_REQUESTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancelled():
            raise WorkersAIRequestError('Workers AI turn stopped before completion')
        if structured_report and ordinal == MAX_TURN_REQUESTS and not report_only:
            report_only = True
            messages.append({'role': 'user', 'content': FINAL_REPORT})
        request_id = sha256(f'{attempt.id}:{ordinal}'.encode()).hexdigest()
        result = client.complete(
            project=attempt.project, attempt_id=attempt.id, request_id=request_id,
            active_projects=active_projects(), messages=messages,
            tools=[] if report_only else tools,
            timeout=remaining, review_of=attempt.review_of, reviewer_project=attempt.reviewer_project)
        if cancelled() or time.monotonic() >= deadline or attempt.project not in active_projects():
            raise WorkersAIRequestError('Workers AI turn stopped before applying a response')

        choices = result.get('choices')
        message = choices[0]['message'] if choices else result
        content = message.get('content', message.get('response'))
        calls = message.get('tool_calls') or []
        if not calls:
            if structured_report and not _has_native_report(content):
                if report_only:
                    raise WorkersAIRequestError('Workers AI did not return a structured native report')
                report_only = True
                if isinstance(content, str) and content.strip():
                    messages.append({'role': 'assistant', 'content': content})
                messages.append({'role': 'user', 'content': FINAL_REPORT})
                continue
            if not isinstance(content, str) or not content.strip():
                if tools and not report_only and choices and choices[0].get('finish_reason') == 'length':
                    report_only = True
                    messages.append({'role': 'user', 'content': (
                        'Return only the final report requested above. Report the actions actually '
                        'completed and any remaining gaps. No further tools are available.'
                    )})
                    continue
                raise WorkersAIRequestError('Workers AI did not return a final message')
            return content
        if report_only:
            raise WorkersAIRequestError('Workers AI requested a tool during a report-only response')

        permitted_calls = _validate_tool_calls(calls, tool_names)
        if permitted_calls is None:
            if not structured_report:
                raise WorkersAIRequestError('Invalid or unapproved Workers AI tool call')
            report_only = True
            messages.append({'role': 'user', 'content': (
                'The controller declined the last tool-call batch before execution. '
                'Report the outcome using the verified evidence already available. ' + FINAL_REPORT
            )})
            continue

        messages.append({'role': 'assistant', 'content': content or '', 'tool_calls': calls})
        for call_id, name, args in permitted_calls:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or cancelled() or attempt.project not in active_projects():
                raise WorkersAIRequestError('Workers AI turn stopped before executing a tool')
            try:
                if isinstance(args, str):
                    args = json.loads(args)
                if not isinstance(args, dict):
                    raise RepositoryToolError('Expected a JSON object for tool arguments')
                output = execute_tool(name, args, remaining)
            except json.JSONDecodeError:
                output = 'Tool did not run. Supply arguments as a valid JSON object.'
            except RepositoryToolError as error:
                output = f'Tool did not run. {error}'
            if not isinstance(output, str) or len(output.encode()) > 32_000:
                raise WorkersAIRequestError('Workspace tool response exceeded its text boundary')
            messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': output})
    raise WorkersAIRequestError('Workers AI turn exhausted its request limit')


def _validate_tool_calls(calls, tool_names):
    if not isinstance(calls, list):
        return None
    validated, seen = [], set()
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get('function'), dict):
            return None
        function = call['function']
        call_id, name = call.get('id'), function.get('name')
        if (not isinstance(call_id, str) or not call_id or call_id in seen
                or not isinstance(name, str) or name not in tool_names):
            return None
        seen.add(call_id)
        validated.append((call_id, name, function.get('arguments')))
    return validated


def _has_native_report(content):
    if not isinstance(content, str):
        return False
    match = re.search(r'```json\s*(.*?)\s*```', content, re.IGNORECASE | re.DOTALL)
    try:
        report = json.loads(match.group(1) if match else content)
    except json.JSONDecodeError:
        return False
    return (isinstance(report, dict) and isinstance(report.get('summary'), str)
            and bool(report['summary'].strip()) and isinstance(report.get('verdict'), str)
            and bool(report['verdict'].strip()))
