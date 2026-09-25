#!/usr/bin/env bash

# ⚑ ABSENCE IS SILENT, EVERYTHING ELSE IS NOT. The notebook layer is never seeded, so a box
# that has no hook here is the NORMAL case and must not raise. A script that EXISTS
# and exits non-zero is a bug in someone's own hook, and swallowing that is how an
# extension point becomes undebuggable — `|| true` cannot tell the two apart.
notebook_hook=~/canon/notebook/scripts/hooks/clear-end.sh
if [ -e "$notebook_hook" ]; then "$notebook_hook"; fi
