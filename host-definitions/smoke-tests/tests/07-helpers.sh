#!/usr/bin/env bash
# Test: helper system (spawn, messaging, broadcast).

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "container starts with helpers" "kanibako or podman not available"
    skip "comms directory exists" "kanibako or podman not available"
    return 0
fi

_HELPER_DIR=$(mktemp -d /tmp/kanibako-helper-XXXXXX)
_HELPER_NAME="helper-test-$$"

_cleanup_helper() {
    kanibako stop "$_HELPER_NAME" 2>/dev/null || true
    rm -rf "$_HELPER_DIR"
    kanibako box forget "$_HELPER_NAME" --force 2>/dev/null || true
}
trap '_cleanup_helper' EXIT

kanibako init "$_HELPER_DIR" >/dev/null 2>&1 || { fail "helper setup (init)"; trap - EXIT; _cleanup_helper; return 0; }

# Start WITH helpers (default)
if kanibako start "$_HELPER_NAME" >/dev/null 2>&1; then
    ok "container starts with helpers"
else
    fail "container starts with helpers"
    trap - EXIT; _cleanup_helper
    return 0
fi

# Check comms directory exists inside container
if kanibako shell "$_HELPER_NAME" -- test -d /home/agent/comms 2>&1; then
    ok "comms directory exists"
else
    fail "comms directory exists"
fi

trap - EXIT
_cleanup_helper
