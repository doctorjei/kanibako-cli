#!/usr/bin/env bash
# Test: container has network access.

if ! command -v kanibako &>/dev/null || ! podman info >/dev/null 2>&1; then
    skip "container DNS resolution" "kanibako or podman not available"
    skip "container internet access" "kanibako or podman not available"
    return 0
fi

_NET_DIR=$(mktemp -d /tmp/kanibako-net-XXXXXX)
_NET_NAME="net-test-$$"

_cleanup_net() {
    kanibako stop "$_NET_NAME" 2>/dev/null || true
    rm -rf "$_NET_DIR"
    kanibako box forget "$_NET_NAME" --force 2>/dev/null || true
}
trap '_cleanup_net' EXIT

kanibako init "$_NET_DIR" >/dev/null 2>&1 || { fail "networking setup (init)"; trap - EXIT; _cleanup_net; return 0; }
kanibako start "$_NET_NAME" --no-helpers >/dev/null 2>&1 || { fail "networking setup (start)"; trap - EXIT; _cleanup_net; return 0; }

# DNS resolution
if kanibako shell "$_NET_NAME" -- getent hosts github.com >/dev/null 2>&1; then
    ok "container DNS resolution"
else
    fail "container DNS resolution"
fi

# Internet connectivity
_http_code=$(kanibako shell "$_NET_NAME" -- curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://github.com 2>&1 || echo "000")
if [[ "$_http_code" =~ ^(200|301|302)$ ]]; then
    ok "container internet access"
else
    fail "container internet access" "HTTP status: $_http_code"
fi

trap - EXIT
_cleanup_net
