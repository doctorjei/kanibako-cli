#!/usr/bin/env bash

KANIBAKO_AGENT_PIDFILE="${KANIBAKO_AGENT_PIDFILE:-/tmp/kanibako/agent.pid}"
KANIBAKO_AGENT_MARKERS_DIR="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"

mkdir -p "$(dirname "$KANIBAKO_AGENT_PIDFILE")" && printf %s "$PPID" > "$KANIBAKO_AGENT_PIDFILE"
mkdir -p "$KANIBAKO_AGENT_MARKERS_DIR" && printf %s "$PPID" > "$KANIBAKO_AGENT_MARKERS_DIR/$PPID"
