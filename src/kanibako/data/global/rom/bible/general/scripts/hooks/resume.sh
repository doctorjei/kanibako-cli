#!/usr/bin/env bash

~/canon/bible/general/scripts/util/session-pid.sh >/dev/null 2>&1 || true
python3 "$HOME/canon/bible/general/scripts/util/directives.py" --context "$KANIBAKO_DIRECTIVE_SEED" >/dev/null 2>&1 || true
~/canon/handbook/general/scripts/hooks/resume.sh >/dev/null 2>&1 || true
