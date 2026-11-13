"""Tests only the new offline drift comparator; fixtures are not host evidence."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/delivery/check_foo_stokowski_drift.py"
REPORT = ROOT / "docs/operations/foo-stokowski-drift.json"
SPEC = importlib.util.spec_from_file_location("foo_drift", SCRIPT)
drift = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drift)


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(REPORT.read_text(encoding="utf-8"))
        self.declared = self.report["declared"]
        # Explicitly synthetic complete matching observation.
        self.observed = copy.deepcopy(self.declared)

    def compare(self):
        return drift.compare_configurations(self.declared, self.observed)

    def cli(self, contents):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(contents, encoding="utf-8")
            return subprocess.run([sys.executable, "-B", str(SCRIPT), str(path)],
                                  capture_output=True, text=True, check=False)

    def test_matching_configuration(self):
        result = self.compare()
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["counts"], {"match": 18, "mismatch": 0, "unknown": 0})
        self.assertEqual(result["mismatches"], [])

    def test_concrete_mismatches(self):
        cases = [
            ("framework_owner", "offmenu-cloud-github-owner-01a1866b"),
            ("store_id", "different-store"), ("epoch", "2"),
            ("service", "offmenu-stokowski-current.service"),
            ("service_user", "root"), ("service_uid", 0),
            ("entrypoint.mode", "python-m"),
            ("entrypoint.target", "python -m uif.ledger_coordinator"),
            ("source.release", "/opt/uif-stokowski/stale-release"),
            ("source.host_runner.path", "/opt/stale/host-runner.json"),
            ("source.host_runner.sha256", "0" * 64),
            ("source.owner_registration.path", "/etc/stale/owner-runtime.json"),
            ("source.owner_registration.sha256", "0" * 64),
            ("runtime_root", "/tmp/other-runtime"),
            ("pool_database", "/tmp/moved-pool/state.sqlite"),
            ("project_scope.kind", "offmenu-product"),
            ("project_scope.product_owner", "offmenu-owner"),
            ("project_scope.isolated", False),
        ]
        for field, actual in cases:
            with self.subTest(field=field):
                observed = copy.deepcopy(self.declared)
                target = observed
                parts = field.split(".")
                for part in parts[:-1]:
                    target = target[part]
                expected = target[parts[-1]]
                target[parts[-1]] = actual
                result = drift.compare_configurations(self.declared, observed)
                self.assertEqual(result["status"], "drift")
                self.assertEqual(result["mismatches"], [{"field": field,
                    "status": "mismatch", "declared": expected, "observed": actual}])

    def test_supplied_report_keeps_unknowns(self):
        result = drift.compare_configurations(self.declared, self.report["observed"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["counts"], {"match": 13, "mismatch": 0, "unknown": 5})
        self.assertIn("project_scope.product_owner", {f["field"] for f in result["unknowns"]})

    def test_drift_does_not_hide_unknowns(self):
        self.observed["entrypoint"] = None
        self.observed["service_uid"] = 0
        result = self.compare()
        self.assertEqual(result["status"], "drift")
        self.assertEqual(len(result["unknowns"]), 2)
        self.assertEqual(result["mismatches"][0]["field"], "service_uid")

    def test_refuses_incomplete_or_extra_configuration(self):
        for side in ("declared", "observed"):
            for malformed in ({}, dict(self.declared, credentials="unsupported")):
                with self.subTest(side=side, keys=list(malformed)):
                    args = {"declared": self.declared, "observed": self.observed}
                    args[side] = malformed
                    with self.assertRaises(drift.InvalidInput):
                        drift.compare_configurations(**args)

    def test_refuses_bad_types_hashes_paths(self):
        for actual in (True, "1000", -1):
            with self.subTest(uid=actual), self.assertRaises(drift.InvalidInput):
                self.observed["service_uid"] = actual
                self.compare()
        self.observed = copy.deepcopy(self.declared)
        self.observed["source"]["host_runner"]["sha256"] = "unverified"
        with self.assertRaises(drift.InvalidInput):
            self.compare()
        for path in ("relative/state.sqlite", "/tmp/../state.sqlite"):
            self.observed = copy.deepcopy(self.declared)
            self.observed["pool_database"] = path
            with self.assertRaises(drift.InvalidInput):
                self.compare()

    def test_refuses_unsafe_declaration(self):
        for field, value in (("entrypoint", {"mode": "python-m", "target": "uif.ledger_coordinator"}),
                             ("project_scope", {"kind": "offmenu", "product_owner": None, "isolated": True})):
            with self.subTest(field=field):
                declared = copy.deepcopy(self.declared)
                declared[field] = value
                with self.assertRaises(drift.InvalidInput):
                    drift.compare_configurations(declared, self.observed)

    def test_cli_match_drift_and_unknown_exit_codes(self):
        for uid, entrypoint, expected in ((1000, self.observed["entrypoint"], 0),
                                          (0, self.observed["entrypoint"], 1),
                                          (1000, None, 3)):
            with self.subTest(exit_code=expected):
                self.report["observed"] = copy.deepcopy(self.observed)
                self.report["observed"]["service_uid"] = uid
                self.report["observed"]["entrypoint"] = entrypoint
                result = self.cli(json.dumps(self.report))
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn(json.loads(result.stdout)["status"], ("match", "drift", "unknown"))

    def test_cli_refuses_malformed_reports(self):
        for contents in ("{", "[]", "{}", '{"schema_version":NaN}',
                         '{"declared":{},"declared":{}}',
                         json.dumps(dict(self.report, observed=[])),
                         json.dumps(dict(self.report, schema_version="unsupported"))):
            with self.subTest(contents=contents):
                result = self.cli(contents)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "refused")

    def test_cli_requires_explicit_input(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_missing_input_file_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                     str(Path(directory) / "absent.json")],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "refused")

    def test_no_mutation_or_following_embedded_paths(self):
        before = copy.deepcopy((self.declared, self.observed))
        self.compare()
        self.assertEqual((self.declared, self.observed), before)
        # Nonexistent paths are compared as strings, never opened or executed.
        self.declared["source"]["release"] = "/definitely-absent/release"
        self.observed["source"]["release"] = "/definitely-absent/release"
        self.assertEqual(self.compare()["status"], "match")


if __name__ == "__main__":
    unittest.main()
