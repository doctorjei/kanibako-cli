#!/usr/bin/env bash

~/canon/bible/general/scripts/util/pid-rm.sh "$PPID"

# ⚑ ABSENCE IS SILENT, EVERYTHING ELSE IS NOT. A layer a user never created must not
# raise; a script that EXISTS and exits non-zero is a bug in their own hook and
# has to stay visible. `|| true` cannot tell those apart, so it hid both.
handbook_hook=~/canon/handbook/general/scripts/hooks/end.sh
if [ -e "$handbook_hook" ]; then "$handbook_hook"; fi
