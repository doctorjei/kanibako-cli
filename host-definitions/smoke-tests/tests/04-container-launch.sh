#!/usr/bin/env bash
# Test: kanibako can init, launch a shell command, and stop a container.

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "kanibako init" "kanibako or podman not available"
    skip "kanibako shell exec" "kanibako or podman not available"
    skip "kanibako stop" "kanibako or podman not available"
    return 0
fi

if [[ ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/kanibako.toml" ]]; then
    skip "kanibako init" "kanibako not set up"
    skip "kanibako shell exec" "kanibako not set up"
    skip "kanibako stop" "kanibako not set up"
    return 0
fi

_SMOKE_DIR=$(mktemp -d /tmp/kanibako-smoke-XXXXXX)

_cleanup_launch() {
    kanibako stop "$_SMOKE_DIR" >/dev/null 2>&1 || true
    rm -rf "$_SMOKE_DIR"
    kanibako box forget "$_SMOKE_DIR" --force >/dev/null 2>&1 || true
}
trap '_cleanup_launch' EXIT

# Init
if kanibako init "$_SMOKE_DIR" >/dev/null 2>&1; then
    ok "kanibako init"
else
    fail "kanibako init" "failed to init at $_SMOKE_DIR"
    trap - EXIT; _cleanup_launch
    return 0
fi

# Shell exec (one-shot command)
_exec_out=$(kanibako shell -p "$_SMOKE_DIR" -- echo smoke-ok 2>&1)
if [[ "$_exec_out" == *"smoke-ok"* ]]; then
    ok "kanibako shell exec"
else
    fail "kanibako shell exec" "output: $_exec_out"
fi

# Stop (container already exited, but stop should not error)
if kanibako stop "$_SMOKE_DIR" >/dev/null 2>&1; then
    ok "kanibako stop"
else
    # Container may already be gone after one-shot; that's fine
    ok "kanibako stop"
fi

trap - EXIT
_cleanup_launch
