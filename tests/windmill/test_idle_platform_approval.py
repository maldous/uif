from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import idle_platform_approval as binding


class IdlePlatformApprovalTest(unittest.TestCase):
    def setUp(self):
        self.operation = binding.IdleOperation(*binding.SCOPE, "start", "a"*64, "b"*64,
                                                "c"*64, "d"*64)
        self.approval = binding.ApprovedRunnable("u/admin/stokowski_guarded_idle",
                                                 "e"*16, "u/admin", "authorized-actor", self.operation)
        self.job = binding.CurrentJob("12345678-1234-1234-1234-123456789abc",
            self.approval.path, self.approval.script_hash, self.approval.effective_principal,
            self.approval.initiating_actor, True, "script", self.operation, 100, 160)

    def qualify(self, lookup, op=None, now=120):
        return binding.qualify(self.approval, op or self.operation, current_job=lookup, now=now)

    def test_exact_server_job_is_sufficient_without_second_signature(self):
        lookup = Mock(return_value=self.job)
        self.assertEqual(binding.PlatformApproval(self.job.job_id, self.operation, 160),
                         self.qualify(lookup))
        lookup.assert_called_once_with()

    def test_unavailable_and_projected_wire_metadata_refuse(self):
        for look in (None, lambda: dict(vars(self.job)), lambda: None):
            with self.assertRaises(PermissionError):
                self.qualify(look)

    def test_wrong_job_role_hash_and_lifecycle_refuse(self):
        for key, value in (("path", "u/admin/foo_root_jobs"), ("script_hash", "f"*16),
                           ("effective_principal", "u/other"), ("initiating_actor", "wrong-actor"),
                           ("running", False), ("job_kind", "preview"),
                           ("job_id", "0"*36)):
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.qualify(lambda: replace(self.job, **{key: value}))

    def test_wrong_original_phase_scope_and_pins_do_not_request_lookup(self):
        for key, value in (("operation_id", "0"*64), ("phase", "apply"),
                           ("unit", "offmenu-stokowski-current.service"),
                           ("bundle_sha256", "0"*64), ("preflight_sha256", "0"*64)):
            lookup = Mock()
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.qualify(lookup, replace(self.operation, **{key: value}))
            lookup.assert_not_called()

    def test_freshness_is_bounded_and_fail_closed(self):
        for changed, now in (({}, 99), ({}, 160), ({"expires_at": 1000}, 120)):
            with self.subTest(changed=changed, now=now), self.assertRaises(PermissionError):
                self.qualify(lambda: replace(self.job, **changed), now=now)
