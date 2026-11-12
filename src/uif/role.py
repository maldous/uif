"""Verify the explicit UIF control-source manifest against a source root."""

from hashlib import sha256
from pathlib import PurePosixPath
import json
import re

from .source import SourceHold, exact_path, file_digest


def verify_control_role(root, manifest_path, expected_sha256):
    root = exact_path(root)
    manifest_path = exact_path(manifest_path)
    raw = manifest_path.read_bytes()
    if file_digest(manifest_path) != expected_sha256:
        raise SourceHold("control_role_manifest_pin")
    if sha256(raw).hexdigest() != expected_sha256:
        raise SourceHold("control_role_manifest_pin")
    manifest = json.loads(raw)
    if manifest["schema"] != "uif-stokowski-control-role-v1" or manifest["repository"] != "maldous/uif":
        raise SourceHold("control_role_identity")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        raise SourceHold("control_role_file_set")
    for name, pin in files.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or str(relative) != name or not re.fullmatch("[a-f0-9]{64}", pin)):
            raise SourceHold("control_role_file_binding")
        path = exact_path(root / name)
        if file_digest(path) != pin:
            raise SourceHold("control_role_file_pin")
    return tuple(sorted(files))
