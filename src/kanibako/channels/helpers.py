"""Helper spawning: B-ary tree numbering and spawn budget management."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc

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

DEFAULT_DEPTH = 4
DEFAULT_BREADTH = 4


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
    ro_config: SpawnBudget | None,
    host_config: SpawnBudget | None,
    cli_depth: int | None,
    cli_breadth: int | None,
) -> SpawnBudget:
    """Resolve the effective spawn budget using config precedence.

    Order: RO config > host config > CLI flags > built-in defaults.
    CLI flags only apply when neither RO nor host config exist.
    """
    if ro_config is not None:
        return ro_config
    if host_config is not None:
        return host_config
    depth = cli_depth if cli_depth is not None else DEFAULT_DEPTH
    breadth = cli_breadth if cli_breadth is not None else DEFAULT_BREADTH
    return SpawnBudget(depth=depth, breadth=breadth)


# ---------------------------------------------------------------------------
# Spawn config I/O
# ---------------------------------------------------------------------------


def read_spawn_config(path: Path) -> SpawnBudget | None:
    """Read spawn limits from a config file (kanibako.cfg or RO spawn config).

    Looks for a ``spawn`` section with ``depth`` and ``breadth`` keys.
    Returns ``None`` if the file or section is absent.
    """
    if not path.exists():
        return None
    data = load_doc(path)
    spawn = data.get("spawn")
    if spawn is None:
        return None
    return SpawnBudget(
        depth=int(spawn.get("depth", DEFAULT_DEPTH)),
        breadth=int(spawn.get("breadth", DEFAULT_BREADTH)),
    )


def write_spawn_config(path: Path, budget: SpawnBudget) -> None:
    """Write spawn limits as a ``spawn`` section in a config file.

    For RO spawn configs this creates a standalone file.
    For kanibako.cfg this preserves other sections.
    """
    existing = load_doc(path)
    existing["spawn"] = {"depth": budget.depth, "breadth": budget.breadth}
    dump_doc(path, existing)


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

#: Where a PARENT keeps its own override copy of the entrypoint wrapper.  The parent IS
#: a box, so this is the canon address for a reusable helper script.
PARENT_SCRIPTS_RELPATH = ("canon", "notebook", "scripts")


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
