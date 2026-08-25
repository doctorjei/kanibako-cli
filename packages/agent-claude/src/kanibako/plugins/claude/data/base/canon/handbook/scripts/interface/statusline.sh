#!/bin/bash
# Kanibako: Status Line (Context Usage & Cummulative Cost
#
# To (re-)install, add path to ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "~/canon/handbook/agent/scripts/interface/statusline.sh"
#   }

input=$(cat)

# Persist raw status for agent self-monitoring
echo "$input" > ~/.claude/context-status.json

CTX_SIZE=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')

# Context occupancy is the EXACT sum of the three input-side token fields the
# harness already reports. Do NOT derive it from the integer used_percentage:
# at a 1M window each 1% is 10,000 tokens, so that route rounds away up to 10k.
# output_tokens is excluded deliberately -- it is generated, not yet occupying
# this window.
USED_TOKENS=$(echo "$input" | jq -r '
    (.context_window.current_usage // {})
    | ((.input_tokens // 0)
       + (.cache_creation_input_tokens // 0)
       + (.cache_read_input_tokens // 0))')
[ -n "$USED_TOKENS" ] || USED_TOKENS=0

CTX_K=$(( CTX_SIZE / 1000 ))
if [ "$CTX_SIZE" -gt 0 ] 2>/dev/null && [ "$USED_TOKENS" -gt 0 ] 2>/dev/null; then
    ADJ_PCT=$(( USED_TOKENS * 100 / CTX_SIZE ))
    USED_K=$(( (USED_TOKENS + 500) / 1000 ))
    # Two-decimal precision: tokens in K and percentage
    USED_K_DEC=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS / 1000}")
    ADJ_PCT_DEC=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS * 100 / $CTX_SIZE}")
    HAVE_DATA=1
else
    USED_TOKENS=0
    ADJ_PCT=0
    USED_K=0
    USED_K_DEC="0.00"
    ADJ_PCT_DEC="0.00"
    HAVE_DATA=0
fi

# Persist adjusted context usage for auto-loop reads
# Format: "<tokens_k> <percentage>" e.g. "45.01 22.50"
echo "$USED_K_DEC $ADJ_PCT_DEC" > ~/.claude/context-usage.txt

# Format cost to 2 decimal places
COST_FMT=$(printf '%.2f' "$COST" 2>/dev/null || echo "$COST")

# Color: green < 60%, yellow 60-79%, red >= 80%
if [ "$ADJ_PCT" -ge 80 ]; then
    COLOR='\033[31m'
elif [ "$ADJ_PCT" -ge 60 ]; then
    COLOR='\033[33m'
else
    COLOR='\033[32m'
fi
RESET='\033[0m'

# With no usage data yet, say so. A constant rendered as a reading is
# indistinguishable from a real one.
if [ "$HAVE_DATA" -eq 0 ]; then
    echo -e "${COLOR}—/${CTX_K}k${RESET} \$${COST_FMT}"
else
    echo -e "${COLOR}${USED_K}k/${CTX_K}k (${ADJ_PCT}%)${RESET} \$${COST_FMT}"
fi
