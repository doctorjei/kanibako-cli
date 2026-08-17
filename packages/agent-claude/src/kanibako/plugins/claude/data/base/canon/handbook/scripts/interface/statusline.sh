#!/bin/bash
# Kanibako: Status Line (Context Usage & Cummulative Cost
#
# To (re-)install, add path to ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "~/canon/handbook/scripts/hooks/statusline.sh"
#   }

input=$(cat)

# Persist raw status for agent self-monitoring
echo "$input" > ~/.claude/context-status.json

USED_PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
CTX_SIZE=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')

# Compact buffer (3k tokens) is not included in used_percentage.
# Add it back for a more accurate estimate.
COMPACT_BUFFER=3000

# Derive used tokens from percentage and window size
if [ "$CTX_SIZE" -gt 0 ] 2>/dev/null; then
    USED_TOKENS=$(( USED_PCT * CTX_SIZE / 100 + COMPACT_BUFFER ))
    ADJ_PCT=$(( USED_TOKENS * 100 / CTX_SIZE ))
    USED_K=$(( (USED_TOKENS + 500) / 1000 ))
    CTX_K=$(( CTX_SIZE / 1000 ))
    # Two-decimal precision: tokens in K and percentage
    USED_K_DEC=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS / 1000}")
    ADJ_PCT_DEC=$(awk "BEGIN {printf \"%.2f\", $USED_TOKENS * 100 / $CTX_SIZE}")
else
    USED_TOKENS=0
    ADJ_PCT=0
    USED_K=0
    CTX_K=0
    USED_K_DEC="0.00"
    ADJ_PCT_DEC="0.00"
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

echo -e "${COLOR}${USED_K}k/${CTX_K}k (${ADJ_PCT}%)${RESET} \$${COST_FMT}"
