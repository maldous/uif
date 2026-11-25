from dataclasses import replace
import json
import unittest

from uif.codex_metadata_bridge import (
    BoundMetadataReplies, MAX_REPLY_BYTES, observe_signed_in_metadata,
)


class Fake:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def read_bound(self, **options):
        self.calls.append(options)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def data(obj):
    return json.dumps(obj).encode()


class MetadataBridgeTests(unittest.TestCase):
    def setUp(self):
        window = dict(usedPercent=88, windowDurationMins=300, resetsAt=2000)
        self.limits = dict(rateLimitsByLimitId={"codex": {
            "limitId": "codex", "primary": window, "secondary": window,
        }})
        self.result = BoundMetadataReplies(
            "account", "session", 1000,
            data({"account": {"type": "chatgpt", "email": "fixture@example.test"},
                  "requiresOpenaiAuth": True}),
            data(self.limits),
        )

    def observe(self, port, clock=lambda: 1000):
        return observe_signed_in_metadata(
            port, account_id="account", session_id="session", limit_id="codex", clock=clock,
        )

    def test_fixed_reads_no_refresh_no_entitlement(self):
        port = Fake(self.result)
        result = self.observe(port)
        self.assertEqual(port.calls, [dict(
            methods=("account/read", "account/rateLimits/read"), refresh_token=False,
            maximum_reply_bytes=MAX_REPLY_BYTES, timeout_seconds=2.0,
        )])
        self.assertEqual(tuple(w.used_percent for w in result.windows), (88, 88))
        self.assertIsNone(result.ordinary_usage_allowed)
        self.assertIsNone(result.spend_control_reached)
        self.assertNotIn("fixture@", repr(result))

    def test_unavailable_invalid_stale_unbound_or_non_chatgpt(self):
        wire = data({"account": {"type": "apiKey"}, "requiresOpenaiAuth": True})
        for value in (
            None, TimeoutError(), replace(self.result, session_id="other"),
            replace(self.result, account_id="other"), replace(self.result, observed_at=999),
            replace(self.result, limits_result=data(dict(self.limits, accountId="other"))),
            replace(self.result, limits_result=b" " * (MAX_REPLY_BYTES + 1)),
            replace(self.result, limits_result=b'{"accountId":"other","accountId":"account"}'),
            replace(self.result, account_result=wire),
        ):
            self.assertIsNone(self.observe(Fake(value)))
        self.assertIsNone(self.observe(None))
        self.assertIsNone(self.observe(Fake(self.result), clock=iter((1000, 1003)).__next__))
