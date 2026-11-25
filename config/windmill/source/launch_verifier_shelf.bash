#!/usr/bin/bash
# Run these exact reviewed bytes directly in the trusted root maintenance session.
# The staged Python entry is data until this launcher verifies it.
set -euo pipefail
export LC_ALL=C
[[ $# == 1 && $1 =~ ^[0-9a-f]{64}$ ]] || exit 64
[[ $EUID == 0 && $(/usr/bin/id -ru) == 0 ]] || exit 77
leaf=/usr/local/lib/uif-maintenance/verifier_shelf_provision.py
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
[[ $observed == e21a51e53b78ae2bcdc4c73fb0dac67119e4e1be0a0fc50a592daac8febbf838 ]] || exit 77
exec /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C \
    /usr/bin/python3.12 -I -S -B "$descriptor" offmenu "$1"
