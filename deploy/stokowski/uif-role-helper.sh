#!/bin/sh
# Render these literals from the pinned release manifest before qualification.
set -eu
unset ENV BASH_ENV CDPATH PYTHONPATH PYTHONHOME
PATH=/usr/bin:/bin
export PATH
python='@UIF_ISOLATED_PYTHON@'
python_sha='@UIF_ISOLATED_PYTHON_SHA256@'
helper='@UIF_ROLE_HELPER_SOURCE@'
helper_sha='@UIF_ROLE_HELPER_SHA256@'
manifest='@UIF_ROLE_PACKAGE_MANIFEST@'
manifest_sha='@UIF_ROLE_PACKAGE_MANIFEST_SHA256@'

[ "$#" -eq 2 ] || exit 2
case "$1" in start|observe|cancel) ;; *) exit 2 ;; esac
case "$2" in ''|*[!a-f0-9]*) exit 2 ;; esac
[ "${#2}" -eq 64 ] || exit 2

for path in "$python" "$helper" "$manifest"; do
    case "$path" in *[!a-zA-Z0-9_./-]*|*//*|*/./*|*/../*|*/.|*/..|*/) exit 2 ;; esac
    case "$path" in /*) ;; *) exit 2 ;; esac
done
for digest in "$python_sha" "$helper_sha" "$manifest_sha"; do
    case "$digest" in ''|*[!a-f0-9]*) exit 2 ;; esac
    [ "${#digest}" -eq 64 ] || exit 2
done
printf '%s  %s\n' "$python_sha" "$python" "$helper_sha" "$helper" |
    /usr/bin/sha256sum --check --status || exit 2
exec "$python" -I -B -S -c '
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys

helper, helper_sha, manifest, manifest_sha, *args = sys.argv[1:]
helper, manifest = Path(helper), Path(manifest)
package = helper.parent
uid = os.geteuid()

def checked(path, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022
            or (not directory and (info.st_nlink != 1 or info.st_mode & 0o6000))):
        raise ValueError("UIF package custody")

def pinned(path, digest):
    checked(path)
    if not isinstance(digest, str) or sha256(data := path.read_bytes()).hexdigest() != digest:
        raise ValueError("UIF package pin")
    return data

def unique(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("UIF package duplicate key")
    return value

try:
    if (helper.name != "role_broker.py" or package.name != "uif"
            or any(path != path.resolve(strict=True) for path in (package, manifest))):
        raise ValueError("UIF package path")
    checked(package, directory=True)
    if uid == 0:
        for parent in set(package.parents) | set(manifest.parents):
            checked(parent, directory=True)
    record = json.loads(pinned(manifest, manifest_sha), object_pairs_hook=unique)
    if (not isinstance(record, dict) or set(record) != {"schema", "files"}
            or record["schema"] != "uif-role-package/1" or not isinstance(record["files"], dict)):
        raise ValueError("UIF package manifest")
    files = record["files"]
    if (files.get(helper.name) != helper_sha or "__init__.py" not in files
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name) for name in files)
            or set(files) != {path.name for path in package.iterdir()}):
        raise ValueError("UIF package file set")
    for name, digest in files.items():
        pinned(package / name, digest)
except (OSError, RuntimeError, ValueError):
    sys.exit(2)

spec = importlib.util.spec_from_file_location("uif", package / "__init__.py",
                                             submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules["uif"] = module
spec.loader.exec_module(module)
sys.argv = [str(helper), *args]
runpy.run_path(str(helper), run_name="__main__")
' "$helper" "$helper_sha" "$manifest" "$manifest_sha" "$@"
