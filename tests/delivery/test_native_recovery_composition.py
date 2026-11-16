"""NEW composition-only synthetic contracts; no native source or old tests."""
from contextlib import redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "native_recovery_composition", ROOT / "tools/delivery/native_recovery_composition.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

ORCHESTRATOR = (b"class Orchestrator:\n"
                b"    def _management_context(self, issue):\n" + M.LABEL.NEEDLE + b"\n"
                b"        return machine_labels\n\n"
                b"    def get_state_snapshot(self):\n        return {}\n\n"
                b"class MultiOrchestrator:\n"
                b"    def get_state_snapshot(self):\n        return {}\n")
WEB = (b"def create_app(orchestrator):\n    app = FastAPI()\n"
       b"    @app.get(\"/api/v1/{issue_identifier}\")\n"
       b"    async def api_issue(issue_identifier):\n        return JSONResponse({})\n"
       b"    return app\n")


class NativeRecoveryComposition(unittest.TestCase):
    def setUp(self):
        # Test-local fixture admission only. Production primitives/pins are unedited.
        self.enterContext(patch.object(M.LABEL, "EXPECTED_SOURCE_SHA256", M.CUSTODY.digest(ORCHESTRATOR)))
        self.enterContext(patch.dict(M.CUSTODY.PINS, {
            "orchestrator.py": M.CUSTODY.digest(ORCHESTRATOR),
            "web.py": M.CUSTODY.digest(WEB)}))
        self.labels, _ = M.LABEL.render_native_source(ORCHESTRATOR)
        self.custody, _ = M.CUSTODY.prepare_native_sources(ORCHESTRATOR, WEB)
        self.enterContext(patch.dict(M.STANDALONE_PINS, {
            "label_orchestrator": M.CUSTODY.digest(self.labels),
            "custody_orchestrator": M.CUSTODY.digest(self.custody["orchestrator.py"]),
            "custody_web": M.CUSTODY.digest(self.custody["web.py"])}))

    def test_sibling_loading_never_emits_bytecode(self):
        with patch.object(importlib.machinery.SourceFileLoader, "set_data",
                          side_effect=AssertionError("No bytecode files")) as write:
            loaded = M.sibling("owner_label_context")
        self.assertEqual(loaded.EXPECTED_SOURCE_SHA256,
                         "7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea")
        write.assert_not_called()

    def test_both_strict_preparers_receive_only_the_same_originals(self):
        label_pin = M.LABEL.EXPECTED_SOURCE_SHA256
        custody_pins = dict(M.CUSTODY.PINS)
        with patch.object(M.LABEL, "render_native_source", wraps=M.LABEL.render_native_source) as labels:
            with patch.object(M.CUSTODY, "prepare_native_sources", wraps=M.CUSTODY.prepare_native_sources) as custody:
                M.compose_native_sources(ORCHESTRATOR, WEB)
        labels.assert_called_once_with(ORCHESTRATOR)
        custody.assert_called_once_with(ORCHESTRATOR, WEB)
        self.assertEqual(M.LABEL.EXPECTED_SOURCE_SHA256, label_pin)
        self.assertEqual(M.CUSTODY.PINS, custody_pins)

    def test_exact_union_has_both_separate_and_original_inverses(self):
        after, result = M.compose_native_sources(ORCHESTRATOR, WEB)
        combined = after["orchestrator.py"]
        self.assertEqual(combined.replace(M.LABEL.REPLACEMENT, M.LABEL.NEEDLE, 1),
                         self.custody["orchestrator.py"])
        without_custody = combined.replace(M.CUSTODY.ORCHESTRATOR_METHOD, b"", 1)
        without_custody = without_custody.replace(M.CUSTODY.MULTI_METHOD, b"", 1)
        self.assertEqual(without_custody, self.labels)
        self.assertEqual(without_custody.replace(M.LABEL.REPLACEMENT, M.LABEL.NEEDLE, 1), ORCHESTRATOR)
        self.assertEqual(after["web.py"], self.custody["web.py"])
        self.assertEqual(after["web.py"].replace(M.CUSTODY.WEB_ROUTE, b"", 1), WEB)
        self.assertTrue(all(result["proof"].values()))
        self.assertNotEqual(result["files"]["orchestrator.py"]["composed_sha256"],
                            result["files"]["orchestrator.py"]["label_only_sha256"])

    def test_receipt_keeps_all_effects_false_and_authority_unknown(self):
        _, result = M.compose_native_sources(ORCHESTRATOR, WEB)
        for name in ("native_installed", "runtime_adopted", "native_imported", "endpoint_invoked",
                     "current_custody_observed", "original_records_changed",
                     "ordinary_comments_neutralized", "input_hold_compatibility_verified",
                     "old_gate_compatibility_verified", "retry_preservation_qualified", "grants_authority"):
            self.assertIs(result[name], False)
        self.assertTrue(all(value is None for value in result["unknown"].values()))
        json.dumps(result, allow_nan=False)
        self.assertNotIn("_management_context", json.dumps(result))

    def test_drift_types_and_all_transformed_inputs_refuse(self):
        combined, _ = M.compose_native_sources(ORCHESTRATOR, WEB)
        for raw in (ORCHESTRATOR + b"# drift\n", self.labels,
                    self.custody["orchestrator.py"], combined["orchestrator.py"], "not bytes"):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(M.CompositionError):
                M.compose_native_sources(raw, WEB)
        with self.assertRaises(M.CompositionError):
            M.compose_native_sources(ORCHESTRATOR, WEB + b"# drift\n")

    def test_unqualified_standalone_hash_refuses(self):
        with patch.dict(M.STANDALONE_PINS, {"label_orchestrator": "0" * 64}):
            with self.assertRaises(M.CompositionError):
                M.compose_native_sources(ORCHESTRATOR, WEB)

    def test_unrelated_label_drift_refuses_even_if_selected_hash_matches(self):
        drift = self.labels + b"# unrelated\n"
        with patch.object(M.LABEL, "render_native_source", return_value=(drift, {})):
            with patch.dict(M.STANDALONE_PINS, {"label_orchestrator": M.CUSTODY.digest(drift)}):
                with self.assertRaises(M.CompositionError):
                    M.compose_native_sources(ORCHESTRATOR, WEB)

    def test_missing_custody_span_and_ambiguous_label_span_refuse(self):
        variants = [
            self.custody["orchestrator.py"].replace(M.CUSTODY.MULTI_METHOD, b"", 1),
            self.custody["orchestrator.py"] + M.LABEL.NEEDLE + b"\n",
        ]
        for raw in variants:
            selected = dict(self.custody, **{"orchestrator.py": raw})
            with patch.object(M.CUSTODY, "prepare_native_sources", return_value=(selected, {})):
                with patch.dict(M.STANDALONE_PINS, {"custody_orchestrator": M.CUSTODY.digest(raw)}):
                    with self.assertRaises(M.CompositionError):
                        M.compose_native_sources(ORCHESTRATOR, WEB)

    def test_composed_output_bound_refuses(self):
        # No large allocation; lower only this new composer bound in the fixture.
        with patch.object(M, "MAX_OUTPUT_BYTES", len(self.custody["orchestrator.py"])):
            with self.assertRaises(M.CompositionError):
                M.compose_native_sources(ORCHESTRATOR, WEB)

    def cli(self, extra=(), inputs=None):
        stream = io.StringIO()
        with patch.object(M.CUSTODY, "read_source", side_effect=inputs or [ORCHESTRATOR, WEB]) as read:
            with redirect_stdout(stream):
                result = M.main(["--orchestrator-source", "selected-orchestrator.py",
                                 "--web-source", "selected-web.py", *extra])
        self.assertEqual(read.call_args_list[0].args, (Path("selected-orchestrator.py"),))
        self.assertEqual(read.call_args_list[1].args, (Path("selected-web.py"),))
        return result, stream.getvalue()

    def test_cli_default_is_metadata_only_with_no_file_emission(self):
        with patch.object(M.LABEL, "emit_candidate", side_effect=AssertionError("No file writes")):
            code, output = self.cli()
        self.assertEqual(code, 0)
        self.assertIs(json.loads(output)["native_installed"], False)
        self.assertNotIn("class Orchestrator", output)

    def test_cli_patch_is_only_generic_source_diff(self):
        code, output = self.cli(["--patch"])
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("--- a/orchestrator.py"))
        self.assertIn("+++ b/web.py", output)
        self.assertIn("native-custody/", output)
        self.assertNotIn('"schema": "native-recovery-composition/1"', output)

    def test_cli_drift_refusal_does_not_echo_source(self):
        code, output = self.cli(inputs=[ORCHESTRATOR + b"# private marker", WEB])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output), {"status": "refused", "native_installed": False})
        self.assertNotIn("private marker", output)


if __name__ == "__main__":
    unittest.main()
