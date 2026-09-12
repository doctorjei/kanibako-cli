#!/usr/bin/env bash

KANIBAKO_AGENT_PIDFILE="${KANIBAKO_AGENT_PIDFILE:-/tmp/kanibako/agent.pid}"
KANIBAKO_AGENT_MARKERS_DIR="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"

# $1 = the agent PID to record; callers in the hook CASCADE must pass their own
# "$PPID" explicitly.  A cascaded caller cannot let this script default: by then
# $PPID is the caller's transient shell, which exits immediately, so the marker
# would name a dead process.  The bare default is correct ONLY when this script
# is itself the hook command.
AGENT_PID="${1:-$PPID}"

mkdir -p "$(dirname "$KANIBAKO_AGENT_PIDFILE")" && printf %s "$AGENT_PID" > "$KANIBAKO_AGENT_PIDFILE"
mkdir -p "$KANIBAKO_AGENT_MARKERS_DIR" && printf %s "$AGENT_PID" > "$KANIBAKO_AGENT_MARKERS_DIR/$AGENT_PID"
