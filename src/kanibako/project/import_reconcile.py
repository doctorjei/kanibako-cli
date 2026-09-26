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

⚑ The two modes differ in where the NAME comes from, and only there.  A standalone
box composes one from its stored ``workset.kuid`` plus the live dir leaf.  A workset
records no name at all — its identity is the global registry's ``worksets:`` entry —
so a workset being imported has none to read and takes the LEAF DIRECTORY BASENAME
([R139]), which is what ``workset create`` already defaults an unnamed workset to.
⚑⚑ That a workset's name is not on disk is a fact about NAMING; it never made a
workset root unfindable, and treating it as though it had is what removed this
function once.  The marker is :func:`~kanibako.project.workset.is_workset_skeleton`.

Conflict semantics: a name colliding SAME-KIND — with an entity of the same kind
already registered to a *different* root/path — **REFUSES** the import; nothing is
mutated and :class:`ImportConflictError` is raised.  An entity already registered to
its current path is a silent idempotent no-op.  ⚑ A CROSS-KIND collision (a workset
name matching a primary BOX name) does NOT refuse: it imports and WARNS.  Refusing at
``workset create`` is affordable because a human typed the name and can retype it; on
an import nobody typed anything and there is no ``--force`` to offer, so a refusal
would strand the tree it was meant to recover ([R139]).

Reference: ``llm-docs/kanibako/project/import_reconcile.py.md``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from kanibako.identifiers import find_identifier
from kanibako.project import registry_store
from kanibako.project.names import cross_kind_shadow_hatch, register_name
from kanibako.errors import KanibakoError
from kanibako.log import get_logger
from kanibako.settings.bootstrap import STANDALONE_META_DIR

logger = get_logger("import_reconcile")


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
    at the root, ``workset.yaml``).  Returns the registered box name, or
    ``None`` when *root* carries no standalone MARKER.
    """
    root = root.resolve()
    root_str = str(root)

    # Already registered to this exact root → no-op; clear any stale J2 entry.
    existing_name = registry_store.standalone_name_for_root(registry, root)
    if existing_name is not None:
        _clear_stale_import(journal, root / STANDALONE_META_DIR)
        return existing_name

    # ⚑ Gate on the standalone MARKER (design D4): the box's own settings FILE is
    # the signal — NOT ``project.mode``.  No marker → nothing to import.
    from kanibako.launch import box_resolve

    if not box_resolve.standalone_settings_present(root):
        return None

    # ⚑ The LIVE name, by THE one naming rule; an unregistered box has no stored
    # registry name, so a pre-kuid box falls back to its leaf.
    name = box_resolve.standalone_box_name(root, None)

    # Collision check against a DIFFERENT root.
    other_root = registry_store.standalone_root(registry, name)  # ⚑ case-blind (§0)
    if other_root is not None and other_root != root_str:
        raise _conflict("standalone box", name, root, other_root)

    # J2 write-ahead: register-only, NO seed — the box is already seeded on disk.
    with _journal_register(
        journal, root / STANDALONE_META_DIR,
        op="import", name=name, mode="standalone",
    ):
        registry_store.register_standalone(registry, name, root)
    _alert("standalone box", name, root)
    return name


# ---------------------------------------------------------------------------
# NAMED (worksets)
# ---------------------------------------------------------------------------

def import_named_workset(
    registry: Path, root: Path, *,
    primary_workset: Path, journal: Path | None = None,
) -> str | None:
    """Reconcile an on-disk workset at *root* against ``registry.worksets``.

    Names it after *root*'s LEAF DIRECTORY basename ([R139]) — a workset records no
    name on disk, and the basename is the answer in the absence of another one.
    Returns that name, or ``None`` when *root* cannot be imported as a workset —
    an empty or reserved basename, or ``$HOME`` (see the guards below).
    ⚑ Does NOT rewrite the workset-create skeleton; it only registers.
    ⚑ *primary_workset* is REQUIRED, not defaulted: it is the sole input to the
    cross-kind check below, and a caller free to omit it would import a shadowed
    workset without the one warning that tells the user how to reach it.
    """
    root = root.resolve()
    root_str = str(root)

    from kanibako.project.workset import is_reserved_workset_name

    # ⚑ A DERIVED name must clear the SAME bars ``create_workset`` puts in front of a
    # typed one: no empty name (only the filesystem root has one), no reserved
    # sentinel.  It RETURNS where create RAISES, and the difference is who is asking —
    # create answers a user who can retype, this answers a treewalk stepping past an
    # ordinary directory, and a dir named ``default`` must not fail every command.
    # Declining to import leaves it what it already was: a plain primary-mode dir.
    name = root.name
    if not name or is_reserved_workset_name(name):
        return None

    # ⚑ $HOME is DECLINED here for the same reason, one step earlier than
    # ``register_name``'s refusal of it: the walk arrives at $HOME under its own
    # steam — it is the walk's own stop condition, not a path anyone chose — so a
    # home dir that happens to carry the four-dir skeleton would raise out of
    # EVERY command's mode detection.  Declining leaves it what it already was: a
    # plain primary-mode dir.  Tested directly, never caught: an ``except
    # ProjectError`` here would swallow the SAME-KIND collision refusal below.
    if root == Path.home().resolve():
        return None

    names_section = registry_store.load_section(registry, "worksets")
    current = names_section.get(name)
    if current is not None:
        if str(Path(current).resolve()) == root_str:
            # Already registered to this root → no-op; clear a stale entry.
            _clear_stale_import(journal, root)
            return name
        # SAME-KIND: the name is another WORKSET's.  Refuse, leave the tree on disk.
        raise _conflict("workset", name, root, str(current))

    # CROSS-KIND: the name is a primary BOX's.  Bare-name resolution is deterministic
    # (box before workset), so this workset lands shadowed — import it anyway and say
    # so, naming the same escape hatch that resolution names.  ⚑ NOT a refusal: see
    # the module docstring's conflict paragraph for why create's refusal cannot carry.
    from kanibako.settings.paths import load_primary_boxes

    # ⚑ Case-blind (spec §0, ⚑ NAMING RULES) — the twin of the shadow WARN in
    # ``names.resolve_name``, and it must see the same collisions that one does.
    if find_identifier(name, load_primary_boxes(primary_workset)) is not None:
        logger.warning(
            "imported workset '%s' shares its bare name with a primary box; the "
            "bare name resolves to the box, so %s.",
            name, cross_kind_shadow_hatch(name),
        )

    # Register name → root through ``register_name``, the SOLE writer of the global
    # ``worksets`` section — its own $HOME refusal still stands and is simply never
    # reached from here.  J2 write-ahead: register-only, never seeds.  A workset has
    # no single ``home/``, so its journal key is the workset ROOT.
    with _journal_register(
        journal, root, op="import", name=name, mode="named", workset=name,
    ):
        register_name(registry, name, root_str, section="worksets")
    _alert("workset", name, root)
    return name
