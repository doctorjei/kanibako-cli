#!/usr/bin/env bash
# Test: kanibako can init, start, exec into, and stop a container.

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "kanibako init" "kanibako or podman not available"
    skip "kanibako start" "kanibako or podman not available"
    skip "kanibako shell exec" "kanibako or podman not available"
    skip "kanibako stop" "kanibako or podman not available"
    return 0
fi

_SMOKE_DIR=$(mktemp -d /tmp/kanibako-smoke-XXXXXX)
_SMOKE_NAME="smoke-test-$$"

_cleanup_launch() {
    kanibako stop "$_SMOKE_NAME" 2>/dev/null || true
    rm -rf "$_SMOKE_DIR"
    kanibako box forget "$_SMOKE_NAME" --force 2>/dev/null || true
}
trap '_cleanup_launch' EXIT

# Init
if kanibako init "$_SMOKE_DIR" 2>&1; then
    ok "kanibako init"
else
    fail "kanibako init" "failed to init at $_SMOKE_DIR"
    trap - EXIT; _cleanup_launch
    return 0
fi

# Start (no-agent, non-interactive, detached)
if kanibako start "$_SMOKE_NAME" --no-helpers 2>&1; then
    ok "kanibako start"
else
    fail "kanibako start" "failed to start $_SMOKE_NAME"
    trap - EXIT; _cleanup_launch
    return 0
fi

# Exec
_exec_out=$(kanibako shell "$_SMOKE_NAME" -- echo smoke-ok 2>&1)
if [[ "$_exec_out" == *"smoke-ok"* ]]; then
    ok "kanibako shell exec"
else
    fail "kanibako shell exec" "output: $_exec_out"
fi

# Stop
if kanibako stop "$_SMOKE_NAME" 2>&1; then
    ok "kanibako stop"
else
    fail "kanibako stop"
fi

trap - EXIT
_cleanup_launch
