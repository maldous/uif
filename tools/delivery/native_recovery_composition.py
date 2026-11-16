"""Compose two strict original-input source preparations; never adopt them."""
import argparse
import importlib.util
import json
from pathlib import Path


def sibling(name):
    # Import only the accepted repository tools, never installed native modules.
    spec = importlib.util.spec_from_file_location(
        "_uif_composition_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    # Read source directly rather than exec_module/get_code: no bytecode emission.
    raw = spec.loader.get_source(spec.name)
    exec(compile(raw, str(Path(__file__).with_name(name + ".py")), "exec"), module.__dict__)
    return module


LABEL = sibling("owner_label_context")
CUSTODY = sibling("native_custody_observation")
MAX_OUTPUT_BYTES = 1024 * 1024
STANDALONE_PINS = {
    "label_orchestrator": "b1c9fbd852cef7f9c999848a0c782e8fdc8fc5b0a28bf4e213d4b43e3af6f41a",
    "custody_orchestrator": "45175b39df3d2b644df30771f0f56e44b4f27908342109d16a130027adcad7ea",
    "custody_web": "0e4d50d23db903541c59f5a2937f0f9fb74b5b420af357227102d75f30aca2c3",
}


class CompositionError(ValueError):
    pass


def replace_once(raw, old, new):
    if type(raw) is not bytes or type(old) is not bytes or not old or raw.count(old) != 1:
        raise CompositionError("Unique exact composition span required")
    return raw.replace(old, new, 1)


def component_pin(name, raw):
    if (type(raw) is not bytes or len(raw) > MAX_OUTPUT_BYTES
            or CUSTODY.digest(raw) != STANDALONE_PINS[name]):
        raise CompositionError("Exact qualified standalone output required")


def remove_custody(raw):
    raw = replace_once(raw, CUSTODY.ORCHESTRATOR_METHOD, b"")
    return replace_once(raw, CUSTODY.MULTI_METHOD, b"")


def compose_native_sources(orchestrator_source, web_source):
    """Pure bytes-to-bytes union; both strict preparers receive originals only."""
    try:
        label_only, _ = LABEL.render_native_source(orchestrator_source)
        custody_only, _ = CUSTODY.prepare_native_sources(orchestrator_source, web_source)
        component_pin("label_orchestrator", label_only)
        component_pin("custody_orchestrator", custody_only["orchestrator.py"])
        component_pin("custody_web", custody_only["web.py"])

        expected_label = replace_once(orchestrator_source, LABEL.NEEDLE, LABEL.REPLACEMENT)
        if label_only != expected_label:
            raise CompositionError("Standalone label span differs")
        combined = replace_once(custody_only["orchestrator.py"], LABEL.NEEDLE, LABEL.REPLACEMENT)
        if len(combined) > MAX_OUTPUT_BYTES:
            raise CompositionError("Composed source exceeds bound")

        without_label = replace_once(combined, LABEL.REPLACEMENT, LABEL.NEEDLE)
        without_custody = remove_custody(combined)
        original_web = replace_once(custody_only["web.py"], CUSTODY.WEB_ROUTE, b"")
        if (without_label != custody_only["orchestrator.py"]
                or without_custody != label_only
                or remove_custody(without_label) != orchestrator_source
                or replace_once(without_custody, LABEL.REPLACEMENT, LABEL.NEEDLE) != orchestrator_source
                or original_web != web_source):
            raise CompositionError("Exact disjoint composition inverse failed")
        after = {"orchestrator.py": combined, "web.py": custody_only["web.py"]}
        for raw in after.values():
            CUSTODY.parse_source(raw)
    except (LABEL.CorrectionError, CUSTODY.PreparationError) as error:
        raise CompositionError("Strict original source preparation refused") from error

    return after, {
        "schema": "native-recovery-composition/1",
        "files": {
            "orchestrator.py": {
                "before_sha256": CUSTODY.digest(orchestrator_source),
                "label_only_sha256": CUSTODY.digest(label_only),
                "custody_only_sha256": CUSTODY.digest(custody_only["orchestrator.py"]),
                "composed_sha256": CUSTODY.digest(combined),
            },
            "web.py": {
                "before_sha256": CUSTODY.digest(web_source),
                "label_only_sha256": CUSTODY.digest(web_source),
                "custody_only_sha256": CUSTODY.digest(custody_only["web.py"]),
                "composed_sha256": CUSTODY.digest(after["web.py"]),
            },
        },
        "proof": {
            "same_original_inputs": True,
            "remove_labels_equals_custody": True,
            "remove_custody_equals_labels": True,
            "remove_all_equals_original": True,
            "ast_syntax": True,
        },
        "source_prepared": True,
        "native_installed": False,
        "runtime_adopted": False,
        "native_imported": False,
        "endpoint_invoked": False,
        "current_custody_observed": False,
        "original_records_changed": False,
        "ordinary_comments_neutralized": False,
        "input_hold_compatibility_verified": False,
        "old_gate_compatibility_verified": False,
        "retry_preservation_qualified": False,
        "grants_authority": False,
        "unknown": {
            "external_owner": None, "current_issuer": None, "lease": None,
            "epoch": None, "effect_fence": None, "original_settlement": None,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orchestrator-source", type=Path, required=True)
    parser.add_argument("--web-source", type=Path, required=True)
    parser.add_argument("--patch", action="store_true")
    args = parser.parse_args(argv)
    try:
        before = {
            "orchestrator.py": CUSTODY.read_source(args.orchestrator_source),
            "web.py": CUSTODY.read_source(args.web_source),
        }
        after, result = compose_native_sources(before["orchestrator.py"], before["web.py"])
    except (CompositionError, CUSTODY.PreparationError, OSError, ValueError):
        print(json.dumps({"status": "refused", "native_installed": False}))
        return 2
    print(CUSTODY.patch_text(before, after) if args.patch else json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
