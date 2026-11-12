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

[ "$#" -eq 2 ] || exit 2
case "$1" in start|observe|cancel) ;; *) exit 2 ;; esac
case "$2" in ''|*[!a-f0-9]*) exit 2 ;; esac
[ "${#2}" -eq 64 ] || exit 2

for path in "$python" "$helper"; do
    case "$path" in *[!a-zA-Z0-9_./-]*|*//*|*/./*|*/../*|*/.|*/..|*/) exit 2 ;; esac
    case "$path" in /*) ;; *) exit 2 ;; esac
done
for digest in "$python_sha" "$helper_sha"; do
    case "$digest" in ''|*[!a-f0-9]*) exit 2 ;; esac
    [ "${#digest}" -eq 64 ] || exit 2
done
printf '%s  %s\n' "$python_sha" "$python" "$helper_sha" "$helper" |
    /usr/bin/sha256sum --check --status || exit 2
exec "$python" -I "$helper" "$@"
