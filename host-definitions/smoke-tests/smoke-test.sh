#!/usr/bin/env bash
# Smoke test runner for kanibako host deployments.
# Usage:
#   ./smoke-test.sh          # run all tests
#   ./smoke-test.sh 01 03    # run specific tests
#   ./smoke-test.sh --list   # list available tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/tap.sh
source "$SCRIPT_DIR/lib/tap.sh"

TEST_DIR="$SCRIPT_DIR/tests"

# List mode
if [[ "${1:-}" == "--list" ]]; then
    for f in "$TEST_DIR"/[0-9]*.sh; do
        [[ -f "$f" ]] || continue
        basename "$f"
    done
    exit 0
fi

# Collect test files (all or filtered)
tests=()
if [[ $# -eq 0 ]]; then
    for f in "$TEST_DIR"/[0-9]*.sh; do
        [[ -f "$f" ]] && tests+=("$f")
    done
else
    for pattern in "$@"; do
        for f in "$TEST_DIR"/${pattern}*.sh; do
            [[ -f "$f" ]] && tests+=("$f")
        done
    done
fi

if [[ ${#tests[@]} -eq 0 ]]; then
    echo "No test files found."
    exit 1
fi

# Run each test file
for test_file in "${tests[@]}"; do
    name="$(basename "$test_file")"
    printf '\n%s=== %s ===%s\n' "$_C_BOLD" "$name" "$_C_RESET"
    source "$test_file"
done

# Print summary and exit
tap_summary
