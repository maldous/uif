import json
import unittest

from uif.codex_quota_observation import parse_account_rate_limits


class CodexAppServerQuotaObservationTests(unittest.TestCase):
    def setUp(self):
        self.reply = {
            "accountId": "synthetic-account",
            "ordinaryUsageAllowed": True,
            "rateLimits": {"limitId": "compatibility-only"},
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex", "spendControlReached": False,
                    "primary": {"usedPercent": 42, "windowDurationMins": 60,
                                "resetsAt": 2000000000},
                    "secondary": {"usedPercent": 5, "windowDurationMins": 1440,
                                  "resetsAt": 2000100000},
                },
            },
        }

    def parse(self, raw=None, **kwargs):
        return parse_account_rate_limits(
            raw if raw is not None else json.dumps(self.reply).encode(),
            **(dict(account="synthetic-account", limit_id="codex",
                    now_epoch_seconds=1999990000) | kwargs),
        )

    def test_identified_windows_not_compatibility_mirror(self):
        obs = self.parse()
        self.assertEqual([x.used_percent for x in obs.windows], [42, 5])
        self.assertEqual([x.name for x in obs.windows], ["primary", "secondary"])
        self.assertIs(obs.ordinary_usage_allowed, True)
        self.assertIs(obs.spend_control_reached, False)
        self.assertIsNone(self.parse(limit_id="compatibility-only"))

    def test_account_absent_or_mismatch_refuses(self):
        self.assertIsNone(self.parse(account="another"))
        self.reply.pop("accountId")
        self.assertIsNone(self.parse())

    def test_missing_or_expired_secondary_refuses(self):
        self.assertIsNone(self.parse(now_epoch_seconds=2000000000))
        self.reply["rateLimitsByLimitId"]["codex"]["secondary"] = None
        self.assertIsNone(self.parse())

    def test_null_is_not_inferred_allowed(self):
        self.reply["ordinaryUsageAllowed"] = None
        self.assertIsNone(self.parse().ordinary_usage_allowed)

    def test_duplicate_structural_keys_and_false_integers_refuse(self):
        raw = json.dumps(self.reply).replace('"usedPercent": 42', '"usedPercent": 42, "usedPercent": 0')
        self.assertIsNone(self.parse(raw.encode()))
        self.reply["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = True
        self.assertIsNone(self.parse())
