#!/bin/sh
# Source template. Bind literal values in the reviewed installation artifact.
# Derived from maldous/offmenu 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e,
# deploy/stokowski/offmenu-coding-dropper.sh (SHA-256
# 64f8152a07d50cc2994f89f0956df2009aebc2394c25eeae4cb05fc3d0856646).
# Validate no caller-controlled arguments in the root phase.
unset ENV BASH_ENV CDPATH
uid='@UIF_SERVICE_UID@'
gid='@UIF_SERVICE_GID@'
guard='@UIF_GUARD_PATH@'

# Refuse an unqualified template before invoking setpriv.
case "$uid" in ''|0*|*[!0-9]*) exit 126 ;; esac
case "$gid" in ''|0*|*[!0-9]*) exit 126 ;; esac
case "$guard" in *[!a-zA-Z0-9_./-]*|*//*|*/./*|*/../*|*/.|*/..|*/) exit 126 ;; esac
case "$guard" in /*) ;; *) exit 126 ;; esac

exec /usr/bin/setpriv --reuid="$uid" --regid="$gid" --clear-groups \
    --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
    --no-new-privs --reset-env -- "$guard" "$@"
