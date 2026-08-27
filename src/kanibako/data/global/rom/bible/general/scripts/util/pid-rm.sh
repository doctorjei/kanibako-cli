#!/usr/bin/env bash

# $1 = the agent PID whose marker to drop — see pid-add.sh; the two MUST agree on
# which pid they name, or add and remove target different files.
AGENT_PID="${1:-$PPID}"

# ⚑ THE TWO STORES HAVE DIFFERENT ARITY, so they are removed differently.  The
# marker dir holds ONE FILE PER PID and is safe to drop by name.  The pidfile is a
# SINGLE SHARED PATH that pid-add.sh overwrites, so with two agents in one box it
# names whichever wrote last — and removing it unconditionally let the FIRST agent
# to end clear a pidfile another agent still owned.  Drop it ONLY while it still
# names us; a mismatch means it is not ours to remove.
PIDFILE="${KANIBAKO_AGENT_PIDFILE:-/tmp/kanibako/agent.pid}"
[ "$(cat "$PIDFILE" 2>/dev/null)" = "$AGENT_PID" ] && rm -f "$PIDFILE"

rm -f "${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}/$AGENT_PID"

