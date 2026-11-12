"""Seal and consume one controller-owned role launch without sharing authority."""

from dataclasses import asdict
from hashlib import sha256
import os
from pathlib import Path
import shutil
import stat
from tempfile import NamedTemporaryFile
import time

from .dispatch import _digest, _encode, _record, _sync_directory
from .role_broker import MAX_RECORD, LaunchObservation, LaunchRequest, projection_digest, slot_for_lease
from .source import SourceHold, exact_path, file_digest


class _ReturnImportHold(SourceHold):
    """The original import may have changed controller-owned files."""


def _write_new(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_role_launch(cfg, *, key, admission_path, intent_path, request_path, workspace,
                        policy_sha256, pool_binding):
    """Seal the helper view under the caller's existing original operation fence."""
    control = cfg["controller_root"]
    slot = slot_for_lease(key.lease_id)
    directory = control / slot
    workspace = exact_path(workspace)
    paths = {name: exact_path(path) for name, path in (
        ("admission.json", admission_path), ("intent.json", intent_path), ("request.json", request_path),
    )}
    if not all(path.is_relative_to(control) for path in (*paths.values(), workspace)):
        raise SourceHold("uif_controller_launch_custody")
    records = {}
    for name, path in paths.items():
        with path.open("rb") as stream:
            records[name] = stream.read(MAX_RECORD + 1)
        if len(records[name]) > MAX_RECORD or file_digest(path) != sha256(records[name]).hexdigest():
            raise SourceHold("uif_controller_launch_input_drift")
    if sha256(records["intent.json"]).hexdigest() != key.intent_sha256:
        raise SourceHold("uif_controller_launch_intent")
    budget = cfg["role_limits"][key.role]["max_projection_bytes"]
    digest = projection_digest(workspace, budget, custody_root=control)
    request = LaunchRequest(key, slot, sha256(records["admission.json"]).hexdigest(),
                            sha256(records["request.json"]).hexdigest(), digest, policy_sha256)
    _sync_directory(control)
    directory.mkdir(mode=0o700)
    _sync_directory(control)
    projection = directory / "projection"
    shutil.copytree(workspace, projection, symlinks=True)
    if (projection_digest(projection, budget, custody_root=control) != digest
            or projection_digest(workspace, budget, custody_root=control) != digest):
        raise SourceHold("uif_controller_launch_projection_drift")
    for current, _, files in os.walk(projection, topdown=False):
        for name in files:
            with (Path(current) / name).open("rb") as stream:
                os.fsync(stream.fileno())
        _sync_directory(Path(current))
    for name, data in records.items():
        _write_new(directory / name, data)
    _write_new(directory / "fence.json", _encode(dict(
        schema="uif-role-launch/1", request_sha256=request.digest, lease=key.lease_id,
        binding_sha256=_digest(pool_binding))))
    _write_new(directory / "launch.json", _encode(asdict(request)))
    _sync_directory(directory)
    cfg["_fresh_role_launch"] = request
    return request


def _workspace_tree(root):
    paths = {}
    directories = set()
    for current, dirs, files in os.walk(root):
        directory = Path(current)
        if directory == root:
            dirs[:] = [name for name in dirs if name != ".git"]
            files = [name for name in files if name != ".git"]
        directories.update((directory / name).relative_to(root).as_posix() for name in dirs)
        for name in files:
            path = directory / name
            paths[path.relative_to(root).as_posix()] = (file_digest(path), path.stat().st_mode & stat.S_IXUSR)
    return paths, directories


def _verify_return_head(original, returned):
    head = original / ".git" / "HEAD"
    with head.open("rb") as stream:
        symbolic = stream.read(4097)
    if len(symbolic) > 4096 or not symbolic.startswith(b"ref: refs/heads/"):
        raise SourceHold("uif_controller_launch_source_identity")
    reference = Path(symbolic.removeprefix(b"ref: ").strip().decode())
    if not reference.is_relative_to("refs/heads") or ".." in reference.parts:
        raise SourceHold("uif_controller_launch_source_identity")
    for name in ("HEAD", reference, "packed-refs"):
        before, after = (tree / ".git" / name for tree in (original, returned))
        if before.exists() != after.exists() or (before.exists() and file_digest(before) != file_digest(after)):
            raise SourceHold("uif_controller_launch_source_identity")


def _replace_file(target, source, expected):
    target = exact_path(target)
    with NamedTemporaryFile(dir=target.parent, prefix=".role-return-", delete=False) as stream:
        staged = Path(stream.name)
        try:
            with source.open("rb") as inp:
                shutil.copyfileobj(inp, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600 | (source.stat().st_mode & stat.S_IXUSR))
            os.fsync(stream.fileno())
            if file_digest(staged) != expected:
                raise SourceHold("uif_controller_launch_return_drift")
            os.replace(staged, target)
            _sync_directory(target.parent)
        finally:
            staged.unlink(missing_ok=True)


def _import_return(cfg, request, observation, workspace, directory, write_roots, output_bytes):
    control = cfg["controller_root"]
    slot = control / request.slot
    import_intent = slot / "import-intent.json"
    if os.path.lexists(import_intent):
        raise _ReturnImportHold("uif_controller_launch_import_requires_reconciliation")
    source = slot / "returned"
    manifest, digest = _record(source / "manifest.json", observation.output_sha256)
    returned = source / "workspace"
    original = slot / "projection"
    budget = cfg["role_limits"][request.key.role]["max_projection_bytes"]
    if (digest != observation.output_sha256 or set(manifest) != {"workspace_sha256", "artifacts"}
            or projection_digest(returned, budget, custody_root=control) != manifest["workspace_sha256"]
            or projection_digest(original, budget, custody_root=control) != request.projection_sha256
            or projection_digest(workspace, budget, custody_root=control) != request.projection_sha256):
        raise SourceHold("uif_controller_launch_return_drift")
    _verify_return_head(original, returned)
    before, before_dirs = _workspace_tree(original)
    after, after_dirs = _workspace_tree(returned)
    # Replacements between node kinds require a different import transaction.
    if before.keys() & after_dirs or after.keys() & before_dirs:
        raise SourceHold("uif_controller_launch_return_shape")
    changes = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    new_dirs = after_dirs - before_dirs
    removed_dirs = before_dirs - after_dirs
    implied_dirs = {parent.as_posix() for name in changes for parent in Path(name).parents}

    def in_scope(name):
        return ".git" not in Path(name).parts and any(
            name == root or name.startswith(root + "/") for root in write_roots)

    if (any(not in_scope(name) for name in changes)
            or any(name not in implied_dirs and not in_scope(name) for name in new_dirs | removed_dirs)):
        raise SourceHold("uif_controller_launch_output_scope")
    artifacts = manifest["artifacts"]
    if (not isinstance(artifacts, dict) or not {"stdout.jsonl", "stderr.log"} <= artifacts.keys()
            or not artifacts.keys() <= {"stdout.jsonl", "stderr.log", "final-message.txt"}
            or any(file_digest(source / name) != pin for name, pin in artifacts.items())):
        raise SourceHold("uif_controller_launch_artifacts")
    size = sum((returned / name).stat().st_size for name in changes if name in after)
    if "final-message.txt" in artifacts:
        size += (source / "final-message.txt").stat().st_size
    if size > output_bytes:
        raise SourceHold("uif_controller_launch_output_budget")
    if any(os.path.lexists(directory / name) for name in artifacts):
        raise _ReturnImportHold("uif_controller_launch_import_artifact_exists")
    # Retain the original projection and exact returned manifest under the
    # existing slot. Any failure after this intent keeps the lease held.
    try:
        _write_new(import_intent, _encode(dict(
            launch_sha256=request.digest, projection_sha256=request.projection_sha256,
            output_sha256=digest, workspace=str(workspace), attempt=str(directory))))
        _sync_directory(slot)
        # Returned Git metadata stays out of the controller workspace.
        for name in sorted(new_dirs):
            target = exact_path(workspace / name)
            target.mkdir(mode=0o700)
            _sync_directory(target.parent)
        for name in changes:
            target = exact_path(workspace / name)
            if name in after:
                _replace_file(target, returned / name, after[name][0])
            else:
                target.unlink()
                _sync_directory(target.parent)
        for name in sorted(removed_dirs, reverse=True):
            target = exact_path(workspace / name)
            target.rmdir()
            _sync_directory(target.parent)
        if _workspace_tree(workspace) != (after, after_dirs):
            raise SourceHold("uif_controller_launch_return_readback")
        for name, pin in artifacts.items():
            with (source / name).open("rb") as stream:
                data = stream.read(budget + 1)
            if sha256(data).hexdigest() != pin:
                raise SourceHold("uif_controller_launch_return_drift")
            _write_new(directory / name, data)
        _sync_directory(directory)
    except (OSError, ValueError) as error:
        raise _ReturnImportHold("uif_controller_launch_import_requires_reconciliation") from error


def run_role_launch(cfg, launch, *, directory, workspace, write_roots, output_bytes, stop):
    """Use one helper start, exact observation and trusted original cleanup evidence."""
    broker = cfg["role_broker"]
    fresh = cfg.get("_fresh_role_launch") is launch
    if fresh:
        del cfg["_fresh_role_launch"]
    request, _ = _record(cfg["controller_root"] / launch.slot / "request.json", launch.request_sha256)
    timeout = request.get("timeout_seconds")
    if (set(request) != {"prompt", "timeout_seconds"} or not isinstance(request["prompt"], str)
            or not request["prompt"].strip() or type(timeout) is not int or not 1 <= timeout <= 600):
        raise SourceHold("uif_controller_launch_request")
    deadline = time.monotonic() + timeout + 15
    cancelled = stop()
    if cancelled:
        # Only the helper can make a no-fork decision exclusive with its
        # original reservation. An absent or ambiguous reply cannot release.
        observation = broker.cancel(launch)
    else:
        observation = broker.start(launch) if fresh else broker.observe(launch)
    if (fresh or cancelled) and observation.state == "uncertain":
        observation = broker.observe(launch)
    while observation.state in {"prepared", "running"} and time.monotonic() < deadline:
        if not cancelled and stop():
            cancelled = True
            observation = broker.cancel(launch)
        else:
            time.sleep(0.05)
            observation = broker.observe(launch)
    if (not isinstance(observation, LaunchObservation) or observation.request_sha256 != launch.digest
            or not observation.release_safe):
        return dict(status="uncertain", child_tree_gone=False, exit_code=None)
    result = dict(status="cancelled" if cancelled else "failed", child_tree_gone=True,
                  child_started=observation.state == "exited-clean", exit_code=observation.exit_code,
                  launch_sha256=launch.digest, launch_receipt_sha256=observation.receipt_sha256)
    if observation.output_sha256 is None or observation.state != "exited-clean":
        return result
    try:
        _import_return(cfg, launch, observation, workspace, directory, write_roots, output_bytes)
    except _ReturnImportHold as error:
        result.update(status="uncertain", error_type=type(error).__name__)
        return result
    except (OSError, ValueError) as error:
        result["error_type"] = type(error).__name__
        return result
    if not cancelled and observation.exit_code == 0:
        result["status"] = "succeeded"
    return result
