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

# PINNED — the hub socket lives under the fixed resolve-before-liveness root
# ~/.kanibako/state/, never under $XDG_STATE_HOME: the mount destination is written
# into the container runtime's arguments before this box exists, so the host cannot
# ask the box where its XDG dirs are.  Single source of truth for the root:
# settings_resolve.BOX_PINNED_ROOT_RELPATH (this literal is pinned to it by a test).
# The box's own $XDG_STATE_HOME/kanibako is symlinked onto that dir after boot.
SOCKET_PATH="$HOME/.kanibako/state/helper.sock"

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
