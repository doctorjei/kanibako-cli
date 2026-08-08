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

# POST-LIVENESS XDG PROJECTION — serve $XDG_STATE_HOME/kanibako from the pinned dir
# now that there IS a box to ask.  A helper box's PID-1 is this script, not the
# kanibako supervisor, so nothing else would run the projection here.
#
# ⚑ VERBATIM COPY of box_supervisor.xdg_projection_sh() — bash can import nothing, so
# this is the same third-copy arrangement as SOCKET_PATH above; a test pins the two.
# Edit the GENERATOR, never this block.  Skips when the two paths are already the
# same, never re-points a symlink, never clobbers a real path (a pre-v1.8.0 box has a
# real directory there — MIGRATION.md §2.22), never fails.  Idempotent.
_kb_pin="$HOME/.kanibako/state"
_kb_xdg="${XDG_STATE_HOME:-}"
case "$_kb_xdg" in /*) ;; *) _kb_xdg="$HOME/.local/state" ;; esac
_kb_link="$_kb_xdg/kanibako"
if [ "$_kb_link" != "$_kb_pin" ]; then
    mkdir -p "$_kb_pin" 2>/dev/null || true
    if [ ! -L "$_kb_link" ] && [ ! -e "$_kb_link" ]; then
        mkdir -p "$_kb_xdg" 2>/dev/null && ln -s "$_kb_pin" "$_kb_link" 2>/dev/null || true
    fi
fi
unset _kb_pin _kb_xdg _kb_link

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
