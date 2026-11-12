"""Verify that the UIF composition binds downstream effects to original receipts."""

from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import test_publication as publication_fixtures
from uif.admission import OwnerStages
from uif.source import SourceHold


class OwnerStageIntegrationTests(IsolatedAsyncioTestCase):
    async def test_publish_requires_the_accepted_validation_and_independent_review(self):
        fixture = publication_fixtures.PublicationFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        request = fixture.request
        dispatch = SimpleNamespace(ledger=request.admission, issue=request.issue)
        binding = SimpleNamespace(
            dispatch=dispatch, base=request.source_base, head=request.source_head, tree=request.source_tree,
            branch=request.source_branch, coding_receipt="fixture-coding", coding_receipt_sha256="b" * 64,
        )
        outcome = SimpleNamespace(status="succeeded", lease_id=request.capacity_lease_id,
                                  worker_receipt_path=binding.coding_receipt,
                                  worker_receipt_sha256=binding.coding_receipt_sha256)
        validation = SimpleNamespace(binding=binding, operation_key=request.validation_operation_id,
                                     windmill_job_id=request.validation_job_id,
                                     receipt_sha256=request.validation_receipt_sha256)
        review = SimpleNamespace(binding=binding, operation_key=request.review_operation_id,
                                 reviewer=request.reviewer, receipt_sha256=request.review_receipt_sha256,
                                 validation_receipt_sha256=validation.receipt_sha256)
        verify_claim = Mock()
        validate = AsyncMock(return_value=validation)
        read_review = AsyncMock(return_value=review)
        publish = Mock(return_value="fixture accepted publication")
        recovered = SimpleNamespace(publication="fixture recovered publication")
        observe = Mock(return_value=recovered)
        complete = Mock()
        stages = OwnerStages(
            SimpleNamespace(ledger=SimpleNamespace(verify_claim=verify_claim)),
            SimpleNamespace(accepted=validate), SimpleNamespace(accepted=read_review),
            SimpleNamespace(consume_boundary=publish), SimpleNamespace(observe=observe), complete,
        )
        spec = SimpleNamespace(binding=binding, validation="fixture-validation-spec")
        with patch("uif.dispatch.read_outcome", return_value=outcome):
            with self.assertRaisesRegex(SourceHold, "publication_stage_binding"):
                wrong_pin = ("1" if request.review_receipt_sha256[0] != "1" else "2") + request.review_receipt_sha256[1:]
                await stages.publish(replace(request, review_receipt_sha256=wrong_pin), spec)
            publish.assert_not_called()
            complete.assert_not_called()
            self.assertEqual(await stages.publish(request, spec), "fixture accepted publication")
        publish.assert_called_once_with(request)
        complete.assert_called_once_with("fixture accepted publication")
        complete.reset_mock()
        self.assertEqual(stages.observe_original(request), recovered)
        complete.assert_not_called()
        self.assertEqual(stages.recover_delivery(request), recovered)
        complete.assert_called_once_with("fixture recovered publication")
        self.assertEqual(observe.call_count, 2)
        verify_claim.assert_called_with(request.admission, request.issue)
