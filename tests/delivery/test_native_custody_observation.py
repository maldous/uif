"""NEW synthetic adapter contracts; no native process, records or endpoints."""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "native_custody_observation", ROOT / "tools/delivery/native_custody_observation.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

ORCHESTRATOR = b'''class Orchestrator:
    def __init__(self):
        self.project_name = "ANQR"
        self.retry_attempts = {}
        self.fields = (None, None, False, None, None, None, None)

    def _intake_snapshot_custody(self, issue_id):
        return self.fields

    def get_state_snapshot(self):
        return {"old": "state"}

class MultiOrchestrator:
    def __init__(self, orchestrator):
        self.orchestrators = {"ANQR": orchestrator}

    def get_state_snapshot(self):
        return {"old": "aggregate"}
'''
WEB = b'''def create_app(orchestrator):
    app = FastAPI()
    @app.get("/api/v1/state")
    async def api_state():
        return JSONResponse(orchestrator.get_state_snapshot())

    @app.get("/api/v1/{issue_identifier}")
    async def api_issue(issue_identifier):
        return JSONResponse({"old": issue_identifier})
    return app
'''


class FakeApp:
    def __init__(self):
        self.routes = {}

    def get(self, path):
        def register(function):
            self.routes[path] = function
            return function
        return register


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code


class Secret:
    def __str__(self):
        raise AssertionError("Private value stringification forbidden")

    def __repr__(self):
        raise AssertionError("Private value representation forbidden")


class NativeCustodyObservation(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(M.PINS, {
            "orchestrator.py": M.digest(ORCHESTRATOR), "web.py": M.digest(WEB)}))

    def prepared(self):
        return M.prepare_native_sources(ORCHESTRATOR, WEB)

    def objects(self):
        after, _ = self.prepared()
        namespace = {"datetime": datetime, "timezone": timezone, "os": os, "Any": object}
        # Execute ONLY fixed credential-free synthetic fixtures with the reviewed
        # inserted snippets, never installed native input selected by the CLI.
        exec(compile(after["orchestrator.py"], "<synthetic-native>", "exec"), namespace)
        orchestrator = namespace["Orchestrator"]()
        return orchestrator, namespace["MultiOrchestrator"](orchestrator)

    def app(self):
        _, multi = self.objects()
        after, _ = self.prepared()
        namespace = {"FastAPI": FakeApp, "JSONResponse": FakeResponse}
        exec(compile(after["web.py"], "<synthetic-web>", "exec"), namespace)
        return namespace["create_app"](multi)

    def test_exact_sources_and_existing_byte_inverse(self):
        after, receipt = self.prepared()
        restored = after["orchestrator.py"].replace(M.ORCHESTRATOR_METHOD, b"", 1)
        restored = restored.replace(M.MULTI_METHOD, b"", 1)
        self.assertEqual(restored, ORCHESTRATOR)
        self.assertEqual(after["web.py"].replace(M.WEB_ROUTE, b"", 1), WEB)
        self.assertEqual(receipt["files"]["web.py"]["after_sha256"], M.digest(after["web.py"]))

    def test_preparation_flags_do_not_claim_observation_or_authority(self):
        _, receipt = self.prepared()
        for name in ("native_installed", "runtime_adopted", "endpoint_invoked",
                     "current_custody_observed", "grants_authority",
                     "retry_preservation_qualified", "original_records_changed"):
            self.assertIs(receipt[name], False)

    def test_wrong_pin_changed_source_and_second_application_refuse(self):
        after, _ = self.prepared()
        for raw in (ORCHESTRATOR + b"# drift\n", after["orchestrator.py"], "not bytes"):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(M.PreparationError):
                M.prepare_native_sources(raw, WEB)
        with self.assertRaises(M.PreparationError):
            M.prepare_native_sources(ORCHESTRATOR, WEB + b"# drift\n")

    def test_ambiguous_class_anchor_refuses(self):
        raw = ORCHESTRATOR + ORCHESTRATOR
        with patch.dict(M.PINS, {"orchestrator.py": M.digest(raw)}):
            with self.assertRaises(M.PreparationError):
                M.prepare_native_sources(raw, WEB)

    def test_wrong_existing_get_anchor_refuses(self):
        raw = WEB.replace(b"/api/v1/{issue_identifier}", b"/api/v1/changed")
        with patch.dict(M.PINS, {"web.py": M.digest(raw)}):
            with self.assertRaises(M.PreparationError):
                M.prepare_native_sources(ORCHESTRATOR, raw)

    def test_existing_observation_method_refuses_even_in_synthetic_pin(self):
        raw = ORCHESTRATOR.replace(
            b'    def _intake_snapshot_custody',
            b'    def get_native_custody_observation(self, issue_id):\n        return None\n\n'
            b'    def _intake_snapshot_custody')
        with patch.dict(M.PINS, {"orchestrator.py": M.digest(raw)}):
            with self.assertRaises(M.PreparationError):
                M.prepare_native_sources(raw, WEB)

    def test_known_process_presence_is_json_safe_and_non_authorizing(self):
        orchestrator, multi = self.objects()
        hidden = Secret()
        orchestrator.fields = (hidden, hidden, True, "review", "investigate", 3, hidden)
        orchestrator.retry_attempts["issue-1"] = SimpleNamespace(
            attempt=49, native_context=hidden, native_claim=hidden)
        value = multi.get_native_custody_observation("ANQR", "issue-1")
        self.assertTrue(value["presence"]["claimed_set_membership"])
        self.assertTrue(value["presence"]["task_registered"])
        self.assertEqual(value["state"], {
            "gate": "review", "stage": "investigate", "run": 3, "retry_attempt": 49})
        self.assertTrue(all(item is False for item in value["authority"].values()))
        self.assertTrue(all(item is None for item in value["unknown"].values()))
        json.dumps(value, allow_nan=False)
        self.assertNotIn("native_context", value)
        self.assertNotIn("session_id", value)

    def test_observation_does_not_mutate_existing_native_fields(self):
        orchestrator, multi = self.objects()
        fields = orchestrator.fields
        retry_map = orchestrator.retry_attempts
        multi.get_native_custody_observation("ANQR", "issue-1")
        self.assertIs(orchestrator.fields, fields)
        self.assertIs(orchestrator.retry_attempts, retry_map)
        self.assertEqual(retry_map, {})

    def test_unknown_issue_does_not_become_authority_or_verified_absence(self):
        _, multi = self.objects()
        value = multi.get_native_custody_observation("ANQR", "unknown-issue")
        self.assertTrue(all(item is False for item in value["presence"].values()))
        self.assertIs(value["authority"]["issue_exists_verified"], False)
        self.assertIs(value["authority"]["external_owner_verified"], False)
        self.assertIsNone(value["unknown"]["current_issuer"])

    def test_unrecognized_and_oversized_metadata_stays_unknown(self):
        orchestrator, _ = self.objects()
        orchestrator.fields = (None, None, 1, Secret(), "s" * 129, True, None)
        orchestrator.retry_attempts["issue-1"] = SimpleNamespace(attempt=2 ** 80)
        value = orchestrator.get_native_custody_observation("issue-1")
        self.assertIsNone(value["presence"]["claimed_set_membership"])
        self.assertEqual(value["state"], {
            "gate": None, "stage": None, "run": None, "retry_attempt": None})
        json.dumps(value, allow_nan=False)

    def test_selector_bounds_and_no_coercion(self):
        orchestrator, multi = self.objects()
        for issue in ("", "x" * 129, "../private", "é", None, 3):
            with self.subTest(kind=type(issue).__name__), self.assertRaises(ValueError):
                orchestrator.get_native_custody_observation(issue)
        for project in ("", "x" * 129, None):
            with self.subTest(kind=type(project).__name__), self.assertRaises(ValueError):
                multi.get_native_custody_observation(project, "issue-1")
        self.assertIsNone(multi.get_native_custody_observation("missing", "issue-1"))

    def test_each_result_has_fresh_non_authorizing_containers(self):
        orchestrator, _ = self.objects()
        first = orchestrator.get_native_custody_observation("issue-1")
        first["authority"]["grants_publication"] = True
        first["unknown"]["current_issuer"] = "caller mutation"
        second = orchestrator.get_native_custody_observation("issue-1")
        self.assertIs(second["authority"]["grants_publication"], False)
        self.assertIsNone(second["unknown"]["current_issuer"])

    def test_web_get_retains_existing_routes_and_unknown_project_response(self):
        app = self.app()
        route = "/api/v1/projects/{project_name}/native-custody/{issue_id}"
        self.assertEqual(set(app.routes), {"/api/v1/state", "/api/v1/{issue_identifier}", route})
        old = asyncio.run(app.routes["/api/v1/state"]())
        self.assertEqual(old.data, {"old": "aggregate"})
        old_issue = asyncio.run(app.routes["/api/v1/{issue_identifier}"]("old"))
        self.assertEqual(old_issue.data, {"old": "old"})
        missing = asyncio.run(app.routes[route]("missing", "issue-1"))
        self.assertEqual(missing.status_code, 404)

    def test_web_get_selector_failure_and_unverified_issue_observation(self):
        app = self.app()
        route = app.routes["/api/v1/projects/{project_name}/native-custody/{issue_id}"]
        invalid = asyncio.run(route("ANQR", "../private"))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.data["error"]["code"], "invalid_observation_selector")
        value = asyncio.run(route("ANQR", "unknown-issue"))
        self.assertEqual(value.status_code, 200)
        self.assertIs(value.data["authority"]["issue_exists_verified"], False)
        self.assertTrue(all(item is False for item in value.data["authority"].values()))

    def test_regular_bounded_nofollow_nonblocking_source_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.py"
            path.write_bytes(ORCHESTRATOR)
            self.assertEqual(M.read_source(path), ORCHESTRATOR)
            alias = path.with_name("alias.py")
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                M.read_source(alias)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(M.PreparationError):
                M.read_source(path)
            path.unlink()
            path.write_bytes(b"x" * (M.MAX_SOURCE_BYTES + 1))
            with self.assertRaises(M.PreparationError):
                M.read_source(path)

    def test_cli_prepares_only_metadata_and_never_writes_selected_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orch = root / "orchestrator.py"
            web = root / "web.py"
            orch.write_bytes(ORCHESTRATOR)
            web.write_bytes(WEB)
            stream = io.StringIO()
            with redirect_stdout(stream):
                result = M.main(["--orchestrator-source", str(orch), "--web-source", str(web)])
            self.assertEqual(result, 0)
            self.assertIs(json.loads(stream.getvalue())["native_installed"], False)
            self.assertEqual(orch.read_bytes(), ORCHESTRATOR)
            self.assertEqual(web.read_bytes(), WEB)
            self.assertEqual(set(item.name for item in root.iterdir()), {"orchestrator.py", "web.py"})

    def test_cli_wrong_source_refuses_without_source_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.py"
            path.write_bytes(b"secret or wrong source")
            stream = io.StringIO()
            with redirect_stdout(stream):
                result = M.main(["--orchestrator-source", str(path), "--web-source", str(path)])
            self.assertEqual(result, 2)
            self.assertNotIn("secret", stream.getvalue())
            self.assertIs(json.loads(stream.getvalue())["native_installed"], False)


if __name__ == "__main__":
    unittest.main()
