#!/usr/bin/env bash

# ⚑ ABSENCE IS SILENT, FAILURE IS NOT — see the sibling hooks. This one fires on
# EVERY Write/Edit, so its chatter is suppressed; its exit STATUS is not. Hiding
# output is a courtesy, hiding a failure is a defect.
notebook_hook=~/canon/notebook/scripts/hooks/edited.sh
if [ -e "$notebook_hook" ]; then "$notebook_hook" >/dev/null 2>&1; fi
