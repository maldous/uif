"""One-shot, private coordinator capture, NOT a standalone cloud status feed.

The caller supplies the authorized runtime's read_thread function. No credentials,
HTTP client, timer, publication, or production roster is created here. Only a
fresh callback result is projected; raw messages/errors/IDs never leave it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import time
from typing import Any, Awaitable, Callable

SOURCE_ID = "coordinator_observer"
TOPICS = frozenset({"infrastructure", "monitoring", "research", "planning",
                    "engineering", "review", "testing", "delivery"})
# The source promises only a latest recorded turn and may lag. A new read does
# not turn an old inProgress record into fresh active execution evidence.
STATE = {"inProgress": "unknown", "completed": "completed",
         "failed": "unknown", "interrupted": "unknown"}
MIN_DATE = 946684800
MAX_DATE = 253402300799


class CaptureError(Exception):
    """Fixed diagnostic only; never wrap a private source exception."""


def _instant(value: Any) -> int:
    if type(value) not in (int, float) or not MIN_DATE <= value <= MAX_DATE:
        raise CaptureError("invalid_clock")
    return int(value)


def _iso(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bindings(value: Any) -> list[dict]:
    if not isinstance(value, list) or len(value) > 6:
        raise CaptureError("invalid_bindings")
    result, aliases, threads = [], set(), set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"threadId", "alias", "topicKeys"}:
            raise CaptureError("invalid_binding")
        thread, alias, topics = item["threadId"], item["alias"], item["topicKeys"]
        if (not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", thread)
                or not isinstance(alias, str) or alias not in tuple("ABCDEF")
                or alias in aliases or thread in threads):
            raise CaptureError("invalid_binding_identity")
        if (not isinstance(topics, list) or len(topics) > 3
                or any(not isinstance(key, str) or key not in TOPICS for key in topics)
                or len(set(topics)) != len(topics)):
            raise CaptureError("invalid_topic_keys")
        aliases.add(alias)
        threads.add(thread)
        # Do not retain a mutable caller-owned mapping across the awaited read.
        result.append({"threadId": thread, "alias": alias, "topicKeys": list(topics)})
    return result


def _session(response: Any, binding: dict, captured_at: int) -> dict:
    # Only structured tool results qualify. Text bodies are never parsed, and
    # an unavailable/malformed read is never replaced with an old cached result.
    if not isinstance(response, dict) or response.get("isError", False) is not False:
        raise CaptureError("source_unavailable")
    body = response.get("structuredContent")
    if not isinstance(body, dict) or body.get("threadId") != binding["threadId"]:
        raise CaptureError("source_identity_mismatch")
    turn = body.get("latestTurn")
    if "latestTurn" not in body:
        raise CaptureError("source_schema_drift")
    blockers = []
    if turn is None:
        state = "unknown"
    else:
        if (not isinstance(turn, dict) or not isinstance(turn.get("id"), str)
                or not 1 <= len(turn["id"]) <= 256
                or not isinstance(turn.get("status"), str) or turn["status"] not in STATE):
            raise CaptureError("source_schema_drift")
        state = STATE[turn["status"]]
        if turn["status"] == "failed":
            blockers = ["execution_failed"]
    return {"alias": binding["alias"], "assignment": "assigned",
            "observedAt": _iso(captured_at), "state": state,
            "proof": "coordinator_lifecycle", "topicKeys": list(binding["topicKeys"]),
            "blockerKeys": blockers}


def _discard_read(task: asyncio.Task) -> None:
    """Cancel without waiting for an uncooperative reader; retain no late result."""
    def consume(finished: asyncio.Task) -> None:
        try:
            finished.exception()
        except BaseException:
            # Never leak a private exception from a detached late read.
            pass
    task.add_done_callback(consume)
    if not task.done():
        task.cancel()


async def capture(read_thread: Callable[[str], Awaitable[dict]], bindings: list[dict], *,
                  roster_generation: int, sequence: int,
                  clock: Callable[[], float] = time.time,
                  timeout_seconds: float = 5.0) -> dict:
    """Read each explicit private binding once; return a receiver-compatible DTO.

    A runtime bridge calls supported read_thread(threadId=id, limit=1) for the
    supplied ID. It must not replay a saved response as a fresh callback result.
    The existing durable outbox, not this pure adapter, allocates sequence and
    stores retries. Retry delivery of that stored event unchanged; do not invoke
    capture merely to refresh timestamps. No callback bridge is installed here.
    The callback must not block the event loop. Timed-out reads are cancelled,
    never awaited further or accepted late; the runtime must supervise callbacks
    that suppress cancellation because Python cannot forcibly stop a coroutine.
    """
    roster = _bindings(bindings)
    if (type(roster_generation) is not int or not 1 <= roster_generation <= 1000000
            or type(sequence) is not int or not 1 <= sequence <= 9007199254740991
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 10):
        raise CaptureError("invalid_capture_config")
    start = _instant(clock())

    async def one(binding: dict) -> dict | None:
        async def invoke():
            return await read_thread(binding["threadId"])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        task = asyncio.create_task(invoke())
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
            if task not in done or loop.time() > deadline:
                _discard_read(task)
                return None
            result = task.result()
        except Exception:
            return None
        except BaseException:
            _discard_read(task)
            raise
        captured_at = _instant(clock())
        if captured_at < start or captured_at - start > timeout_seconds + 2:
            raise CaptureError("invalid_clock")
        try:
            return _session(result, binding, captured_at)
        except CaptureError:
            return None

    rows = await asyncio.gather(*(one(binding) for binding in roster))
    finished_at = _instant(clock())
    if finished_at < start or finished_at - start > timeout_seconds + 2:
        raise CaptureError("invalid_clock")
    sessions = [row for row in rows if row is not None]
    if any(row["observedAt"] > _iso(finished_at) for row in sessions):
        raise CaptureError("invalid_clock")
    return {"schemaVersion": 1, "sourceId": SOURCE_ID, "eventId": str(sequence),
            "sequence": sequence, "rosterGeneration": roster_generation,
            "observedAt": _iso(finished_at),
            "health": "unconfigured" if not roster else ("ok" if sessions else "error"),
            "coverage": {"complete": False, "observed": len(sessions), "total": None},
            "sessions": sessions}
