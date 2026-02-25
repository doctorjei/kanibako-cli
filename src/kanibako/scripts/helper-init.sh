#!/usr/bin/env bash
# helper-init.sh — Default helper entrypoint wrapper (kanibako)
#
# This script is copied into every helper's playbook/scripts/ directory
# by the parent agent.  It runs as the container entrypoint.
#
# The parent creates the directory structure (vault, workspace, playbook,
# peers, broadcast channels) before launching the helper.  This script
# handles registration with the hub and additional bootstrap, then execs
# the agent command.
#
# Parents can replace this with a custom version in their own
# playbook/scripts/helper-init.sh — kanibako will use the parent's
# version if it exists, falling back to this bundled default.
#
# Usage: helper-init.sh HELPER_NUM [COMMAND...]
#   HELPER_NUM — this helper's global agent number
#   COMMAND    — the agent command to exec (default: claude)

set -euo pipefail

HELPER_NUM="${1:-unknown}"
shift || true

SOCKET_PATH="$HOME/.kanibako/helper.sock"

# Register with the hub via kanibako CLI (one-shot)
if [ -S "$SOCKET_PATH" ] && command -v kanibako >/dev/null 2>&1; then
    kanibako helper register "$HELPER_NUM" 2>/dev/null || true
fi

# Source parent startup script from broadcast channel if present
if [ -f "$HOME/all/ro/startup.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOME/all/ro/startup.sh"
fi

echo "Helper $HELPER_NUM initialized." >&2

# Exec the agent command (or claude if none given)
if [ $# -gt 0 ]; then
    exec "$@"
else
    exec claude
fi
