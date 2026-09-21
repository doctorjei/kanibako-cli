#!/bin/bash
# Kanibako: Status Line (Context Usage & Cummulative Cost
#
# To (re-)install, add path to ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "~/canon/charter/agent/scripts/interface/statusline.sh"
#   }

# Capture stdin and pipe it directly into handbook script
cat | ~/canon/handbook/agent/scripts/interface/statusline.sh
