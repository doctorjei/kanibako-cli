"""Helper spawning: B-ary tree numbering and spawn budget management."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path

from kanibako.settings.bootstrap import SPAWN_BUDGET_DEFAULTS
from kanibako.settings.config import SYSTEM_HELPERS_SECTION, read_system_helpers
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.core_defaults import CANON_GUEST_ROOT, NOTEBOOK_SCRIPTS_REL

# When breadth is unlimited (-1), use 2^16 for numbering purposes.
# Large enough to never collide; small enough for human-readable numbers.
UNLIMITED_BREADTH = 2**16


def effective_breadth(breadth: int) -> int:
    """Return the breadth used for numbering.

    Maps -1 (unlimited) to ``UNLIMITED_BREADTH``.  Positive values pass
    through unchanged.
    """
    if breadth == -1:
        return UNLIMITED_BREADTH
    if breadth < 1:
        msg = f"breadth must be positive or -1, got {breadth}"
        raise ValueError(msg)
    return breadth


def parent_of(agent: int, breadth: int) -> int | None:
    """Return the global number of *agent*'s parent.

    Returns ``None`` if *agent* is the director (agent 0).
    """
    if agent == 0:
        return None
    b = effective_breadth(breadth)
    return (agent - 1) // b


# ---------------------------------------------------------------------------
# Spawn budget
# ---------------------------------------------------------------------------

#: The built-in budget, applied when NO tier carries ``system.helpers.*``.  ⚑ DERIVED
#: from the declared table, never restated: the launch floor spells the same two values
#: as dotted keys (``settings_launch.SYSTEM_SCALAR_FLOOR``), and a second literal here
#: would let a box's resolved ``@system.helpers.depth`` and this in-box fallback disagree.
DEFAULT_DEPTH = SPAWN_BUDGET_DEFAULTS["depth"]
DEFAULT_BREADTH = SPAWN_BUDGET_DEFAULTS["breadth"]

#: The RO budget filename — the document a PARENT writes into its child's home,
#: carrying that child's decremented budget.  ⚑ BOTH ENDS TAKE THIS CONSTANT: the
#: helper reads it at ``Path.home() / SPAWN_CONFIG_FILENAME`` and the hub mounts it
#: at the matching guest dest, so a dest spelled by hand would land where the reader
#: never looks (``helper_listener._build_helper_mounts``).
#:
#: ⚑ ITS CONTENT IS A SETTINGS DOCUMENT, not a bespoke shape: the two declared
#: ``system.helpers.*`` keys in their ordinary ``system: helpers:`` table, which is why
#: :func:`read_spawn_budget` also answers a box's own ``@config.settings``.
SPAWN_CONFIG_FILENAME = "spawn.yaml"


@dataclass(frozen=True)
class SpawnBudget:
    """Spawn limits for an agent.  Immutable."""

    depth: int = DEFAULT_DEPTH
    breadth: int = DEFAULT_BREADTH


def check_spawn_allowed(budget: SpawnBudget, current_children: int) -> str | None:
    """Return an error message if spawning is not allowed, else ``None``."""
    if budget.depth == 0:
        return "spawn depth exhausted (depth=0)"
    if budget.breadth != -1 and current_children >= budget.breadth:
        return f"breadth limit reached ({current_children}/{budget.breadth})"
    return None


def child_budget(parent: SpawnBudget) -> SpawnBudget:
    """Compute the spawn budget for a child of *parent*.

    Depth is decremented by 1 (unless unlimited).  Breadth is inherited.
    """
    new_depth = parent.depth if parent.depth == -1 else parent.depth - 1
    return SpawnBudget(depth=new_depth, breadth=parent.breadth)


def resolve_spawn_budget(
    handed_down: SpawnBudget | None,
    own_settings: SpawnBudget | None,
    cli_depth: int | None,
    cli_breadth: int | None,
) -> SpawnBudget:
    """Resolve the effective spawn budget.

    Order: the budget a PARENT handed down > this box's own ``system.helpers.*`` >
    CLI flags > built-in defaults.  The flags only apply when neither tier carries a
    budget: a handed-down limit is not something the limited box may raise.
    """
    if handed_down is not None:
        return handed_down
    if own_settings is not None:
        return own_settings
    depth = cli_depth if cli_depth is not None else DEFAULT_DEPTH
    breadth = cli_breadth if cli_breadth is not None else DEFAULT_BREADTH
    return SpawnBudget(depth=depth, breadth=breadth)


# ---------------------------------------------------------------------------
# Spawn budget I/O — the DECLARED ``system.helpers.*`` keys (spec §2g)
# ---------------------------------------------------------------------------
#
# ⚑ THERE IS ONE READER FOR BOTH TIERS, and that is the point of the declaration.
# Until 2026-09-19 the budget was an undeclared ``spawn:`` table read out of a bespoke
# ``<XDG_CONFIG_HOME>/kanibako/spawn.yaml``; a §0 keyspace has no such key, so the file
# and the table are gone and both tiers are now ordinary settings documents.


def read_spawn_budget(path: Path) -> SpawnBudget | None:
    """The budget a settings document carries, or ``None`` when it declares neither leaf.

    A document that names only one leaf gets the built-in for the other — that is a
    partial override, not an absence.
    """
    leaves = read_system_helpers(path)
    if not leaves:
        return None
    return SpawnBudget(
        depth=leaves.get("depth", DEFAULT_DEPTH),
        breadth=leaves.get("breadth", DEFAULT_BREADTH),
    )


def write_spawn_budget(path: Path, budget: SpawnBudget) -> None:
    """Write *budget* as the two declared ``system.helpers.*`` keys.

    🛑 DELIBERATELY NOT ``config_io.write_nested_key``, and the reason is the guard on
    that seam rather than convenience.  It is the one write primitive for a CASCADE
    settings file, allowlisted so that a runtime-computed DEFAULT cannot be persisted
    into one (``tests/test_settings/test_defaults_enforcement.py``) — and a child's
    budget IS runtime-computed, ``child_budget`` of whatever the parent resolved.
    ⚑ *path* is never a cascade file: it is the per-child DELIVERY document at
    ``helpers/<N>/spawn.yaml``, mounted RO into that helper and read back by explicit
    path.  No cascade assembles it, so nothing this writes can reach a user's settings.
    Routing it through the guarded seam would ask that guard to bless the exact write it
    exists to catch; writing a cascade file from here would be the end run.
    """
    doc = load_doc(path)
    node = doc
    for section in SYSTEM_HELPERS_SECTION:
        child = node.get(section)
        if not isinstance(child, dict):
            child = {}
            node[section] = child
        node = child
    node["depth"] = budget.depth
    node["breadth"] = budget.breadth
    dump_doc(path, doc)


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------

#: The helper-root-relative scripts dir, holding the entrypoint wrapper.
#:
#: ⚑ FLAT, and deliberately NOT ``canon/notebook/scripts``.  A helper home is not a
#: box: it has no canon binds, and ``core_defaults.materialize_canon_skeleton_if_present``
#: keys off the presence of a ``canon/`` dir, so putting the script under one would turn
#: that no-op into a real skeleton materialization — "a silent layout change made by the
#: wrong seam", in that function's own words.  The rest of a helper root is flat plain
#: dirs (``workspace``, ``vault``, ``peers``) and this belongs with them.  It replaces a
#: vestigial two-level path whose first level carried nothing at all.
HELPER_SCRIPTS_RELPATH = "scripts"

#: Where a PARENT keeps its own override copy of the entrypoint wrapper, home-relative.
#: The parent IS a box, so this is the canon address for a reusable helper script, read
#: from the canon layout.  ⚑ Its ``scripts`` leaf shares a spelling with
#: :data:`HELPER_SCRIPTS_RELPATH` and nothing else: the two sides were repointed
#: independently, and renaming the helper's dir must not move a canon address.
PARENT_SCRIPTS_RELPATH = f"{CANON_GUEST_ROOT}/{NOTEBOOK_SCRIPTS_REL}"


def create_helper_dirs(helpers_dir: Path, helper_num: int) -> Path:
    """Create the directory layout for a single helper.

    Creates vault (with ro, rw), workspace, ``scripts``,
    and peers directories.  Returns the helper's root directory.
    """
    root = helpers_dir / str(helper_num)
    root.mkdir(parents=True, exist_ok=True)

    # Vault with communication channels
    vault = root / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "ro").mkdir(exist_ok=True)
    (vault / "rw").mkdir(exist_ok=True)

    # Standard layout
    (root / "workspace").mkdir(exist_ok=True)
    (root / HELPER_SCRIPTS_RELPATH).mkdir(exist_ok=True)

    # Peers directory
    (root / "peers").mkdir(exist_ok=True)

    return root


def create_broadcast_dirs(helpers_dir: Path) -> Path:
    """Create the broadcast channel directories under ``helpers/``.

    Creates ``all/rw`` and ``all/ro``.  Idempotent.
    Returns the ``all/`` directory.
    """
    all_dir = helpers_dir / "all"
    (all_dir / "rw").mkdir(parents=True, exist_ok=True)
    (all_dir / "ro").mkdir(parents=True, exist_ok=True)
    return all_dir


def create_peer_channels(
    helpers_dir: Path,
    new_helper: int,
    existing_helpers: list[int],
) -> None:
    """Create peer channels between *new_helper* and each existing sibling.

    For each pair (A, B) where A < B, creates:
    - ``A:B-ro`` directory (A writes, B reads)
    - ``B:A-ro`` directory (B writes, A reads)
    - ``A:B-rw`` directory (shared read-write, owned by lower number)

    The directories are created under ``helpers_dir`` and symlinked into
    each helper's ``peers/`` directory.
    """
    channels_dir = helpers_dir / "channels"
    channels_dir.mkdir(exist_ok=True)

    for existing in existing_helpers:
        lower = min(new_helper, existing)
        higher = max(new_helper, existing)

        # Create the three channel directories
        ro_low_high = channels_dir / f"{lower}:{higher}-ro"
        ro_high_low = channels_dir / f"{higher}:{lower}-ro"
        rw_shared = channels_dir / f"{lower}:{higher}-rw"

        ro_low_high.mkdir(exist_ok=True)
        ro_high_low.mkdir(exist_ok=True)
        rw_shared.mkdir(exist_ok=True)

        # Symlink into each helper's peers/
        _link_peer(helpers_dir, lower, f"{lower}:{higher}-ro", ro_low_high)
        _link_peer(helpers_dir, lower, f"{higher}:{lower}-ro", ro_high_low)
        _link_peer(helpers_dir, lower, f"{lower}:{higher}-rw", rw_shared)

        _link_peer(helpers_dir, higher, f"{lower}:{higher}-ro", ro_low_high)
        _link_peer(helpers_dir, higher, f"{higher}:{lower}-ro", ro_high_low)
        _link_peer(helpers_dir, higher, f"{lower}:{higher}-rw", rw_shared)


def _link_peer(helpers_dir: Path, helper_num: int, name: str, target: Path) -> None:
    """Create a symlink in helper's peers/ pointing to a channel directory."""
    link = helpers_dir / str(helper_num) / "peers" / name
    if not link.exists():
        link.symlink_to(target.resolve())


def link_broadcast(helpers_dir: Path, helper_num: int) -> None:
    """Create an ``all`` symlink in a helper's filesystem pointing to broadcast dirs."""
    all_dir = helpers_dir / "all"
    link = helpers_dir / str(helper_num) / "all"
    if not link.exists():
        link.symlink_to(all_dir.resolve())


def remove_helper_dirs(
    helpers_dir: Path,
    helper_num: int,
    sibling_helpers: list[int],
) -> None:
    """Remove a helper's directory tree and clean up its peer channels.

    Removes:
    - The helper's root directory (``helpers/{N}/``)
    - Channel directories involving this helper
    - Peer symlinks in siblings that pointed to removed channels
    """
    import shutil

    # Remove peer symlinks in siblings and channel dirs
    channels_dir = helpers_dir / "channels"
    for sibling in sibling_helpers:
        lower = min(helper_num, sibling)
        higher = max(helper_num, sibling)
        channel_names = [
            f"{lower}:{higher}-ro",
            f"{higher}:{lower}-ro",
            f"{lower}:{higher}-rw",
        ]
        # Remove symlinks from the sibling's peers/
        for name in channel_names:
            link = helpers_dir / str(sibling) / "peers" / name
            if link.is_symlink():
                link.unlink()
        # Remove channel directories
        for name in channel_names:
            chan = channels_dir / name
            if chan.exists():
                shutil.rmtree(chan)

    # Remove the helper's root directory
    helper_root = helpers_dir / str(helper_num)
    if helper_root.exists():
        shutil.rmtree(helper_root)


# ---------------------------------------------------------------------------
# helper-init.sh template
# ---------------------------------------------------------------------------

_INIT_SCRIPT_NAME = "helper-init.sh"


def bundled_init_script() -> Path:
    """Return the path to the bundled default ``helper-init.sh``."""
    resource = importlib.resources.files("kanibako.scripts").joinpath(_INIT_SCRIPT_NAME)
    return Path(str(resource))


def resolve_init_script(parent_scripts_dir: Path | None) -> Path:
    """Return the init script to use for helpers.

    Checks the parent's ``canon/notebook/scripts/`` for a custom version
    first, then falls back to the bundled default.
    """
    if parent_scripts_dir is not None:
        custom = parent_scripts_dir / _INIT_SCRIPT_NAME
        if custom.is_file():
            return custom
    return bundled_init_script()
