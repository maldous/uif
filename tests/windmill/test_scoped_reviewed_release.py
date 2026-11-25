import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / "config/windmill/source/scoped_reviewed_release.py"
SPEC = importlib.util.spec_from_file_location("scoped_reviewed_release", PATH)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

ONE = "12345678-1234-1234-1234-123456789abc"
TWO = "12345678-1234-1234-1234-123456789def"


class ScopedReleaseTest(unittest.TestCase):
    def setUp(self):
        self.request = dict(zip(release.FIELDS, (letter * 64 for letter in "abcd")))

    def test_route_context_not_caller_and_unbound_refuses(self):
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": ONE}):
            with self.assertRaisesRegex(ValueError, "FIELDS"):
                release.main(dict(self.request, job_id=TWO))
            with self.assertRaisesRegex(RuntimeError, "CHANNEL_UNBOUND"):
                release.main(self.request)

    def test_exact_original_not_run_id_keys_recovery(self):
        calls, journal = [], set()
        def original(operation, job, _pins):
            calls.append((operation, job))
            if operation in journal:
                return "original"
            journal.add(operation)
            return "first"
        review = release.exact_release(self.request)
        action = release.bind_action(release.bind_foo_handler(review, original))
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": ONE}):
            self.assertEqual("first", action(self.request))
            with self.assertRaisesRegex(PermissionError, "UNREVIEWED"):
                action(dict(self.request, package_sha256="e"*64))
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": TWO}):
            self.assertEqual("original", action(self.request))
        self.assertEqual([(self.request["operation_id"], ONE),
                          (self.request["operation_id"], TWO)], calls)

    def test_wrong_workspace_and_invalid_generated_id_never_dispatch(self):
        action = release.bind_action(lambda *_: self.fail("unexpected dispatch"))
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "offmenu", "WM_JOB_ID": ONE}):
            with self.assertRaisesRegex(PermissionError, "WORKSPACE"):
                action(self.request)
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": "  "}):
            with self.assertRaisesRegex(ValueError, "RUN_ID"):
                action(self.request)
