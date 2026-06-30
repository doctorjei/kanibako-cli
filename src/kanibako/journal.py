"""Lifecycle journal — a write-ahead log of in-flight box-lifecycle operations.

Design authority: ``plans/lifecycle-journal-DESIGN.md`` (Jei-blessed 2026-06-30b).

The REGISTRY (``config.registry``) is the steady-state truth ("what boxes
exist"); the JOURNAL (``config.journal``) is the transient truth ("what ops are
mid-flight").  A journal entry is written BEFORE the SEED step (and before the
registry write), so a crash anywhere in the seed->register window is forward-
recoverable — this is the window B3's create-recovery defect lived in.  At rest
the journal is normally EMPTY; an entry is the rare in-flight/crashed op.

NOTE — not yet a TRUE pre-dir write-ahead: today the resolver materializes the
box dir + meta BEFORE the entry is written (the resolver still creates dirs).  A
crash DURING that resolver dir-creation, before the entry exists, leaves the same
(narrower) unrecoverable limbo.  Closing it fully = the deferred tech-debt of
extracting registration + dir-creation OUT of the resolver (then the entry can be
written before any dir).  The design doc's "write-ahead before any dir exists"
goal is realized then; J1 covers the seed->register window, which is the defect.

Recovery model (Jei-blessed): forward-complete by REPLAY.  Every lifecycle op is
idempotent (create-if-absent / register-if-absent / remove-if-absent), so a
recovery re-runs the recorded op from step 1 and done steps skip.  NO ``phase``
field, NO rollback (rollback is a user-initiated ABORT concern, out of scope).

Write-ahead order per op: **write entry -> (idempotent op steps) -> clear entry**.
The HARD INVARIANT (from B3): the entry is cleared immediately after the op's
committing step, so ``registered ==> no pending entry`` holds at rest.

Schema (``global/journal.yaml``)::

    entries:
      /home/jei/projects/foo:        # keyed by box host-side PATH (pre-registration)
        op: create                   # create|import|connect|move|convert|...
        name: foo                    # assigned (pick_name); may not be registered yet
        mode: primary                # primary|workset|standalone
        workset: __PRIMARY__         # if relevant (else absent)
        started_at: 2026-06-30T07:12:00Z
        host: blue                   # reserved for liveness detection (later)

The entry KEY is the box host-side path (``str(Path(proj.shell_path).parent)`` —
the dir CONTAINING ``home/``, uniform across all modes, known at write-ahead
time).

Atomicity: reads/writes go through :mod:`kanibako.config_io` (``load_doc`` /
``dump_doc``), whose ``dump_doc`` writes atomically (temp file + ``os.replace``
via :func:`kanibako._atomic.atomic_write_text`), so a crash mid-journal-write can
never leave a torn/corrupt journal document on disk.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc

# The single top-level mapping in the journal document.
_ENTRIES = "entries"


def _key(box_path: str | Path) -> str:
    """Normalize a box host-side path into the journal entry key."""
    return str(box_path)


def read_journal(journal_path: Path) -> dict[str, dict]:
    """Return the journal's ``entries`` mapping (``{box_path: entry}``).

    An absent / empty / malformed journal file yields an empty dict — the
    normal at-rest state (no in-flight ops).
    """
    doc = load_doc(journal_path)
    entries = doc.get(_ENTRIES)
    return entries if isinstance(entries, dict) else {}


def write_entry(
    journal_path: Path,
    box_path: str | Path,
    *,
    op: str,
    name: str,
    mode: str,
    workset: str | None = None,
) -> None:
    """Write-ahead: record an in-flight lifecycle op keyed by *box_path*.

    Atomic read-modify-write of the journal document.  Stamps ``started_at``
    (real ``datetime.now(UTC)``) and ``host`` (``socket.gethostname()``, reserved
    for future liveness detection).  Overwrites any existing entry for the same
    *box_path* (a re-run before recovery re-stamps the intent — harmless, the op
    is idempotent).
    """
    key = _key(box_path)
    doc = load_doc(journal_path)
    entries = doc.get(_ENTRIES)
    if not isinstance(entries, dict):
        entries = {}
        doc[_ENTRIES] = entries
    entry: dict = {
        "op": op,
        "name": name,
        "mode": mode,
    }
    if workset is not None:
        entry["workset"] = workset
    entry["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["host"] = socket.gethostname()
    entries[key] = entry
    dump_doc(journal_path, doc)


def clear_entry(journal_path: Path, box_path: str | Path) -> None:
    """Atomically drop the entry keyed by *box_path* (no-op if absent).

    JC-J1-3 (lean): keep the journal document, drop the key — the document
    persists (``entries: {}`` when the last entry clears) rather than deleting an
    empty file.  No-op when the file is absent or the key is not present.
    """
    key = _key(box_path)
    doc = load_doc(journal_path)
    entries = doc.get(_ENTRIES)
    if not isinstance(entries, dict) or key not in entries:
        return
    del entries[key]
    dump_doc(journal_path, doc)


def pending_entry(journal_path: Path, box_path: str | Path) -> dict | None:
    """Return the in-flight entry for *box_path*, or ``None`` if none pending."""
    return read_journal(journal_path).get(_key(box_path))


def pending_create(journal_path: Path, box_path: str | Path) -> dict | None:
    """Return the pending entry for *box_path* iff it is a ``create`` op.

    The create/seed-path recovery signal: a non-``None`` result means a create
    was started for this box but never completed (crash before ``clear_entry``).
    """
    entry = pending_entry(journal_path, box_path)
    if entry is not None and entry.get("op") == "create":
        return entry
    return None


# Register-only ops (J2): import/connect REGISTER an externally-seeded box and
# NEVER seed (CONVENTIONS "Seed model" B7).  Their replay is register-if-absent
# -> clear, with NO seed step — so the op-typed entry is exactly how the journal
# distinguishes a create (seed+register) from an import/connect (register-only).
_IMPORT_OPS = ("import", "connect")


def pending_import(journal_path: Path, box_path: str | Path) -> dict | None:
    """Return the pending entry for *box_path* iff it is a register-only op.

    The import/connect recovery signal: a non-``None`` result means a
    register-only op (``import`` or ``connect``) was started for this box but
    never completed (crash before ``clear_entry``).  Distinct from
    :func:`pending_create` so a create entry never drives a register-only replay
    (and vice-versa) — the op TYPE selects the replay table (DESIGN "Recovery
    model"): a ``create`` replays seed+register, an ``import``/``connect``
    replays register-only (NO seed).
    """
    entry = pending_entry(journal_path, box_path)
    if entry is not None and entry.get("op") in _IMPORT_OPS:
        return entry
    return None
