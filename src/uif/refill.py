"""Read a verified predecessor for RecoveredLedger's existing refill qualifier."""

from collections.abc import Callable
from dataclasses import dataclass

from .ledger import RowKey
from .publication import AcceptedPublication, Publisher, digest, identifier, require


@dataclass(frozen=True)
class DeliveryCompletion:
    """Authenticated original completion from the existing delivery authority."""

    publication: AcceptedPublication
    outcome: str
    outcome_receipt_sha256: str


@dataclass(frozen=True)
class VerifiedRefill:
    store_id: str
    read_completion: Callable[[str], DeliveryCompletion | None]
    publisher: Publisher

    def verify_refill(self, original_operation_id: str) -> RowKey:
        """Resolve the original admission operation to its exact stable row."""
        require(identifier(original_operation_id), "uif_refill_original_operation")
        completed = self.read_completion(original_operation_id)
        require(isinstance(completed, DeliveryCompletion)
                and completed.outcome == "completed"
                and digest(completed.outcome_receipt_sha256)
                and isinstance(completed.publication, AcceptedPublication),
                "uif_refill_final_outcome_required")
        publication = completed.publication
        request = publication.request
        require(request.original_operation_id == original_operation_id
                and request.key.store_id == self.store_id, "uif_refill_completion_binding")
        # The historical completion fence authorizes readback only. Its scope
        # keeps final-outcome, intent and readback checks in the same transaction.
        with self.publisher.fence(request, "completion") as owner:
            require(self.read_completion(original_operation_id) == completed,
                    "uif_refill_completion_drift")
            observed = self.publisher.observe_original(request, owner)
            require(observed == publication, "uif_refill_publication_drift")
            require(self.read_completion(original_operation_id) == completed,
                    "uif_refill_completion_drift")
            return request.key
