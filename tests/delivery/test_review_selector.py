from dataclasses import asdict, replace
import os
import unittest
from unittest.mock import patch

import test_review

from uif.dispatch import SCHEMA
from uif.review_launcher import ReviewInvocation, ReviewLauncher, ReviewSettings
from uif.review_selector import ReviewSelector, ReviewWorkerRegistration, decode_review_spec, invoke_review
from uif.source import SourceHold
from uif.validation import ValidationAdapter, WindmillValidationBackend, decode, sha, wire


class OriginalCoding(test_review.ValidationFixture):
    def __init__(self, root):
        super().__init__(root)
        backend = WindmillValidationBackend(self.client, workspace="foo", script_path="u/uif/runner")
        self.adapter = ValidationAdapter(self.journal, self.adapter.fence, backend)
        spec = self.spec
        report = spec.binding.dispatch
        attempt = root / "attempts" / report.issue.identifier
        attempt.mkdir(parents=True)
        self.author_binding["head"] = spec.binding.base
        dispatch = attempt / "dispatch-intent.json"
        dispatch.write_bytes(wire(dict(
            schema=SCHEMA, issue=asdict(report.issue), admission=asdict(report.ledger),
            workspace=str(report.workspace), attempt=report.attempt_number, lease_id="coding-lease",
            pool_database=str(root / "state.sqlite"), worker_binding=self.author_binding,
        )))
        (attempt / "intent.json").write_bytes(wire(dict(lease="coding-lease", binding=self.author_binding)))
        coding = attempt / "receipt.json"
        coding.write_bytes(wire(dict(
            decode(self.coding.read_bytes()),
            issue=report.issue.identifier, parent_binding=self.author_binding,
            binding_sha256=sha(wire(self.author_binding)), parent_runtime=str(root),
            attempt=report.issue.identifier, attempt_path=str(attempt), accepted_delivery=False,
            turn_completed=True, source_identity_changed=False, provider_route="codex-chatgpt",
            requested_model="gpt-6.1-sol", requested_effort="medium",
        )))
        self.coding = coding
        report = replace(report, receipt_path=dispatch, receipt_sha256=sha(dispatch.read_bytes()))
        self.spec = replace(spec, binding=replace(
            spec.binding, dispatch=report, coding_receipt=coding, coding_receipt_sha256=sha(coding.read_bytes()),
        ))


    def completed(self, **updates):
        receipt = super().completed(**updates)
        self.client.job["workspace_id"] = "foo"
        return receipt


class Owner:
    def __init__(self, record, stage_fence):
        self.record = record
        self.stage_fence = stage_fence
        self.invocations = []

    def review_record(self, invocation):
        self.invocations.append(invocation)
        return self.record

    def review_fence(self, selection):
        raise AssertionError("this fixture must not start a launcher turn")

    check_review = review_fence


class ReviewSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = f = test_review.ReviewTests()
        f.addCleanup = self.addCleanup
        with patch.object(test_review, "ValidationFixture", OriginalCoding):
            await f.asyncSetUp()
        self.settings = ReviewSettings("codex-chatgpt", "gpt-6.1-sol", "medium")
        f.pool["resources"] = f.policy["resources"] = {"output_bytes": 65536}
        f.spec.policy.write_bytes(wire(f.policy))
        f.spec = replace(f.spec, policy_sha256=sha(f.spec.policy.read_bytes()), pool_binding_json=wire(f.pool))
        spec = f.spec
        self.invocation = ReviewInvocation(f.f.root, "UIF-1", spec.policy, spec.policy_sha256)
        self.argv = ["--runtime", str(f.f.root), "--issue", "UIF-1", "--native-review-policy", str(spec.policy),
                     "--native-review-policy-sha256", spec.policy_sha256]
        admission = spec.binding.dispatch.ledger
        self.record = dict(
            schema="uif-owner-review/1", store_id=admission.store_id, row_id=admission.row_id,
            operation_id=admission.original_operation_id, owner=admission.owner, epoch=admission.epoch,
            runtime=str(f.f.root), issue="UIF-1", policy_path=str(spec.policy), policy_sha256=spec.policy_sha256,
            spec=decode(f.adapter._pin(spec))["spec"], installation_sha256="b" * 64,
            capacity_policy_sha256="c" * 64, provider_proof_sha256="d" * 64,
        )
        self.owner = Owner(self.record, f.adapter.fence)
        self.registration = ReviewWorkerRegistration(
            self.settings, f.executable, sha(f.executable.read_bytes()), os.getuid(), f.db,
            "foo", "u/uif/runner",
        )
        self.selector = ReviewSelector(self.owner, f.adapter, self.settings)

    def test_reads_sealed_selection_and_exact_original_job_and_receipts(self):
        f = self.fixture
        selected = self.selector.select(self.invocation)
        self.assertEqual(selected.spec, f.spec)
        self.assertEqual(selected.spec.validation.windmill_job_id, f.proof.windmill_job_id)
        self.assertEqual(selected.spec.validation_receipt_sha256, f.proof.receipt_sha256)
        self.assertEqual(selected.spec.reviewer, f.reviewer)
        self.assertEqual(selected.spec.lease, "review-lease")
        self.assertEqual(selected.installation_sha256, "b" * 64)
        self.assertEqual(selected.capacity_policy_sha256, "c" * 64)
        self.assertEqual(selected.provider_proof_sha256, "d" * 64)

    def test_fixed_entrypoint_selects_into_the_existing_launcher(self):
        f = self.fixture
        before = (f.f.journal.path.read_bytes(), f.db.read_bytes())
        with patch.object(ReviewLauncher, "run", autospec=True, return_value={"status": "succeeded"}) as run:
            result = invoke_review(self.argv, authority=self.owner, registration=self.registration,
                                   invoked_path=f.executable)
        self.assertEqual(result, {"status": "succeeded"})
        _, invoked, selected = run.call_args.args
        self.assertEqual(invoked, self.invocation)
        self.assertEqual(selected.spec, f.spec)
        self.assertEqual((f.f.journal.path.read_bytes(), f.db.read_bytes()), before)

    def test_foreign_project_coding_permissions_and_author_are_refused(self):
        data = self.record["spec"]
        original = data["pool_binding_json"]
        coding = dict(decode(original), mode="workspace-write", write_roots=["src"])
        data["pool_binding_json"] = wire(coding).decode()
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            decode_review_spec(data)
        data["pool_binding_json"] = original
        data["reviewer"] = self.fixture.f.owner
        with self.assertRaisesRegex(SourceHold, "read_only_binding"):
            decode_review_spec(data)
        data["validation"]["binding"]["dispatch"]["issue"]["project_slug"] = "other"
        with self.assertRaisesRegex(SourceHold, "selector_issue"):
            decode_review_spec(data)

    def test_invocation_or_owner_epoch_mismatch_holds(self):
        with self.assertRaisesRegex(SourceHold, "sealed_record"):
            self.selector.select(replace(self.invocation, policy_sha256="7" * 64))
        self.record["epoch"] = "another-epoch"
        with self.assertRaisesRegex(SourceHold, "owner_binding"):
            self.selector.select(self.invocation)

    def test_running_original_coding_lease_cannot_authorize_review(self):
        f = self.fixture
        f.put_lease("coding-lease", f.f.author_binding, status="running", outcome=None)
        with self.assertRaisesRegex(SourceHold, "original_coding"):
            self.selector.select(self.invocation)

    def test_unbound_windmill_job_or_lease_is_rejected(self):
        data = self.record["spec"]
        job = data["validation"]["windmill_job_id"]
        data["validation"]["windmill_job_id"] = "6ec0e63e-bb4f-487c-a3fb-eb0bfe7e00c9"
        with self.assertRaisesRegex(SourceHold, "operation_rebind"):
            self.selector.select(self.invocation)
        data["validation"]["windmill_job_id"] = job
        data["lease"] = "coding-lease"
        with self.assertRaisesRegex(SourceHold, "original_coding"):
            self.selector.select(self.invocation)

    def test_executable_identity_must_match_before_owner_resolution(self):
        f = self.fixture
        f.executable.write_text("drifted executable")
        with self.assertRaisesRegex(SourceHold, "executable_binding"):
            invoke_review(self.argv, authority=self.owner, registration=self.registration, invoked_path=f.executable)
        self.assertEqual(self.owner.invocations, [])

    def test_missing_journal_never_creates_replacement_state(self):
        f = self.fixture
        path = f.f.journal.path
        path.rename(path.with_suffix(".retained"))
        with self.assertRaises(FileNotFoundError):
            invoke_review(self.argv, authority=self.owner, registration=self.registration, invoked_path=f.executable)
        self.assertFalse(path.exists())

    def test_requires_explicit_foo_windmill_workspace(self):
        with self.assertRaisesRegex(SourceHold, "windmill_workspace"):
            replace(self.registration, windmill_workspace="another-workspace")

    def test_sealed_reviewer_must_match_the_authorized_policy(self):
        data = self.record["spec"]
        data["reviewer"] = "other-reviewer"
        data["pool_binding_json"] = wire(dict(decode(data["pool_binding_json"]), owner="other-reviewer")).decode()
        with self.assertRaisesRegex(SourceHold, "policy_binding"):
            self.selector.select(self.invocation)
