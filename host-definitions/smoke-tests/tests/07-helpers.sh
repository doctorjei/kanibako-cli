#!/usr/bin/env bash
# Test: helper system (comms directory inside container).

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "comms directory exists in container" "kanibako or podman not available"
    return 0
fi

if [[ ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/kanibako.toml" ]]; then
    skip "comms directory exists in container" "kanibako not set up"
    return 0
fi

_HELPER_DIR=$(mktemp -d /tmp/kanibako-helper-XXXXXX)

_cleanup_helper() {
    rm -rf "$_HELPER_DIR"
    kanibako box forget "$_HELPER_DIR" --force >/dev/null 2>&1 || true
}
trap '_cleanup_helper' EXIT

kanibako init "$_HELPER_DIR" >/dev/null 2>&1 || { fail "helper setup (init)"; trap - EXIT; _cleanup_helper; return 0; }

# Check comms directory exists inside container
if kanibako shell -p "$_HELPER_DIR" -- test -d /home/agent/comms 2>/dev/null; then
    ok "comms directory exists in container"
else
    fail "comms directory exists in container"
fi

trap - EXIT
_cleanup_helper
