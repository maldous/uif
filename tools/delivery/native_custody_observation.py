"""Prepare a pinned, non-authorizing native-process observation adapter.

No native imports, endpoint calls, installation, state mutation or restoration.
"""
import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat

MAX_SOURCE_BYTES = 1024 * 1024
PINS = {
    "orchestrator.py": "7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea",
    "web.py": "c0e18143f3e16cf2c7584b030bf53fd7a60a5802456383e92f0cc2c2e5786c6a",
}
ORCHESTRATOR_METHOD = b'''    def get_native_custody_observation(self, issue_id: str) -> dict[str, Any]:
        """Observe bounded process-local metadata; never authorize an effect."""
        if (type(issue_id) is not str or not 1 <= len(issue_id) <= 128
                or not issue_id.isascii()
                or not issue_id.replace("-", "").replace("_", "").isalnum()):
            raise ValueError("Bounded native issue id required")
        running, task, claimed, gate, stage, run, repair = self._intake_snapshot_custody(issue_id)
        retry = self.retry_attempts.get(issue_id)

        def text(value):
            return value if type(value) is str and len(value) <= 128 else None

        def number(value):
            return value if type(value) is int and 0 <= value <= 2147483647 else None

        return {
            "schema": "native-custody-observation/1",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "process_id": os.getpid(),
            "project_name": text(self.project_name),
            "requested_issue_id": issue_id,
            "presence": {
                "running_entry": running is not None,
                "task_registered": task is not None,
                "claimed_set_membership": claimed if type(claimed) is bool else None,
                "gate_entry": gate is not None,
                "input_hold_repair": repair is not None,
                "retry_entry": retry is not None,
            },
            "state": {
                "gate": text(gate),
                "stage": text(stage),
                "run": number(run),
                "retry_attempt": number(getattr(retry, "attempt", None)),
            },
            "unknown": {
                "external_owner": None,
                "current_issuer": None,
                "lease": None,
                "epoch": None,
                "effect_fence": None,
                "original_settlement": None,
            },
            "authority": {
                "issue_exists_verified": False,
                "external_owner_verified": False,
                "current_issuer_verified": False,
                "lease_verified": False,
                "effect_fenced": False,
                "grants_acquire_release": False,
                "grants_dispatch_restore": False,
                "grants_publication": False,
                "original_record_settled": False,
                "retry_preservation_qualified": False,
            },
        }

'''
MULTI_METHOD = b'''    def get_native_custody_observation(self, project_name: str, issue_id: str):
        """Select an existing project, without creating or claiming anything."""
        if type(project_name) is not str or not 1 <= len(project_name) <= 128:
            raise ValueError("Bounded native project name required")
        orchestrator = self.orchestrators.get(project_name)
        if orchestrator is None:
            return None
        return orchestrator.get_native_custody_observation(issue_id)

'''
WEB_ROUTE = b'''    @app.get("/api/v1/projects/{project_name}/native-custody/{issue_id}")
    async def api_native_custody(project_name: str, issue_id: str):
        # Process-local observation is not an issuer, lease or effect grant.
        try:
            value = orchestrator.get_native_custody_observation(project_name, issue_id)
        except ValueError:
            return JSONResponse(
                {"error": {"code": "invalid_observation_selector",
                           "message": "Bounded project and native issue id required"}},
                status_code=400,
            )
        if value is None:
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": "Unknown project"}},
                status_code=404,
            )
        return JSONResponse(value)

'''
ISSUE_ROUTE = b'    @app.get("/api/v1/{issue_identifier}")\n'


class PreparationError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_source(raw):
    try:
        return ast.parse(raw)
    except (SyntaxError, UnicodeError, RecursionError) as error:
        raise PreparationError("Valid bounded native Python source required") from error


def checked_source(name, raw):
    if (type(raw) is not bytes or len(raw) > MAX_SOURCE_BYTES
            or digest(raw) != PINS[name]):
        raise PreparationError("Exact pinned native source required: " + name)
    return parse_source(raw)


def unique(nodes, name, kind):
    selected = [node for node in nodes if isinstance(node, kind) and node.name == name]
    if len(selected) != 1:
        raise PreparationError("Unique source anchor required: " + name)
    return selected[0]


def insert_at(raw, line, addition):
    offset = sum(len(part) for part in raw.splitlines(keepends=True)[:line - 1])
    return raw[:offset] + addition + raw[offset:]


def add_method(raw, class_name, addition):
    tree = parse_source(raw)
    selected = unique(tree.body, class_name, ast.ClassDef)
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    if any(isinstance(node, functions) and node.name == "get_native_custody_observation"
           for node in selected.body):
        raise PreparationError("Observation method already exists")
    anchor = unique(selected.body, "get_state_snapshot", functions)
    line = min([anchor.lineno] + [node.lineno for node in anchor.decorator_list])
    return insert_at(raw, line, addition)


def add_route(raw):
    tree = parse_source(raw)
    app = unique(tree.body, "create_app", ast.FunctionDef)
    anchor = unique(app.body, "api_issue", ast.AsyncFunctionDef)
    if raw.count(ISSUE_ROUTE) != 1 or len(anchor.decorator_list) != 1:
        raise PreparationError("Exact existing issue GET anchor required")
    line = anchor.decorator_list[0].lineno
    if raw.splitlines(keepends=True)[line - 1] != ISSUE_ROUTE:
        raise PreparationError("Existing issue GET route differs")
    if any(isinstance(node, ast.AsyncFunctionDef) and node.name == "api_native_custody"
           for node in app.body):
        raise PreparationError("Observation route already exists")
    return insert_at(raw, line, WEB_ROUTE)


def metadata(before, after):
    return {
        "schema": "native-custody-source-preparation/1",
        "files": {name: {"before_sha256": digest(before[name]),
                         "after_sha256": digest(after[name])} for name in before},
        "source_prepared": True,
        "native_installed": False,
        "runtime_adopted": False,
        "endpoint_invoked": False,
        "current_custody_observed": False,
        "grants_authority": False,
        "retry_preservation_qualified": False,
        "original_records_changed": False,
    }


def prepare_native_sources(orchestrator_source, web_source):
    before = {"orchestrator.py": orchestrator_source, "web.py": web_source}
    for name, raw in before.items():
        checked_source(name, raw)
    orchestrator = add_method(orchestrator_source, "Orchestrator", ORCHESTRATOR_METHOD)
    orchestrator = add_method(orchestrator, "MultiOrchestrator", MULTI_METHOD)
    after = {"orchestrator.py": orchestrator, "web.py": add_route(web_source)}
    restored = orchestrator.replace(ORCHESTRATOR_METHOD, b"", 1).replace(MULTI_METHOD, b"", 1)
    if restored != orchestrator_source or after["web.py"].replace(WEB_ROUTE, b"", 1) != web_source:
        raise PreparationError("Existing source preservation failed")
    for raw in after.values():
        parse_source(raw)
    return after, metadata(before, after)


def read_source(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise PreparationError("Bounded regular native source required")
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise PreparationError("Native source exceeds bound")
    return raw


def patch_text(before, after):
    return "".join("".join(difflib.unified_diff(
        before[name].decode().splitlines(keepends=True),
        after[name].decode().splitlines(keepends=True),
        fromfile="a/" + name, tofile="b/" + name,
    )) for name in before)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orchestrator-source", type=Path, required=True)
    parser.add_argument("--web-source", type=Path, required=True)
    parser.add_argument("--patch", action="store_true", help="Print generic-source patch, never install it")
    args = parser.parse_args(argv)
    try:
        before = {"orchestrator.py": read_source(args.orchestrator_source),
                  "web.py": read_source(args.web_source)}
        after, receipt = prepare_native_sources(before["orchestrator.py"], before["web.py"])
    except (PreparationError, OSError, ValueError):
        print(json.dumps({"status": "refused", "native_installed": False}))
        return 2
    print(patch_text(before, after) if args.patch else json.dumps(receipt, sort_keys=True), end="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
