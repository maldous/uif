"""Local synthetic provider observations. No provider, service or tracker calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_quota_handoff as fixtures
from uif.handoff_store import Hold, digest
from uif.native_quota import AsyncNativeQuotaBoundary, NativeProfile, NativeQuotaBoundary, PoolPermit
from uif.provider_pool import (IncludedResponsesGrant, IncludedResponsesRequest, PoolRequest,
                               ProviderRoute, RouteAvailability, responses_request_binding,
                               select_route)
from uif.quota_handoff import LinearKey

try:
    from uif.openai_quota_admission import ApiCreditEvidence, ApiRoute
except ModuleNotFoundError as exc:
    if exc.name != "uif.openai_quota_admission":
        raise
    ApiRoute = None


WORKSPACE = "00000000-0000-0000-0000-000000000001"
PROJECT = "00000000-0000-0000-0000-000000000002"
ISSUE = "00000000-0000-0000-0000-000000000003"
OTHER_ISSUE = "00000000-0000-0000-0000-000000000004"
OTHER_PROJECT = "00000000-0000-0000-0000-000000000005"
NOW = 10_000
PIN = "a" * 64
SOURCE, SCOPE = "b" * 64, "c" * 64
KEY = LinearKey(WORKSPACE, PROJECT, ISSUE)
ROUTE_A = ProviderRoute("codex-approved", "codex", "codex", "model-a", PIN, "1" * 64,
                        "2" * 64, "8" * 64, WORKSPACE, PROJECT, "owner/repo", frozenset({"author", "review"}))
ROUTE_B = ProviderRoute("groq-approved", "groq", "opencode", "model-b", PIN, "3" * 64,
                        "4" * 64, "9" * 64, WORKSPACE, PROJECT, "owner/repo", frozenset({"author", "review"}))
REQUEST = PoolRequest(KEY, "owner/repo", SOURCE, SCOPE, "op-original", "ordinary-software",
                      "author", "new", (ROUTE_A.route_id,))
RESPONSES_ROUTE = replace(ROUTE_A, route_id="responses-approved", provider="openai-responses",
                          client="responses", model="exact-example-id")
RESPONSES_CALL = IncludedResponsesRequest(True, "f" * 64, NOW + 10, 100)
RESPONSES_REQUEST = replace(REQUEST, route_ids=(RESPONSES_ROUTE.route_id,),
                            responses=RESPONSES_CALL)


def evidence(route=ROUTE_A, request=REQUEST, **change):
    return replace(RouteAvailability(route, request, "available", NOW - 1, NOW + 30, PIN,
                                    True, True, True), **change)


def advise(request=REQUEST, routes=(ROUTE_A, ROUTE_B), rows=None, *,
           now=NOW, paused=False, pending=False):
    return select_route(request, routes=routes,
                        observations=(evidence(request=request),) if rows is None else rows,
                        now=now, owner_paused=paused, pending_original=pending)


class CommonPoolTests(unittest.TestCase):
    def test_independent_route_keeps_working_when_first_throttles_and_resumes_individually(self):
        other = replace(REQUEST, key=LinearKey(WORKSPACE, PROJECT, OTHER_ISSUE),
                        route_ids=(ROUTE_B.route_id,), operation_id="op-independent")
        rows = (evidence(classification="throttled"), evidence(ROUTE_B, other))
        with ThreadPoolExecutor(max_workers=2) as workers:
            hold = workers.submit(advise, REQUEST, (ROUTE_A, ROUTE_B), rows)
            ready = workers.submit(advise, other, (ROUTE_A, ROUTE_B), rows)
            self.assertEqual(hold.result().rejected, ((ROUTE_A.route_id, "throttled"),))
            self.assertEqual(ready.result().route, ROUTE_B)
        self.assertEqual(advise(rows=(evidence(),)).route, ROUTE_A)
        self.assertEqual(advise(other, rows=rows).route, ROUTE_B)

    def test_only_explicit_admission_can_offer_a_second_route_for_a_new_operation(self):
        plan = replace(REQUEST, route_ids=(ROUTE_A.route_id, ROUTE_B.route_id))
        self.assertIsNone(advise(rows=(evidence(ROUTE_B),)).route)
        out = advise(plan, rows=(evidence(ROUTE_A, plan, classification="throttled"),
                                 evidence(ROUTE_B, plan)))
        self.assertEqual(out.route, ROUTE_B)
        self.assertEqual(out.observation.request, plan)
        first = advise(plan, rows=(evidence(ROUTE_A, plan), evidence(ROUTE_B, plan)))
        self.assertEqual(first.route, ROUTE_A)

    def test_paid_unknown_alias_and_unproved_fallback_stay_closed(self):
        bad_routes = (
            replace(ROUTE_A, provider="openai", client="api"),
            replace(ROUTE_A, provider="antigravity"),
            replace(ROUTE_A, provider="candidate-unapproved"),
            replace(ROUTE_A, client="unregistered"),
            replace(ROUTE_A, model="openrouter/auto"),
            replace(ROUTE_A, model="openrouter/auto:free"),
            replace(ROUTE_A, model="latest"),
            replace(ROUTE_A, credential_epoch_sha256="unknown"),
        )
        for route in bad_routes:
            with self.subTest(route=route):
                self.assertIsNone(advise(routes=(route,), rows=(evidence(route),)).route)
        for fields in (dict(included_only=False), dict(included_only=1),
                       dict(fallback_disabled=False), dict(fallback_disabled=1),
                       dict(classification="unknown"), dict(classification="auth-failure"),
                       dict(can_reserve=False), dict(can_reserve=True, receipt_sha256="not-a-pin")):
            with self.subTest(fields=fields):
                self.assertIsNone(advise(rows=(evidence(**fields),)).route)

    def test_openrouter_requires_explicit_terminal_free_model_even_with_available_receipt(self):
        route = replace(ROUTE_A, provider="openrouter", client="opencode", model="vendor/model:free")
        self.assertEqual(advise(routes=(route,), rows=(evidence(route),)).route, route)
        for model in ("vendor/model", "vendor/model:paid", "vendor/model:free:variant",
                      "vendor/model:free/path", "vendor/auto:free", "model:free",
                      "Vendor/model:free", "vendor/model:FREE"):
            with self.subTest(model=model):
                other = replace(route, model=model)
                result = advise(routes=(other,), rows=(evidence(other),))
                self.assertIsNone(result.route)
        paid = replace(route, model="vendor/model")
        self.assertEqual(advise(routes=(paid,), rows=(evidence(paid),)).rejected,
                         ((paid.route_id, "openrouter-free-model-required"),))

    def test_actual_upstream_descriptor_digest_cannot_relabel_available_receipt(self):
        def fingerprint(upstream):
            return digest({"schema": "offline-adapter-route-example/1", "provider": "openrouter",
                           "model": "vendor/model:free", "upstream": upstream})
        configured = replace(ROUTE_A, provider="openrouter", client="opencode",
                             model="vendor/model:free", adapter_route_sha256=fingerprint("a"))
        intended = replace(configured, adapter_route_sha256=fingerprint("b"))
        # All display/provider/model, account, binary and operation fields are equal.
        original = evidence(configured)
        self.assertEqual(advise(routes=(configured,), rows=(original,)).route, configured)
        self.assertEqual(advise(routes=(intended,), rows=(original,)).rejected,
                         ((intended.route_id, "evidence-binding-mismatch"),))
        self.assertEqual(advise(routes=(intended,), rows=(evidence(intended),)).route, intended)
        invalid = replace(configured, adapter_route_sha256="unsigned-upstream")
        self.assertEqual(advise(routes=(invalid,), rows=(evidence(invalid),)).rejected,
                         ((invalid.route_id, "route-invalid"),))

    def test_stop_unknown_pause_pending_original_and_bad_task_scope_hold(self):
        for kwargs in ({"paused": True}, {"paused": None}, {"paused": 0},
                       {"pending": True}, {"pending": None}, {"pending": 0}, {"now": False}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(advise(**kwargs).route)
        for purpose in ("unknown", "", "not-owner-admitted"):
            self.assertEqual(advise(replace(REQUEST, purpose=purpose)).hold, "request-not-admitted")
        self.assertEqual(advise(replace(REQUEST, route_ids=())).hold, "request-not-admitted")

    def test_observation_must_be_fresh_and_exact_to_issue_source_scope_and_operation(self):
        for fields in (dict(observed_at=NOW + 1), dict(observed_at=NOW - 91),
                       dict(expires_at=NOW), dict(expires_at=float("nan")),
                       dict(observed_at=True), dict(observed_at=2**1000)):
            self.assertIsNone(advise(rows=(evidence(**fields),)).route)
        for change in (dict(source_sha256="d" * 64), dict(scope_sha256="e" * 64),
                       dict(operation_id="op-another"),
                       dict(key=LinearKey(WORKSPACE, PROJECT, OTHER_ISSUE))):
            changed = replace(REQUEST, **change)
            no = advise(changed, rows=(evidence(),))
            self.assertEqual(no.rejected, ((ROUTE_A.route_id, "evidence-binding-mismatch"),))

    def test_changed_credential_and_model_do_not_reuse_previous_proof(self):
        for new_route in (replace(ROUTE_A, model="different-model"),
                          replace(ROUTE_A, credential_epoch_sha256="5" * 64),
                          replace(ROUTE_A, client_build_sha256="7" * 64)):
            result = advise(routes=(new_route,), rows=(evidence(),))
            self.assertEqual(result.rejected, ((ROUTE_A.route_id, "evidence-binding-mismatch"),))

    def test_route_scope_distinguishes_card_move_and_repository_remap(self):
        remapped = replace(REQUEST, key=LinearKey(WORKSPACE, OTHER_PROJECT, ISSUE))
        self.assertEqual(remapped.key.claim_identity, REQUEST.key.claim_identity)
        for request in (remapped, replace(REQUEST, repository="owner/replacement")):
            result = advise(request, rows=(evidence(request=request),))
            self.assertEqual(result.rejected, ((ROUTE_A.route_id, "route-out-of-scope"),))

    def test_continuation_and_retry_require_exact_original_route_no_exhausted_replacement(self):
        for intent in ("continuation", "retry"):
            request = replace(REQUEST, intent=intent, original_route=ROUTE_A)
            self.assertEqual(advise(request).route, ROUTE_A)
            held = advise(request, rows=(evidence(ROUTE_A, request, classification="hard-quota"),
                                         evidence(ROUTE_B, request)))
            self.assertIsNone(held.route)
            self.assertEqual(held.rejected, ((ROUTE_A.route_id, "hard-quota"),))
            self.assertEqual(advise(replace(request, route_ids=(ROUTE_B.route_id,))).hold,
                             "request-not-admitted")
            changed = replace(ROUTE_A, model="same-alias-new-model")
            self.assertEqual(advise(request, routes=(changed,)).rejected,
                             ((ROUTE_A.route_id, "original-route-drift"),))
            self.assertEqual(advise(request, pending=True).hold,
                             "original-operation-reconciliation-required")

    def test_independent_review_requires_distinct_proved_account_not_just_provider(self):
        independent = replace(REQUEST, role="review", author_account_sha256=ROUTE_A.account_sha256,
                              route_ids=(ROUTE_B.route_id,))
        self.assertEqual(advise(independent, rows=(evidence(ROUTE_B, independent),)).route, ROUTE_B)
        alias = replace(ROUTE_B, account_sha256=ROUTE_A.account_sha256)
        self.assertEqual(advise(independent, routes=(alias,), rows=(evidence(alias, independent),)).rejected,
                         ((ROUTE_B.route_id, "independent-review-required"),))
        self.assertEqual(advise(replace(independent, author_account_sha256=None)).hold,
                         "request-not-admitted")

    def test_missing_ambiguous_or_malformed_rows_hold_only_the_affected_route(self):
        self.assertEqual(advise(rows=()).rejected, ((ROUTE_A.route_id, "evidence-missing"),))
        self.assertEqual(advise(rows=(evidence(), evidence())).rejected,
                         ((ROUTE_A.route_id, "evidence-ambiguous"),))
        self.assertEqual(advise(routes=(ROUTE_A, ROUTE_A)).rejected,
                         ((ROUTE_A.route_id, "route-ambiguous"),))
        self.assertEqual(advise(rows=[evidence()]).hold, "pool-input-unavailable")
        other = replace(REQUEST, operation_id="op-making-progress")
        current = advise(rows=(evidence(request=other), evidence(), {}))
        self.assertEqual(current.route, ROUTE_A)


class NativeReservationBoundaryTests(unittest.TestCase):
    def test_advisory_choice_is_not_a_pool_permit_and_cannot_launch(self):
        fx = fixtures.QuotaHandoffTests()
        fx.setUp()
        self.addCleanup(fx.tearDown)
        fx.provider("available", 1)
        fx.claim()
        profile = NativeProfile(fx.key, "native-session", "native", 1, fixtures.SOURCE, 3, 30000)
        issue = SimpleNamespace(id=fx.key.issue_id)
        route = replace(ROUTE_A, workspace_id=fx.key.workspace_id,
                        project_id=fx.key.project_id, repository=profile.source["repository"])
        request = replace(REQUEST, key=fx.key, repository=route.repository,
                          source_sha256=digest(profile.source), operation_id="advisory-only")
        choice = advise(request, routes=(route,), rows=(evidence(route, request),))
        self.assertEqual(choice.route, route)
        self.assertNotIsInstance(choice, PoolPermit)
        calls = []
        boundary = NativeQuotaBoundary(
            fx.registry, profile_for=lambda _: profile,
            pool_admit=lambda *args: calls.append("pool") or choice,
            outcome_read=lambda *args: self.fail("advisory cannot create an outcome"),
            provider_read=lambda *args: self.fail("advisory cannot observe a provider"))
        with self.assertRaisesRegex(Hold, "^native_pool_binding$"):
            boundary.dispatch(issue, operation_id=request.operation_id,
                              dispatch_native=lambda *args: self.fail("unreserved launch"))
        self.assertEqual(calls, ["pool"])
        operations = fx.status()["tasks"][fx.key.identity]["operations"]
        self.assertEqual(operations[request.operation_id]["status"], "unknown")
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            boundary.dispatch(issue, operation_id=request.operation_id,
                              dispatch_native=lambda *args: self.fail("unreserved replay"))
        self.assertEqual(calls, ["pool"])


class ResponsesAvailabilityTests(unittest.TestCase):
    def test_exact_list_and_literal_opt_in_are_independently_required(self):
        for call in (None, replace(RESPONSES_CALL, opt_in=False),
                     replace(RESPONSES_CALL, opt_in=1), replace(RESPONSES_CALL, payload_sha256=""),
                     replace(RESPONSES_CALL, deadline=True), replace(RESPONSES_CALL, upper_microusd=0),
                     replace(RESPONSES_CALL, upper_microusd=2**100)):
            req = replace(RESPONSES_REQUEST, responses=call)
            result = advise(req, (RESPONSES_ROUTE,), rows=(evidence(RESPONSES_ROUTE, req),))
            self.assertEqual(result.rejected, ((RESPONSES_ROUTE.route_id,
                                                "responses-explicit-included-request-required"),))
        req = replace(RESPONSES_REQUEST, route_ids=(ROUTE_A.route_id,))
        self.assertEqual(advise(req, (RESPONSES_ROUTE, ROUTE_A),
                                rows=(evidence(RESPONSES_ROUTE, req), evidence(ROUTE_A, req))).route,
                         ROUTE_A)

    def test_uncomposed_adapter_holds_only_responses_and_never_adds_fallback(self):
        for ids, chosen in (((RESPONSES_ROUTE.route_id,), None),
                            ((RESPONSES_ROUTE.route_id, ROUTE_A.route_id), ROUTE_A)):
            req = replace(RESPONSES_REQUEST, route_ids=ids)
            grant = IncludedResponsesGrant(object(), object(), PIN,
                                            responses_request_binding(req, RESPONSES_ROUTE), 0)
            with patch.dict(sys.modules, {"uif.openai_quota_admission": None}):
                result = advise(req, (RESPONSES_ROUTE, ROUTE_A),
                                rows=(evidence(RESPONSES_ROUTE, req, responses_grant=grant),
                                      evidence(ROUTE_A, req)))
            self.assertEqual(result.rejected,
                             ((RESPONSES_ROUTE.route_id, "responses-adapter-unavailable"),))
            self.assertEqual(result.route, chosen)


@unittest.skipUnless(ApiRoute is not None, "requires the separate typed Responses adapter")
class ComposedResponsesGrantTests(unittest.TestCase):
    def setUp(self):
        self.api = ApiRoute("fixture-org", "fixture-project", RESPONSES_ROUTE.credential_epoch_sha256,
                            RESPONSES_ROUTE.model, "responses.create")
        stamps = tuple(datetime.fromtimestamp(NOW + delta, timezone.utc) for delta in (-1, 30, 60))
        self.credit = ApiCreditEvidence(self.api, *stamps, 1000, 500, True, True, True)
        self.route = self.bind(self.api)
        self.grant = IncludedResponsesGrant(self.api, self.credit, PIN,
                                            responses_request_binding(RESPONSES_REQUEST, self.route), 10)

    def bind(self, api):
        return replace(RESPONSES_ROUTE, adapter_route_sha256=digest(asdict(api)),
                       account_sha256=digest({"organization": api.organization}))

    def outcome(self, *, request=RESPONSES_REQUEST, route=None, grant=None, **fields):
        if route is None:
            route = self.route
        return advise(request, (route, ROUTE_A),
                      rows=(evidence(route, request, responses_grant=grant, **fields),
                            evidence(ROUTE_A, request)))

    def test_explicit_typed_included_grant_selects_advice_not_permit(self):
        result = self.outcome(grant=self.grant)
        self.assertEqual(result.route, self.route)
        self.assertIs(result.observation.responses_grant, self.grant)
        self.assertNotIsInstance(result, PoolPermit)
        self.assertEqual(self.outcome().rejected,
                         ((self.route.route_id, "responses-typed-grant-required"),))
        lookalike = replace(self.grant, evidence=SimpleNamespace(**asdict(self.credit)))
        self.assertIsNone(self.outcome(grant=lookalike).route)

    def test_included_no_overage_and_concurrent_request_upper_bound(self):
        for fields in ({"included_only": False}, {"hard_no_overage": False},
                       {"complete_binding": False}, {"hard_no_overage": 1},
                       {"included_balance_lower_microusd": 260},
                       {"credit_expires_at": datetime.fromtimestamp(NOW + 10, timezone.utc)}):
            with self.subTest(fields=fields):
                self.assertIsNone(self.outcome(grant=replace(self.grant,
                                                            evidence=replace(self.credit, **fields))).route)
        self.assertIsNone(self.outcome(grant=replace(self.grant, unsettled_upper_microusd=250)).route)
        self.assertIsNone(self.outcome(grant=self.grant, fallback_disabled=False).route)
        self.assertIsNone(self.outcome(grant=self.grant, expires_at=NOW + 9).route)
        self.assertIsNone(self.outcome(grant=self.grant, expires_at=NOW + 31).route)
        self.assertIsNone(self.outcome(grant=self.grant, observed_at=NOW - 2).route)

    def test_exact_provider_operation_project_account_model_and_credential_are_bound(self):
        for changes in ({"project": "changed-project"}, {"organization": "other-org"},
                        {"model": "another-model"}, {"credential_epoch": "5" * 64},
                        {"operation": "chat.completions.create"}):
            api = replace(self.api, **changes)
            with self.subTest(changes=changes):
                self.assertIsNone(self.outcome(grant=replace(
                    self.grant, route=api, evidence=replace(self.credit, route=api))).route)
        same_account = self.bind(replace(self.api, project="another-project"))
        self.assertEqual(same_account.account_sha256, self.route.account_sha256)
        self.assertNotEqual(same_account.adapter_route_sha256, self.route.adapter_route_sha256)
        review = replace(RESPONSES_REQUEST, role="review",
                         author_account_sha256=same_account.account_sha256)
        self.assertEqual(self.outcome(request=review, grant=self.grant).rejected,
                         ((self.route.route_id, "independent-review-required"),))
        self.assertIsNone(self.outcome(
            grant=replace(self.grant, route=replace(self.api, model="wrong-model"))).route)

    def test_body_bound_and_matching_receipt_no_silent_quote_reuse(self):
        for fields in ({"payload_sha256": "e" * 64}, {"upper_microusd": 99},
                       {"deadline": NOW + 11}):
            req = replace(RESPONSES_REQUEST, responses=replace(RESPONSES_CALL, **fields))
            self.assertEqual(self.outcome(request=req, grant=self.grant).rejected,
                             ((self.route.route_id, "responses-grant-binding-mismatch"),))
        self.assertIsNone(self.outcome(grant=self.grant, receipt_sha256="a9" * 32).route)
        for req in (replace(RESPONSES_REQUEST, operation_id="other-operation"),
                    replace(RESPONSES_REQUEST, key=LinearKey(WORKSPACE, PROJECT, OTHER_ISSUE)),
                    replace(RESPONSES_REQUEST, source_sha256="e" * 64)):
            self.assertIsNone(self.outcome(request=req, grant=self.grant).route)

    def test_continuation_and_retry_use_their_exact_route_and_separate_floor(self):
        lower = replace(self.grant, evidence=replace(self.credit,
                                                     included_balance_lower_microusd=250))
        self.assertIsNone(self.outcome(grant=lower).route)
        for intent in ("continuation", "retry"):
            req = replace(RESPONSES_REQUEST, intent=intent, original_route=self.route)
            self.assertIsNone(self.outcome(request=req, grant=lower).route)
            continued = replace(lower, request_bound_sha256=responses_request_binding(req, self.route))
            self.assertEqual(self.outcome(request=req, grant=continued).route, self.route)
            self.assertIsNone(self.outcome(request=req,
                                          grant=replace(continued, unsettled_upper_microusd=150)).route)
            self.assertEqual(self.outcome(request=replace(req, route_ids=(ROUTE_A.route_id,)),
                                          grant=continued).hold, "request-not-admitted")


class AwaitedReservationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_awaited_entry_point_refuses_unreserved_advice(self):
        fx = fixtures.QuotaHandoffTests()
        fx.setUp()
        self.addCleanup(fx.tearDown)
        fx.provider("available", 1)
        fx.claim()
        profile = NativeProfile(fx.key, "native-session", "native", 1, fixtures.SOURCE, 3, 30000)
        issue = SimpleNamespace(id=fx.key.issue_id)
        route = replace(ROUTE_A, workspace_id=fx.key.workspace_id,
                        project_id=fx.key.project_id, repository=profile.source["repository"])
        request = replace(REQUEST, key=fx.key, repository=route.repository,
                          source_sha256=digest(profile.source), operation_id="async-advisory-only")
        choice = advise(request, routes=(route,), rows=(evidence(route, request),))
        self.assertEqual(choice.route, route)
        calls = []

        async def advise_only(*_):
            calls.append("pool")
            return choice

        boundary = AsyncNativeQuotaBoundary(
            fx.registry, profile_for=lambda _: profile, pool_admit=advise_only,
            outcome_read=lambda *args: self.fail("unreserved outcome"),
            provider_read=lambda *args: self.fail("unreserved observation"))

        with self.assertRaisesRegex(Hold, "^native_pool_binding$"):
            await boundary.dispatch_stage(issue, stage_type="agent", operation_id=request.operation_id,
                                          dispatch_native=lambda *args: self.fail("unreserved dispatch"),
                                          release_ephemeral_slot=lambda *args: calls.append("release"))
        self.assertEqual(calls, ["pool"])
        task = fx.status()["tasks"][fx.key.identity]
        self.assertEqual(task["operations"][request.operation_id]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
