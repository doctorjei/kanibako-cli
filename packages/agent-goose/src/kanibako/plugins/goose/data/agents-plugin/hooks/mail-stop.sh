#!/usr/bin/env bash
# mail-stop.sh — goose Stop hook: turns a check-comms.sh alert into a Stop block.
#
# goose discards PostToolUse hook output, and check-comms.sh advances its mail marker
# once it has emitted, so wiring it there would mark mail seen without showing it.  A
# Stop hook's {"decision":"block","reason":...} is the one hook output goose hands the
# model: the reason becomes a context message and the turn continues.
#
# ⚑ Never exit non-zero: goose logs the failure and allows the stop, and exit 2 would
# block with stderr as the reason.  Every path below ends in `exit 0`.
#
# An alert identical to the last one this script blocked with is not blocked again
# until a clean check clears the memo: a check-comms fault re-alerts on every run, and
# goose would otherwise block every turn end up to GOOSE_STOP_HOOK_BLOCK_CAP times.

cat > /dev/null

out=$(~/canon/handbook/general/scripts/behavior/check-comms.sh < /dev/null 2>/dev/null) \
    || exit 0

memo_dir="/tmp/kanibako-comms-${KANIBAKO_NAME:-unknown}"
memo="$memo_dir/goose-stop-last"

# check-comms.sh prints pretty JSON through jq when it has jq, and one line of
# hand-escaped JSON otherwise; the same test picks the matching reader here.
if command -v jq > /dev/null 2>&1; then
    msg=$(printf '%s' "$out" | jq -r '.systemMessage // empty' 2>/dev/null) || msg=""
    [[ -n "$msg" ]] && reply=$(jq -nc --arg r "$msg" '{decision: "block", reason: $r}')
else
    # The captured group is still a JSON string body, escapes intact.
    msg=$(printf '%s' "$out" \
        | sed -nE 's/.*"systemMessage": "(([^"\\]|\\.)*)".*/\1/p' | head -n 1)
    [[ -n "$msg" ]] && reply="{\"decision\": \"block\", \"reason\": \"$msg\"}"
fi

if [[ -z "$msg" ]]; then
    rm -f "$memo" 2> /dev/null
    exit 0
fi

if [[ -f "$memo" ]] && [[ "$(cat "$memo" 2> /dev/null)" == "$msg" ]]; then
    exit 0
fi
mkdir -p "$memo_dir" 2> /dev/null
printf '%s' "$msg" > "$memo" 2> /dev/null
printf '%s\n' "$reply"
exit 0
