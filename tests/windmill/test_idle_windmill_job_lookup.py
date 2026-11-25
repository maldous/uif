from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import idle_platform_approval as approval
    import idle_windmill_job_lookup as lookup


class WindmillLookupTest(unittest.TestCase):
    def setUp(self):
        operation = approval.IdleOperation(*approval.SCOPE, "start", *[c*64 for c in "abcd"])
        self.policy = approval.ApprovedRunnable("u/admin/stokowski_guarded_idle", "e"*16,
                                                "g/idle", "admin", operation)
        self.id = "12345678-1234-1234-1234-123456789abc"
        self.raw = dict(id=self.id, workspace_id="foo", type="QueuedJob", job_kind="script",
                        running=True, canceled=False, args={}, script_path=self.policy.path,
                        script_hash=self.policy.script_hash, permissioned_as="g/idle",
                        created_by="admin", started_at="2026-11-25T00:00:00Z",
                        trigger_kind="ui", is_flow_step=False, is_retry=False)
        self.client = Mock()
        self.client.get_job.return_value = self.raw

    def qualify(self):
        with patch.dict(lookup.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": self.id}):
            return approval.qualify(self.policy, self.policy.operation, current_job=
                lookup.bind_server_lookup(self.client, self.policy.operation), now=1795564800)

    def test_exact_server_record_drives_pure_approval_end_to_end(self):
        self.assertEqual(self.id, self.qualify().job_id)
        self.client.get_job.assert_called_once_with(self.id)

    def test_preview_arguments_other_origin_and_shared_principal_refused(self):
        for key, value in (("job_kind", "preview"), ("args", {"command": "fixture"}),
                           ("workspace_id", "offmenu"), ("created_by", "other"),
                           ("type", "CompletedJob"), ("canceled", True), ("parent_job", self.id)):
            with self.subTest(key=key), patch.dict(self.raw, {key: value}):
                with self.assertRaises(PermissionError):
                    self.qualify()

    def test_launch_provenance_has_to_be_explicit_and_unambiguous(self):
        for key, value in (("trigger_kind", None), ("trigger_kind", "api"),
                           ("trigger_kind", "schedule"), ("trigger_kind", "webhook"),
                           ("trigger_kind", "http"), ("trigger_kind", "app"),
                           ("trigger_kind", "future"), ("trigger_path", "f/other/route"),
                           ("trigger", "f/other/route"), ("schedule_path", "f/other/schedule"),
                           ("is_retry", True), ("is_flow_step", True)):
            with self.subTest(key=key, value=value), patch.dict(self.raw, {key: value}):
                with self.assertRaisesRegex(PermissionError, "UNQUALIFIED"):
                    self.qualify()
        for key in ("trigger_kind", "is_retry", "is_flow_step"):
            with self.subTest(missing=key), patch.dict(self.raw):
                del self.raw[key]
                with self.assertRaisesRegex(PermissionError, "UNQUALIFIED"):
                    self.qualify()
