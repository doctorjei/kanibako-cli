#!/usr/bin/env bash
# helper-init.sh — Default helper initialization script (kanibako)
#
# This script is copied into every helper's playbook/scripts/ directory
# by the parent agent.  It runs inside the helper container at startup.
#
# The parent creates the directory structure (vault, workspace, playbook,
# peers, broadcast channels) before launching the helper.  This script
# handles registration with the hub and additional bootstrap.
#
# Parents can replace this with a custom version in their own
# playbook/scripts/helper-init.sh — kanibako will use the parent's
# version if it exists, falling back to this bundled default.
#
# Usage: helper-init.sh [HELPER_NUM]
#   HELPER_NUM — this helper's global agent number

set -euo pipefail

HELPER_NUM="${1:-unknown}"
SOCKET_PATH="$HOME/.kanibako/helper.sock"

# Register with the hub if the socket exists
if [ -S "$SOCKET_PATH" ]; then
    # Use kanibako helper client to register (if available)
    if command -v kanibako >/dev/null 2>&1; then
        echo "{\"action\":\"register\",\"helper_num\":$HELPER_NUM}" \
            | socat - UNIX-CONNECT:"$SOCKET_PATH" 2>/dev/null || true
    fi
fi

# Source parent startup script from broadcast channel if present
if [ -f "$HOME/all/ro/startup.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOME/all/ro/startup.sh"
fi

echo "Helper $HELPER_NUM initialized." >&2
