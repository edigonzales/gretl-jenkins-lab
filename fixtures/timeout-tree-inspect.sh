#!/bin/sh
set -eu
test -s work/timeout-tree
cat work/term-seen
printf '\n'
while read -r role pid parent group started; do
    target_group=$group
    state=gone
    if stat=$(cat "/proc/$pid/stat" 2>/dev/null); then
        fields=${stat##*) }
        set -- $fields
        if [ "${20}" = "$started" ]; then state=$1; fi
    fi
    printf 'TIMEOUT_TREE %s %s %s %s %s %s\n' "$role" "$pid" "$parent" "$group" "$started" "$state"
done < work/timeout-tree

# Include the timeout wrapper and any other surviving member of the group.
for file in /proc/[0-9]*/stat; do
    stat=$(cat "$file" 2>/dev/null) || continue
    fields=${stat##*) }
    set -- $fields
    if [ "$3" = "$target_group" ] && [ "$1" != Z ]; then
        printf 'TIMEOUT_GROUP_SURVIVOR %s %s\n' "$file" "$1"
        exit 1
    fi
done
printf 'TIMEOUT_GROUP_STOPPED %s\n' "$target_group"
