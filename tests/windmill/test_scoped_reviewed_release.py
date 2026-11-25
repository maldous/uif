import importlib.util
import json
from pathlib import Path
from functools import partial
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / "config/windmill/source/scoped_reviewed_release.py"
SPEC = importlib.util.spec_from_file_location("scoped_reviewed_release", PATH)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

ROOT = PATH.parents[3]
CANONICAL = ROOT / "docs/agents/environment-profiles.json"
POLICY = ROOT / "config/windmill/release-projects.json"
REGISTRY = release.build_registry(json.loads(CANONICAL.read_bytes()), json.loads(POLICY.read_bytes()))
ONE = "12345678-1234-1234-1234-123456789abc"
TWO = "12345678-1234-1234-1234-123456789def"


class ScopedReleaseTest(unittest.TestCase):
    def setUp(self):
        self.pins = dict(zip(release.FIELDS, (letter * 64 for letter in "abcd")))
        self.request = dict(project="offmenu", repository="maldous/offmenu", **self.pins)
        self.bind_action = partial(release.bind_project_action, REGISTRY)

    def test_route_context_not_caller_and_unbound_refuses(self):
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": ONE}):
            with self.assertRaisesRegex(ValueError, "FIELDS"):
                self.bind_action(None)(dict(self.request, job_id=TWO))
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
        review = release.exact_release(self.pins)
        handler = release.bind_foo_handler(review, original)
        receiver = release.bind_project_receiver(REGISTRY, {("offmenu", "maldous/offmenu", release.PROFILE): handler})
        action = self.bind_action(receiver)
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": ONE}):
            self.assertEqual("first", action(self.request))
            with self.assertRaisesRegex(PermissionError, "UNREVIEWED"):
                action(dict(self.request, package_sha256="e"*64))
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": TWO}):
            self.assertEqual("original", action(self.request))
        self.assertEqual([(self.request["operation_id"], ONE),
                          (self.request["operation_id"], TWO)], calls)

    def test_wrong_workspace_and_invalid_generated_id_never_dispatch(self):
        action = self.bind_action(lambda *_: self.fail("unexpected dispatch"))
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "offmenu", "WM_JOB_ID": ONE}):
            with self.assertRaisesRegex(PermissionError, "WORKSPACE"):
                action(self.request)
        with patch.dict(release.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": "  "}):
            with self.assertRaisesRegex(ValueError, "RUN_ID"):
                action(self.request)

    def test_canonical_six_roles_only_offmenu_eligible(self):
        self.assertEqual({"uif", "offmenu", "anqr", "circularquay", "cix", "cloud"},
                         {project for project, _ in REGISTRY})
        self.assertEqual({"offmenu"}, {route.project for route in REGISTRY.values()
                                             if route.state == "eligible"})
        with self.assertRaisesRegex(ValueError, "SURFACE"):
            release.resolve(REGISTRY, "cix", "")
        self.assertEqual("develop", release.resolve(REGISTRY, "cix", "cix-project/cix").baseline)
        self.assertEqual("develop", release.resolve(REGISTRY, "cix", "cix-project/cix-operations").baseline)
        self.assertEqual("feature/initial-implementation",
                         release.resolve(REGISTRY, "circularquay", "maldous/CircularQuay").baseline)
        self.assertEqual("main", release.resolve(REGISTRY, "cloud", "maldous/aldous-info-platform").baseline)

    def test_held_and_caller_relabelled_sources_never_dispatch(self):
        receiver = release.bind_project_receiver(REGISTRY, {})
        with self.assertRaisesRegex(PermissionError, "NOT_ELIGIBLE"):
            receiver(release.PROFILE, ONE, dict(self.request, project="cix", repository="cix-project/cix"))
        with self.assertRaisesRegex(ValueError, "SURFACE"):
            receiver(release.PROFILE, ONE, dict(self.request, repository="maldous/uif"))
        with self.assertRaisesRegex(ValueError, "PROFILE"):
            receiver("different", ONE, self.request)
