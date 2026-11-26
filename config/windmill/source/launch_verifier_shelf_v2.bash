#!/usr/bin/bash
# Run these exact reviewed bytes directly in the trusted root maintenance session.
# The staged Python entry is data until this launcher verifies it.
set -euo pipefail
export LC_ALL=C
[[ $# == 1 && $1 =~ ^[0-9a-f]{64}$ ]] || exit 64
[[ $EUID == 0 && $(/usr/bin/id -ru) == 0 ]] || exit 77
leaf=/usr/local/lib/uif-maintenance/verifier_shelf_provision_v2.py
for parent in / /usr /usr/local /usr/local/lib /usr/local/lib/uif-maintenance; do
    [[ ! -L $parent && -d $parent ]] || exit 77
    read -r owner mode < <(/usr/bin/stat -c '%u %a' -- "$parent")
    [[ $owner == 0 && $mode =~ ^[0-7]{3,4}$ ]] || exit 77
    (( (8#$mode & 022) == 0 )) || exit 77
done
[[ ! -L $leaf && -f $leaf ]] || exit 77
exec 9< "$leaf"
# Compare the descriptor, not a second open of the pathname. Root-only ancestors
# and no other rollout writer fence renames before this descriptor is obtained.
descriptor=/proc/self/fd/9
[[ -f $descriptor ]] || exit 77
read -r owner mode links < <(/usr/bin/stat -Lc '%u %a %h' -- "$descriptor")
[[ $owner == 0 && $links == 1 && $mode =~ ^[0-7]{3,4}$ ]] || exit 77
(( (8#$mode & 022) == 0 )) || exit 77
read -r observed _ < <(/usr/bin/sha256sum "$descriptor")
[[ $observed == defb6bfaea95f0d4d76ea8b1bde4f0877fb438c1999e57cd8f5f83b7d9f48193 ]] || exit 77
exec /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C \
    /usr/bin/python3.12 -I -S -B "$descriptor" offmenu-v2 "$1"
