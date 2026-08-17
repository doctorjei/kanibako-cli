#!/usr/bin/env bash

rm -f "${KANIBAKO_AGENT_PIDFILE:-/tmp/kanibako/agent.pid}"
rm -f "${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}/$PPID"

