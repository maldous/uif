"""Bind ledger task identity to the canonical reconciliation snapshot."""

from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from .ledger import SNAPSHOT_SHA256, _digest, _identity, read_snapshot
from .source import SourceHold

SCHEMA = "uif-ledger-task/1"
_ROW_ID = re.compile(r"[A-Za-z0-9_.:-]+")
_FIELDS = frozenset({
    "schema", "store_id", "row_id", "snapshot_sha256", "source_sha256",
    "owner", "epoch", "original_operation_id",
})


@dataclass(frozen=True)
class LedgerTaskKey:
    store_id: str
    row_id: str

    def __post_init__(self):
        if not _identity(self.store_id) or not isinstance(self.row_id, str) or not _ROW_ID.fullmatch(self.row_id):
            raise SourceHold("uif_ledger_task_key")

    @property
    def workspace_key(self) -> str:
        identity = json.dumps({"store_id": self.store_id, "row_id": self.row_id},
                              sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return "ledger-" + sha256(identity).hexdigest()


@dataclass(frozen=True)
class LedgerTaskEnvelope:
    """Canonical source identity. Current owner, claim and effect checks are separate."""

    key: LedgerTaskKey
    snapshot_sha256: str
    source_sha256: str
    owner: str
    epoch: str
    original_operation_id: str
    snapshot_path: InitVar[Path]

    def __post_init__(self, snapshot_path):
        if not isinstance(self.key, LedgerTaskKey) or not all(_identity(value) for value in (self.owner, self.epoch)):
            raise SourceHold("uif_ledger_task_binding")
        if not _identity(self.original_operation_id):
            raise SourceHold("uif_ledger_task_operation_unbound")
        if self.snapshot_sha256 != SNAPSHOT_SHA256:
            raise SourceHold("uif_ledger_snapshot_pin")
        row = read_snapshot(snapshot_path).get(self.key.row_id)
        if row is None:
            raise SourceHold("uif_ledger_unknown_original_row")
        if not _digest(self.source_sha256) or self.source_sha256 != row.source_sha256:
            raise SourceHold("uif_ledger_task_row_pin")

    @classmethod
    def from_record(cls, record: Mapping, *, snapshot_path: Path) -> "LedgerTaskEnvelope":
        if not isinstance(record, Mapping) or set(record) != _FIELDS or record["schema"] != SCHEMA:
            raise SourceHold("uif_ledger_task_record")
        return cls(key=LedgerTaskKey(record["store_id"], record["row_id"]),
                   snapshot_sha256=record["snapshot_sha256"], source_sha256=record["source_sha256"],
                   owner=record["owner"], epoch=record["epoch"],
                   original_operation_id=record["original_operation_id"], snapshot_path=snapshot_path)

    def to_record(self) -> dict[str, str]:
        return {
            "schema": SCHEMA, "store_id": self.key.store_id, "row_id": self.key.row_id,
            "snapshot_sha256": self.snapshot_sha256, "source_sha256": self.source_sha256,
            "owner": self.owner, "epoch": self.epoch, "original_operation_id": self.original_operation_id,
        }
