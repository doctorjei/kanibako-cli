"""Drop-in import-on-discovery: reconcile the registry from on-disk truth.

On-disk metadata is **authoritative**; ``system.registry`` is a *derived,
rebuildable index*.  When detection/resolution encounters an on-disk
box/workset/project that is NOT in the registry, kanibako **imports** it:
registers it into the appropriate registry section, prints an ALERT to stderr,
then proceeds — with **no confirmation prompt**.  This is what lets a user copy
or move a box/workset/project tree to a new location (or machine) and have
kanibako re-discover it (it "effectively becomes an import").

Two live modes are supported, both backed by one uniform "reconcile registry
from on-disk truth" mechanism (no per-mode special-casing):

* **STANDALONE** — :func:`import_standalone`, called lazily during the
  ancestor-walk when a ``box_data/`` marker is found whose box is not in
  ``registry.standalone``.
* **NAMED** — :func:`import_named_workset`, called lazily during the walk when a
  workset-root marker (a ``settings.yaml`` carrying a ``workset.meta`` identity)
  is found that is not a registered workset root.

A third **PRIMARY** mode (``import_primary_box`` /
``import_primary_box_for_workspace`` / ``reconcile_primary_boxes``) used to
re-associate a central primary box with its external workspace by reading its
on-disk ``project:``/``resolved:`` meta.  It was **RETIRED FROM THE LIVE PATH
(P8b/Option A)**: under sparse create a primary box no longer self-describes its
identity on disk, so there is nothing on disk to re-import from — the registry is
the sole identity authority.  Its enumerate / match-by-workspace /
collision-refuse / journal-atomic skeleton — the seed of a future heuristic
``system recover`` — was **sequestered** into ``salvage/primary_reconcile.py``
(a non-shipping frozen reference snapshot, P8c); it is no longer part of the live
package.

Conflict semantics (all modes): if the import's NAME collides with an entity
already registered to a *different* root/path, the import is **REFUSED** —
nothing is mutated and :class:`ImportConflictError` is raised explaining that a
``rename`` mechanism is coming (future work).  If the on-disk entity is already
registered to its current path, the import is a silent idempotent no-op.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from kanibako import registry_store
from kanibako.config import BOX_META_FILE
from kanibako.errors import KanibakoError


# The standalone box's host-side box dir (sibling of ``settings.yaml`` under the
# project root, holding ``home/``); mirrors ``paths._STANDALONE_META_DIR``.  The
# J2 journal entry for a standalone import is keyed by ``<root>/box_data`` (the
# dir CONTAINING ``home/``), the uniform host-side box-dir key scheme.
_STANDALONE_BOX_DIR = "box_data"


class ImportConflictError(KanibakoError):
    """A drop-in import was refused because its name collides with a different
    already-registered entity.

    The import leaves the on-disk entity untouched and mutates nothing in the
    registry.  Resolving the collision needs a ``rename`` mechanism (future
    work, not 1.6.0).
    """


def _alert(mode: str, name: str, path: Path) -> None:
    """Print the import ALERT to stderr (no confirmation; the import proceeds)."""
    print(f"Imported {mode} '{name}' at {path}", file=sys.stderr)


def _conflict(
    mode: str, name: str, new_path: Path, existing_path: str,
) -> ImportConflictError:
    """Build the shared REFUSE error explaining the name collision."""
    return ImportConflictError(
        f"Cannot import {mode} '{name}' at {new_path}: the name '{name}' is "
        f"already registered to a different location ({existing_path}). "
        "Refusing the import to avoid clobbering the existing entry. "
        "A 'rename' mechanism to resolve such collisions is planned "
        "(future work); for now, rename or relocate one of them manually."
    )


# ---------------------------------------------------------------------------
# J2 lifecycle journal — register-only write-ahead for import/connect
# ---------------------------------------------------------------------------
#
# Import/connect REGISTER an externally-seeded box and NEVER seed (CONVENTIONS
# "Seed model" B7).  J2 makes the register seam ATOMIC via the journal: a
# write-ahead ``op: import``/``op: connect`` entry brackets the register so a
# crash between write-entry and clear-entry leaves the entry, and the NEXT
# resolve re-enters this same (idempotent, register-if-absent) import and
# replays it — register-if-absent -> clear, NO seed.  The op TYPE (import, not
# create) is what keeps "import never seeds" true: the replay table for these
# ops has no seed step (the box was seeded where it was created).  Because the
# import functions ARE the lazy-on-resolve trigger, wrapping them here gives BOTH
# write-ahead atomicity AND recovery — re-running the resolve completes a
# half-done import and clears the entry.
#
# The journal path is threaded as an OPTIONAL ``journal`` argument from the
# resolver call sites (``std.journal``).  When it is None (a std-less direct
# caller that has no journal — e.g. a low-level test or a registry-only utility),
# the wrapper degrades to a plain register, so default behavior is byte-identical
# to the pre-J2 register-only path: the entry is written and cleared WITHIN the
# call, leaving the journal empty at rest (HARD INVARIANT: registered ==> no
# pending entry).


@contextmanager
def _journal_register(
    journal: Path | None,
    box_path: Path,
    *,
    op: str,
    name: str,
    mode: str,
    workset: str | None = None,
):
    """Bracket a register-only import/connect with a write-ahead journal entry.

    Write-ahead order (DESIGN): write entry -> (register body, the ``with``
    block) -> clear entry.  The entry is cleared as the IMMEDIATE step after the
    register, so ``registered ==> no pending entry`` holds at rest.  If the
    register body raises (a genuine collision), the entry is intentionally LEFT
    (the import is incomplete) and the exception propagates — recovery resumes it
    on the next resolve.  A None *journal* (std-less caller) is a no-op bracket
    (plain register), preserving the pre-J2 behavior exactly.

    *box_path* is the host-side box dir (the dir CONTAINING ``home/``), the same
    key scheme as J1 (``str(Path(shell_path).parent)``) — known pre-registration.
    """
    if journal is None:
        yield
        return
    from kanibako.launch import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op=op, name=name, mode=mode, workset=workset,
    )
    yield
    # Committing step done (register returned) — clear immediately.  A crash here
    # (register done, entry not yet cleared) leaves a stale entry that the next
    # resolve clears via this same idempotent replay.
    journal_mod.clear_entry(journal, box_path)


def _clear_stale_import(journal: Path | None, box_path: Path) -> None:
    """Clear a stale register-only entry on an already-registered box.

    The register -> clear-entry window (crash AFTER the registry write, BEFORE
    the entry cleared) leaves the box REGISTERED with a lingering ``import`` /
    ``connect`` entry.  A re-resolve / reconcile sweep that finds the box already
    registered hits the import's idempotent NO-OP branch and would otherwise skip
    the clear; calling this there completes the recovery (register-if-absent is
    already satisfied, so recovery == clear the stale entry), restoring the HARD
    INVARIANT ``registered ==> no pending entry``.  Only a register-only
    (import/connect) entry is cleared — a ``create`` entry is left for the
    create-recovery path.  A None *journal* is a no-op.
    """
    if journal is None:
        return
    from kanibako.launch import journal as journal_mod

    if journal_mod.pending_import(journal, box_path) is not None:
        journal_mod.clear_entry(journal, box_path)


# ---------------------------------------------------------------------------
# STANDALONE
# ---------------------------------------------------------------------------

def import_standalone(
    registry: Path, root: Path, *, journal: Path | None = None,
) -> str | None:
    """Reconcile an on-disk standalone box at *root* against ``registry.standalone``.

    Behavior (see module docstring):

    * Already registered to *root* (by ``standalone_name_for_root``) → silent
      no-op, returns the registered name.
    * Otherwise the identity is COMPOSED kuid-first (P8b — the box no longer
      self-describes its ``project.name`` on disk): the stored ``workset.kuid``
      (sparse-persisted at create) prefixes the LIVE dir leaf
      (``<kuid>_<leaf>``, mirroring
      :func:`kanibako.launch.box_resolve.resolve_box_identity`); a pre-kuid box
      (SENTINEL) falls back to the dir leaf.  The composed name is registered to
      *root* + alerted, UNLESS it already maps to a DIFFERENT root →
      :class:`ImportConflictError` (refuse, no mutation).

    *root* is the standalone project root (the dir containing ``box_data/`` and,
    at the root, ``settings.yaml``).  Returns the registered box name, or
    ``None`` when *root* carries no standalone MARKER (``box_data/`` +
    ``settings.yaml`` — nothing to import).
    """
    root = root.resolve()
    root_str = str(root)

    # Already registered to this exact root → idempotent no-op.  J2: a re-resolve
    # over a complete box clears any stale register-only entry (register->clear
    # window crash recovery).
    existing_name = registry_store.standalone_name_for_root(registry, root)
    if existing_name is not None:
        _clear_stale_import(journal, root / _STANDALONE_BOX_DIR)
        return existing_name

    # Gate on the standalone MARKER (design D4: the box's own settings FILE is the
    # standalone signal — NOT ``project.mode``).  No marker → nothing to import.
    from kanibako import kuid
    from kanibako.launch import box_identity, box_resolve
    from kanibako.config import read_workset_kuid

    if not box_resolve.standalone_settings_present(root):
        return None

    # Compose the LIVE name kuid-first (mirrors box_resolve's standalone branch):
    # the stored ``workset.kuid`` prefixes the current dir leaf so a MOVED box
    # keeps its stable kuid identity while the leaf tracks the new dir.  A pre-kuid
    # box (SENTINEL) falls back to the dir leaf.
    stored_kuid = read_workset_kuid(root / BOX_META_FILE)
    if stored_kuid != kuid.SENTINEL:
        name = box_identity.compose_standalone_name(stored_kuid, root)
    else:
        name = root.name

    # Collision check against a DIFFERENT root.
    registered = registry_store.load_standalone(registry)
    other_root = registered.get(name)
    if other_root is not None and other_root != root_str:
        raise _conflict("standalone box", name, root, other_root)

    # J2: write-ahead the register (register-only; NO seed — the box is already
    # seeded on disk).  box dir = <root>/box_data (where home/ lives).
    with _journal_register(
        journal, root / _STANDALONE_BOX_DIR,
        op="import", name=name, mode="standalone",
    ):
        registry_store.register_standalone(registry, name, root)
    _alert("standalone box", name, root)
    return name


# ---------------------------------------------------------------------------
# NAMED (worksets)
# ---------------------------------------------------------------------------

def import_named_workset(
    registry: Path, root: Path, *, journal: Path | None = None,
) -> str | None:
    """Reconcile an on-disk workset at *root* against the workset registry.

    Reads the workset name from *root*'s ``settings.yaml`` ``workset.meta`` table
    and reconciles it against ``registry.worksets`` (the name → root index that
    backs both name lookups AND workset discovery, written by
    :mod:`kanibako.workset`):

    * The name is already registered to *root* → silent idempotent no-op.
    * The name is registered to a DIFFERENT root → :class:`ImportConflictError`.
    * Otherwise register name → root in the ``worksets`` section (matching
      ``create_workset``), alert, return the name.

    Returns the workset name, or ``None`` when *root* has no readable
    ``settings.yaml`` ``workset.meta`` identity (nothing to import).  Does NOT
    rewrite the workset-create skeleton — it only registers an already-on-disk
    workset.
    """
    root = root.resolve()
    root_str = str(root)

    from kanibako.workset import WORKSET_META_FILE, read_workset_meta

    meta = read_workset_meta(root / WORKSET_META_FILE)
    if meta is None:
        return None
    name = (meta.get("name") or "").strip()
    if not name:
        return None

    names_section = registry_store.load_section(registry, "worksets")
    current = names_section.get(name)
    if current is not None:
        if str(Path(current).resolve()) == root_str:
            # Already registered to this root → no-op; clear a stale entry.
            _clear_stale_import(journal, root)
            return name
        raise _conflict("workset", name, root, str(current))

    # Register name → root in the ``worksets`` section (the single workset
    # registry, matching ``create_workset``).  J2: write-ahead the register
    # (register-only; a workset import never seeds a box).  A workset has no
    # single ``home/`` dir, so its journal key is the workset ROOT (the host-side
    # identity dir, known pre-registration).
    with _journal_register(
        journal, root, op="import", name=name, mode="named", workset=name,
    ):
        names_section[name] = root_str
        registry_store.save_section(registry, "worksets", names_section)
    _alert("workset", name, root)
    return name
