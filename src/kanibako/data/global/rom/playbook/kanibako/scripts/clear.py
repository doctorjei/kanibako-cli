#!/usr/bin/env python3
"""KANIBAKO_TRIGGER_CLEAR -- regenerate the flattened directive file on reload.

Fired when the agent clears/compacts its context (e.g. a claude ``SessionStart``
hook with source ``clear``/``compact``). The action is identical to start today
-- re-flatten seed -> dest -- so this delegates to ``start.main()``; the two
lifecycle triggers are kept separate so they can diverge later.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import start  # type: ignore[import-not-found]  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(start.main())
