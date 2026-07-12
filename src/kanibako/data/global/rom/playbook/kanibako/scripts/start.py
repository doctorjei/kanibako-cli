#!/usr/bin/env python3
"""KANIBAKO_TRIGGER_START -- (re)generate the flattened directive file.

Runs at box start (and, where an agent exposes a reload/clear hook, again then).
Thin wrapper: resolve the seed + destination from the environment and run the
flattener as root so the generated file lands root-owned, mode 644 -- readable by
the agent, not casually writable, regenerated in place on the next trigger.

  KANIBAKO_DIRECTIVE_SEED   source kickoff file piped through the flattener
  KANIBAKO_DIRECTIVE_FINAL  destination (the agent's native instruction slot)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FLATTENER = Path(__file__).resolve().parent / "import-directives.py"


def main() -> int:
    seed = os.environ.get("KANIBAKO_DIRECTIVE_SEED")
    dest = os.environ.get("KANIBAKO_DIRECTIVE_FINAL")
    if not seed or not dest:
        sys.stderr.write(
            "kanibako start: KANIBAKO_DIRECTIVE_SEED / KANIBAKO_DIRECTIVE_FINAL "
            "not set; skipping directive flatten\n"
        )
        return 0
    argv = [str(FLATTENER), os.path.expanduser(seed), os.path.expanduser(dest)]

    # Prefer running the flattener as root so the output is root:644 (soft
    # protection + clear ownership); --preserve-env=HOME keeps ~ expansion anchored
    # to the agent's home. Fall back to a direct run on a locked-down box that
    # strips sudo -- there the file is simply agent-owned.
    try:
        proc = subprocess.run(
            ["sudo", "-n", "--preserve-env=HOME", sys.executable, *argv]
        )
        if proc.returncode == 0:
            return 0
        sys.stderr.write("kanibako start: sudo flatten failed; retrying directly\n")
    except FileNotFoundError:
        pass
    return subprocess.run([sys.executable, *argv]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
