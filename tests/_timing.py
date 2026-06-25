"""Opt-in timing-instrumentation pytest plugin for kanibako test/e2e runs.

Part of the kanibako timing harness (shell emitter ``timed.sh`` + this plugin
+ ``analyze-timings.py``).  Captures START + END wall-clock spans for the
**testing** category so the GAPS between ops are computable, and so testing
time stays STRICTLY SEPARATE from coding time (the conflation this harness
exists to fix).

Categories emitted here (each span carries ``category`` + ``level``):

- category=**testing**, level=**prep**: the per-test *setup* phase
  (fixture / env setup — e.g. the e2e ``e2e_env`` fixture building an isolated
  HOME, copying the claude stub, writing config).
- category=**testing**, level=**test**: the per-test *call* phase (the test
  body itself).
- the *teardown* phase is also emitted as level=**prep** (fixture finalizers).

OPT-IN / ZERO-IMPACT (the #1 safety property): this plugin is COMPLETELY INERT
unless the ``KANI_TIMING_LOG`` environment variable is set.  Every hook
early-returns when it is unset, so normal runs, CI, and the rework gates pay
nothing and no timing file is written.  Each emitted line is appended to the
SAME JSONL file the shell emitter (``timed.sh``) writes, with the SAME schema::

    {"run_id": "<$KANI_RUN_ID or unknown>", "ts_wall": <float unix seconds>,
     "phase": "start"|"end", "category": "infra|testing|coding",
     "level": "env|setup|container|prep|test|agent", "label": "<nodeid>",
     "rc": <int|null>}

L3 CONTAINER SPANS — KNOWN GAP / FOLLOW-UP
------------------------------------------
The e2e suite does NOT launch the box through a single shared helper that this
plugin could wrap: ``tests/e2e/conftest.py`` exposes ``run_kanibako(...)`` as a
*module-level function* (not a fixture), and each e2e test calls it inline
(``run_kanibako(["start", ...], env)``) for start/shell/stop.  There is no
shared launch seam to instrument here, and wrapping every test is out of scope
(it would touch every e2e test).  So per-container L3 (category=infra,
level=container) spans are NOT emitted by this plugin.  The ``e2e_env`` fixture
setup/teardown is the closest proxy and is already captured as the
testing/prep span for each e2e test.  CLEAN FOLLOW-UP: route box launches
through a shared ``run_kanibako``-style helper that wraps the subprocess in a
container start/end span (category=infra, level=container) — then L3 falls out
for free.  Until then, L3 is best emitted from the shell side via ``timed.sh``
around the e2e recipe's box ops if/when those become shell-driven.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pytest


def _log_path() -> str | None:
    """Return the timing log path, or None when instrumentation is off.

    This is the single gate: a None return makes every hook a no-op.
    """
    path = os.environ.get("KANI_TIMING_LOG")
    return path or None


def _emit(
    log_path: str,
    *,
    phase: str,
    category: str,
    level: str,
    label: str,
    rc: int | None,
) -> None:
    """Append one JSONL span record (same schema as timed.sh)."""
    record = {
        "run_id": os.environ.get("KANI_RUN_ID") or "unknown",
        "ts_wall": time.time(),
        "phase": phase,
        "category": category,
        "level": level,
        "label": label,
        "rc": rc,
    }
    line = json.dumps(record, separators=(",", ":"))
    # Best-effort append; never let instrumentation break a test run.
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# Map a pytest phase -> (level, category) for the testing spans we record.
# setup + teardown are fixture/env work (level=prep); call is the test itself.
_PHASE_SPEC = {
    "setup": ("prep", "testing"),
    "call": ("test", "testing"),
    "teardown": ("prep", "testing"),
}


def pytest_runtest_logstart(nodeid: str, location: object) -> None:
    """Mark the very start of a test item (setup START)."""
    log_path = _log_path()
    if log_path is None:
        return
    # We emit the per-phase start/end in the makereport hook below; logstart is
    # not phase-resolved, so we skip it to avoid an unpaired span.  Kept as an
    # explicit early-return so the opt-in gate is obvious here too.
    return


def pytest_runtest_makereport(item: "pytest.Item", call: "pytest.CallInfo") -> None:  # noqa: F821
    """Emit a start/end span pair for each pytest phase (setup/call/teardown).

    ``call.start`` and ``call.stop`` are wall-clock unix timestamps (floats)
    bracketing the phase, so we get a true start+end pair per phase for gap
    math — without needing to hook each phase boundary separately.
    """
    log_path = _log_path()
    if log_path is None:
        return
    spec = _PHASE_SPEC.get(call.when or "")
    if spec is None:
        return
    level, category = spec
    label = item.nodeid
    rc = 0 if call.excinfo is None else 1
    # Emit start at call.start, end at call.stop.  Both carry the same
    # (category, level, label) so the analyzer pairs them by order.
    start_ts = getattr(call, "start", None)
    stop_ts = getattr(call, "stop", None)
    if start_ts is None or stop_ts is None:
        return
    _emit_at(log_path, start_ts, phase="start", category=category,
             level=level, label=label, rc=None)
    _emit_at(log_path, stop_ts, phase="end", category=category,
             level=level, label=label, rc=rc)


def _emit_at(
    log_path: str,
    ts_wall: float,
    *,
    phase: str,
    category: str,
    level: str,
    label: str,
    rc: int | None,
) -> None:
    """Like _emit but with an explicit ts_wall (from pytest's CallInfo)."""
    record = {
        "run_id": os.environ.get("KANI_RUN_ID") or "unknown",
        "ts_wall": ts_wall,
        "phase": phase,
        "category": category,
        "level": level,
        "label": label,
        "rc": rc,
    }
    line = json.dumps(record, separators=(",", ":"))
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
