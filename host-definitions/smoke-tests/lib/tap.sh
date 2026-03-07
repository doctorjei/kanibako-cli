#!/usr/bin/env bash
# TAP (Test Anything Protocol) helper library for smoke tests.
# Source this file; do not execute directly.

_TAP_PASS=0
_TAP_FAIL=0
_TAP_SKIP=0
_TAP_NUM=0

# Detect color support
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    _C_GREEN=$'\033[32m'
    _C_RED=$'\033[31m'
    _C_YELLOW=$'\033[33m'
    _C_CYAN=$'\033[36m'
    _C_RESET=$'\033[0m'
    _C_BOLD=$'\033[1m'
else
    _C_GREEN="" _C_RED="" _C_YELLOW="" _C_CYAN="" _C_RESET="" _C_BOLD=""
fi

ok() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_PASS=$((_TAP_PASS + 1))
    printf '%sok %d - %s%s\n' "$_C_GREEN" "$_TAP_NUM" "$1" "$_C_RESET"
}

fail() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_FAIL=$((_TAP_FAIL + 1))
    printf '%snot ok %d - %s%s\n' "$_C_RED" "$_TAP_NUM" "$1" "$_C_RESET"
    if [[ -n "${2:-}" ]]; then
        diag "$2"
    fi
}

skip() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_SKIP=$((_TAP_SKIP + 1))
    printf '%sok %d - %s # SKIP %s%s\n' "$_C_YELLOW" "$_TAP_NUM" "$1" "${2:-}" "$_C_RESET"
}

diag() {
    printf '%s# %s%s\n' "$_C_CYAN" "$1" "$_C_RESET"
}

# Run a command and call ok/fail based on exit code.
# Usage: check "description" command arg1 arg2 ...
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        ok "$desc"
    else
        fail "$desc" "command failed: $*"
    fi
}

# Run a command and check that stdout contains a substring.
# Usage: check_output "description" "expected_substring" command arg1 ...
check_output() {
    local desc="$1" expected="$2"; shift 2
    local output
    if output=$("$@" 2>&1); then
        if echo "$output" | grep -qF "$expected"; then
            ok "$desc"
        else
            fail "$desc" "expected '$expected' in output, got: $output"
        fi
    else
        fail "$desc" "command failed (rc=$?): $*"
    fi
}

tap_summary() {
    local total=$((_TAP_PASS + _TAP_FAIL + _TAP_SKIP))
    echo ""
    printf '%s1..%d%s\n' "$_C_BOLD" "$total" "$_C_RESET"
    printf '# pass: %s%d%s  fail: %s%d%s  skip: %s%d%s\n' \
        "$_C_GREEN" "$_TAP_PASS" "$_C_RESET" \
        "$_C_RED" "$_TAP_FAIL" "$_C_RESET" \
        "$_C_YELLOW" "$_TAP_SKIP" "$_C_RESET"
    if [[ $_TAP_FAIL -gt 0 ]]; then
        printf '%sFAILED%s\n' "$_C_RED" "$_C_RESET"
        return 1
    else
        printf '%sALL PASSED%s\n' "$_C_GREEN" "$_C_RESET"
        return 0
    fi
}
