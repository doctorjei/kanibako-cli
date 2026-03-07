#!/usr/bin/env bash
# Test: files written via shell persist in the project workspace.

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "write marker file in container" "kanibako or podman not available"
    skip "marker file persists across runs" "kanibako or podman not available"
    return 0
fi

if [[ ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/kanibako.toml" ]]; then
    skip "write marker file in container" "kanibako not set up"
    skip "marker file persists across runs" "kanibako not set up"
    return 0
fi

_PERSIST_DIR=$(mktemp -d /tmp/kanibako-persist-XXXXXX)

_cleanup_persist() {
    rm -rf "$_PERSIST_DIR"
    kanibako box forget "$_PERSIST_DIR" --force 2>/dev/null || true
}
trap '_cleanup_persist' EXIT

kanibako init "$_PERSIST_DIR" >/dev/null 2>&1 || { fail "persistent-state setup (init)"; trap - EXIT; _cleanup_persist; return 0; }

# Write marker file via shell
if kanibako shell -p "$_PERSIST_DIR" -- touch /home/agent/workspace/.smoke-marker 2>/dev/null; then
    ok "write marker file in container"
else
    fail "write marker file in container"
    trap - EXIT; _cleanup_persist
    return 0
fi

# Verify marker persists (the workspace dir IS the project dir via bind mount)
if kanibako shell -p "$_PERSIST_DIR" -- test -f /home/agent/workspace/.smoke-marker 2>/dev/null; then
    ok "marker file persists across runs"
else
    fail "marker file persists across runs"
fi

trap - EXIT
_cleanup_persist
