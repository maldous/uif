"""Owned synthetic files and fake root metadata, never tools or host provisioning."""

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/windmill/stage_toolchain.py"
SPEC = importlib.util.spec_from_file_location("uif_stage_toolchain", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
BUNDLE = ROOT / "config/windmill/project-checks"
SYNTHETIC = b"OWNED_SYNTHETIC_ARCHIVE_NOT_EXECUTED"


def values(source):
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "ENGINE":
                result[name] = ast.literal_eval(node.value)
            elif name == "CATALOG":
                result[name] = json.loads(ast.literal_eval(node.value.args[0]))
    return result


def root_metadata(info):
    # Only the root-owner field is fabricated. Descriptor/file identities,
    # modes, sizes, bytes and real exclusive link operations stay observable.
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    return SimpleNamespace(**{name: getattr(info, name) for name in fields}, st_uid=0)


class CustodyContracts(unittest.TestCase):
    def test_baseline_is_exact_approved_source_not_an_import_or_restore_claim(self):
        metadata = json.loads((BUNDLE / "custody.json").read_bytes())
        data = (BUNDLE / "source/project_checks.py").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), metadata["source"]["sha256"])
        self.assertEqual(len(data), metadata["source"]["bytes"])
        self.assertFalse(metadata["restore_qualified"])
        self.assertIsNone(metadata["source"]["resolved_dependency_lock"])
        self.assertIsNone(metadata["source"]["deployed_schema_pin"])
        self.assertFalse(metadata["deployment_authorized_by_clone"])

    def test_desired_source_reverses_only_fixed_archive_path_and_engine_provenance(self):
        record = json.loads((BUNDLE / "migration.json").read_bytes())
        baseline = (BUNDLE / record["baseline_source"]).read_text()
        desired = (BUNDLE / record["desired_source"]).read_text()
        self.assertEqual(hashlib.sha256(desired.encode()).hexdigest(), record["desired_sha256"])
        restored = desired
        for change in record["changes"]:
            self.assertEqual(restored.count(change["to"]), 1)
            restored = restored.replace(change["to"], change["from"], 1)
        self.assertEqual(restored, baseline)
        old, new = values(baseline), values(desired)
        wanted = copy.deepcopy(old["CATALOG"])
        wanted["circularquay_feature"]["targets"]["cq_source_sdk_join"]["base_engine_sha256"] = record["desired_engine_sha256"]
        self.assertEqual(new["CATALOG"], wanted)
        self.assertEqual(hashlib.sha256(new["ENGINE"].encode()).hexdigest(), record["desired_engine_sha256"])
        self.assertEqual(new["ENGINE"].replace(record["changes"][0]["to"], record["changes"][0]["from"], 1), old["ENGINE"])

    def test_toolchain_manifest_matches_fixed_utility_without_download_or_deletion(self):
        record = json.loads((BUNDLE / "toolchains.json").read_bytes())
        self.assertEqual(record["go"]["sha256"], P.ARCHIVE_SHA256)
        self.assertEqual(record["go"]["maximum_bytes"], P.MAX_ARCHIVE_BYTES)
        self.assertEqual(record["go"]["managed_archive_location"], str(P.DIRECTORY / P.ARCHIVE_NAME))
        self.assertEqual(record["go"]["independently_authenticated_acquisition_url"], "https://go.dev/dl/" + P.ARCHIVE_NAME)
        self.assertEqual(len(P.ARCHIVE_SHA256), 64)
        self.assertEqual(int(P.ARCHIVE_SHA256, 16).bit_length() <= 256, True)
        original = values((BUNDLE / "source/project_checks.py").read_text())["ENGINE"]
        self.assertEqual(original.count(P.ARCHIVE_SHA256), 1)
        self.assertLess(record["go"]["vendor_declared_bytes"], P.MAX_ARCHIVE_BYTES)
        self.assertFalse(any(record["staging_effects"].values()))


class StagingContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "input-archive"
        self.source.write_bytes(SYNTHETIC)
        self.source.chmod(0o600)
        self.destination = self.root / "managed"
        self.destination.mkdir(mode=0o700)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(P, "ARCHIVE_SHA256", hashlib.sha256(SYNTHETIC).hexdigest()).start()
        mock.patch.object(P.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.geteuid())).start()

    def root_context(self):
        stack = contextlib.ExitStack()
        actual_fstat, actual_stat = os.fstat, os.stat
        stack.enter_context(mock.patch.object(P.os, "fstat", side_effect=lambda *a, **kw: root_metadata(actual_fstat(*a, **kw))))
        stack.enter_context(mock.patch.object(P.os, "stat", side_effect=lambda *a, **kw: root_metadata(actual_stat(*a, **kw))))
        stack.enter_context(mock.patch.object(P.os, "geteuid", return_value=0))
        stack.enter_context(mock.patch.object(P, "open_directory", side_effect=lambda: os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY)))
        return stack

    def test_held_source_hash_and_size_are_required_before_any_publication(self):
        self.assertEqual(P.read_archive(self.source), SYNTHETIC)
        with mock.patch.object(P, "ARCHIVE_SHA256", "0" * 64), self.assertRaises(ValueError):
            P.read_archive(self.source)
        with mock.patch.object(P, "MAX_ARCHIVE_BYTES", len(SYNTHETIC) - 1), self.assertRaises(ValueError):
            P.read_archive(self.source)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_source_aliases_and_unprotected_source_refuse(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            P.read_archive(alias)
        alias.unlink()
        os.link(self.source, alias)
        with self.assertRaises(ValueError):
            P.read_archive(self.source)
        alias.unlink()
        self.source.chmod(0o622)
        with self.assertRaises(ValueError):
            P.read_archive(self.source)

    def test_source_descriptor_change_is_not_accepted_as_authenticated_bytes(self):
        original = os.fstat
        count = 0
        def changed(descriptor):
            nonlocal count
            count += 1
            value = root_metadata(original(descriptor))
            if count == 2:
                value.st_ctime_ns += 1
            return value
        with mock.patch.object(P.os, "geteuid", return_value=0), mock.patch.object(P.os, "fstat", side_effect=changed), self.assertRaises(ValueError):
            P.read_archive(self.source)

    def test_publish_and_same_destination_reconciliation_preserve_source(self):
        with self.root_context():
            first = P.stage(self.source)
            second = P.stage(self.source)
        self.assertEqual(first["state"], "staged-not-installed")
        self.assertEqual(second["state"], "already-staged")
        self.assertFalse(first["helper_changed"])
        self.assertFalse(first["tool_executed"])
        self.assertEqual(self.source.read_bytes(), SYNTHETIC)
        self.assertEqual((self.destination / P.ARCHIVE_NAME).read_bytes(), SYNTHETIC)
        self.assertEqual([p.name for p in self.destination.iterdir()], [P.ARCHIVE_NAME])

    def test_existing_different_or_public_destination_is_never_repaired(self):
        final = self.destination / P.ARCHIVE_NAME
        final.write_bytes(b"EXISTING")
        final.chmod(0o600)
        with self.root_context(), self.assertRaises(ValueError):
            P.stage(self.source)
        self.assertEqual(final.read_bytes(), b"EXISTING")
        final.write_bytes(SYNTHETIC)
        final.chmod(0o644)
        with self.root_context(), self.assertRaises(ValueError):
            P.stage(self.source)
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o644)

    def test_exclusive_publication_collision_cannot_overwrite_existing_file(self):
        final = self.destination / P.ARCHIVE_NAME
        final.write_bytes(b"DO_NOT_REPLACE")
        final.chmod(0o600)
        with self.root_context():
            descriptor = os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(FileExistsError):
                    P.publish_archive(descriptor, SYNTHETIC)
            finally:
                os.close(descriptor)
        self.assertEqual(final.read_bytes(), b"DO_NOT_REPLACE")
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_write_error_removes_only_own_temporary_not_original_or_final(self):
        with self.root_context():
            descriptor = os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(P.os, "fsync", side_effect=OSError("PRIVATE_DATA")), self.assertRaises(OSError):
                    P.publish_archive(descriptor, SYNTHETIC)
            finally:
                os.close(descriptor)
        self.assertEqual(self.source.read_bytes(), SYNTHETIC)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_nonroot_refuses_before_archive_read_or_directory_open(self):
        with mock.patch.object(P.os, "geteuid", return_value=1000), mock.patch.object(P, "read_archive") as read, self.assertRaises(ValueError):
            P.stage(self.source)
        read.assert_not_called()


class DirectoryAndCLIContracts(unittest.TestCase):
    def test_fixed_protected_ancestor_walk_never_creates_or_repairs_directories(self):
        metadata = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        with mock.patch.object(P.os, "open", side_effect=[10, 11, 12, 13]) as opening, mock.patch.object(P.os, "close") as closing, mock.patch.object(P.os, "fstat", return_value=metadata):
            self.assertEqual(P.open_directory(), 13)
        self.assertEqual([call.args[0] for call in opening.call_args_list], ["/", "opt", "morgan-toolchains", "archives"])
        self.assertEqual([call.args[0] for call in closing.call_args_list], [10, 11, 12])
        self.assertTrue(all(call.args[1] & os.O_NOFOLLOW for call in opening.call_args_list))

    def test_unprotected_or_nonroot_ancestor_fails_closed_and_closes_handle(self):
        for info in (SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0), SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)):
            with self.subTest(info=info), mock.patch.object(P.os, "open", return_value=10), mock.patch.object(P.os, "close") as close, mock.patch.object(P.os, "fstat", return_value=info), self.assertRaises(ValueError):
                P.open_directory()
            close.assert_called_once_with(10)

    def test_cli_refusal_is_constant_without_sensitive_exception_or_effect_retry(self):
        output = io.StringIO()
        with mock.patch.object(P.sys, "argv", ["stage_toolchain.py", "--archive", "/authorized/input"]), mock.patch.object(P, "stage", side_effect=OSError("PRIVATE_DATA")) as stage, contextlib.redirect_stderr(output):
            self.assertEqual(P.main(), 2)
        stage.assert_called_once_with(Path("/authorized/input"))
        self.assertEqual(output.getvalue(), P.REFUSAL + "\n")


if __name__ == "__main__":
    unittest.main()
