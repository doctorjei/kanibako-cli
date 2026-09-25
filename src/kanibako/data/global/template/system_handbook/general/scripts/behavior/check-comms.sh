#!/usr/bin/env bash
# check-comms.sh — PostToolUse hook that monitors ~/channels/ for new messages.
#
# Install: run it IN PLACE from canon — do NOT copy it anywhere.  ~/.claude/hooks/
# was retired 2026-08-08c and must never be pointed at again.
#
# Add to ~/.claude/settings.json under "hooks.PostToolUse":
#   {
#     "matcher": "Bash|Read|Write|Edit|Glob|Grep|Agent",
#     "hooks": [
#       {
#         "type": "command",
#         "command": "~/canon/handbook/general/scripts/behavior/check-comms.sh",
#         "timeout": 5
#       }
#     ]
#   }
set -euo pipefail

INSTANCE="${KANIBAKO_NAME:-unknown}"
COMMS_DIR="$HOME/channels"
MAILBOX_DIR="$COMMS_DIR/inbox"
CHAT_DIR="$COMMS_DIR/chat"
BROADCAST="$CHAT_DIR/broadcast.md"

# This JSON on stdout is the hook's only voice to the user, which is why the failure
# trap below speaks through it too rather than through an exit status.
emit() {
    if command -v jq &>/dev/null; then
        jq -n --arg msg "$1" \
            '{"continue": true, "systemMessage": $msg}'
    else
        # Backslashes FIRST: reversing the two turns \" into \\" and ends the
        # JSON string early.  A trailing backslash is the reachable case.
        local escaped=${1//\\/\\\\}
        escaped=${escaped//\"/\\\"}
        echo "{\"continue\": true, \"systemMessage\": \"${escaped}\"}"
    fi
}

# ⚑ THIS HOOK MUST NEVER EXIT NON-ZERO WITH AN EMPTY STDERR.  The harness reads a
# non-zero, non-2 exit as "show the user stderr" — so an abort reads to them as broken
# tooling with no cause in it, on every tool call, for the life of the box.  Under
# `set -e` ANY command here can abort mid-script on an ordinary transient: $HOME/channels
# is typically a soft NFS mount, and a redirect to a full disk fails as readily.  The
# guarantee is therefore made once, by construction, instead of re-argued at each call
# site — and it is a DEGRADED report, never silence, because a monitor that stops
# monitoring without saying so is indistinguishable from one with nothing to report.
# ERR fires MID-SCRIPT, so it must carry what we had already found: an abort after
# the mail scan must not swallow the mail alert (${alerts:+...} is safe while alerts
# is still unset).  ``|| true`` because set -e is LIVE inside a trap handler -- a
# failing emit would skip the exit 0 and re-create the empty-stderr abort this trap
# exists to prevent.
trap 'rc=$?; trap - ERR; emit "${alerts:+$alerts | }check-comms.sh aborted (rc=${rc}, line ${LINENO}) — comms monitoring is DEGRADED for this box until it is fixed." || true; exit 0' ERR

cat > /dev/null

[[ -d "$COMMS_DIR" ]] || exit 0

STATE_DIR="/tmp/kanibako-comms-${INSTANCE}"
mkdir -p "$STATE_DIR" 2>/dev/null || true

MAIL_MARKER="$STATE_DIR/last-mail-check"
BCAST_MARKER="$STATE_DIR/last-bcast-check"
SCAN_STDERR="$STATE_DIR/last-mail-scan-stderr"

alerts=""
advance_marker=""

if [[ -d "$MAILBOX_DIR" ]]; then
    if [[ ! -f "$MAIL_MARKER" ]]; then
        # Backdate so messages arriving between sessions are detected
        touch -t 197001010000 "$MAIL_MARKER"
    fi
    # ⚑ `find` IS ALLOWED TO FAIL HERE, and its two failure signals travel separately:
    # redirecting stderr hides the diagnostic but not the exit status.  So empty output
    # means "no new mail" only when the scan also SUCCEEDED; on a partial read it means
    # "could not look", and the two must never collapse into one answer.
    scan_rc=0
    new_mail=$(find "$MAILBOX_DIR" -type f -newer "$MAIL_MARKER" ! -name '*.replied.*' \
        2>"$SCAN_STDERR") || scan_rc=$?
    if [[ -n "$new_mail" ]]; then
        count=$(echo "$new_mail" | wc -l)
        files=$(echo "$new_mail" | xargs -I{} basename {} | sort)
        alerts="NEW MAIL (${count}): ${files//$'\n'/, }"
    fi
    if (( scan_rc != 0 )); then
        # ⚑ Partial results are REAL — `find` prints what it could read and still exits
        # non-zero — so any mail found above is kept and reported.  The marker is
        # deliberately NOT advanced: a message in the part that could not be read is
        # already older than a fresh marker would be, so advancing it would hide that
        # message permanently.  Re-alerting until the fault is fixed is the price, and
        # it is the cheaper of the two.
        reason=$(head -n 1 "$SCAN_STDERR" 2>/dev/null) || reason=""
        alerts="${alerts:+$alerts | }MAIL SCAN INCOMPLETE (find rc=${scan_rc}):"
        alerts="$alerts ${reason:-no diagnostic} — part of $MAILBOX_DIR could not be read,"
        alerts="$alerts so mail there is NOT being reported."
    elif [[ -n "$new_mail" ]]; then
        # DEFERRED: the marker may only advance once the alert has actually been
        # emitted.  Advancing here would lose this mail for good if anything below
        # aborts first -- find -newer would never select it again.
        advance_marker=1
    fi
fi

if [[ -f "$BROADCAST" ]]; then
    current_hash=$(md5sum "$BROADCAST" 2>/dev/null | cut -d' ' -f1)
    last_hash=""
    [[ -f "$BCAST_MARKER" ]] && last_hash=$(cat "$BCAST_MARKER")
    if [[ "$current_hash" != "$last_hash" ]]; then
        if [[ -n "$last_hash" ]]; then
            alerts="${alerts:+$alerts | }NEW BROADCAST on $BROADCAST"
        fi
        echo "$current_hash" > "$BCAST_MARKER"
    fi
fi

if [[ -n "$alerts" ]]; then
    emit "$alerts"
fi

# ONLY now is forgetting this mail safe: it has been reported.
# ``|| true`` is load-bearing: NOTHING FALLIBLE MAY FOLLOW emit UNGUARDED.  The ERR
# trap emits too, so a failure here would put a SECOND JSON object on stdout after
# the first already succeeded -- and a failed touch is the safe way to be wrong
# anyway, since it only re-reports this mail next run.
if [[ -n "$advance_marker" ]]; then
    touch "$MAIL_MARKER" || true
fi

exit 0
