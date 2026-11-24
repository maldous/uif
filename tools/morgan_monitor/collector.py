"""Bounded collection loop, configuration and safe local metadata entry point."""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from .events import validate_bindings, make_event, SOURCE_CAPABILITIES
from .allowance import FooAllowance, DEFAULT_ALLOWANCE_CONFIG, validate_allowance_config
from .sources import CodexThreads, CodexTurns, SymphonyLeases, SystemdService, ProcessIdentity, UnconfiguredCloud
from .store import Store
from .transport import HTTPSReceiver, flush


class ConfigurationError(Exception):
    pass


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ConfigurationError("invalid_config_fields")
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            text = handle.read(65537)
        if len(text.encode()) > 65536:
            raise ConfigurationError("config_too_large")
        config = json.loads(text, object_pairs_hook=_unique_keys)
    except (OSError, ValueError):
        raise ConfigurationError("config_unavailable") from None
    return validate_config(config, base=Path(path).absolute().parent)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def validate_config(config: Any, *, base: Path = Path.cwd()) -> dict[str, Any]:
    required = {"schemaVersion", "statePath", "intervalSeconds", "maxPending", "sources", "bindings", "receiver"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - required - {"selectedSources"}:
        raise ConfigurationError("invalid_config_fields")
    if type(config["schemaVersion"]) is not int or config["schemaVersion"] != 1:
        raise ConfigurationError("invalid_config_version")
    if type(config["intervalSeconds"]) is not int or not 5 <= config["intervalSeconds"] <= 3600:
        raise ConfigurationError("invalid_interval")
    if type(config["maxPending"]) is not int or not 10 <= config["maxPending"] <= 100000:
        raise ConfigurationError("invalid_outbox_budget")
    if not isinstance(config["statePath"], str) or not config["statePath"] or len(config["statePath"]) > 4096:
        raise ConfigurationError("state_path_required")
    config = json.loads(json.dumps(config, allow_nan=False))
    selected = config.get("selectedSources", list(SOURCE_CAPABILITIES))
    if (not isinstance(selected, list) or not 1 <= len(selected) <= len(SOURCE_CAPABILITIES)
            or any(not isinstance(source, str) or source not in SOURCE_CAPABILITIES for source in selected)
            or len(set(selected)) != len(selected)):
        raise ConfigurationError("invalid_source_selection")
    config["selectedSources"] = list(selected)
    state = Path(config["statePath"]).expanduser()
    config["statePath"] = str((base / state).resolve())
    sources = config["sources"]
    required_sources = {"codexRegistry", "codexTurns", "symphonyRegistry", "controllerService", "processIdentity"}
    if not isinstance(sources, dict) or not required_sources <= set(sources) or set(sources) - required_sources - {"fooAllowance"}:
        raise ConfigurationError("invalid_config_fields")
    try:
        sources["fooAllowance"] = validate_allowance_config(sources.get("fooAllowance", DEFAULT_ALLOWANCE_CONFIG), base)
    except ValueError:
        raise ConfigurationError("invalid_allowance_config") from None
    for name in ("codexRegistry", "codexTurns", "symphonyRegistry"):
        source = _exact(sources[name], {"path", "pageSize", "maxRecords", "deadlineSeconds"})
        if type(source["pageSize"]) is not int or not 1 <= source["pageSize"] <= 500 or type(source["maxRecords"]) is not int or not 1 <= source["maxRecords"] <= 10000:
            raise ConfigurationError("invalid_source_budget")
        deadline = source["deadlineSeconds"]
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 0.05 <= deadline <= 10:
            raise ConfigurationError("invalid_source_budget")
        if source["path"] is not None:
            if not isinstance(source["path"], str) or not source["path"] or len(source["path"]) > 4096:
                raise ConfigurationError("invalid_source_path")
            source["path"] = str((base / Path(source["path"]).expanduser()).resolve())
            if source["path"] == config["statePath"]:
                raise ConfigurationError("state_source_collision")
    service = _exact(sources["controllerService"], {"unit", "user", "timeoutSeconds"})
    if type(service["user"]) is not bool or isinstance(service["timeoutSeconds"], bool) or not isinstance(service["timeoutSeconds"], (int, float)) or not 0.1 <= service["timeoutSeconds"] <= 10:
        raise ConfigurationError("invalid_service_config")
    process = _exact(sources["processIdentity"], {"pid", "startTicks"})
    validate_bindings(config["bindings"])
    if any(binding["sourceId"] not in selected for binding in config["bindings"]):
        raise ConfigurationError("binding_source_not_selected")
    # Constructors validate without reading any sources or making HTTP calls.
    SystemdService(service["unit"], user=service["user"], timeout=service["timeoutSeconds"])
    ProcessIdentity(process["pid"], process["startTicks"])
    HTTPSReceiver(config["receiver"])
    return config


class Collector:
    def __init__(self, config: dict[str, Any], *, store=None, adapters=None, receiver=None):
        self.config = config
        self.store = store or Store(config["statePath"], max_pending=config["maxPending"])
        self.receiver = receiver or HTTPSReceiver(config["receiver"])
        self.adapters = adapters if adapters is not None else self._adapters()

    def _adapters(self):
        config = self.config["sources"]
        selected = set(self.config.get("selectedSources", SOURCE_CAPABILITIES))
        result = []
        for source_id, key, cls in (("codex_registry", "codexRegistry", CodexThreads),
                                    ("codex_turns", "codexTurns", CodexTurns),
                                    ("symphony_registry", "symphonyRegistry", SymphonyLeases)):
            if source_id in selected:
                source = config[key]
                result.append(cls(source["path"], page_size=source["pageSize"], max_records=source["maxRecords"], deadline_seconds=source["deadlineSeconds"]))
        if "controller_service" in selected:
            service = config["controllerService"]
            result.append(SystemdService(service["unit"], user=service["user"], timeout=service["timeoutSeconds"]))
        if "process_identity" in selected:
            process = config["processIdentity"]
            result.append(ProcessIdentity(process["pid"], process["startTicks"]))
        if "cloud_feed" in selected:
            result.append(UnconfiguredCloud())
        if "foo_allowance" in selected:
            result.append(FooAllowance(config["fooAllowance"]))
        return result

    def collect_once(self, *, now: int | None = None) -> dict[str, Any]:
        # Delivery must run before a save can reject a full queue. Keep the
        # same total request budget across the pre/post collection drains.
        delivery_budget = 50
        delivery = flush(self.store, self.receiver, now, budget=delivery_budget, source_ids=self.config.get("selectedSources"))
        pending = self.store.status()["pending"]
        # One authoritative collector owns the state. Check headroom for the
        # entire pass so a slow-recovering queue cannot repeatedly admit only
        # the first source and starve later sources. No unpersisted probes are
        # taken; retained evidence and sequence numbers remain unchanged.
        deferred = pending + len(self.adapters) > self.store.max_pending
        if not deferred:
            # Explicit now is for deterministic synthetic tests only. Production
            # timestamps each probe individually, not the start of a long loop.
            for adapter in self.adapters:
                observed_at = int(time.time()) if now is None else now
                observation = adapter.collect(observed_at)
                self.store.save(observation, lambda sequence, observation=observation: make_event(observation, sequence, self.config["bindings"]))
            remaining = delivery_budget - delivery["sent"] - delivery["failed"]
            if remaining > 0:
                after = flush(self.store, self.receiver, now, budget=remaining, source_ids=self.config.get("selectedSources"))
                for key in delivery:
                    delivery[key] += after[key]
        return {"snapshot": self.store.snapshot(), "delivery": delivery,
                "collectionDeferred": deferred,
                "selectedSources": list(self.config.get("selectedSources", SOURCE_CAPABILITIES)),
                "transportConfigured": self.receiver.config["enabled"]}

    def run(self, *, stop_requested=lambda: False, sleep=time.sleep, on_result=lambda value: None):
        while not stop_requested():
            on_result(self.collect_once())
            # Start cadence after the completed pass. A slow delivery drain can
            # exceed intervalSeconds; anchoring to the old start would cause an
            # immediate same-second source re-read and false clock-regression
            # failure. Never invent or increment wall-clock observation times.
            deadline = time.monotonic() + self.config["intervalSeconds"]
            # Delivery has an independent cadence between source polls. It can
            # drain an outage backlog faster than new observations are produced,
            # without sleeping through the whole collection interval after a
            # single source event. Store.pending enforces durable source pacing.
            while not stop_requested():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sleep(min(remaining, 1))
                if not stop_requested():
                    flush(self.store, self.receiver, budget=max(1, len(self.adapters)), source_ids=self.config.get("selectedSources"))

    def close(self):
        self.store.close()
