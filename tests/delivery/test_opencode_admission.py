from dataclasses import replace
from datetime import UTC, datetime, timedelta
import unittest

from uif.opencode_admission import FreeEvidence, Refused, Route, SingleChatAttempt, admissible, request_matches


class OpenCodeAdmission(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 23, tzinfo=UTC)
        self.router = Route("openrouter", "fixture/ordinary:free", "a" * 64, "fixture", "fixture/fp8/us")
        self.groq = Route("groq", "fixture/ordinary", "a" * 64, "fixture-org")

    def test_exact_credential_and_binary_and_current_free_only_evidence(self):
        for route in (self.router, self.groq):
            evidence = FreeEvidence(route, self.now, self.now + timedelta(minutes=2), True, True)
            def ready(e=evidence, r=route, digest=route.binary_sha256):
                return admissible(r, e, now=self.now, observed_binary_sha256=digest)
            self.assertTrue(ready())
            for mutation in (
                    replace(evidence, expires_at=self.now),
                    replace(evidence, observed_at=self.now - timedelta(seconds=91)),
                    None, object(),
                    replace(evidence, observed_at=self.now + timedelta(seconds=1)),
                    replace(evidence, observed_at=self.now.replace(tzinfo=None)),
                    replace(evidence, route=replace(route, account="other")),
                    replace(evidence, free_only=False),
                    replace(evidence, no_paid_fallback=False)):
                self.assertFalse(ready(mutation))
            self.assertFalse(ready(digest="b" * 64))
        self.assertFalse(admissible(None, None, now=self.now, observed_binary_sha256="a" * 64))
        for slug in (None, "fixture//us", "fixture/us\n", "fixture/us?price=paid", "a/b/c/d/e"):
            unknown = replace(self.router, upstream=slug)
            self.assertFalse(admissible(unknown, FreeEvidence(unknown, self.now,
                             self.now + timedelta(minutes=1), True, True),
                             now=self.now, observed_binary_sha256="a" * 64))
        for model in ("openrouter/free", "fixture/ordinary", "openrouter/auto:free", "fixture/latest:free"):
            route = replace(self.router, model=model)
            evidence = FreeEvidence(route, self.now, self.now + timedelta(minutes=1), True, True)
            self.assertFalse(admissible(route, evidence, now=self.now, observed_binary_sha256="a" * 64))

    def test_openrouter_wire_blocks_paid_and_implicit_fallback(self):
        url = "https://openrouter.ai/api/v1/chat/completions"
        flags = {"allow_fallbacks": False, "only": [self.router.upstream],
                 "require_parameters": True,
                 "max_price": {"prompt": 0, "completion": 0, "request": 0}}
        body = {"model": self.router.model, "provider": flags}
        self.assertTrue(request_matches(self.router, method="POST", url=url, body=body))
        for changes in (
            {"model": "fixture/ordinary"}, {"model": "openrouter/free"},
            {"models": ["fixture/paid"]}, {"route": "fallback"}, {"preset": "fixture"},
            {"plugins": [{"id": "web"}]},
            {"provider": {**flags, "allow_fallbacks": True}},
            {"provider": {**flags, "require_parameters": False}},
            {"provider": {**flags, "only": ["fixture"]}},
            {"provider": {**flags, "only": ["other-upstream"]}},
            {"provider": {**flags, "max_price": {"prompt": 0, "completion": 0}}},
            {"provider": {**flags, "max_price": {**flags["max_price"], "request": 1}}},
            {"provider": {**flags, "max_price": {**flags["max_price"], "completion": 1}}},
        ):
            self.assertFalse(request_matches(self.router, method="POST", url=url, body=body | changes))

    def test_groq_wire_bound_does_not_allow_openrouter_or_alternate_model(self):
        url = "https://api.groq.com/openai/v1/chat/completions"
        body = {"model": self.groq.model}
        self.assertTrue(request_matches(self.groq, method="POST", url=url, body=body))
        self.assertFalse(request_matches(self.groq, method="POST", url=url + "?redirect", body=body))
        self.assertFalse(request_matches(self.groq, method="POST", url=url, body=body | {"model": "other"}))
        self.assertFalse(request_matches(self.groq, method="POST", url=url, body=body | {"models": ["other"]}))
        self.assertFalse(request_matches(replace(self.groq, model="default"), method="POST",
                                         url=url, body={"model": "default"}))

    def test_relay_normalizes_once_and_refuses_follow_up_on_quota_or_billing(self):
        for status in (200, 402, 429, 503):
            for route, url in (
                (self.router, "https://openrouter.ai/api/v1/chat/completions"),
                (self.groq, "https://api.groq.com/openai/v1/chat/completions"),
            ):
                with self.subTest(status=status, route=route.provider):
                    slot = SingleChatAttempt(route, max_output_tokens=100)
                    call = dict(purpose="main", method="POST", url=url)
                    body = {"model": route.model, "messages": [{"role": "user", "content": "fixture"}],
                            "stream": True, "stream_options": {"include_usage": True}}
                    wire = slot.admit(**call, body=body)
                    self.assertTrue(request_matches(route, method="POST", url=url, body=wire))
                    self.assertNotIn("stream_options", wire)
                    self.assertEqual(wire["max_completion_tokens"], 100)
                    if route.provider == "openrouter":
                        self.assertEqual(wire["provider"], {"allow_fallbacks": False,
                                         "only": [route.upstream], "require_parameters": True,
                                         "max_price": {"prompt": 0, "completion": 0, "request": 0}})
                    # A second concurrent send is impossible before outcome, too.
                    with self.assertRaises(Refused):
                        slot.admit(**call, body=body)
                    if status >= 400:
                        slot.stop()
                    with self.assertRaises(Refused):
                        slot.admit(**call, body=body)
                    self.assertEqual(slot.attempts, 1)

    def test_relay_denies_recorded_helper_and_tool_shapes_before_consuming_slot(self):
        slot = SingleChatAttempt(self.router, max_output_tokens=100)
        call = dict(method="POST", url="https://openrouter.ai/api/v1/chat/completions")
        body = {"model": self.router.model, "messages": [{"role": "user", "content": "fixture"}]}
        with self.assertRaises(Refused):
            slot.admit(**call, purpose="helper", body=body)
        for changes in (
            {"tools": []}, {"reasoning_effort": "medium"}, {"model": "other:free"},
            {"route": "fallback"}, {"provider": {}}, {"max_completion_tokens": 101},
            {"messages": [{"role": "tool", "content": "not approved"}]},
        ):
            with self.assertRaises(Refused):
                slot.admit(**call, purpose="main", body=body | changes)
        self.assertEqual(slot.attempts, 0)
        slot.stop()
        with self.assertRaises(Refused):
            slot.admit(**call, purpose="main", body=body)
