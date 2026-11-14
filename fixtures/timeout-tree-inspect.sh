#!/bin/sh
set -eu
test -s work/timeout-tree
read -r started_at < work/timeout-start
printf 'TIMEOUT_START %s\n' "$started_at"
cat work/term-seen
printf '\n'
pid_state() {
    if stat=$(cat "/proc/$pid/stat" 2>/dev/null); then
        fields=${stat##*) }
        set -- $fields
        if [ "${20}" = "$started" ]; then
            printf '%s\n' "$1"
            return
        fi
    fi
    printf 'gone\n'
}
while read -r role pid parent group started; do
    target_group=$group
    state=$(pid_state)
    if [ "${WAIT_FOR_REAP:-0}" = 1 ] && [ "$state" = Z ]; then
        for attempt in 1 2 3; do
            sleep 1
            state=$(pid_state)
            [ "$state" = Z ] || break
        done
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
read -r observed_at remainder < /proc/uptime
printf 'TIMEOUT_OBSERVED %s\n' "$observed_at"
