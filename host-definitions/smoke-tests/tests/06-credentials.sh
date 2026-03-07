#!/usr/bin/env bash
# Test: credential flow works (target-dependent).

if ! command -v kanibako &>/dev/null; then
    skip "agent plugin installed" "kanibako not on PATH"
    skip "credential check path exists" "kanibako not on PATH"
    return 0
fi

# Check if any agent plugin is installed (not just no_agent)
_image_out=$(kanibako image list 2>&1 || true)
if echo "$_image_out" | grep -qi "claude\|plugin"; then
    ok "agent plugin installed"
else
    skip "agent plugin installed" "no agent plugin detected"
    skip "credential check path exists" "no agent plugin"
    return 0
fi

# Claude-specific: check ~/.claude/ exists on host
if [[ -d "${HOME}/.claude" ]]; then
    ok "credential check path exists"
else
    skip "credential check path exists" "~/.claude not found (may need auth first)"
fi
