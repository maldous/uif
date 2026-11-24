#!/usr/bin/env python3
"""Read-only, stdlib, immutable handoff snapshot verifier; no runtime attestation.

Only adjacent JSON and Markdown are inputs. Pins are comparison expectations,
not authentication. Updating a pending stage requires original evidence and a
reviewed checker update; passing this check never qualifies a runtime stage.
"""
import hashlib
import json
from pathlib import Path
import sys

EXPECTED = {'schema': 'da276451dbbe3167a12c399b456bd36973bd2842756d012a10c98acf15ed8ed8', 'identity': '23bd5eab2d7efb8d1b83a8bc9817c822049a905cbabce0b755c5b0b4ee264cbe', 'sources': '61a0f329530a1d58d0c47e510cc3a4531b6c3458798e16e3c6bd45d3cef50e15', 'continuation': '99ae89b91174ce126c18e69e212f83da8a04138ec1a8ef3fdc2cbda8915c8e52', 'ledger_preservation': '141fc70c1d1b20d638d1740c817f4f36bf60d6a240b2852dec4f5f688e7cd481', 'prior_evidence': '3e1dc221a7f374f6920de23bd00e35c1e455af6d9ff18791f5d7b6c2a054e871', 'current_stages': 'e19393ba4863ec3c0ec3070a1ab8b91824a36ee443e2fd46aa7def9536d2f4e8', 'holds': 'f755176fe756d762119f5e72b4e733a0a16f14d94a5386aabb4755c71fe0589b', 'recovery_validation': 'd2a0469ca645247e4d21fc2c352f7c6def43becc7f7c874f771d7715854c6d63', 'readiness': '9763a9334ef17673dd1b5d2b868663b518ec1afc57b345bf8f5ed4d14d1f536b', 'checker_boundary': '08c797ab51eda6ea050d37713ae1d5c6800321e776c72bb1e5d8f703b43065d5', 'dependency_qualification': '296efdd1a9b4de8123fe56ade471a2e142f5aab65ab0b25122b004d9fb6ca440'}
MARKDOWN_SHA256 = 'a28e65bd9ec7efbe91502ac42964cb865c61714507c39c3a109c0925014ce213'


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("non-finite JSON constant: " + value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(data, markdown):
    require(type(data) is dict, "root must be an object")
    require(set(data) == set(EXPECTED), "missing or extra schema fields")
    require(data["schema"] == "uif-restart-foo-validation-v1", "unknown schema")
    identity = data["identity"]
    require(type(identity) is dict, "identity must be an object")
    for key in ("repository", "project", "deliverable", "store", "owner",
                "original_row_source_sha256"):
        require(type(identity.get(key)) is str and bool(identity[key]),
                "missing identity field: " + key)
    require(type(identity.get("epoch")) is int and identity["epoch"] == 1,
            "epoch must be integer 1")
    sources = data["sources"]
    require(sources["accepted_task_source"] != sources["reviewed_runtime_source"],
            "task/runtime conflation")
    continuation = data["continuation"]
    require(continuation["operation_id"] is None and continuation["receipt"] is None,
            "invented continuation identity or receipt")
    for name in ("coding", "validation", "independent_review", "canonical_publication"):
        stage = data["current_stages"][name]
        require(stage["qualification"] == "unqualified", "fabricated stage completion: " + name)
        for key in ("operation_id", "invocation_id", "original_receipt", "accepted_result"):
            require(stage[key] is None, "unsupplied stage evidence: " + name + "." + key)
    ledger = data["ledger_preservation"]
    for key, count in (("original_ids", 213), ("nonadditive_references", 11)):
        values = ledger[key]
        require(type(values) is list and len(values) == count and
                all(type(v) is str and v for v in values) and len(set(values)) == count,
                "invalid original inventory: " + key)
    # Exact per-section pins also reject nested extra/missing fields, type changes,
    # altered histories, invented evidence, omitted holds, reordered/replaced IDs,
    # and source/operation conflation without consulting a live ledger or service.
    for key, expected in EXPECTED.items():
        require(canonical_digest(data[key]) == expected,
                "pinned schema/evidence boundary changed: " + key)
    require(hashlib.sha256(markdown).hexdigest() == MARKDOWN_SHA256,
            "Markdown evidence boundaries changed")


def main():
    try:
        require(len(sys.argv) == 1, "no arguments accepted; only adjacent artifacts are inputs")
        base = Path(__file__).resolve().parent
        json_path = base / "restart-foo-validation.json"
        md_path = base / "restart-foo-validation.md"
        require(not json_path.is_symlink() and not md_path.is_symlink(),
                "artifact symlinks are not accepted")
        data = json.loads(json_path.read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object, parse_constant=reject_constant)
        validate(data, md_path.read_bytes())
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        return 1
    print("PASS: local handoff schema and evidence boundaries; 213 original IDs, 11 references. "
          "Current stages and Foo/cloud/shared/combined readiness remain unqualified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
