"""Source-only quota handoff controls for the existing Stokowski lifecycle.

Linear remains the work system. This private registry records claims, operation
intents and mode only; it never selects work, invokes a model, changes Linear,
starts a service or wakes a cloud session. Installed native/publisher/MCP
adapters must call these guards before effects. Unadapted executors are not fenced.
"""

from dataclasses import asdict, dataclass
from contextlib import nullcontext
from copy import deepcopy
import asyncio
import inspect
import math
import os
import re
import time
from typing import Protocol
from uuid import UUID

from .handoff_store import Hold, PrivateStore, digest, require, wire

LOCAL_READY = "local-ready"
TRANSFER_PENDING = "transfer-pending"
CLOUD_ACTIVE = "cloud-active"
LOCAL_RECOVERY = "local-recovery"
MODES = {LOCAL_READY, TRANSFER_PENDING, CLOUD_ACTIVE, LOCAL_RECOVERY}
FRESH_SECONDS = 90
RECOVERY_SECONDS = 300
RAMP = (1, 4, 8, 16, 32, 64)
SHA = re.compile(r"[a-f0-9]{64}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
TERMINAL_OPERATIONS = {"verified", "failed", "denied"}


def token(value):
    require(isinstance(value, str) and TOKEN.fullmatch(value), "invalid_identity")
    return value


def timestamp(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "invalid_timestamp")
    return value


def source_binding(value):
    require(isinstance(value, dict) and set(value) == {"repository", "branch", "base", "head", "tree"},
            "source_binding")
    require(isinstance(value["repository"], str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]), "repository_identity")
    require(isinstance(value["branch"], str) and 0 < len(value["branch"]) <= 256
            and not any(ord(c) < 32 for c in value["branch"]), "source_branch")
    require(all(isinstance(value[k], str) and re.fullmatch(r"[a-f0-9]{40}", value[k])
                for k in ("base", "head", "tree")), "source_pins")
    return dict(value)


@dataclass(frozen=True)
class LinearKey:
    workspace_id: str
    project_id: str
    issue_id: str

    def __post_init__(self):
        for value in asdict(self).values():
            try:
                require(isinstance(value, str) and str(UUID(value)) == value, "linear_uuid_required")
            except (ValueError, AttributeError):
                raise Hold("linear_uuid_required") from None

    @classmethod
    def from_record(cls, value):
        require(isinstance(value, dict) and set(value) == {"workspace_id", "project_id", "issue_id"},
                "linear_key_shape")
        return cls(**value)

    @property
    def identity(self):
        return ":".join(asdict(self).values())

    @property
    def claim_identity(self):
        return f"{self.workspace_id}:{self.issue_id}"


@dataclass(frozen=True)
class RecoveryContext:
    key: LinearKey
    source: dict
    generation: int
    lane: str
    owner: str
    evidence_ref: str
    origin_owner: str | None = None
    origin_generation: int | None = None


@dataclass(frozen=True)
class RecoveryReceipt:
    report_sha256: str
    evidence_ref: str
    checked_at: float


class RecoveryVerifier(Protocol):
    def __call__(self, expected: RecoveryContext) -> RecoveryReceipt:
        """Re-evaluate trusted white-room/remote evidence, or raise Hold.

        Called under the authority lock. This callable is installed owner code,
        never selected by command JSON. It must qualify current Linear scope,
        accepted Git source, ownership, checkpoints and pending effects.
        """


@dataclass(frozen=True)
class OperationContext:
    key: LinearKey
    generation: int
    operation_id: str
    payload_sha256: str
    status: str
    receipt_sha256: str


@dataclass(frozen=True)
class HandoffContext:
    key: LinearKey
    generation: int
    owner: str
    source: dict
    checkpoint: dict
    completed_steps: tuple
    stopped_evidence_sha256: str
    evidence_ref: str
    operations: dict


@dataclass(frozen=True)
class CompletionContext:
    key: LinearKey
    generation: int
    owner: str
    source: dict
    original_operation_id: str
    accepted: bool
    receipt_sha256: str
    operations: dict


@dataclass(frozen=True)
class CoordinatorTransferContext:
    generation: int
    execution_id: str
    principal: str
    successor_execution_id: str
    successor_principal: str
    evidence_ref: str


class _Proofs:
    """Run trusted readers outside the global lock; recheck their exact context."""

    def __init__(self):
        self.values = {}
        self.replay = False

    def get(self, name, context, callback):
        fingerprint = digest(asdict(context) if hasattr(context, "__dataclass_fields__") else context)
        if self.replay:
            require(name in self.values and self.values[name][0] == fingerprint, "verification_context_changed")
            return self.values[name][1]
        require(callable(callback), name + "_observer_unavailable")
        try:
            result = callback(context)
        except Hold:
            raise
        except Exception:
            raise Hold(name + "_unavailable") from None
        self.values[name] = (fingerprint, result)
        return result


class Registry:
    def __init__(self, root, *, recovery_verifier=None, operation_verifier=None,
                 provider_verifier=None, handoff_verifier=None, completion_verifier=None,
                 coordinator_verifier=None, clock=time.time):
        self.store = PrivateStore(root)
        self.recovery_verifier = recovery_verifier
        self.operation_verifier = operation_verifier
        self.provider_verifier = provider_verifier
        self.handoff_verifier = handoff_verifier
        self.completion_verifier = completion_verifier
        self.coordinator_verifier = coordinator_verifier
        self.clock = clock

    @classmethod
    def initialize(cls, root, *, workspace_id, actors, principals, morgan_actor,
                   morgan_execution_id, morgan_principal,
                   local_ceiling=64, recovery_verifier=None, operation_verifier=None,
                   provider_verifier=None, handoff_verifier=None, completion_verifier=None,
                   coordinator_verifier=None, clock=time.time):
        """Provision only after authorization; never infer grants from task input.

        actors maps known role identities to {role, projects}; principals maps
        authenticated transport/OS identities to permitted actor identities.
        The CLI derives its principal from UID, never from request JSON.
        """
        LinearKey(workspace_id, workspace_id, workspace_id)
        require(type(local_ceiling) is int and 1 <= local_ceiling <= 64, "local_ceiling")
        registration = dict(schema="uif-quota-authority-registration/1", uid=os.geteuid(),
                            workspace_id=workspace_id, actors=actors, principals=principals,
                            morgan_actor=morgan_actor, local_ceiling=local_ceiling)
        cls._registration(registration)
        token(morgan_execution_id)
        require(morgan_actor in principals.get(morgan_principal, ()), "coordinator_principal_binding")
        state = dict(schema="uif-quota-authority/1", revision=0, mode=LOCAL_RECOVERY,
                     stopped=True, local_limit=1, provider=None, morgan=None,
                     coordinator=dict(generation=1, execution_id=morgan_execution_id,
                                      principal=morgan_principal, pending=None),
                     tasks={}, requests={}, events=[])
        PrivateStore.initialize(root, registration, state)
        return cls(root, recovery_verifier=recovery_verifier,
                   operation_verifier=operation_verifier, provider_verifier=provider_verifier,
                   handoff_verifier=handoff_verifier, completion_verifier=completion_verifier,
                   coordinator_verifier=coordinator_verifier, clock=clock)

    @staticmethod
    def _registration(reg):
        require(set(reg) == {"schema", "uid", "workspace_id", "actors", "principals", "morgan_actor",
                             "local_ceiling"} and reg["schema"] == "uif-quota-authority-registration/1"
                and reg["uid"] == os.geteuid(), "registration_binding")
        require(type(reg["local_ceiling"]) is int and 1 <= reg["local_ceiling"] <= 64, "local_ceiling")
        require(isinstance(reg["actors"], dict) and reg["actors"] and isinstance(reg["principals"], dict),
                "actor_registration")
        for actor, value in reg["actors"].items():
            token(actor)
            require(isinstance(value, dict) and set(value) == {"role", "projects"}
                    and value["role"] in {"morgan", "native", "project", "observer"}
                    and isinstance(value["projects"], list), "actor_registration")
            for project in value["projects"]:
                LinearKey(reg["workspace_id"], project, project)
        require(reg["morgan_actor"] in reg["actors"]
                and reg["actors"][reg["morgan_actor"]]["role"] == "morgan", "morgan_registration")
        for principal, actors in reg["principals"].items():
            token(principal)
            require(isinstance(actors, list) and actors and all(a in reg["actors"] for a in actors),
                    "principal_registration")

    def _authenticate(self, reg, principal, actor):
        self._registration(reg)
        require(isinstance(principal, str) and actor in reg["principals"].get(principal, ()),
                "unauthenticated_actor")
        return reg["actors"][actor]

    def _fresh(self, observed_at, seconds=FRESH_SECONDS):
        now = self.clock()
        timestamp(now)
        timestamp(observed_at)
        require(0 <= now - observed_at <= seconds, "stale_evidence")

    def _morgan(self, reg, state):
        require(state["morgan"] is not None
                and state["morgan"]["actor"] == reg["morgan_actor"], "active_morgan_required")
        require(state["coordinator"]["pending"] is None
                and state["morgan"]["generation"] == state["coordinator"]["generation"]
                and state["morgan"]["execution_id"] == state["coordinator"]["execution_id"],
                "active_morgan_required")
        self._fresh(state["morgan"]["observed_at"])

    def _provider(self, state):
        require(state["provider"] is not None and state["provider"]["classification"] == "available",
                "local_provider_unavailable")
        self._fresh(state["provider"]["observed_at"])

    def _recover(self, key, task, *, generation=None, owner=None, lane=None, evidence_ref=None, proofs=None):
        require(callable(self.recovery_verifier), "reconstruction_adapter_unavailable")
        successor = generation is not None and generation != task["generation"]
        origin = task.get("checkpoint_origin")
        context = RecoveryContext(key, dict(task["source"]), generation or task["generation"],
                                  lane or task["lane"], owner or task["owner"],
                                  evidence_ref or task["recovery_ref"],
                                  task["owner"] if successor else origin["owner"] if origin else None,
                                  task["generation"] if successor else origin["generation"] if origin else None)
        receipt = (proofs or _Proofs()).get("reconstruction", context, self.recovery_verifier)
        require(isinstance(receipt, RecoveryReceipt) and isinstance(receipt.report_sha256, str)
                and SHA.fullmatch(receipt.report_sha256) and receipt.evidence_ref == context.evidence_ref,
                "reconstruction_binding")
        self._fresh(receipt.checked_at, RECOVERY_SECONDS)
        return asdict(receipt)

    @staticmethod
    def _fields(request, fields):
        require(set(request) == {"action", "request_id", *fields}, "request_shape")
        token(request["request_id"])

    def _has_other_project_claim(self, state, key):
        prefix = key.workspace_id + ":"
        suffix = ":" + key.issue_id
        return (any(identity != key.identity and identity.startswith(prefix) and identity.endswith(suffix)
                    for identity in state["tasks"])
                or self.store.has_other_project_claim(key.workspace_id, key.project_id, key.issue_id))

    def _task(self, state, key, generation):
        require(type(generation) is int and generation > 0, "claim_generation")
        task = state["tasks"].get(key.identity)
        if task is None:
            task = self.store.archived_task(key.identity)
            if task is not None:
                state["tasks"][key.identity] = task
        require(task is not None and task["generation"] == generation and task["key"] == asdict(key),
                "obsolete_claim")
        return task

    def _active(self, reg, state, actor, key, generation, *, reasoning=False):
        require(state["stopped"] is False, "service_stopped")
        task = self._task(state, key, generation)
        require("shared_lease" not in task, "typed_shared_authority_required")
        require(task["owner"] == actor and task["state"] == "active", "claim_not_active")
        require(not self._has_other_project_claim(state, key), "conflicting_project_claims")
        if reasoning and task["lane"] == "local":
            self._provider(state)
            require(state["mode"] in (LOCAL_READY, LOCAL_RECOVERY), "local_admission_closed")
        return task

    def invoke(self, *, principal, actor, request):
        """Authenticated adapter boundary. No principal/role is accepted in JSON."""
        require(isinstance(request, dict) and len(wire(request)) <= 32768, "request_size")
        if request == {"action": "status"}:
            with self.store.transaction(readonly=True) as (reg, state, _):
                self._authenticate(reg, principal, actor)
                return dict(schema=state["schema"], revision=state["revision"], mode=state["mode"],
                            stopped=state["stopped"], local_limit=state["local_limit"], coordinator=state["coordinator"],
                            provider=state["provider"], morgan=state["morgan"],
                            tasks={key: task for key, task in state["tasks"].items()
                                   if "shared_lease" not in task},
                            archive=state.get("cold_archive"), archive_degraded=state.get("archive_degraded"),
                            terminal_history="task_status-by-exact-Linear-key")
        if set(request) == {"action", "key"} and request["action"] == "task_status":
            key = LinearKey.from_record(request["key"])
            with self.store.transaction(readonly=True) as (reg, state, _):
                role = self._authenticate(reg, principal, actor)
                require(key.workspace_id == reg["workspace_id"] and
                        (key.project_id in role["projects"] or actor == reg["morgan_actor"]), "project_authority")
                task = state["tasks"].get(key.identity) or self.store.archived_task(key.identity, readonly=True)
                require(task is None or "shared_lease" not in task, "typed_shared_authority_required")
                return deepcopy(task)
        token(request.get("request_id"))
        task_actions = {"claim", "retry_claim", "capture_handoff", "acknowledge_handoff", "resolve_operation", "finish"}
        key = LinearKey.from_record(request.get("key")) if request.get("action") in task_actions else None
        fence = self.store.task_fence(key.claim_identity) if key is not None else nullcontext()
        with fence:
            with self.store.transaction() as (reg, state, _):
                role, command, prior = self._command(reg, state, principal, actor, request)
                if prior is not None:
                    return prior
                reg, snapshot = deepcopy(reg), deepcopy(state)
            proofs = _Proofs()
            # Readers can do bounded remote reconstruction without holding the
            # global metadata lock. The task fence still prevents its transfer.
            self._apply(reg, snapshot, actor, role, command, proofs)
            proofs.replay = True
            with self.store.transaction() as (reg, state, save):
                role, command, prior = self._command(reg, state, principal, actor, request)
                if prior is not None:
                    return prior
                # Re-evaluate live state, mode, capacity and exact proof contexts.
                # Other tasks/heartbeats remain free to advance during reads.
                result = self._apply(reg, state, actor, role, command, proofs)
                state["revision"] += 1
                result = dict(result, revision=state["revision"])
                state["requests"][actor + ":" + request["request_id"]] = dict(sha256=digest(request), result=result)
                state["events"].append(dict(revision=state["revision"], actor=actor,
                                            action=request["action"], request_id=request["request_id"],
                                            request_sha256=digest(request), at=self.clock()))
                save()
                return result

    def _command(self, reg, state, principal, actor, request):
        role = self._authenticate(reg, principal, actor)
        require(state.get("schema") in {"uif-quota-authority/1", "uif-quota-authority/2"} and state.get("mode") in MODES,
                "state_schema")
        command = dict(request)
        if role["role"] == "morgan":
            coordinator = command.pop("coordinator", None)
            expected = state["coordinator"]
            if command.get("action") == "acknowledge_coordinator":
                expected = expected["pending"] or expected
            # Reject even an idempotent old heartbeat before consulting its
            # receipt: the old result cannot renew a superseded coordinator.
            require(expected is not None and coordinator == {
                "execution_id": expected["execution_id"], "generation": expected["generation"]}
                and principal == expected["principal"], "obsolete_coordinator")
            require(command.get("action") == "acknowledge_coordinator"
                    or state["coordinator"]["pending"] is None, "coordinator_transfer_pending")
        else:
            require("coordinator" not in command, "coordinator_identity_forbidden")
        prior = state["requests"].get(actor + ":" + request["request_id"])
        if prior is None:
            prior = self.store.request_receipt(actor + ":" + request["request_id"])
        if prior is not None:
            require(prior["sha256"] == digest(request), "request_identity_conflict")
            return role, command, prior["result"]
        return role, command, None

    def _apply(self, reg, state, actor, role, req, proofs):
        action = req.get("action")
        if action == "transfer_coordinator":
            self._fields(req, {"successor_execution_id", "successor_principal", "evidence_ref"})
            require(actor == reg["morgan_actor"], "morgan_identity_required")
            token(req["successor_execution_id"])
            token(req["evidence_ref"])
            require(reg["morgan_actor"] in reg["principals"].get(req["successor_principal"], ())
                    and req["successor_execution_id"] != state["coordinator"]["execution_id"],
                    "coordinator_successor_binding")
            require(callable(self.coordinator_verifier), "coordinator_observer_unavailable")
            current = state["coordinator"]
            context = CoordinatorTransferContext(current["generation"], current["execution_id"],
                                                 current["principal"], req["successor_execution_id"],
                                                 req["successor_principal"], req["evidence_ref"])
            require(proofs.get("coordinator", context, self.coordinator_verifier) is True,
                    "coordinator_reconciliation_unverified")
            current["pending"] = dict(generation=current["generation"] + 1,
                                      execution_id=req["successor_execution_id"],
                                      principal=req["successor_principal"], evidence_ref=req["evidence_ref"])
            state["morgan"] = None
            return dict(status="coordinator-transfer-pending", generation=current["pending"]["generation"])
        if action == "acknowledge_coordinator":
            self._fields(req, set())
            require(actor == reg["morgan_actor"], "morgan_identity_required")
            prior = state["coordinator"]
            pending = prior["pending"]
            require(pending is not None, "coordinator_transfer_not_pending")
            require(callable(self.coordinator_verifier), "coordinator_observer_unavailable")
            context = CoordinatorTransferContext(prior["generation"], prior["execution_id"], prior["principal"],
                                                 pending["execution_id"], pending["principal"], pending["evidence_ref"])
            require(proofs.get("coordinator", context, self.coordinator_verifier) is True,
                    "coordinator_reconciliation_unverified")
            state["coordinator"] = dict(generation=pending["generation"], execution_id=pending["execution_id"],
                                        principal=pending["principal"], pending=None)
            return dict(status="coordinator-acknowledged", generation=pending["generation"])
        if action == "heartbeat":
            self._fields(req, {"observed_at"})
            require(actor == reg["morgan_actor"], "morgan_identity_required")
            self._fresh(req["observed_at"])
            if state["morgan"] is not None:
                require(req["observed_at"] > state["morgan"]["observed_at"], "nonmonotonic_heartbeat")
            state["morgan"] = dict(actor=actor, execution_id=state["coordinator"]["execution_id"],
                                   generation=state["coordinator"]["generation"], observed_at=req["observed_at"])
            return dict(status="acknowledged")
        if action == "set_stopped":
            self._fields(req, {"stopped"})
            require(role["role"] == "morgan" and type(req["stopped"]) is bool, "stop_authority")
            state["stopped"] = req["stopped"]
            return dict(stopped=state["stopped"], mode=state["mode"])
        if action == "provider_observation":
            self._fields(req, {"classification", "observed_at", "sequence", "evidence_sha256"})
            require(role["role"] in {"native", "morgan"}, "provider_observer_required")
            require(req["classification"] in {"available", "hard-quota", "transient", "auth-failure", "unknown"}
                    and type(req["sequence"]) is int and req["sequence"] > 0
                    and isinstance(req["evidence_sha256"], str) and SHA.fullmatch(req["evidence_sha256"]),
                    "provider_observation")
            self._fresh(req["observed_at"])
            require(callable(self.provider_verifier), "provider_observer_unavailable")
            require(proofs.get("provider", {k: req[k] for k in
                    ("classification", "sequence", "observed_at", "evidence_sha256")}, self.provider_verifier) is True,
                    "provider_observation_unverified")
            previous = state["provider"]
            if previous is not None:
                require(req["sequence"] > previous["sequence"]
                        and req["observed_at"] >= previous["observed_at"], "nonmonotonic_provider")
            state["provider"] = {k: req[k] for k in ("classification", "observed_at", "sequence", "evidence_sha256")}
            classification = req["classification"]
            if classification == "hard-quota":
                if state["mode"] != CLOUD_ACTIVE:
                    state["mode"] = TRANSFER_PENDING
                state["local_limit"] = 0
            elif classification == "available" and (previous is None or previous["classification"] != "available"):
                state["mode"] = LOCAL_RECOVERY
                state["local_limit"] = 1
            # Transient/auth/unknown observations stop dependent new local
            # inference via _provider; they never impersonate quota exhaustion.
            return dict(mode=state["mode"], classification=classification,
                        local_limit=state["local_limit"])
        if action == "claim":
            self._fields(req, {"key", "lane", "source", "evidence_ref", "original_operation_id"})
            key = LinearKey.from_record(req["key"])
            require(key.workspace_id == reg["workspace_id"] and key.project_id in role["projects"],
                    "project_authority")
            require(req["lane"] in {"local", "cloud"}
                    and role["role"] == ("native" if req["lane"] == "local" else "project"), "lane_authority")
            require(not state["stopped"], "service_stopped")
            require(key.identity not in state["tasks"] and self.store.archived_task(key.identity) is None
                    and not self._has_other_project_claim(state, key),
                    "existing_claim_requires_reconciliation")
            self.store.require_headroom(state, active_delta=1)
            if req["lane"] == "local":
                self._provider(state)
                require(state["mode"] in {LOCAL_READY, LOCAL_RECOVERY}, "local_admission_closed")
                active = sum(t["lane"] == "local" and t["state"] in {"active", "handoff-pending"}
                             for t in state["tasks"].values())
                require(active < min(state["local_limit"], reg["local_ceiling"]), "local_ramp_capacity")
            else:
                self._morgan(reg, state)
            token(req["original_operation_id"])
            token(req["evidence_ref"])
            task = dict(key=asdict(key), owner=actor, lane=req["lane"], generation=1, state="active",
                        source=source_binding(req["source"]), recovery_ref=req["evidence_ref"],
                        original_operation_id=req["original_operation_id"], operations={}, handoffs=[],
                        attempts=[], checkpoint_origin=None)
            task["reconstruction"] = self._recover(key, task, proofs=proofs)
            state["tasks"][key.identity] = task
            if req["lane"] == "cloud" and state["mode"] == TRANSFER_PENDING and not any(
                    t["lane"] == "local" and t["state"] in {"active", "handoff-pending"}
                    for t in state["tasks"].values()):
                state["mode"] = CLOUD_ACTIVE
            return dict(status="claimed", generation=1)
        require(action in {"retry_claim", "capture_handoff", "acknowledge_handoff", "resolve_operation", "finish"},
                "unsupported_action")
        key = LinearKey.from_record(req.get("key"))
        task = self._task(state, key, req.get("generation"))
        require("shared_lease" not in task, "typed_shared_authority_required")
        require(key.workspace_id == reg["workspace_id"] and key.project_id in role["projects"], "project_authority")
        if action in {"retry_claim", "capture_handoff", "acknowledge_handoff"}:
            require(not self._has_other_project_claim(state, key), "conflicting_project_claims")
        if action == "retry_claim":
            self._fields(req, {"key", "generation", "lane", "source", "evidence_ref", "original_operation_id"})
            require(task["state"] == "failed" and not state["stopped"], "failed_attempt_required")
            require(req["lane"] in {"local", "cloud"}
                    and role["role"] == ("native" if req["lane"] == "local" else "project"), "lane_authority")
            require(all(o["status"] in TERMINAL_OPERATIONS for o in task["operations"].values()), "uncertain_operations_held")
            self.store.require_headroom(state, active_delta=1)
            if req["lane"] == "local":
                self._provider(state)
                require(state["mode"] in {LOCAL_READY, LOCAL_RECOVERY}, "local_admission_closed")
                active = sum(t["lane"] == "local" and t["state"] in {"active", "handoff-pending"}
                             for t in state["tasks"].values())
                require(active < min(state["local_limit"], reg["local_ceiling"]), "local_ramp_capacity")
            else:
                self._morgan(reg, state)
            token(req["original_operation_id"])
            token(req["evidence_ref"])
            history = task["attempts"] + [{k: deepcopy(v) for k, v in task.items() if k != "attempts"}]
            require(all(h["original_operation_id"] != req["original_operation_id"] for h in history),
                    "new_attempt_identity_required")
            source = source_binding(req["source"])
            origin = None
            if source == task["source"]:
                # Exact same-source retry may consume the genuine earlier
                # checkpoint through its retained owner/generation. A changed
                # source needs fresh qualification; never restamp old metadata.
                origin = deepcopy(task.get("checkpoint_origin")) or dict(
                    owner=task["owner"], generation=task["generation"])
            successor = dict(task, owner=actor, lane=req["lane"], generation=task["generation"] + 1,
                             state="active", source=source, recovery_ref=req["evidence_ref"],
                             original_operation_id=req["original_operation_id"], operations={}, handoffs=[],
                             attempts=history, checkpoint_origin=origin)
            successor.pop("completion", None)
            successor["reconstruction"] = self._recover(key, successor, proofs=proofs)
            state["tasks"][key.identity] = successor
            return dict(status="reclaimed", generation=successor["generation"])
        if action == "capture_handoff":
            self._fields(req, {"key", "generation", "source", "checkpoint", "completed_steps",
                               "child_tree_gone", "stopped_evidence_sha256", "evidence_ref"})
            require(task["owner"] == actor and task["lane"] == "local" and task["state"] == "active"
                    and state["mode"] == TRANSFER_PENDING, "handoff_not_eligible")
            self._morgan(reg, state)
            require(req["child_tree_gone"] is True and isinstance(req["stopped_evidence_sha256"], str)
                    and SHA.fullmatch(req["stopped_evidence_sha256"]), "stopped_child_proof_required")
            checkpoint = req["checkpoint"]
            require(isinstance(checkpoint, dict) and set(checkpoint) == {"kind", "reference", "sha256"}
                    and checkpoint["kind"] in {"remote", "foo-custody"}
                    and isinstance(checkpoint["reference"], str) and 0 < len(checkpoint["reference"]) <= 512
                    and isinstance(checkpoint["sha256"], str) and SHA.fullmatch(checkpoint["sha256"]), "checkpoint_required")
            require(isinstance(req["completed_steps"], list) and len(req["completed_steps"]) <= 128
                    and all(isinstance(s, str) and 0 < len(s) <= 256 for s in req["completed_steps"]), "completed_steps")
            token(req["evidence_ref"])
            source = source_binding(req["source"])
            require(callable(self.handoff_verifier), "handoff_observer_unavailable")
            context = HandoffContext(key, task["generation"], task["owner"], source, dict(checkpoint),
                                     tuple(req["completed_steps"]), req["stopped_evidence_sha256"],
                                     req["evidence_ref"], {k: dict(v) for k, v in task["operations"].items()})
            require(proofs.get("handoff", context, self.handoff_verifier) is True, "handoff_evidence_unverified")
            task["source"] = source
            task["recovery_ref"] = req["evidence_ref"]
            task["state"] = "handoff-pending"
            task["handoffs"].append(dict(generation=task["generation"], source=task["source"],
                                          checkpoint=checkpoint, completed_steps=req["completed_steps"],
                                          child_tree_gone=True, stopped_evidence_sha256=req["stopped_evidence_sha256"],
                                          unresolved=[k for k, v in task["operations"].items()
                                                      if v["status"] not in TERMINAL_OPERATIONS]))
            return dict(status="captured", generation=task["generation"])
        if action == "acknowledge_handoff":
            self._fields(req, {"key", "generation", "evidence_ref"})
            require(role["role"] == "project" and task["state"] == "handoff-pending"
                    and not state["stopped"], "handoff_not_pending")
            self._morgan(reg, state)
            require(all(o["status"] in TERMINAL_OPERATIONS for o in task["operations"].values()),
                    "uncertain_operations_held")
            token(req["evidence_ref"])
            receipt = self._recover(key, task, generation=task["generation"] + 1,
                                    owner=actor, lane="cloud", evidence_ref=req["evidence_ref"], proofs=proofs)
            origin = dict(owner=task["owner"], generation=task["generation"])
            task.update(generation=task["generation"] + 1, owner=actor, lane="cloud", state="active",
                        recovery_ref=req["evidence_ref"], reconstruction=receipt, checkpoint_origin=origin)
            if not any(t["lane"] == "local" and t["state"] in {"active", "handoff-pending"}
                       for t in state["tasks"].values()):
                state["mode"] = CLOUD_ACTIVE
            return dict(status="acknowledged", generation=task["generation"])
        if action == "resolve_operation":
            self._fields(req, {"key", "generation", "operation_id", "status", "receipt_sha256"})
            require(task["owner"] == actor or actor == reg["morgan_actor"], "operation_owner_required")
            require(req["operation_id"] in task["operations"] and req["status"] in TERMINAL_OPERATIONS
                    and isinstance(req["receipt_sha256"], str) and SHA.fullmatch(req["receipt_sha256"]),
                    "original_operation_receipt_required")
            operation = task["operations"][req["operation_id"]]
            require(operation["status"] not in TERMINAL_OPERATIONS, "terminal_operation_immutable")
            require(callable(self.operation_verifier), "operation_observer_unavailable")
            context = OperationContext(key, task["generation"], req["operation_id"],
                                       operation["payload_sha256"], req["status"], req["receipt_sha256"])
            require(proofs.get("operation", context, self.operation_verifier) is True, "operation_receipt_unverified")
            operation.update(status=req["status"], receipt_sha256=req["receipt_sha256"])
            return dict(status="reconciled", operation_id=req["operation_id"])
        self._fields(req, {"key", "generation", "accepted", "receipt_sha256"})
        self._active(reg, state, actor, key, task["generation"])
        self._recover(key, task, proofs=proofs)
        require(type(req["accepted"]) is bool and isinstance(req["receipt_sha256"], str)
                and SHA.fullmatch(req["receipt_sha256"]), "completion_receipt_required")
        require(all(o["status"] in TERMINAL_OPERATIONS for o in task["operations"].values()), "uncertain_operations_held")
        require(callable(self.completion_verifier), "completion_observer_unavailable")
        context = CompletionContext(key, task["generation"], task["owner"], dict(task["source"]),
                                    task["original_operation_id"], req["accepted"], req["receipt_sha256"],
                                    {k: dict(v) for k, v in task["operations"].items()})
        require(proofs.get("completion", context, self.completion_verifier) is True, "completion_acceptance_unverified")
        task.update(state="complete" if req["accepted"] else "failed",
                    completion=dict(accepted=req["accepted"], receipt_sha256=req["receipt_sha256"]))
        if task["lane"] == "local" and state["mode"] == LOCAL_RECOVERY:
            limit = state["local_limit"]
            state["local_limit"] = min(reg["local_ceiling"], next((n for n in RAMP if n > limit), 64)) if req["accepted"] else 1
            if req["accepted"] and state["local_limit"] == reg["local_ceiling"]:
                state["mode"] = LOCAL_READY
        return dict(status="completed", local_limit=state["local_limit"], mode=state["mode"])

    def perform_effect(self, *, principal, actor, key, generation, operation_id,
                       payload_sha256, effect, reasoning=False):
        """Hold the authority fence across one trusted, bounded external adapter.

        Persist intent before execution. Lost/crashed replies remain pending or
        unknown under the original operation ID. No JSON command selects effect.
        Returned {status, receipt_sha256} must reflect independently observed
        destination state. A generic tool's exit status is not sufficient.
        """
        self._effect_arguments(key, operation_id, payload_sha256, effect)
        binding = dict(principal=principal, actor=actor, key=key, generation=generation,
                       operation_id=operation_id, payload_sha256=payload_sha256, reasoning=reasoning)
        with self.store.task_fence(key.claim_identity):
            snapshot, previous = self._effect_snapshot(**binding)
            if previous is not None:
                return previous
            proofs = self._effect_proofs(key, snapshot)
            operation = self._begin_effect(**binding, proofs=proofs)
            try:
                result = effect()
                self._validate_effect_result(result)
            except BaseException:
                self._retain_unknown(binding, operation)
                raise
            return self._record_effect(**binding, operation=operation, outcome=result)

    async def perform_effect_async(self, *, principal, actor, key, generation, operation_id,
                                   payload_sha256, effect, reasoning=False):
        """Await one real effect with the same journal/identity as synchronous use.

        Only this task's stable effect/transfer fence spans awaits. Metadata
        transactions remain short. The trusted reconstruction reader is read
        only and runs off the event-loop thread; cancellation cannot turn its
        eventual result into a new intent or effect. Callback success records
        this operation's observed outcome, never final task acceptance.
        """
        self._effect_arguments(key, operation_id, payload_sha256, effect)
        binding = dict(principal=principal, actor=actor, key=key, generation=generation,
                       operation_id=operation_id, payload_sha256=payload_sha256, reasoning=reasoning)
        with self.store.task_fence(key.claim_identity):
            snapshot, previous = self._effect_snapshot(**binding)
            if previous is not None:
                return previous
            # Installed authenticated readers retain their own bounded HTTP
            # and checkpoint deadlines. Cancellation can leave only that
            # read-only reader finishing; its late result never admits work.
            proofs = await asyncio.to_thread(self._effect_proofs, key, snapshot)
            # A quota/stop observation may have changed while reconstruction
            # awaited. Recheck immediately before durably admitting the effect.
            operation = self._begin_effect(**binding, proofs=proofs)
            try:
                pending = effect()
                require(inspect.isawaitable(pending), "async_effect_adapter_required")
                result = await pending
                self._validate_effect_result(result)
            except BaseException:
                self._retain_unknown(binding, operation)
                raise
            return self._record_effect(**binding, operation=operation, outcome=result)

    @staticmethod
    def _effect_arguments(key, operation_id, payload_sha256, effect):
        require(isinstance(key, LinearKey) and callable(effect), "effect_adapter_required")
        token(operation_id)
        require(isinstance(payload_sha256, str) and SHA.fullmatch(payload_sha256), "effect_payload_binding")

    def recheck_effect(self, *, principal, actor, key, generation, operation_id, source,
                       reasoning=False):
        """Check an already-admitted effect immediately before a child/API call.

        The installed caller must still hold perform_effect[_async]'s task
        fence. This read creates no permit and cannot admit a new operation.
        It is needed after an awaited Pool reservation, during which quota or
        the independent stopped flag can change.
        """
        with self.store.transaction() as (reg, state, _):
            self._authenticate(reg, principal, actor)
            task = self._active(reg, state, actor, key, generation, reasoning=reasoning)
            operation = task["operations"].get(operation_id)
            require(task["source"] == source and operation is not None
                    and operation["generation"] == generation and operation["status"] == "pending",
                    "effect_intent_drift")

    def _effect_snapshot(self, *, principal, actor, key, generation, operation_id,
                         payload_sha256, reasoning):
        with self.store.transaction() as (reg, state, _):
            self._authenticate(reg, principal, actor)
            task = self._active(reg, state, actor, key, generation, reasoning=reasoning)
            previous = task["operations"].get(operation_id)
            if previous is not None:
                require(previous["payload_sha256"] == payload_sha256, "operation_identity_conflict")
                require(previous["status"] in TERMINAL_OPERATIONS, "original_operation_requires_reconciliation")
                return None, dict(previous)
            require(not any(operation_id in attempt["operations"] for attempt in task["attempts"]),
                    "historical_operation_requires_reconciliation")
            return deepcopy(task), None

    def _effect_proofs(self, key, snapshot):
        proofs = _Proofs()
        self._recover(key, snapshot, proofs=proofs)
        proofs.replay = True
        return proofs

    def _begin_effect(self, *, principal, actor, key, generation, operation_id,
                      payload_sha256, reasoning, proofs):
        with self.store.transaction() as (reg, state, save):
            self._authenticate(reg, principal, actor)
            task = self._active(reg, state, actor, key, generation, reasoning=reasoning)
            self._recover(key, task, proofs=proofs)
            require(operation_id not in task["operations"] and all(
                o["status"] in TERMINAL_OPERATIONS for o in task["operations"].values()), "uncertain_operations_held")
            self.store.require_headroom(state)
            operation = dict(status="pending", payload_sha256=payload_sha256, generation=generation)
            task["operations"][operation_id] = operation
            state["revision"] += 1
            save()
            return operation

    def _record_effect(self, *, principal, actor, key, generation, operation_id,
                       payload_sha256, reasoning, operation, outcome):
        with self.store.transaction() as (reg, state, save):
            self._authenticate(reg, principal, actor)
            task = self._task(state, key, generation)
            original = task["operations"].get(operation_id)
            require(task["owner"] == actor and original == operation, "effect_intent_drift")
            original.update(outcome)
            state["revision"] += 1
            save()
            return dict(original)

    def _retain_unknown(self, binding, operation):
        try:
            self._record_effect(**binding, operation=operation, outcome=dict(status="unknown"))
        except (Hold, OSError):
            # A temporarily busy/unavailable authority leaves the original
            # durable pending intent held. Preserve the original exception or
            # cancellation; never turn a failed observation into safe replay.
            pass

    @staticmethod
    def _validate_effect_result(result):
        require(isinstance(result, dict) and set(result) == {"status", "receipt_sha256"}
                and result["status"] in TERMINAL_OPERATIONS
                and isinstance(result["receipt_sha256"], str) and SHA.fullmatch(result["receipt_sha256"]),
                "effect_destination_receipt_required")
