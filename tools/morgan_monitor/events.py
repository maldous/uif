"""Exact public-safe event DTO. No free text, private IDs, paths or URLs."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from .sources import Observation

TOPICS = frozenset({"infrastructure", "monitoring", "research", "planning", "engineering", "review", "testing", "delivery"})
BLOCKERS = frozenset({"source_unavailable", "coverage_partial", "awaiting_input", "dependency_unavailable", "quota_unavailable", "execution_failed"})
SOURCE_CAPABILITIES = {
    "codex_registry": "registry", "codex_turns": "turn_history", "symphony_registry": "registry",
    "controller_service": "service_process", "process_identity": "service_process", "cloud_feed": "none", "foo_allowance": "foo_allowance",
}
RECORD_STATES = {"recorded": "unknown", "archived": "unknown", "inProgress": "unknown",
                 "completed": "completed", "failed": "failed", "interrupted": "unknown",
                 "running": "unknown", "cancel_requested": "unknown", "uncertain": "unknown", "finished": "completed"}


class EventError(Exception):
    pass


def iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_bindings(bindings: Any) -> list[dict[str, Any]]:
    if not isinstance(bindings, list) or len(bindings) > 64:
        raise EventError("invalid_bindings")
    aliases = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"sourceId", "recordKey", "alias", "topicKeys", "blockerKeys"}:
            raise EventError("invalid_binding")
        if binding["sourceId"] not in {"codex_registry", "codex_turns", "symphony_registry"}:
            # No guessed systemd/PID-to-session association. Service metadata
            # is useful independently and does not assert session execution.
            raise EventError("invalid_binding_source")
        if not isinstance(binding["recordKey"], str) or not 1 <= len(binding["recordKey"]) <= 513:
            raise EventError("invalid_binding")
        if not isinstance(binding["alias"], str) or not re.fullmatch(r"(?:[1-9]|[1-5][0-9]|6[0-4])", binding["alias"]):
            raise EventError("invalid_alias")
        if binding["alias"] in aliases:
            raise EventError("duplicate_alias")
        aliases.add(binding["alias"])
        for key, allowed in (("topicKeys", TOPICS), ("blockerKeys", BLOCKERS)):
            values = binding[key]
            if not isinstance(values, list) or len(values) > 3 or any(not isinstance(v, str) or v not in allowed for v in values) or len(set(values)) != len(values):
                raise EventError("invalid_catalog_key")
    return bindings


def make_event(observation: Observation, sequence: int, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    if observation.source not in SOURCE_CAPABILITIES:
        raise EventError("invalid_source")
    if observation.health not in {"ok", "unavailable", "unconfigured"}:
        raise EventError("invalid_health")
    sessions = []
    if observation.health == "ok":
        # Private identity matching happens here; the record key is never copied
        # to an event. No match means no observation, not 'Unassigned'.
        lookup = {record["key"]: record for record in observation.records}
        for binding in bindings:
            if binding["sourceId"] != observation.source:
                continue
            record = lookup.get(binding["recordKey"])
            if record is None or record.get("record_at") is None or record["record_at"] < 946684800:
                continue
            if record["state"] not in RECORD_STATES:
                raise EventError("invalid_record_state")
            sessions.append({
                "alias": binding["alias"], "assignment": "assigned", "observedAt": iso(record["record_at"]),
                "state": RECORD_STATES[record["state"]], "proof": SOURCE_CAPABILITIES[observation.source],
                "topicKeys": list(binding["topicKeys"]), "blockerKeys": list(binding["blockerKeys"]),
            })
    # A registry is not an exhaustive worker inventory. Even a complete SQL
    # scan cannot prove all sessions, or their execution, were observed.
    event = {"schemaVersion": 1, "sourceId": observation.source, "eventId": str(sequence),
             "sequence": sequence, "observedAt": iso(observation.observed_at),
             "health": {"ok": "ok", "unavailable": "error", "unconfigured": "unconfigured"}[observation.health],
             "coverage": {"complete": False, "observed": len(sessions), "total": None}, "sessions": sessions}
    if observation.capability == "service_process":
        event["serviceState"] = observation.service_state
    if observation.allowance is not None and observation.health == "ok":
        event["fooAllowance"] = {"state": observation.allowance["state"],
                                 "canRunAgents": observation.allowance["canRunAgents"],
                                 "observedAt": iso(observation.allowance["observed_at"])}
    validate_event(event)
    return event


def validate_event(event: dict[str, Any]) -> None:
    """The transport re-validates persisted events, including exact keys."""
    required = {"schemaVersion", "sourceId", "eventId", "sequence", "observedAt", "health", "coverage", "sessions"}
    if not isinstance(event, dict) or not required <= set(event) or set(event) - required - {"serviceState", "fooAllowance"}:
        raise EventError("invalid_event")
    if type(event["schemaVersion"]) is not int or event["schemaVersion"] != 1 or event["sourceId"] not in SOURCE_CAPABILITIES:
        raise EventError("invalid_event")
    if type(event["sequence"]) is not int or event["sequence"] < 1 or event["eventId"] != str(event["sequence"]):
        raise EventError("invalid_event")
    if event["health"] not in {"ok", "error", "unconfigured"}:
        raise EventError("invalid_event")
    event_time = _parse_time(event["observedAt"])
    if "serviceState" in event and (SOURCE_CAPABILITIES[event["sourceId"]] != "service_process" or event["serviceState"] not in {None, "running", "stopped", "unknown"}):
        raise EventError("invalid_event")
    if "fooAllowance" in event:
        value = event["fooAllowance"]
        if (event["sourceId"] != "foo_allowance" or event["health"] != "ok" or not isinstance(value, dict)
                or set(value) != {"state", "canRunAgents", "observedAt"}):
            raise EventError("invalid_allowance")
        pair = (value["state"], value["canRunAgents"])
        if (pair not in (("valid", True), ("exhausted", False), ("unavailable", None))
                or (value["canRunAgents"] is not None and type(value["canRunAgents"]) is not bool)
                or _parse_time(value["observedAt"]) > event_time):
            raise EventError("invalid_allowance")
    coverage, sessions = event["coverage"], event["sessions"]
    if not isinstance(sessions, list) or len(sessions) > 64:
        raise EventError("invalid_event")
    if coverage != {"complete": False, "observed": len(sessions), "total": None} or type(coverage.get("observed")) is not int or coverage.get("complete") is not False:
        raise EventError("invalid_event")
    if event["health"] != "ok" and sessions:
        raise EventError("invalid_event")
    seen = set()
    for session in sessions:
        if not isinstance(session, dict) or set(session) != {"alias", "assignment", "observedAt", "state", "proof", "topicKeys", "blockerKeys"}:
            raise EventError("invalid_event")
        if not isinstance(session["alias"], str) or not re.fullmatch(r"(?:[1-9]|[1-5][0-9]|6[0-4])", session["alias"]) or session["alias"] in seen:
            raise EventError("invalid_event")
        seen.add(session["alias"])
        if session["assignment"] != "assigned" or session["state"] not in {"unknown", "completed", "failed"}:
            raise EventError("invalid_event")
        if session["proof"] not in {"registry", "turn_history"} or session["proof"] != SOURCE_CAPABILITIES[event["sourceId"]]:
            raise EventError("invalid_event")
        if _parse_time(session["observedAt"]) > event_time + 30:
            raise EventError("invalid_event")
        for name, allowed in (("topicKeys", TOPICS), ("blockerKeys", BLOCKERS)):
            values = session[name]
            if not isinstance(values, list) or len(values) > 3 or any(not isinstance(v, str) or v not in allowed for v in values) or len(set(values)) != len(values):
                raise EventError("invalid_event")


def _parse_time(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise EventError("invalid_time")
    try:
        result = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        raise EventError("invalid_time") from None
    if result < 946684800:
        raise EventError("invalid_time")
    return result
