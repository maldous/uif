"""Provider refresh in the normal service lifecycle; never an effect authority."""

import asyncio
from dataclasses import dataclass, replace
from hashlib import sha256
from math import isfinite

from .handoff_store import Hold
from .provider_pool import PoolSelection, ProviderRoute, RouteAvailability, select_route
from .quota_handoff import TOKEN

_MAX_RETRY_DELAY_SECONDS = 300


def neutral_route_order(route_ids, *, operation_id):
    """Order permitted routes for a new operation without a provider priority.

    Use this when forming a request that has no explicit priority. Order is
    stable across refreshes and input ordering. Recovery uses its original route.
    """
    if (type(operation_id) is not str or not TOKEN.fullmatch(operation_id)
            or type(route_ids) is not tuple
            or any(type(route_id) is not str or not TOKEN.fullmatch(route_id)
                   for route_id in route_ids)
            or len(set(route_ids)) != len(route_ids)):
        raise ValueError("Invalid operation or permitted provider route IDs")

    def rank(route_id):
        return sha256(f"{operation_id}\0{route_id}".encode()).digest(), route_id

    return tuple(sorted(route_ids, key=rank))


class ProviderReadCooldown(Hold):
    """A verified metadata reader must wait before checking this route again."""

    def __init__(self, delay_seconds):
        if type(delay_seconds) is not int or not 0 <= delay_seconds <= 2**31 - 1:
            raise ValueError("Invalid provider cooldown")
        super().__init__("provider_metadata_cooldown")
        self.delay_seconds = delay_seconds


@dataclass(frozen=True)
class Adapter:
    """Installed, trusted evidence reader and per-request projections.

    When supplied, capabilities(evidence, request) returns a frozenset of
    capabilities proved by the same observation used for availability.
    The optional describe(evidence, now=...) is a nonsecret diagnostic
    projection of that cached observation, its hold, headroom and reservations.
    """

    route: ProviderRoute
    read: object
    project: object
    capabilities: object = None
    describe: object = None


def _capability_set(value):
    return type(value) is frozenset and all(
        type(item) is str and TOKEN.fullmatch(item) for item in value
    )


@dataclass(frozen=True)
class MetadataAdapter:
    """A configured diagnostic reader without a dispatch route."""

    route_id: str
    read: object | None
    describe: object


class NormalProviderDiscovery:
    def __init__(self, adapters, *, clock, interval, timeout, diagnostics=()):
        if (type(adapters) is not tuple
                or any(type(a) is not Adapter or type(a.route) is not ProviderRoute
                       or not callable(a.read) or not callable(a.project)
                       or (a.capabilities is not None and not callable(a.capabilities))
                       or (a.describe is not None and not callable(a.describe))
                       for a in adapters)
                or type(diagnostics) is not tuple
                or any(type(a) is not MetadataAdapter or type(a.route_id) is not str or not a.route_id
                       or (a.read is not None and not callable(a.read)) or not callable(a.describe)
                       for a in diagnostics)
                or not callable(clock)
                or any(type(v) not in (int, float) or not isfinite(v) for v in (interval, timeout))
                or not 0 < timeout <= interval):
            raise ValueError("Invalid installed discovery adapters")
        keys = tuple(a.route.route_id for a in adapters) + tuple(a.route_id for a in diagnostics)
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate discovery route IDs")
        self.adapters = adapters
        self.diagnostics = diagnostics
        self.clock, self.interval, self.timeout = clock, interval, timeout
        self._evidence = {}
        self._state = {}
        self._inflight = {}
        self._running = False

    @staticmethod
    def _drain(task):
        if not task.cancelled():
            task.exception()

    async def _poll(self, adapter, key=None):
        if key is None:
            key = adapter.route.route_id
        delay = self.interval
        max_delay = max(self.interval, _MAX_RETRY_DELAY_SECONDS)
        while True:
            retry_after = 0
            old = self._inflight.get(key)
            if old is not None and not old.done():
                self._state[key] = "reader-quarantined"
            else:
                async def read():
                    return await adapter.read()
                task = asyncio.create_task(read())
                task.add_done_callback(self._drain)
                self._inflight[key] = task
                done, _ = await asyncio.wait((task,), timeout=self.timeout)
                if task not in done:
                    self._state[key] = "reader-timeout"
                    self._evidence.pop(key, None)
                    task.cancel()
                else:
                    try:
                        self._evidence[key] = task.result()
                    except ProviderReadCooldown as cooldown:
                        self._evidence.pop(key, None)
                        self._state[key] = "reader-throttled"
                        retry_after = cooldown.delay_seconds
                    except (Exception, asyncio.CancelledError):
                        self._evidence.pop(key, None)
                        self._state[key] = "reader-unavailable"
                    else:
                        self._state[key] = ("observed" if self._evidence[key] is not None
                                            else "evidence-missing")
            observed = self._state[key] == "observed"
            if observed:
                delay = self.interval
            await asyncio.sleep(max(delay, retry_after))
            if not observed:
                delay = min(delay * 2, max_delay)

    def status(self):
        """Bounded nonsecret reader status, not admission or quota proof."""
        return tuple((a.route.route_id, self._state.get(a.route.route_id, "not-observed"))
                     for a in self.adapters)

    def _metadata_row(self, adapter, now):
        reader_status = ("reader-unmounted" if adapter.read is None
                         else self._state.get(adapter.route_id, "not-observed"))
        try:
            description = adapter.describe(self.observed(adapter.route_id), now=now)
            if type(description) is not dict:
                raise ValueError("Invalid metadata diagnostics")
        except Exception:
            description = {"metadata_state": "unavailable", "account_observation": None,
                           "dispatch_hold": "quota-diagnostics-unavailable"}
        return {**description, "route_id": adapter.route_id, "reader_status": reader_status}

    def roster(self):
        """Report installed readers and diagnostics without granting eligibility."""
        now = self.clock()
        statuses = self.status()
        routes = []
        for adapter, (route_id, reader_status) in zip(self.adapters, statuses):
            route = adapter.route
            evidence = self.observed(route_id)
            quota, hold, headroom, reservations = None, reader_status, None, None
            if evidence is not None:
                hold = "quota-diagnostics-unavailable"
                if adapter.describe is not None:
                    try:
                        quota, hold, headroom, reservations = adapter.describe(evidence, now=now)
                    except Exception:
                        pass
            routes.append({
                "route_id": route_id, "provider": route.provider, "client": route.client,
                "model": route.model, "account_sha256": route.account_sha256,
                "reader_status": reader_status, "account_observation": quota,
                "headroom": headroom, "reservations": reservations,
                "dispatch_hold": hold,
            })
        return {
            "route_readers": statuses,
            "routes": tuple(routes),
            "metadata_candidates": tuple(self._metadata_row(a, now) for a in self.diagnostics),
        }

    @property
    def active(self):
        return self._running

    def observed(self, route_id):
        """Return only this lifecycle's latest owner adapter observation."""
        return self._evidence.get(route_id) if self._running else None

    def select(self, request, *, owner_paused, pending_original, required_capabilities=frozenset()):
        """Filter current observations for the task's explicitly required capabilities."""
        routes = tuple(a.route for a in self.adapters)
        scoped = select_route(request, routes=routes, observations=(), now=self.clock(),
                              owner_paused=owner_paused, pending_original=pending_original)
        eligible = {route_id for route_id, reason in scoped.rejected if reason == "evidence-missing"}
        if not eligible:
            return scoped
        if not _capability_set(required_capabilities):
            return PoolSelection(None, None, "required-capabilities-invalid")
        rows = []
        unsupported = set()
        for adapter in self.adapters:
            route_id = adapter.route.route_id
            if route_id not in eligible:
                continue
            evidence = self.observed(route_id)
            if evidence is None:
                continue
            try:
                row = adapter.project(evidence, request)
            except Exception:
                continue
            if type(row) is not RouteAvailability or row.route != adapter.route:
                continue
            if required_capabilities and row.request == request and row.classification == "available":
                try:
                    capabilities = (adapter.capabilities(evidence, request)
                                    if adapter.capabilities is not None else None)
                except Exception:
                    capabilities = None
                if not _capability_set(capabilities) or not required_capabilities <= capabilities:
                    unsupported.add(route_id)
                    continue
            rows.append(row)
        selection = select_route(request, routes=routes, observations=tuple(rows), now=self.clock(),
                                 owner_paused=owner_paused, pending_original=pending_original)
        return replace(selection, rejected=tuple(
            (route_id, "capability-unproved"
             if route_id in unsupported and reason == "evidence-missing" else reason)
            for route_id, reason in selection.rejected
        ))

    async def run(self, normal_start):
        """One normal service lifecycle, with independent per-route pollers."""
        if self._running:
            raise RuntimeError("Provider discovery is already running")
        self._running = True
        pollers = []
        startup = None
        try:
            for adapter in self.adapters:
                pollers.append(asyncio.create_task(self._poll(adapter)))
            for adapter in self.diagnostics:
                if adapter.read is not None:
                    pollers.append(asyncio.create_task(self._poll(adapter, adapter.route_id)))
            async def start():
                return await normal_start()
            startup = asyncio.create_task(start())
            done, _ = await asyncio.wait((*pollers, startup), return_when=asyncio.FIRST_COMPLETED)
            for task in pollers:
                if task in done:
                    await task
            return await startup
        finally:
            for task in (*pollers, *((startup,) if startup is not None else ())):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pollers, *((startup,) if startup is not None else ()),
                                 return_exceptions=True)
            for task in self._inflight.values():
                if not task.done() and not task.cancelling():
                    task.cancel()
            self._evidence.clear()
            self._state.clear()
            self._running = False
