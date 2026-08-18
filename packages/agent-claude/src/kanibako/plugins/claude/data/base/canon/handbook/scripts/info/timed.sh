# timed.sh — sourceable timing emitter for kanibako e2e/integration runs.
#
# Captures START + END wall-clock spans into $KANI_TIMING_LOG (JSONL) so the
# GAPS between ops are computable.  KEEP TESTING AND CODING STRICTLY SEPARATE:
# this emitter only ever writes category=infra (L1 env / L2 setup / L3
# container) spans; the pytest plugin emits testing spans; the analyzer parses
# coding spans from transcripts.  Never sum them into one total.
#
# Usage (source it first):
#   export KANI_TIMING_LOG=~/canon/workbook/temp/testing/timings/runs/$(date -u +%Y%m%dT%H%M%SZ).jsonl
#   export KANI_RUN_ID=my-e2e-run
#   source ~/canon/handbook/agent/scripts/info/timed.sh
#   timed infra env   "seadog create" -- seadog create --image kanibako-lxc --ttl 1h
#   timed infra setup "git clone"     -- ssh -i "$KEY" agent@"$IP" 'git clone ...'
#   timed_event infra env "seadog destroy" start
#   ...
#   timed_event infra env "seadog destroy" end 0
#
# Schema (ONE JSON object per line):
#   {"run_id":"<$KANI_RUN_ID or unknown>","ts_wall":<float unix s>,
#    "phase":"start"|"end","category":"infra|testing|coding",
#    "level":"env|setup|container|prep|test|agent","label":"<str>",
#    "rc":<int|null>}
#
# NO-OP SAFETY: every function early-returns if $KANI_TIMING_LOG is unset, so
# sourcing this in a normal shell adds zero behavior.  Safe to source in bash.

# Emit ONE JSONL record.  Args: category level label phase [rc]
# rc empty -> null.  Internal; prefer `timed` / `timed_event`.
_timed_emit() {
    [ -z "${KANI_TIMING_LOG:-}" ] && return 0
    local category="$1" level="$2" label="$3" phase="$4" rc="${5:-}"
    local run_id="${KANI_RUN_ID:-unknown}"
    local ts
    ts=$(date +%s.%N)
    local rc_json
    if [ -z "$rc" ]; then
        rc_json="null"
    else
        rc_json="$rc"
    fi
    # Escape backslashes and double-quotes in the string fields so labels with
    # quotes/paths produce valid JSON.
    local esc_run esc_label
    esc_run=$(printf '%s' "$run_id" | sed 's/\\/\\\\/g; s/"/\\"/g')
    esc_label=$(printf '%s' "$label" | sed 's/\\/\\\\/g; s/"/\\"/g')
    # Append atomically-ish (single printf line).  mkdir -p the log's dir once.
    local dir
    dir=$(dirname -- "$KANI_TIMING_LOG")
    [ -d "$dir" ] || mkdir -p "$dir" 2>/dev/null
    printf '{"run_id":"%s","ts_wall":%s,"phase":"%s","category":"%s","level":"%s","label":"%s","rc":%s}\n' \
        "$esc_run" "$ts" "$phase" "$category" "$level" "$esc_label" "$rc_json" \
        >>"$KANI_TIMING_LOG"
}

# timed <category> <level> <label> -- <cmd...>
# Emits a start span, runs the command, emits an end span carrying its rc and
# returns that rc.  No-op wrapper (still runs the cmd) if KANI_TIMING_LOG unset.
timed() {
    local category="$1" level="$2" label="$3"
    shift 3
    if [ "${1:-}" = "--" ]; then
        shift
    fi
    if [ -z "${KANI_TIMING_LOG:-}" ]; then
        # Inert: just run the command transparently.
        "$@"
        return $?
    fi
    _timed_emit "$category" "$level" "$label" "start"
    "$@"
    local rc=$?
    _timed_emit "$category" "$level" "$label" "end" "$rc"
    return $rc
}

# timed_event <category> <level> <label> <phase> [rc]
# One-shot mark (e.g. bracketing an op you can't wrap in a single cmd).
timed_event() {
    [ -z "${KANI_TIMING_LOG:-}" ] && return 0
    _timed_emit "$1" "$2" "$3" "$4" "${5:-}"
}
