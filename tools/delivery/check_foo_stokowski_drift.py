"""Offline configuration comparison; never discovers or changes a host.

CLI: python tools/delivery/check_foo_stokowski_drift.py INPUT.json
Exit codes: 0 supplied values match, 1 drift, 2 refused input, 3 unknowns.
Matching values are not live-state, service-health or admission attestations.
"""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys


SCHEMA_VERSION = "foo-stokowski-drift-v1"
CONFIG_SCHEMA = {
    "framework_owner": str,
    "store_id": str,
    "epoch": str,
    "service": str,
    "service_user": str,
    "service_uid": int,
    "entrypoint": {"mode": str, "target": str},
    "source": {
        "release": str,
        "host_runner": {"path": str, "sha256": str},
        "owner_registration": {"path": str, "sha256": str},
    },
    "runtime_root": str,
    "pool_database": str,
    "project_scope": {"kind": str, "product_owner": type(None), "isolated": bool},
}


class InvalidInput(ValueError):
    """The supplied document cannot support a bounded comparison."""


def _validate(value, schema, path, observed=False):
    if observed and value is None:
        return
    if observed and schema is type(None) and type(value) is str and value.strip():
        return
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise InvalidInput(f"{path}: expected object")
        if value.keys() != schema.keys():
            missing = sorted(schema.keys() - value.keys())
            extra = sorted(value.keys() - schema.keys())
            raise InvalidInput(f"{path}: missing={missing}, unsupported={extra}")
        for key, child in schema.items():
            _validate(value[key], child, f"{path}.{key}", observed)
        return
    # Exact types prevent True from being accepted as UID 1.
    if type(value) is not schema:
        raise InvalidInput(f"{path}: expected {schema.__name__}")
    if schema is str and not value.strip():
        raise InvalidInput(f"{path}: empty value")
    if path.endswith(".service_uid") and value < 0:
        raise InvalidInput(f"{path}: UID must be nonnegative")
    if path.endswith(".epoch") and not re.fullmatch(r"[0-9]+", value):
        raise InvalidInput(f"{path}: expected decimal epoch string")
    if path.endswith(".sha256") and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InvalidInput(f"{path}: expected lowercase SHA-256")
    if path.rsplit(".", 1)[-1] in {"release", "path", "runtime_root", "pool_database"}:
        if not value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise InvalidInput(f"{path}: expected absolute path without parent traversal")


def compare_configurations(declared, observed):
    """Compare complete objects using CONFIG_SCHEMA; observed null means unknown.

    The declaration is an explicit caller-selected contract, not a discovery
    result. No path contained in either object is opened or executed. Unknown
    subtrees expand to individual fields so every uncovered requirement is visible.
    """
    _validate(declared, CONFIG_SCHEMA, "declared")
    if not isinstance(observed, dict):
        raise InvalidInput("observed: expected object (use null for unknown fields)")
    _validate(observed, CONFIG_SCHEMA, "observed", observed=True)
    if declared["project_scope"] != {
        "kind": "project-neutral-foo-infrastructure",
        "product_owner": None,
        "isolated": True,
    }:
        raise InvalidInput("declared.project_scope: requires neutral isolated Foo infrastructure")
    if declared["entrypoint"] != {
        "mode": "installed-console-script",
        "target": "uif.ledger_coordinator.main",
    }:
        raise InvalidInput("declared.entrypoint: requires installed console entry point")
    fields = []

    def visit(expected, actual, schema, path):
        if isinstance(schema, dict):
            for key, child in schema.items():
                visit(expected[key], None if actual is None else actual[key],
                      child, f"{path}.{key}" if path else key)
            return
        # product_owner=None is an explicit absence of product ownership. A null
        # containing scope still needs to remain unknown, handled below.
        status = "unknown" if actual is None and schema is not type(None) else (
            "match" if expected == actual else "mismatch")
        fields.append({"field": path, "status": status,
                       "declared": expected, "observed": actual})

    visit(declared, observed, CONFIG_SCHEMA, "")
    if observed is None or observed["project_scope"] is None:
        for field in fields:
            if field["field"] == "project_scope.product_owner":
                field["status"] = "unknown"
    counts = {status: sum(f["status"] == status for f in fields)
              for status in ("match", "mismatch", "unknown")}
    status = "drift" if counts["mismatch"] else "unknown" if counts["unknown"] else "match"
    return {"schema_version": SCHEMA_VERSION, "status": status,
            "counts": counts, "fields": fields,
            "mismatches": [f for f in fields if f["status"] == "mismatch"],
            "unknowns": [f for f in fields if f["status"] == "unknown"],
            "basis": "supplied objects only; no live attestation"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise InvalidInput(f"non-JSON constant: {value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="explicit JSON report containing declared and observed")
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
        if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
            raise InvalidInput("unsupported or missing report schema_version")
        if "declared" not in report or "observed" not in report:
            raise InvalidInput("report requires declared and observed objects")
        if not isinstance(report["observed"], dict):
            raise InvalidInput("observed: expected object (use null for unknown fields)")
        result = compare_configurations(report["declared"], report["observed"])
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "refused",
                          "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return {"match": 0, "drift": 1, "unknown": 3}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
