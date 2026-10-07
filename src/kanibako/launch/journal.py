"""Lifecycle journal — a write-ahead log of in-flight box-lifecycle operations.

The REGISTRY (``config.registry``) is the steady-state truth ("what boxes
exist"); the JOURNAL (``config.journal``) is the transient truth ("what ops are
mid-flight").  At rest the journal is normally EMPTY.

Write-ahead order per op: **write entry -> (idempotent op steps) -> clear entry**.
The HARD INVARIANT (from B3): the entry is cleared immediately after the op's
committing step, so ``registered ==> no pending entry`` holds at rest.  The
committing step lives in the CALLERS, so nothing in this module enforces it.

Recovery is forward-complete by REPLAY — the recorded op re-runs from step 1 and
done steps skip — so every lifecycle op must stay idempotent (create-if-absent /
register-if-absent / remove-if-absent).  NO ``phase`` field and NO rollback; do
not add either.

A ``create`` entry is written ONCE, before the create's first box write, and its
``state`` mapping records everything the create was told that sets box state, so a
replay finishes that attempt rather than guessing it.  ``state`` is a RECORD OF
INTENT, not a progress marker: it never changes after the write.

Schema, entry-key derivation, atomicity, the replay table and the design
authority: ``llm-docs/kanibako/launch/journal.py.md``.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import socket
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.utils import literal_path

# The single top-level mapping in the journal document.
_ENTRIES = "entries"


def _key(box_path: str | Path) -> str:
    """Normalize a box host-side path into the journal entry key."""
    return str(box_path)


@contextlib.contextmanager
def _locked(journal_path: Path) -> Iterator[None]:
    """Serialize every read-modify-write of the journal: a ``flock`` on its DIRECTORY.

    The document is replaced by rename, so a lock on the file would not survive a
    write; the directory outlives every rename.
    """
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(journal_path.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def read_journal(journal_path: Path) -> dict[str, dict]:
    """Return the ``entries`` mapping; an absent/empty file or non-mapping ``entries`` is ``{}``.

    A file that is not a YAML mapping raises ``ConfigError`` naming it (``load_doc``).
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
    workspace: str | None = None,
    state: dict | None = None,
    claim: bool = False,
) -> bool:
    """Write-ahead: record an in-flight lifecycle op keyed by *box_path* (atomic RMW).

    *state* is what the op was told that sets box state (omitted when empty).  With
    *claim*, a key that already holds an entry is left alone and ``False`` comes back,
    so two writers can never share one key.
    """
    key = _key(box_path)
    with _locked(journal_path):
        doc = load_doc(journal_path)
        entries = doc.get(_ENTRIES)
        if not isinstance(entries, dict):
            entries = {}
            doc[_ENTRIES] = entries
        if claim and key in entries:
            return False
        entries[key] = _entry(op, name, mode, workset, workspace, state)
        dump_doc(journal_path, doc)
    return True


def _entry(op: str, name: str, mode: str, workset: str | None,
           workspace: str | None, state: dict | None) -> dict:
    """One entry's mapping, in its on-disk key order."""
    entry: dict = {
        "op": op,
        "name": name,
        "mode": mode,
    }
    if workset is not None:
        entry["workset"] = workset
    if workspace is not None:
        entry["workspace"] = workspace
    if state:
        entry["state"] = dict(state)
    # Stamped AFTER the optional fields on purpose: ``dump_doc`` pins
    # ``sort_keys=False``, so insertion order is the on-disk order.  ``host`` has no
    # reader yet — it is reserved for liveness detection.
    entry["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["host"] = socket.gethostname()
    return entry


def clear_entry(journal_path: Path, box_path: str | Path) -> None:
    """Atomically drop the entry keyed by *box_path* (no-op if absent)."""
    # JC-J1-3 (lean): drop the key, KEEP the document — an emptied journal stays as
    # ``entries: {}`` and is never deleted.  The no-op arm is what lets a replay call
    # this unconditionally.
    key = _key(box_path)
    with _locked(journal_path):
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

    The create/seed-path recovery signal: a non-``None`` result means a create was
    started for this box but never completed (crash before ``clear_entry``).
    """
    entry = pending_entry(journal_path, box_path)
    if entry is not None and entry.get("op") == "create":
        return entry
    return None


def pending_create_for_workspace(
    journal_path: Path, workspace: str | Path,
) -> dict | None:
    """Return the pending ``create`` entry whose ``workspace`` == *workspace*, else ``None``.

    The PRIMARY deferred-registration create-recovery signal (P8b): the box was
    mid-create for this workspace but never registered, so its NAME is read from the
    journal — there is no on-disk meta to read it from.
    """
    # The journal is keyed by box PATH, so a lookup BY WORKSPACE has to scan.  Both
    # sides compare literally: twins are two workspaces.
    target = literal_path(workspace)
    for entry in read_journal(journal_path).values():
        if entry.get("op") != "create":
            continue
        ws = entry.get("workspace")
        if ws is None:
            continue
        if literal_path(ws) == target:
            return entry
    return None


# Register-only ops (J2): import/connect REGISTER an externally-seeded box and NEVER
# seed (CONVENTIONS "Seed model" B7); their replay is register-if-absent -> clear.
# The op TYPE is what keeps the two replay tables apart — never collapse this lookup
# into pending_create, or a create entry would drive a register-only replay (and a
# create entry on an imported box would wrongly trigger a re-seed).
_IMPORT_OPS = ("import", "connect")


def pending_import(journal_path: Path, box_path: str | Path) -> dict | None:
    """Return the pending entry for *box_path* iff it is a register-only op.

    The import/connect recovery signal: a non-``None`` result means an ``import`` or
    ``connect`` was started for this box but never completed (crash before
    ``clear_entry``).
    """
    entry = pending_entry(journal_path, box_path)
    if entry is not None and entry.get("op") in _IMPORT_OPS:
        return entry
    return None
