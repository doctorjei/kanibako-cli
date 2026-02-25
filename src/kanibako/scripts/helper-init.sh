#!/usr/bin/env bash
# helper-init.sh — Default helper initialization script (kanibako)
#
# This script is RO-mounted into every helper's playbook/scripts/ directory
# by the parent agent.  It runs inside the helper container at startup.
#
# The parent creates the directory structure (vault, workspace, playbook,
# peers, broadcast channels) before launching the helper.  This script
# handles any additional bootstrap that should happen from inside the
# helper's environment.
#
# Parents can replace this with a custom version in their own
# playbook/scripts/helper-init.sh — kanibako will use the parent's
# version if it exists, falling back to this bundled default.
#
# Usage: helper-init.sh [HELPER_NUM]
#   HELPER_NUM — this helper's global agent number

set -euo pipefail

HELPER_NUM="${1:-unknown}"

echo "Helper $HELPER_NUM initialized." >&2
