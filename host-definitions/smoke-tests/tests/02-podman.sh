#!/usr/bin/env bash
# Test: rootless podman is functional.

if ! command -v podman &>/dev/null; then
    skip "podman is installed" "podman not found"
    skip "podman info succeeds" "podman not found"
    skip "storage driver is overlay" "podman not found"
    skip "podman can pull busybox" "podman not found"
    skip "podman can run a container" "podman not found"
    return 0
fi

ok "podman is installed"
check "podman info succeeds" podman info

# Storage driver
_driver=$(podman info --format '{{.Store.GraphDriverName}}' 2>/dev/null || echo "unknown")
if [[ "$_driver" == "overlay" ]]; then
    ok "storage driver is overlay"
else
    diag "storage driver: $_driver"
    skip "storage driver is overlay" "driver is $_driver (may be fine)"
fi

# Pull and run
if podman pull --quiet docker.io/library/busybox:latest >/dev/null 2>&1; then
    ok "podman can pull busybox"
else
    fail "podman can pull busybox"
fi

_output=$(podman run --rm busybox echo smoke-test-ok 2>&1)
if [[ "$_output" == *"smoke-test-ok"* ]]; then
    ok "podman can run a container"
else
    fail "podman can run a container" "output: $_output"
fi

# Clean up
podman rmi -f busybox >/dev/null 2>&1 || true
