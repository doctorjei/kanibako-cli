#!/usr/bin/env bash
# Test: files persist across container stop/start cycles.

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "write marker file in container" "kanibako or podman not available"
    skip "marker file persists after restart" "kanibako or podman not available"
    return 0
fi

_PERSIST_DIR=$(mktemp -d /tmp/kanibako-persist-XXXXXX)
_PERSIST_NAME="persist-test-$$"

_cleanup_persist() {
    kanibako stop "$_PERSIST_NAME" 2>/dev/null || true
    rm -rf "$_PERSIST_DIR"
    kanibako box forget "$_PERSIST_NAME" --force 2>/dev/null || true
}
trap '_cleanup_persist' EXIT

kanibako init "$_PERSIST_DIR" >/dev/null 2>&1 || { fail "persistent-state setup (init)"; trap - EXIT; _cleanup_persist; return 0; }
kanibako start "$_PERSIST_NAME" --no-helpers >/dev/null 2>&1 || { fail "persistent-state setup (start)"; trap - EXIT; _cleanup_persist; return 0; }

# Write marker file
if kanibako shell "$_PERSIST_NAME" -- touch /home/agent/workspace/.smoke-marker 2>&1; then
    ok "write marker file in container"
else
    fail "write marker file in container"
    trap - EXIT; _cleanup_persist
    return 0
fi

# Stop and restart
kanibako stop "$_PERSIST_NAME" >/dev/null 2>&1
kanibako start "$_PERSIST_NAME" --no-helpers >/dev/null 2>&1 || { fail "persistent-state restart"; trap - EXIT; _cleanup_persist; return 0; }

# Check marker persists
if kanibako shell "$_PERSIST_NAME" -- test -f /home/agent/workspace/.smoke-marker 2>&1; then
    ok "marker file persists after restart"
else
    fail "marker file persists after restart"
fi

trap - EXIT
_cleanup_persist
