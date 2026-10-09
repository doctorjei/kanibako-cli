#!/bin/bash
# Kanibako: Status Line (Context Usage & Cummulative Cost
#
# To (re-)install, add path to ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "~/canon/handbook/agent/scripts/interface/statusline.sh"
#   }

KIBI_MODE=0 # Use 1024-based prefixes? (Claude uses metric/decimal.)
COST_MODE=0 # Display approximate Anthropic dollar cost?
MARK_HELD=1 # Mark a held reading (so it's clear that it is approximate)?
STATE_DIR="$HOME/.claude/context-lastvalid" # Holds last valid context size

TOKEN_LAG=1536 # Approximate number of tokens yet-to-be-reported (empiricall)
CAUTION_PCT=75
DANGER_PCT=87

# Function to parse incoming data via jq; normalizes values to numbers.
JQ_FUNCTION='
    def num: if . == null then 0 elif type == "number" then . else 0 end;
    [ (.session_id // "")
    , (.context_window.context_window_size | num)
    , (.cost.total_cost_usd | num)
    , ((.context_window.current_usage // {}) | .input_tokens | num)
    , ((.context_window.current_usage // {}) | .cache_creation_input_tokens | num)
    , ((.context_window.current_usage // {}) | .cache_read_input_tokens | num)
    ] | @tsv
'

# Renders value / divisor with sig. digits; e.g., fmt_sig(12345,1000,4) > 123.5
fmt_sig() {
    local value=$1 divisor=$2 sig=$3 q round_q dp
    q=$((value / divisor))
    round_q=$(((value + divisor / 2) / divisor))

    # Determine remaining significant digits needed, if any
    local digits=1 limit=10
    while [ "$q" -ge "$limit" ] && [ "$digits" -lt "$sig" ]; do
        digits=$((digits + 1))
        limit=$((limit * 10))
    done
    dp=$((sig - digits))

    # Construct the formatted value
    if [ "$dp" -eq 0 ]; then
        printf '%s' "$((round_q / 1))"
    else
        awk -v v="$value" -v d="$divisor" -v p="$dp" 'BEGIN { printf "%." p "f", v / d }'
    fi
}

# Capture input & persist raw data agent self-monitoring, then parse with jq.
input=$(cat)
echo "$input" > ~/.claude/context-status.json
PARSED=$(echo "$input" | jq -r "$JQ_FUNCTION" 2>/dev/null)

# If payload parsing fails, use a fallback status line.
if [ -z "$PARSED" ]; then
    echo -e '\033[33m[???/???]\033[0m (??%)'
    exit 0
fi

# Read parsed data into script variables; add input-side token counts together.
IFS=$'\t' read -r SESSION_ID CTX_SIZE COST IN_T CC_T CR_T <<<"$PARSED"
USED_TOKENS=$(awk "BEGIN { printf \"%d\", ${IN_T:-0} + ${CC_T:-0} + ${CR_T:-0} + ${TOKEN_LAG} }")

# Some models/servers emit spurious zeros; hold previous good value for display
if [ -n "$SESSION_ID" ]; then
    GOOD_FILE="$STATE_DIR/$SESSION_ID"
    if [ "$USED_TOKENS" -gt 0 ] 2>/dev/null; then
        mkdir -p "$STATE_DIR" 2>/dev/null
        echo "$USED_TOKENS" >"$GOOD_FILE" 2>/dev/null
        find "$STATE_DIR" -type f -mtime +7 -delete 2>/dev/null || true # Prune
    elif [ -f "$GOOD_FILE" ]; then
        PREV=$(head -n 1 "$GOOD_FILE" 2>/dev/null | tr -dc '0-9')
        if [ -n "$PREV" ] && [ "$PREV" -gt 0 ] 2>/dev/null; then
            USED_TOKENS=$PREV
            HELD=1
        fi
    fi
fi

# Set defaults for values that could remain unset.
HELD=${HELD:-0}
HAVE_DATA=${HAVE_DATA:-0}
PCT_OUT=${PCT_OUT:-0}
USED_HELD=${USED_HELD:-0.00}
PCT_HELD=${PCT_HELD:-0.00}
COST=${COST:-0}
CTX_SIZE=${CTX_SIZE:-0}

# Bracket context size to be non-negative.
if [ "$CTX_SIZE" -le 0 ]; then
    CTX_SIZE=0
fi

# Determine the units and divider (1000 vs 1024)
if [ "$KIBI_MODE" -eq 0 ]; then
    DIVISOR=1000
    UNIT="Tok"
else
    DIVISOR=1024
    UNIT="iTok"
fi
M_DIVISOR=$((DIVISOR * DIVISOR))

# Format numbers for recording and output.
if [ "$CTX_SIZE" -gt 0 ] 2>/dev/null; then
    if [ "$USED_TOKENS" -gt 0 ] 2>/dev/null; then
        USED_HELD=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS / $DIVISOR}")
        PCT_HELD=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS * 100 / $CTX_SIZE}")
        HAVE_DATA=1
    else
        USED_TOKENS=0
        USED_HELD="0.00"
        PCT_HELD="0.00"
        HAVE_DATA=0
    fi

    # Percentage to three significant digits, clamped at 100.
    PCT_OUT=$((USED_TOKENS * 100 / CTX_SIZE))
    if [ "$PCT_OUT" -ge 100 ]; then
        PCT_OUT="100"
    else
        PCT_OUT=$(fmt_sig $((USED_TOKENS * 100)) "$CTX_SIZE" 3)
    fi

    # The unit comes from the WINDOW, never from the used value.
    if [ "$CTX_SIZE" -ge "$M_DIVISOR" ]; then
        USED_OUT=$(fmt_sig "$USED_TOKENS" "$M_DIVISOR" 4)
        MAX_OUT="$(fmt_sig "$CTX_SIZE" "$M_DIVISOR" 4) M${UNIT}"
    elif [ "$CTX_SIZE" -ge "$DIVISOR" ]; then
        USED_OUT=$(fmt_sig "$USED_TOKENS" "$DIVISOR" 4)
        MAX_OUT="$(fmt_sig "$CTX_SIZE" "$DIVISOR" 4) k${UNIT}"
    else
        USED_OUT="${USED_TOKENS}"
        MAX_OUT="${CTX_SIZE} tok"
    fi

# Mark no-data case clearly.
else
    PCT_OUT=0
    MAX_OUT="???"
    USED_OUT="???"
fi

# Format cost to 2 decimal places (if active)
if [ "$COST_MODE" -eq 0 ]; then
    COST_FMT=""
else
    COST_FMT=$(printf '($%.2f)' "$COST" 2>/dev/null || echo "(\$$COST)")
fi

# Color: green below CAUTION_PCT, yellow from CAUTION_PCT, red from DANGER_PCT
if [ "${PCT_OUT%.*}" -ge "${DANGER_PCT}" ]; then
    COLOR='\033[31m'
elif [ "${PCT_OUT%.*}" -ge "${CAUTION_PCT}" ]; then
    COLOR='\033[33m'
else
    COLOR='\033[32m'
fi
RESET='\033[0m'

# MARK_HELD=1 flags a carried-over number. On by default.
if [ "$HAVE_DATA" -eq 0 ]; then
    USED_OUT=""
    PCT_OUT="?"

    CTX_OPEN="[?"
else
    if [ "$MARK_HELD" -eq 1 ] && [ "$HELD" -eq 1 ]; then
        CTX_OPEN="[~"
    else
        CTX_OPEN=" ["
    fi
fi
CTX_CLOSE="]"

# Persist context usage; format: "<tokens_k> <percentage>" e.g. "45.01 22.50"
echo "$USED_HELD $PCT_HELD" > ~/.claude/context-usage.txt

# Emit final status line string.
echo -e "${COLOR}${CTX_OPEN}${USED_OUT}/${MAX_OUT}${CTX_CLOSE}${RESET} (${PCT_OUT}%) ${COST_FMT}"
