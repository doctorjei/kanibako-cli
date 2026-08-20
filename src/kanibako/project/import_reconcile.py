"""Drop-in import-on-discovery: reconcile the registry from on-disk truth.

On-disk metadata is **authoritative**; ``system.registry`` is a *derived,
rebuildable index*.  An on-disk box/workset/project that is not in the registry
is **imported** on discovery: registered, ALERTed to stderr, no confirmation
prompt.  That is what lets a user move a tree to a new location (or machine) and
have kanibako re-discover it.

Two live modes, one uniform mechanism (no per-mode special-casing):
:func:`import_standalone` and :func:`import_named_workset`, both called lazily
during the resolver's ancestor walk.  A retired third **PRIMARY** mode is
sequestered in ``salvage/primary_reconcile.py`` — do not revive it here.

Conflict semantics (all modes): a NAME colliding with an entity already
registered to a *different* root/path **REFUSES** the import — nothing is
mutated and :class:`ImportConflictError` is raised.  An entity already
registered to its current path is a silent idempotent no-op.

Reference: ``llm-docs/kanibako/project/import_reconcile.py.md``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from kanibako.project import registry_store
from kanibako.settings.config import BOX_META_FILE
from kanibako.errors import KanibakoError


# The standalone box's host-side box dir (sibling of ``settings.yaml``, holding
# ``home/``), and the J2 key for a standalone import.
# ⚑ Hand-kept 2nd spelling of ``paths_defaults.STANDALONE_META_DIR`` — move both.
_STANDALONE_BOX_DIR = "box_data"


class ImportConflictError(KanibakoError):
    """A drop-in import refused: the name collides with a different entity."""


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
# "Seed model" B7).  A write-ahead ``op: import``/``op: connect`` entry brackets
# the register, so a crash mid-way leaves the entry and the next resolve replays
# this same idempotent register-if-absent import, then clears it — NO seed.  The
# op TYPE is what keeps "import never seeds" true: these ops have no seed step in
# the replay table.  The OPTIONAL ``journal`` argument comes from the resolver
# call sites (``std.journal``); None degrades to a plain register, byte-identical
# to the pre-J2 path.
#
# HARD INVARIANT: registered ==> no pending entry (the journal is empty at rest).


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

    Order (DESIGN): write entry -> register body -> clear entry.  ⚑ If the body
    raises (a genuine collision) the entry is intentionally LEFT and the
    exception propagates; recovery resumes it on the next resolve.  A None
    *journal* is a no-op bracket.  *box_path* is the host-side box dir (the one
    CONTAINING ``home/``), the same key scheme as J1 — known pre-registration.
    """
    if journal is None:
        yield
        return
    from kanibako.launch import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op=op, name=name, mode=mode, workset=workset,
    )
    yield
    # Committing step done (register returned) — clear IMMEDIATELY.  A crash here
    # leaves a stale entry that the next resolve clears via the same replay.
    journal_mod.clear_entry(journal, box_path)


def _clear_stale_import(journal: Path | None, box_path: Path) -> None:
    """Clear a stale register-only entry on an already-registered box.

    Closes the register -> clear-entry crash window: a re-resolve takes the
    import's idempotent NO-OP branch, so the clear has to happen here or the HARD
    INVARIANT breaks.  ⚑ Only an import/connect entry is cleared — a ``create``
    entry is left for the create-recovery path.  A None *journal* is a no-op.
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

    *root* is the standalone project root (the dir containing ``box_data/`` and,
    at the root, ``settings.yaml``).  Returns the registered box name, or
    ``None`` when *root* carries no standalone MARKER.
    """
    root = root.resolve()
    root_str = str(root)

    # Already registered to this exact root → no-op; clear any stale J2 entry.
    existing_name = registry_store.standalone_name_for_root(registry, root)
    if existing_name is not None:
        _clear_stale_import(journal, root / _STANDALONE_BOX_DIR)
        return existing_name

    # ⚑ Gate on the standalone MARKER (design D4): the box's own settings FILE is
    # the signal — NOT ``project.mode``.  No marker → nothing to import.
    from kanibako import kuid
    from kanibako.launch import box_identity, box_resolve
    from kanibako.settings.config import read_workset_kuid

    if not box_resolve.standalone_settings_present(root):
        return None

    # ⚑ Compose the LIVE name kuid-first (mirrors box_resolve's standalone
    # branch): the stored ``workset.kuid`` prefixes the CURRENT dir leaf, so a
    # MOVED box keeps its stable identity.  A pre-kuid box (SENTINEL) → the leaf.
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

    # J2 write-ahead: register-only, NO seed — the box is already seeded on disk.
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
    """Reconcile an on-disk workset at *root* against ``registry.worksets``.

    Reads the name from *root*'s ``settings.yaml`` ``workset.meta`` table.
    Returns the workset name, or ``None`` when there is no readable identity.
    ⚑ Does NOT rewrite the workset-create skeleton; it only registers.
    """
    root = root.resolve()
    root_str = str(root)

    from kanibako.project.workset import WORKSET_META_FILE, read_workset_meta

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

    # Register name → root in the ``worksets`` section, matching
    # ``create_workset``.  J2 write-ahead: register-only, never seeds.  A workset
    # has no single ``home/``, so its journal key is the workset ROOT.
    with _journal_register(
        journal, root, op="import", name=name, mode="named", workset=name,
    ):
        names_section[name] = root_str
        registry_store.save_section(registry, "worksets", names_section)
    _alert("workset", name, root)
    return name
