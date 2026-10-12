"""Helper spawning: the helper tree on disk and spawn budget management."""

from __future__ import annotations

import importlib.resources
import json
import shlex
import stat as _st
from dataclasses import dataclass
from pathlib import Path

from kanibako import boxfs
from kanibako.errors import ConfigError, KanibakoError
from kanibako.settings.bootstrap import SPAWN_BUDGET_DEFAULTS
from kanibako.settings.config import SYSTEM_HELPERS_SECTION, read_system_helpers
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.core_defaults import CANON_GUEST_ROOT, NOTEBOOK_SCRIPTS_REL
from kanibako.utils import stat_unless_gone

# ---------------------------------------------------------------------------
# Spawn budget
# ---------------------------------------------------------------------------

#: The built-in budget, applied when NO tier carries ``system.helpers.*``.  ⚑ DERIVED
#: from the declared table, never restated: the launch floor spells the same two values
#: as dotted keys (``settings_launch.SYSTEM_SCALAR_FLOOR``), and a second literal here
#: would let a box's resolved ``@system.helpers.depth`` and this in-box fallback disagree.
DEFAULT_DEPTH = SPAWN_BUDGET_DEFAULTS["depth"]
DEFAULT_BREADTH = SPAWN_BUDGET_DEFAULTS["breadth"]

#: The RO budget filename — the spawn-budget bind's document (spec §2c
#: ``box.bindings.ro[~/spawn.yaml]``): the host writes a director's resolved budget
#: beside its ``box.yaml``, and a PARENT writes its child's decremented copy into the
#: child's home.  The box reads it at ``Path.home() / SPAWN_CONFIG_FILENAME``; both
#: mounts take their dest from the one declared row (``core_defaults.helper_budget_dest``).
#:
#: ⚑ ITS CONTENT IS A SETTINGS DOCUMENT, not a bespoke shape: the two declared
#: ``system.helpers.*`` keys in their ordinary ``system: helpers:`` table, which is why
#: :func:`read_spawn_budget` also answers a box's own ``@config.settings``.  A helper's
#: copy also carries its ``meta.box.helper_num`` (:func:`write_helper_number`); a
#: director's never does.
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
    """Write the document at *path* WHOLE: the two declared ``system.helpers.*`` keys
    and nothing else.

    ⚑ A replace, not a merge: a director's start rewrites its document this way, so a
    ``meta.box.helper_num`` planted there (keyspec: UNSET for a top-level box) cannot
    survive a start and make the director act as a helper.  A helper's number is the
    hub's to add at each launch (:func:`write_helper_number`), after the parent's write.

    ⚑ *path* is never a cascade file: it is a DELIVERY document — a director's at
    ``{meta.box.path}/spawn.yaml``, a helper's at ``helpers/<N>/spawn.yaml`` — mounted
    RO through the spawn-budget bind and read back by explicit path.  No cascade
    assembles it, so nothing this writes can reach a user's settings, and the guard on
    every ``config_io`` writer (``tests/test_settings/test_defaults_enforcement.py``)
    exempts this file as non-settings.  The budget is runtime-computed (the launch's
    resolve, or ``child_budget``), so writing a cascade file from here would be the
    defaults leak that guard exists to catch.
    """
    doc: dict = {}
    node = _table(doc, SYSTEM_HELPERS_SECTION)
    node["depth"] = budget.depth
    node["breadth"] = budget.breadth
    dump_doc(path, doc)


def _table(doc: dict, sections: tuple[str, ...]) -> dict:
    """The nested table *sections* names in *doc*, created where absent."""
    node = doc
    for section in sections:
        child = node.get(section)
        if not isinstance(child, dict):
            child = {}
            node[section] = child
        node = child
    return node


#: Where a helper's own number sits in its spawn document: keyspec
#: ``meta.box.helper_num`` (``<int, set at spawn>``; UNSET for a top-level box).
HELPER_NUM_KEY: tuple[str, ...] = ("meta", "box", "helper_num")


def write_helper_number(path: Path, helper_num: int) -> None:
    """Record *helper_num* at ``meta.box.helper_num`` in a helper's spawn document.

    The hub calls this at every launch of the helper, spawn and respawn alike, before it
    binds the document RO at ``~/spawn.yaml``.  The helper reads its number from that
    file, so nothing sets a variable in its environment (system-design § Delivery at
    launch), and a ``podman exec`` shell or a user's own init script reads the same
    number.  The budget the parent wrote stays as written.
    """
    doc = load_doc(path)
    _table(doc, HELPER_NUM_KEY[:-1])[HELPER_NUM_KEY[-1]] = helper_num
    dump_doc(path, doc)


def read_helper_number(path: Path) -> int | None:
    """This box's helper number, from its spawn document at *path*; ``None`` when the
    document carries none, as in the director.

    Raises :class:`ConfigError` naming the file when it cannot be read or the value is
    not a whole number from 1.
    """
    # ⚑ Probe with ``stat``, not ``exists()``: from Python 3.12 ``exists()`` reads an
    # unreadable dir as "absent", which would make a helper act as the director.
    if stat_unless_gone(path) is None:
        return None
    node: object = load_doc(path)
    for section in HELPER_NUM_KEY:
        if not isinstance(node, dict):
            return None
        node = node.get(section)
    if node is None:
        return None
    if isinstance(node, bool) or not isinstance(node, int) or node < 1:
        raise ConfigError(
            f"the spawn document {path} sets meta.box.helper_num to {node!r}, which is "
            f"not a helper number; the hub writes a whole number from 1 at the "
            f"helper's launch."
        )
    return node


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
#: dirs (``workspace``, ``vault``) and this belongs with them.  It replaces a
#: vestigial two-level path whose first level carried nothing at all.
HELPER_SCRIPTS_RELPATH = "scripts"

#: Where a PARENT keeps its own override copy of the entrypoint wrapper, home-relative.
#: The parent IS a box, so this is the canon address for a reusable helper script, read
#: from the canon layout.  ⚑ Its ``scripts`` leaf shares a spelling with
#: :data:`HELPER_SCRIPTS_RELPATH` and nothing else: the two sides were repointed
#: independently, and renaming the helper's dir must not move a canon address.
PARENT_SCRIPTS_RELPATH = f"{CANON_GUEST_ROOT}/{NOTEBOOK_SCRIPTS_REL}"


#: A helper's workspace and vault leaves, root-relative, keyed by the ``source`` symbol
#: of the ``core:`` row that declares the director's bind of the same slot.  The helper
#: root IS the helper's home (the hub binds it at ``~``), and the hub binds each leaf
#: at its row's dest and options (``channels.helper_listener._build_helper_mounts``).
HELPER_CORE_LEAVES: dict[str, str] = {
    "project_path": "workspace",
    "vault_ro_path": "vault/ro",
    "vault_rw_path": "vault/rw",
}


# ---------------------------------------------------------------------------
# Helper state — ``helpers/<N>/state.json``
# ---------------------------------------------------------------------------
#
# ⚑ ONE CARRIER FOR BOTH SIDES: the in-box ``kanibako box helper`` verbs write it, and the
# host hub marks it ``stopped`` when it stops the helper's container at the end of the
# director's session, so a later ``respawn`` or ``list`` sees what the hub did.

HELPER_STATE_FILENAME = "state.json"


def read_helper_state(helpers_dir: Path, helper_num: int) -> dict:
    """A helper's state document; an empty dict when it has none."""
    path = helpers_dir / str(helper_num) / HELPER_STATE_FILENAME
    if not path.is_file():
        return {}
    with open(path) as f:
        return json.load(f)


def write_helper_state(helpers_dir: Path, helper_num: int, state: dict) -> None:
    """Write a helper's state document whole."""
    path = helpers_dir / str(helper_num) / HELPER_STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def mark_helper_stopped(helpers_dir: Path, helper_num: int) -> None:
    """Record that the helper's container is gone, keeping the rest of its state.

    A helper with no state document is left without one: there is nothing a verb
    could act on, and inventing a document would describe a helper no verb made.
    """
    state = read_helper_state(helpers_dir, helper_num)
    if not state or state.get("status") == "stopped":
        return
    state["status"] = "stopped"
    write_helper_state(helpers_dir, helper_num, state)


def existing_helpers(helpers_dir: Path, root: Path) -> list[int]:
    """The helper numbers under *helpers_dir*: its ``helpers/<N>/`` directories, sorted.

    Read through :mod:`kanibako.boxfs` under *root*, because the hub reads a box's tree
    with it: a link is not a helper and is skipped, never followed, at an entry or on
    the way to *helpers_dir*.  A missing *helpers_dir* has none; one that cannot be
    read raises :class:`KanibakoError` naming it.
    """
    try:
        names = boxfs.listdir(helpers_dir, root)
    except (FileNotFoundError, NotADirectoryError, boxfs.SymlinkRefusedError):
        return []
    except OSError as exc:
        raise _unreadable(helpers_dir, exc) from exc
    found = []
    for name in names:
        if not (name.isascii() and name.isdigit()):
            continue
        try:
            st = boxfs.stat(helpers_dir / name, root)
        except (FileNotFoundError, boxfs.SymlinkRefusedError):
            continue
        except OSError as exc:
            raise _unreadable(helpers_dir / name, exc) from exc
        if _st.S_ISDIR(st.st_mode):
            found.append(int(name))
    return sorted(found)


def _unreadable(path: Path, exc: OSError) -> KanibakoError:
    """The refusal for a helpers dir that cannot be read.  ⚑ Never a skip: a helper
    number under it would go uncounted, and the hub's counter could hand it out again."""
    return KanibakoError(
        f"cannot read the helper directory {path} ({exc.strerror or exc}), so the "
        f"helper numbers under it are unknown. Make it readable "
        f"(chmod u+rwx {shlex.quote(str(path))}) or remove it, then try again."
    )


def helper_tree_numbers(helpers_dir: Path, root: Path) -> set[int]:
    """Every helper number on disk in the tree under *helpers_dir*: its helpers, their
    ``helpers/`` subtrees, and so on down, read as :func:`existing_helpers` reads one.
    """
    found: set[int] = set()
    pending = [helpers_dir]
    while pending:
        level = pending.pop()
        for num in existing_helpers(level, root):
            found.add(num)
            pending.append(level / str(num) / "helpers")
    return found


def create_helper_dirs(helpers_dir: Path, helper_num: int) -> Path:
    """Create the directory layout for a single helper.

    Creates vault (with ro, rw), workspace, and ``scripts``
    directories.  Returns the helper's root directory.
    """
    root = helpers_dir / str(helper_num)
    root.mkdir(parents=True, exist_ok=True)

    # Workspace and vault (with communication channels)
    for leaf in HELPER_CORE_LEAVES.values():
        (root / leaf).mkdir(parents=True, exist_ok=True)
    (root / HELPER_SCRIPTS_RELPATH).mkdir(exist_ok=True)

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


def remove_helper_dirs(helpers_dir: Path, helper_num: int) -> None:
    """Remove a helper's directory tree (``helpers/{N}/``), if it exists."""
    import shutil

    helper_root = helpers_dir / str(helper_num)
    if helper_root.exists():
        shutil.rmtree(helper_root)


# ---------------------------------------------------------------------------
# helper-init.sh template
# ---------------------------------------------------------------------------

INIT_SCRIPT_NAME = "helper-init.sh"


def bundled_init_script() -> Path:
    """Return the path to the bundled default ``helper-init.sh``."""
    resource = importlib.resources.files("kanibako.scripts").joinpath(INIT_SCRIPT_NAME)
    return Path(str(resource))


def resolve_init_script(parent_scripts_dir: Path | None) -> Path:
    """Return the init script to use for helpers.

    Checks the parent's ``canon/notebook/scripts/`` for a custom version
    first, then falls back to the bundled default.
    """
    if parent_scripts_dir is not None:
        custom = parent_scripts_dir / INIT_SCRIPT_NAME
        if custom.is_file():
            return custom
    return bundled_init_script()
