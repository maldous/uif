"""NEW synthetic-only fixed-root journal contracts. Never read the real store."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "observe_offmenu_integration",
    Path(__file__).resolve().parents[2] / "tools/delivery/observe_offmenu_integration.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ExistingIntegrationObservation(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "integration"
        self.root.mkdir()
        (self.root / "repository-owner.lock").touch()
        self.transaction = self.root / "pr254-fixture"
        self.transaction.mkdir()
        self.binding = {"version": 4, "repository": "maldous/offmenu",
                        "owner": "offmenu-policy-integration-worker", "head": "1" * 40,
                        "base": "2" * 40, "tree": "3" * 40,
                        "private_payload": "DO-NOT-EMIT-SOURCE-SESSION-INTENT"}
        self.claim = {"binding_sha256": M.fingerprint(self.binding),
                      "directory": str(self.transaction), "owner": self.binding["owner"]}
        self.state = {"binding_sha256": self.claim["binding_sha256"],
                      "status": "reconciliation-required", "merge_effect": "unknown",
                      "publication": {"effect": "pending", "intent": "DO-NOT-EMIT"},
                      "release_effect": None, "tag_effect": "verified",
                      "source_session": "DO-NOT-EMIT"}
        self.write("binding.json", self.binding)
        self.write("state.json", self.state)
        self.pointer()

    def write(self, name, value):
        (self.transaction / name).write_text(json.dumps(value))

    def pointer(self, name="active-owner.json"):
        (self.root / name).write_text(json.dumps(self.claim))

    def read(self):
        return M._observe_root(str(self.root))

    def refused(self):
        result = self.read()
        self.assertEqual(result["outcome"], "refused")
        self.assertIsNone(result["metadata"])
        self.assertIs(result["grants_authority"], False)
        self.assertIs(result["effects_authorized"], False)
        return result

    def test_existing_records_yield_only_bounded_non_authorizing_metadata(self):
        result = self.read()
        self.assertEqual(result["outcome"], "observed")
        self.assertTrue(all(result["record_presence"].values()))
        metadata = result["metadata"]
        self.assertEqual(metadata["binding_sha256"], self.claim["binding_sha256"])
        self.assertEqual(metadata["selected_directory"], "pr254-fixture")
        self.assertEqual(metadata["recorded_scalars"], {
            "status": "reconciliation-required", "merge_effect": "unknown",
            "publication_effect": "pending", "release_effect": None, "tag_effect": "verified"})
        raw = json.dumps(result, allow_nan=False)
        for secret in ("DO-NOT-EMIT", self.binding["head"], "claim_available", "intent"):
            self.assertNotIn(secret, raw)
        self.assertLess(len(raw.encode()), M.MAX_OUTPUT_BYTES)
        self.assertTrue(all(value is None for value in result["authority"].values()))
        for key in ("grants_authority", "effects_authorized", "effect_state_verified",
                    "native_custody_verified"):
            self.assertIs(result[key], False)

    def test_legacy_pointer_used_only_when_active_is_missing(self):
        (self.root / "active-owner.json").unlink()
        self.pointer("repository-claim.json")
        self.assertEqual(self.read()["metadata"]["pointer_name"], "repository-claim.json")
        (self.root / "active-owner.json").write_text("broken")
        self.refused()

    def test_missing_root_lock_pointer_or_claimed_state_never_creates_records(self):
        missing = self.root / "absent"
        result = M._observe_root(str(missing))
        self.assertEqual(result["outcome"], "refused")
        self.assertFalse(missing.exists())
        for path in (self.root / "repository-owner.lock", self.root / "active-owner.json",
                     self.transaction / "state.json", self.transaction / "binding.json"):
            original = path.read_bytes()
            path.unlink()
            self.refused()
            self.assertFalse(path.exists())
            path.write_bytes(original)

    def test_exact_pointer_and_binding_and_state_identity_required(self):
        for value in ({**self.claim, "extra": "not-allowed"},
                      {**self.claim, "owner": "other-owner"},
                      {**self.claim, "binding_sha256": "f" * 64}):
            with self.subTest(pointer=value):
                (self.root / "active-owner.json").write_text(json.dumps(value))
                self.refused()
        self.pointer()
        self.write("state.json", {**self.state, "binding_sha256": "f" * 64})
        self.refused()
        self.write("state.json", {"status": "prepared"})
        self.refused()

    def test_partial_source_state_and_wrong_repository_are_refused(self):
        for binding in ({**self.binding, "repository": "other/repository"},
                        {**self.binding, "head": None}, {**self.binding, "version": True}):
            with self.subTest(binding=binding):
                self.write("binding.json", binding)
                self.claim["binding_sha256"] = M.fingerprint(binding)
                self.pointer()
                self.refused()
        self.write("binding.json", self.binding)
        self.claim["binding_sha256"] = M.fingerprint(self.binding)
        self.pointer()
        for state in ({"binding_sha256": self.claim["binding_sha256"]},
                      {**self.state, "publication": []}, {**self.state, "merge_effect": {}}):
            with self.subTest(state=state):
                self.write("state.json", state)
                self.refused()

    def test_traversal_outside_root_and_root_itself_are_refused(self):
        for directory in (str(self.root), str(self.root.parent / "elsewhere"),
                          str(self.root) + "/../pr254-fixture", str(self.root) + "//pr254-fixture"):
            with self.subTest(directory=directory):
                self.claim["directory"] = directory
                self.pointer()
                self.refused()

    def test_symlink_pointer_directory_binding_state_and_root_are_refused(self):
        for path in (self.root / "active-owner.json", self.transaction / "binding.json",
                     self.transaction / "state.json"):
            original = path.read_bytes()
            target = path.with_suffix(".saved")
            path.rename(target)
            path.symlink_to(target)
            self.refused()
            path.unlink()
            target.rename(path)
            self.assertEqual(path.read_bytes(), original)
        link = self.root / "linked"
        link.symlink_to(self.transaction, target_is_directory=True)
        self.claim["directory"] = str(link)
        self.pointer()
        self.refused()
        root_link = Path(self.temp) / "root-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(M._observe_root(str(root_link))["outcome"], "refused")

    def test_fifo_files_refuse_with_nonblocking_open_not_a_hanging_read(self):
        actual_open = os.open
        def checked_open(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_NONBLOCK)
            return actual_open(path, flags, *args, **kwargs)
        for path in (self.transaction / "state.json", self.root / "repository-owner.lock"):
            original = path.read_bytes()
            path.unlink()
            os.mkfifo(path)
            with patch.object(M.os, "open", side_effect=checked_open):
                self.refused()
            path.unlink()
            path.write_bytes(original)

    def test_duplicate_nonfinite_and_malformed_records_refuse(self):
        for raw in ('{"status":"prepared","status":"accepted"}',
                    '{"number":NaN}', '{"number":1e999}', '[]', '{', '\udcff'):
            with self.subTest(raw=repr(raw)):
                (self.transaction / "state.json").write_bytes(raw.encode("utf-8", errors="surrogatepass"))
                self.refused()

    def test_record_structure_and_identity_bounds(self):
        path = self.transaction / "state.json"
        for raw in (b" " * (M.MAX_RECORD_BYTES + 1), b'{"nested":' + b'[' * 40 + b'0' + b']' * 40 + b'}',
                    json.dumps({"items": [0] * M.MAX_NODES}).encode()):
            path.write_bytes(raw)
            self.refused()
        self.write("state.json", self.state)
        self.claim["owner"] = "x" * 129
        self.pointer()
        self.refused()

    def test_existing_exclusive_lock_refuses_without_new_lock(self):
        with (self.root / "repository-owner.lock").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.refused()["reason"], "exclusive-lock-held")
        self.assertEqual(self.read()["outcome"], "observed")

    def test_every_open_is_readonly_and_fixture_bytes_and_names_are_unchanged(self):
        def snapshot():
            return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in self.root.rglob("*") if path.is_file()}
        before = snapshot()
        actual_open = os.open
        def readonly_open(path, flags, *args, **kwargs):
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return actual_open(path, flags, *args, **kwargs)
        with patch.object(M.os, "open", side_effect=readonly_open):
            self.assertEqual(self.read()["outcome"], "observed")
        self.assertEqual(snapshot(), before)

    def test_unicode_canonical_recipe_and_changed_pointer_are_not_reinterpreted(self):
        self.binding["private_payload"] = "Unicode \u03bb"
        self.write("binding.json", self.binding)
        expected = __import__("hashlib").sha256(json.dumps(
            self.binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.claim["binding_sha256"] = expected
        self.state["binding_sha256"] = expected
        self.write("state.json", self.state)
        self.pointer()
        self.assertEqual(self.read()["metadata"]["binding_sha256"], expected)
        original_record = M._record
        reads = []
        def changed(*args):
            value, digest = original_record(*args)
            if args[1] == "active-owner.json":
                reads.append(True)
                if len(reads) == 2:
                    return value, "f" * 64
            return value, digest
        with patch.object(M, "_record", side_effect=changed):
            self.assertEqual(self.refused()["reason"], "pointer-changed-during-read")

    def test_main_has_no_runtime_root_config_or_command_selector(self):
        with patch.object(M, "observe") as observe:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                M.main(["--root", str(self.root)])
            observe.assert_not_called()
            observe.return_value = {"outcome": "refused"}
            with redirect_stdout(io.StringIO()):
                self.assertEqual(M.main([]), 2)
            observe.assert_called_once_with()
        self.assertEqual(M.ROOT, "/home/user/src/offmenu/.delivery/integration")


if __name__ == "__main__":
    unittest.main()
