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
BROADCAST="$CHAT_DIR/broadcast.log"

cat > /dev/null

[[ -d "$COMMS_DIR" ]] || exit 0

STATE_DIR="/tmp/kanibako-comms-${INSTANCE}"
mkdir -p "$STATE_DIR" 2>/dev/null || true

MAIL_MARKER="$STATE_DIR/last-mail-check"
BCAST_MARKER="$STATE_DIR/last-bcast-check"

alerts=""

if [[ -d "$MAILBOX_DIR" ]]; then
    if [[ ! -f "$MAIL_MARKER" ]]; then
        # Backdate so messages arriving between sessions are detected
        touch -t 197001010000 "$MAIL_MARKER"
    fi
    new_mail=$(find "$MAILBOX_DIR" -type f -newer "$MAIL_MARKER" ! -name '*.replied.*' 2>/dev/null)
    if [[ -n "$new_mail" ]]; then
        count=$(echo "$new_mail" | wc -l)
        files=$(echo "$new_mail" | xargs -I{} basename {} | sort)
        alerts="NEW MAIL (${count}): ${files//$'\n'/, }"
        touch "$MAIL_MARKER"
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
    if command -v jq &>/dev/null; then
        jq -n --arg msg "$alerts" \
            '{"continue": true, "systemMessage": $msg}'
    else
        escaped=$(echo "$alerts" | sed 's/"/\\"/g')
        echo "{\"continue\": true, \"systemMessage\": \"${escaped}\"}"
    fi
fi

exit 0
