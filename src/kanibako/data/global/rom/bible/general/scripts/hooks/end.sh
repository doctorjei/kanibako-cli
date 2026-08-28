#!/usr/bin/env bash

# $1 = the agent PID, passed by the hook command because THIS script is a cascaded
# caller of pid-rm.sh: our own $PPID is the hook shell, not the agent.  The bare
# $PPID fallback is correct only when this script IS the hook command.
~/canon/bible/general/scripts/util/pid-rm.sh "${1:-$PPID}"

# ⚑ ABSENCE IS SILENT, EVERYTHING ELSE IS NOT. A layer a user never created must not
# raise; a script that EXISTS and exits non-zero is a bug in their own hook and
# has to stay visible. `|| true` cannot tell those apart, so it hid both.
handbook_hook=~/canon/handbook/general/scripts/hooks/end.sh
if [ -e "$handbook_hook" ]; then "$handbook_hook"; fi
