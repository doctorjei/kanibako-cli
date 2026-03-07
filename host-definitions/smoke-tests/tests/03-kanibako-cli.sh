#!/usr/bin/env bash
# Test: kanibako CLI is installed and functional.

if ! command -v kanibako &>/dev/null; then
    skip "kanibako is installed" "kanibako not on PATH"
    skip "kanibako --version outputs version" "kanibako not on PATH"
    skip "kanibako --help exits 0" "kanibako not on PATH"
    skip "kanibako image list exits 0" "kanibako not on PATH"
    return 0
fi

ok "kanibako is installed"
check_output "kanibako --version outputs version" "kanibako" kanibako --version
check "kanibako --help exits 0" kanibako --help
# image list requires setup; skip if not configured
if [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/kanibako.toml" ]]; then
    check "kanibako image list exits 0" kanibako image list
else
    skip "kanibako image list exits 0" "kanibako not set up (run kanibako setup)"
fi
