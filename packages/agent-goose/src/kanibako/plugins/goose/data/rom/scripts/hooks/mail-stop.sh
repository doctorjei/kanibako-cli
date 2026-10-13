#!/usr/bin/env bash
# mail-stop.sh — goose Stop hook: turns a check-comms.sh alert into a Stop block.
#
# Invoked in place from the charter (the kanibako goose plugin's hooks.json):
#     "command": "bash ~/canon/charter/agent/scripts/hooks/mail-stop.sh"
#
# goose discards PostToolUse hook output, and check-comms.sh advances its mail marker
# once it has emitted, so wiring it there would mark mail seen without showing it.  A
# Stop hook's {"decision":"block","reason":...} is the one hook output goose hands the
# model: the reason becomes a context message and the turn continues.
#
# ⚑ Never exit non-zero: goose logs the failure and allows the stop, and exit 2 would
# block with stderr as the reason.  Every path below ends in `exit 0`.
#
# A check-comms fault re-alerts on every run, and goose would otherwise block every
# turn end up to GOOSE_STOP_HOOK_BLOCK_CAP times.  So a repeat is not blocked again
# when all three hold: the same goose session already got this exact alert, and
# check-comms moved neither of its markers on this run (a moved marker means the
# alert carries something new — fresh mail, or a broadcast whose text matches the
# last one).  A clean check clears the memo.

input=$(cat)

state_dir="/tmp/kanibako-comms-${KANIBAKO_NAME:-unknown}"
mail_marker="$state_dir/last-mail-check"
bcast_marker="$state_dir/last-bcast-check"

# The markers check-comms.sh advances when it reports something new.
markers() {
    stat -c '%y' "$mail_marker" 2> /dev/null
    echo "|"
    cat "$bcast_marker" 2> /dev/null
}

before=$(markers)
out=$(~/canon/handbook/general/scripts/behavior/check-comms.sh < /dev/null 2>/dev/null) \
    || exit 0
after=$(markers)

# check-comms.sh prints pretty JSON through jq when it has jq, and one line of
# hand-escaped JSON otherwise; the same test picks the matching reader here.
if command -v jq > /dev/null 2>&1; then
    msg=$(printf '%s' "$out" | jq -r '.systemMessage // empty' 2>/dev/null) || msg=""
    [[ -n "$msg" ]] && reply=$(jq -nc --arg r "$msg" '{decision: "block", reason: $r}')
    session=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null) || session=""
else
    # The captured group is still a JSON string body, escapes intact.
    msg=$(printf '%s' "$out" \
        | sed -nE 's/.*"systemMessage": "(([^"\\]|\\.)*)".*/\1/p' | head -n 1)
    [[ -n "$msg" ]] && reply="{\"decision\": \"block\", \"reason\": \"$msg\"}"
    session=$(printf '%s' "$input" \
        | sed -nE 's/.*"session_id": *"(([^"\\]|\\.)*)".*/\1/p' | head -n 1)
fi
# The id names a file: anything outside a safe set is dropped.
session=${session//[^A-Za-z0-9_.-]/}
memo="$state_dir/goose-stop-last-${session:-none}"

if [[ -z "$msg" ]]; then
    rm -f "$state_dir"/goose-stop-last-* 2> /dev/null
    exit 0
fi

if [[ "$before" == "$after" && -f "$memo" ]] \
    && [[ "$(cat "$memo" 2> /dev/null)" == "$msg" ]]; then
    exit 0
fi
mkdir -p "$state_dir" 2> /dev/null
printf '%s' "$msg" > "$memo" 2> /dev/null
printf '%s\n' "$reply"
exit 0
