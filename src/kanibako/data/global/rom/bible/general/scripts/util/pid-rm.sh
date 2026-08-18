#!/usr/bin/env bash

# $1 = the agent PID whose marker to drop — see pid-add.sh; the two MUST agree on
# which pid they name, or add and remove target different files.
AGENT_PID="${1:-$PPID}"

rm -f "${KANIBAKO_AGENT_PIDFILE:-/tmp/kanibako/agent.pid}"
rm -f "${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}/$AGENT_PID"

