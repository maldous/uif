"""Pure decision contracts, with synthetic evidence and no service assertions."""

from dataclasses import asdict, replace
import json
import unittest

from uif.continuity import (CapacityEvidence, ContinuityDecider, ContinuityPolicy, ContinuityState,
                            EvidenceReceipt, HealthProbe, _digest)
from uif.handoff_store import Hold

PIN = "d" * 64


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.reader_calls = []
        self.engine = ContinuityDecider(verifier=self.verify)
        self.state = self.engine.initial_state(stopped=False)

    def verify(self, kind, sample):
        self.reader_calls.append((kind, sample))
        # Only a synthetic installed-reader seam. No operational qualification.
        return EvidenceReceipt(kind, sample.source_id, sample.sequence, sample.observed_at,
                               sample.evidence_sha256, _digest(asdict(sample)))

    @staticmethod
    def probe(sequence, at, status="failed"):
        return HealthProbe("qualified-foo-health", sequence, at, PIN, status)

    @staticmethod
    def capacity(sequence, at, **changes):
        return replace(CapacityEvidence(
            "qualified-existing-routes", sequence, at, PIN,
            ("local-inference", "cloud-inference", "deterministic"),
            local_inference="available", cloud_inference="available", shared_authority="available",
            cloud_lifecycle="available", deterministic_execution="available"), **changes)

    def step(self, at, probe, capacity=None):
        self.state, decision = self.engine.step(self.state, now=at, probe=probe,
                                                capacity=capacity or self.capacity(probe.sequence, at))
        return decision

    def test_default_outage_requires_distinct_qualified_failure_after_fifteen_minutes(self):
        first = self.step(0, self.probe(1, 0))
        second = self.step(1, self.probe(2, 1))
        self.assertFalse(first.foo_outage_confirmed)
        self.assertFalse(second.foo_outage_confirmed)
        at_grace = self.step(900, self.probe(3, 900))
        self.assertTrue(at_grace.foo_outage_confirmed)
        self.assertFalse(at_grace.local_inference_allowed)
        self.assertTrue(at_grace.independent_cloud_allowed)
        self.assertTrue(at_grace.hold_uncertain_foo_operations)

    def test_elapsed_timer_and_cached_failures_never_confirm_an_outage(self):
        policy = ContinuityPolicy(evidence_ttl_seconds=2000)
        self.engine = ContinuityDecider(verifier=self.verify, policy=policy)
        self.state = self.engine.initial_state(stopped=False)
        self.step(0, self.probe(1, 0))
        self.step(1, self.probe(2, 1))
        decision = self.step(1000, self.probe(2, 1), self.capacity(3, 1000))
        self.assertFalse(decision.foo_outage_confirmed)
        self.assertEqual(self.state.failure_count, 2)
        self.assertEqual(self.state.last_failure_at, 1)

    def test_healthy_unknown_and_unqualified_observations_break_sustained_failure(self):
        for interruption in ("healthy", "unknown", "unqualified"):
            with self.subTest(interruption=interruption):
                self.state = self.engine.initial_state(stopped=False)
                self.step(0, self.probe(1, 0))
                sample = self.probe(2, 450, "healthy" if interruption == "healthy" else "unknown")
                if interruption == "unqualified":
                    original = self.engine.verifier
                    self.engine.verifier = lambda *_: True
                    self.step(450, sample)
                    self.engine.verifier = original
                else:
                    self.step(450, sample)
                self.assertFalse(self.step(900, self.probe(3, 900)).foo_outage_confirmed)
                self.assertEqual(self.state.failure_since, 900)
                self.assertTrue(self.step(1800, self.probe(4, 1800)).foo_outage_confirmed)

    def test_sparse_sequence_stale_future_and_clockback_cannot_trigger_outage(self):
        self.step(0, self.probe(1, 0))
        self.assertFalse(self.step(901, self.probe(2, 901)).foo_outage_confirmed)
        self.assertEqual(self.state.failure_since, 901)
        self.assertFalse(self.step(1000, self.probe(3, 800)).foo_outage_confirmed)
        self.assertEqual(self.state.failure_count, 0)
        self.assertFalse(self.step(1100, self.probe(4, 1101)).foo_outage_confirmed)
        self.step(1200, self.probe(5, 1200))
        decision = self.step(1199, self.probe(6, 1199), self.capacity(6, 1199))
        self.assertFalse(decision.foo_outage_confirmed)
        self.assertFalse(decision.independent_cloud_allowed)
        self.assertIn("clock-backwards", decision.reasons)

    def test_duplicate_or_out_of_order_probe_never_increments_failure_history(self):
        self.step(0, self.probe(1, 0))
        self.step(1, self.probe(1, 0), self.capacity(2, 1))
        self.assertEqual(self.state.failure_count, 1)
        self.step(2, self.probe(1, 2), self.capacity(3, 2))
        self.assertEqual(self.state.failure_count, 0)
        self.assertEqual(self.state.probe_sequence, 1)

    def test_hard_quota_closes_local_inference_immediately_without_host_outage_or_transfer(self):
        decision = self.step(0, self.probe(1, 0, "healthy"),
                             self.capacity(1, 0, local_inference="hard-quota"))
        self.assertFalse(decision.local_inference_allowed)
        self.assertTrue(decision.independent_cloud_allowed)
        self.assertFalse(decision.foo_outage_confirmed)
        self.assertEqual(self.state.failure_count, 0)
        self.assertIn("foo-inference-quota-exhausted", decision.reasons)

    def test_cloud_requires_qualified_shared_authority_lifecycle_and_authorized_existing_capacity(self):
        for change in (dict(shared_authority="unknown"), dict(shared_authority="unavailable"),
                       dict(cloud_lifecycle="unknown"), dict(cloud_lifecycle="unavailable"),
                       dict(cloud_inference="hard-quota"), dict(authorized_routes=("deterministic",))):
            with self.subTest(change=change):
                self.state = self.engine.initial_state(stopped=False)
                decision = self.step(0, self.probe(1, 0), self.capacity(1, 0, **change))
                self.assertFalse(decision.independent_cloud_allowed)
                self.assertTrue(decision.hold_uncertain_foo_operations)

    def test_all_inference_exhausted_preserves_only_authorized_deterministic_work(self):
        decision = self.step(0, self.probe(1, 0, "healthy"), self.capacity(
            1, 0, local_inference="hard-quota", cloud_inference="hard-quota"))
        self.assertTrue(decision.reasoning_unavailable)
        self.assertTrue(decision.authorized_deterministic_allowed)
        self.assertFalse(decision.local_inference_allowed)
        self.assertFalse(decision.independent_cloud_allowed)
        self.state = self.engine.initial_state(stopped=False)
        decision = self.step(0, self.probe(1, 0, "healthy"), self.capacity(
            1, 0, authorized_routes=("local-inference", "cloud-inference"),
            local_inference="hard-quota", cloud_inference="hard-quota"))
        self.assertFalse(decision.authorized_deterministic_allowed)

    def test_explicit_stop_survives_quota_and_health_changes(self):
        self.state = self.engine.initial_state()
        self.step(0, self.probe(1, 0))
        self.step(900, self.probe(2, 900))
        decision = self.step(901, self.probe(3, 901, "healthy"), self.capacity(
            3, 901, foo_reconstruction="qualified"))
        self.assertTrue(self.state.stopped)
        self.assertFalse(decision.local_inference_allowed)
        self.assertFalse(decision.independent_cloud_allowed)
        self.assertFalse(decision.authorized_deterministic_allowed)

    def test_foo_return_requires_actual_reconstruction_while_cloud_keeps_working(self):
        self.step(0, self.probe(1, 0))
        self.step(900, self.probe(2, 900))
        decision = self.step(901, self.probe(3, 901, "healthy"))
        self.assertFalse(decision.foo_outage_confirmed)
        self.assertTrue(decision.requires_foo_reconstruction)
        self.assertFalse(decision.local_inference_allowed)
        self.assertTrue(decision.independent_cloud_allowed)
        decision = self.step(902, self.probe(4, 902, "healthy"), self.capacity(
            4, 902, foo_reconstruction="qualified"))
        self.assertFalse(decision.requires_foo_reconstruction)
        self.assertTrue(decision.local_inference_allowed)

    def test_state_roundtrip_bounded_history_and_version_or_policy_drift_refusal(self):
        for sequence in range(1, 80):
            self.step(sequence, self.probe(sequence, sequence))
        record = json.loads(json.dumps(self.state.to_record()))
        restored = ContinuityState.from_record(record, self.engine.policy)
        self.assertEqual(restored, self.state)
        self.assertLess(len(json.dumps(record)), 1600)
        for altered in ({**record, "schema": "uif-continuity-state/2"},
                        {**record, "policy_sha256": "a" * 64}, {**record, "stopped": "false"},
                        {**record, "failure_since": None}, {**record, "unexpected": True}):
            with self.assertRaises(Hold):
                ContinuityState.from_record(altered, self.engine.policy)

    def test_boolean_or_wrong_scope_receipt_never_qualifies_capacity(self):
        self.engine.verifier = lambda *_: True
        decision = self.step(0, self.probe(1, 0, "healthy"))
        self.assertFalse(decision.local_inference_allowed)
        self.assertFalse(decision.independent_cloud_allowed)
        self.assertEqual(self.state.probe_sequence, 0)
        self.engine.verifier = lambda kind, sample: EvidenceReceipt(
            "wrong-scope", sample.source_id, sample.sequence, sample.observed_at,
            sample.evidence_sha256, _digest(asdict(sample)))
        decision = self.step(1, self.probe(1, 1, "healthy"))
        self.assertFalse(decision.local_inference_allowed)
        self.assertFalse(decision.authorized_deterministic_allowed)

    def test_explicit_positive_grace_override_is_pinned_and_default_remains_fifteen_minutes(self):
        self.assertEqual(ContinuityPolicy().foo_outage_grace_seconds, 900)
        self.engine = ContinuityDecider(verifier=self.verify, policy=ContinuityPolicy(foo_outage_grace_seconds=600))
        self.state = self.engine.initial_state(stopped=False)
        self.step(0, self.probe(1, 0))
        self.assertTrue(self.step(600, self.probe(2, 600)).foo_outage_confirmed)
        with self.assertRaises(Hold):
            ContinuityPolicy(foo_outage_grace_seconds=0)

    def test_same_capacity_identity_cannot_equivocate_body_or_source(self):
        accepted = self.capacity(1, 0, local_inference="hard-quota", cloud_inference="hard-quota")
        self.step(0, self.probe(1, 0, "healthy"), accepted)
        for changed in (replace(accepted, local_inference="available"),
                        replace(accepted, authorized_routes=()),
                        replace(accepted, source_id="another-source")):
            with self.subTest(changed=changed):
                state, decision = self.engine.step(self.state, now=1,
                                                    probe=self.probe(1, 0, "healthy"), capacity=changed)
                self.assertFalse(decision.local_inference_allowed)
                self.assertFalse(decision.independent_cloud_allowed)
                self.assertEqual(state.capacity_observation_sha256, self.state.capacity_observation_sha256)

    def test_changed_probe_source_cannot_splice_sustained_failure_history(self):
        self.step(0, self.probe(1, 0))
        changed = replace(self.probe(2, 900), source_id="different-health-reader")
        self.assertFalse(self.step(900, changed).foo_outage_confirmed)
        self.assertEqual(self.state.failure_count, 0)
        self.assertEqual(self.state.probe_source_id, "qualified-foo-health")
        self.assertFalse(self.step(901, self.probe(2, 901)).foo_outage_confirmed)

    def test_same_probe_identity_cannot_change_accepted_health_body(self):
        self.step(0, self.probe(1, 0))
        decision = self.step(1, self.probe(1, 0, "healthy"), self.capacity(2, 1))
        self.assertFalse(decision.local_inference_allowed)
        self.assertEqual(self.state.failure_count, 0)


if __name__ == "__main__":
    unittest.main()
