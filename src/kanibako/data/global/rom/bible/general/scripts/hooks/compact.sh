#!/usr/bin/env bash

python3 "$HOME/canon/bible/general/scripts/util/directives.py" --context "$KANIBAKO_DIRECTIVE_SEED" >/dev/null 2>&1 || true
~/canon/handbook/general/scripts/hooks/compact.sh  >/dev/null 2>&1 || true
