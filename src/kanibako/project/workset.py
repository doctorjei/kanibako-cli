"""Workset data model and persistence — see ``llm-docs/kanibako/project/workset.py.md``.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  Terminology:

* **workset root** — the user-chosen dir; holds the boxes dir, the workspaces dir,
  ``vault/``, the logs dir, ``auth/``, ``channels/`` and — BOTH OPTIONAL —
  ``registry.yaml`` and ``workset.yaml``.  ⚑ A freshly created root has FOUR
  DIRS AND NO FILES.  ⚑ Only ``vault/`` is spelled with a literal leaf here: the
  others are REPOINTABLE keys, so their on-disk names are whatever
  ``workset.{boxes,workspaces,logs,channelroot}`` resolve to.  ⚑⚑ ``vault/`` is
  the literal only as a PARENT — its two arms ``workset.{vault_ro,vault_rw}``
  are themselves repointable keys and are RESOLVED, so a box's vault need not
  live under this root at all.
* **identity** — the workset's entry in the GLOBAL registry's ``worksets:``
  section (``@config.registry``), mapping its NAME to its ROOT.  ⚑⚑ THAT MAPPING
  IS THE WHOLE OF A WORKSET'S IDENTITY: nothing under the workset root records a
  name, and there is no identity table in either file there.  ⚑ That is a fact
  about NAMING, not about FINDING ([R139]): a root is still found on disk by its
  four-dir skeleton (:func:`is_workset_skeleton`), and an unregistered one is
  imported under its leaf directory name — exactly as ``workset create`` already
  defaults a name it was not given.  The
  root ``registry.yaml`` carries the ``boxes:`` MEMBERSHIP only; the root
  ``workset.yaml`` carries SETTINGS ONLY, is sparse, and MAY BE ABSENT.  The
  runtime ``meta.workset.*`` keys (spec §1A) are derived at launch from the
  treewalk, never read off disk.  ⚑ 1.6.0/1.7.x kept a ``workset.meta`` identity
  table in ``workset.yaml``, and an unreleased 1.8.0 build a ``meta.workset``
  one; BOTH are RETIRED and now HARD-REFUSE — see
  :func:`refuse_retired_workset_identity`.
* **default workset** — the synthesized, never-persisted group of default-mode
  projects, rooted at ``@config.primary_workset``.
* **connected (external) box** — a member whose registered path lies OUTSIDE the
  workset root (D10).

⚑ The llm-doc carries the CORRECTED root layout; the tree that used to live here
had drifted (it named a ``worksets.yaml`` that no longer exists).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kanibako.project import registry_store, workset_registry
from kanibako.settings import paths_defaults
from kanibako.settings.config_io import load_doc
from kanibako.errors import LegacyWorksetIdentityError, WorksetError
from kanibako.project.names import register_name, unregister_name
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.workset_dirkeys import resolve_workset_dir_key
# ⚑ FORWARD edge of a documented cycle: ``settings/paths.py`` breaks it by DEFERRING
# its ``project.workset`` imports into function bodies — do not add a module-scope
# edge back this way.
from kanibako.settings.paths import StandardPaths

# ⚑⚑ EVERY NAME BELOW IS AN ALIAS, NEVER A VALUE.  The defaults themselves live in
# ``settings/paths_defaults.py``, the designated defaults file, and are materialized
# THERE AND NOWHERE ELSE; re-spelling one here made a second carrier that could drift.
# The local names stay only because the call sites read better with them.

# Default leaves for the RESOLVED workset dir keys — the spec's per-mode default
# formula ``@meta.workset.path/<leaf>``, applied ONCE per key in its resolver below
# (§3.3: real and USED).  ⚑ A default leaf is what a key falls back to, NOT the path
# component: every one of these is repointable, so nothing may join it directly.
BOXES_DIR_NAME = paths_defaults.BOXES_PATH
_WORKSPACES_LEAF = paths_defaults.WORKSPACES_PATH
_STANDALONE_WORKSPACE_LEAF = paths_defaults.WORKSPACE_PATH
_CHANNELROOT_LEAF = paths_defaults.CHANNELS_PATH
_LOGS_LEAF = paths_defaults.LOGS_PATH

# The two leaves the WORKSET STAMP writes (``launch/templates.py``).  ⚑ They are the
# WORKSET-scope spelling of two entries ``templates.SCOPE_WHITELISTS["workset"]``
# permits; ``templates.AGENT_TEMPLATE_STORE_REL`` is the AGENT-scope carrier of the
# same word and stays separate — an agent store's ``template/`` is a fixed store leaf,
# a workset's is the repointable ``workset.template``, and importing one for the other
# would invert the project -> launch dependency as well as conflate two keys.
# ⚑ They are NOT in ``paths_defaults`` with their five siblings because no OTHER module
# spells them; add them there the moment a second consumer appears.
_CANON_LEAF = "canon"
_TEMPLATE_LEAF = "template"

# ⚑ The ONE skeleton dir that names NO KEY: the keyspec declares ``workset.vault_ro``
# and ``workset.vault_rw`` (``@meta.workset.path/vault/{ro,rw}``) and no ``workset.vault``
# at all, so ``vault/`` is only their shared DEFAULT PARENT — there is nothing to resolve
# it through and it is always ``<root>/vault``.  ⚑ DELIBERATE, not an oversight: it keeps
# the skeleton a SINGLE list that ``create_workset`` stamps and ``is_workset_skeleton``
# tests, at the cost of one honestly-documented non-key.
# ⚑⚑ THE PARENT IS THE NON-KEY; THE TWO ARMS ARE NOT.  ``vault_ro`` and ``vault_rw`` are
# declared, CLI-settable, repointable keys, so the arms are RESOLVED below and nothing may
# compose ``_VAULT_LEAF / ro`` again — that composition WAS the defect: a repoint was
# accepted by the settings file and ignored by the filesystem.
_VAULT_LEAF = paths_defaults.VAULT_PATH
_VAULT_RO_KEY = "vault_ro"
_VAULT_RW_KEY = "vault_rw"
_VAULT_RO_LEAF = f"{_VAULT_LEAF}/{paths_defaults.RO_PATH}"
_VAULT_RW_LEAF = f"{_VAULT_LEAF}/{paths_defaults.RW_PATH}"


# ---------------------------------------------------------------------------
# Resolved workset dir keys (workset.workspaces / workset.channelroot / workset.canon
# / workset.template / workset.vault_ro / workset.vault_rw) — thin per-key faces over
# the ONE no-snapshot route,
# ``settings/workset_dirkeys.resolve_workset_dir_key``.  ⚑ These read the leaf out
# of the workset.yaml table; the ROUTE owns every token rule (@-refs, $XDG, ~) and
# owns the refusal.  ``workset_registry.resolve_workset_registry_path`` is a ninth
# face on the same route — do not give any of them a private expansion again.
# ---------------------------------------------------------------------------

def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None:
    """Best-effort read of *root*'s workset ``workset.yaml`` document (``None`` on any failure)."""
    path = root / WORKSET_META_FILE
    if not path.is_file():
        return None
    try:
        data = load_doc(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _workset_path_repoint(
    workset_settings: Mapping[str, Any] | None, leaf: str,
) -> str | None:
    """Return the RAW ``workset.<leaf>`` repoint from the routed ``workset: {<leaf>: …}`` slot."""
    if isinstance(workset_settings, Mapping):
        workset_table = workset_settings.get("workset")
        if isinstance(workset_table, Mapping):
            repoint = workset_table.get(leaf)
            if repoint:
                return str(repoint)
    return None


def resolve_workset_workspaces(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, standalone: bool = False,
) -> Path:
    """Return the resolved ``workset.workspaces`` dir (*standalone* selects the singular default)."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _WORKSPACES_LEAF),
        _STANDALONE_WORKSPACE_LEAF if standalone else _WORKSPACES_LEAF,
        key=_WORKSPACES_LEAF,
    )


def resolve_workset_boxes(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.boxes`` dir — the BOX-tree root under a workset."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, BOXES_DIR_NAME),
        BOXES_DIR_NAME,
        key=BOXES_DIR_NAME,
    )


def resolve_workset_logs(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.logs`` dir — ⚑ primary/named ONLY; standalone logs to the box."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _LOGS_LEAF),
        _LOGS_LEAF,
        key=_LOGS_LEAF,
    )


def resolve_workset_channelroot(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.channelroot`` — ⚑ primary/named ONLY; callers gate on mode."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, "channelroot"),
        _CHANNELROOT_LEAF,
        key="channelroot",
    )


def resolve_workset_canon(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.canon`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _CANON_LEAF),
        _CANON_LEAF,
        key=_CANON_LEAF,
    )


def resolve_workset_template(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.template`` dir — ⚑ primary/named ONLY; <None> in standalone."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _TEMPLATE_LEAF),
        _TEMPLATE_LEAF,
        key=_TEMPLATE_LEAF,
    )


def resolve_workset_vault_ro(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.vault_ro`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _VAULT_RO_KEY),
        _VAULT_RO_LEAF,
        key=_VAULT_RO_KEY,
    )


def resolve_workset_vault_rw(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.vault_rw`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included."""
    return resolve_workset_dir_key(
        workset_root,
        _workset_path_repoint(workset_settings, _VAULT_RW_KEY),
        _VAULT_RW_LEAF,
        key=_VAULT_RW_KEY,
    )


def resolve_workset_vault_pair(workset_root: Path) -> tuple[Path, Path]:
    """The resolved ``(vault_ro, vault_rw)`` for *workset_root*, off ONE workset.yaml read.

    ⚑ The pair form exists because EVERY consumer wants both arms, and reading the file
    once per arm opens a window for the two to disagree about the same document — the
    same reason ``_workset_skeleton_dirs`` takes one read for its three resolutions.
    """
    settings_doc = load_workset_settings_doc(workset_root)
    return (resolve_workset_vault_ro(workset_root, settings_doc),
            resolve_workset_vault_rw(workset_root, settings_doc))


# ---------------------------------------------------------------------------
# Failure-consistency: a tiny LIFO unwind stack for multi-step mutations.
# ⚑ Mirrors ``commands/box/_lifecycle.py::_Unwind`` (minus on_success/finish).
# ---------------------------------------------------------------------------

class _Unwind:
    """LIFO stack of compensating actions for fail-consistent mutations."""

    def __init__(self) -> None:
        self._actions: list[Callable[[], None]] = []

    def push(self, action: Callable[[], None]) -> None:
        self._actions.append(action)

    def run(self) -> None:
        while self._actions:
            action = self._actions.pop()
            try:
                action()
            except Exception:  # noqa: BLE001 - best-effort restore
                pass


@contextmanager
def _journal_connect(
    journal: Path | None,
    box_path: Path,
    *,
    name: str,
    workset: str | None = None,
    workspace: str | None = None,
):
    """Bracket a ``connect`` register with a J2 write-ahead journal entry (no seed step)."""
    if journal is None:
        yield
        return
    from kanibako.launch import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op="connect", name=name, mode="named",
        workset=workset, workspace=workspace,
    )
    yield
    journal_mod.clear_entry(journal, box_path)


# Identity of the synthesized "default" workset — ⚑ VIRTUAL, never written to disk.
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"

# Sentinels reserved by the three-mode model (specs/settings-keyspace-1.8.0.md §2c);
# ⚑ a workset name is a user-typed channel address, so collisions REFUSE, never resolve.
RESERVED_WORKSET_NAMES = frozenset(
    {DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS, "__PRIMARY__", "__STANDALONE__"}
)


def is_reserved_workset_name(name: str) -> bool:
    """Return True if *name* is a reserved sentinel (cannot be a NAMED workset)."""
    return name in RESERVED_WORKSET_NAMES


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WorksetProject:
    """One ``boxes:`` membership row, in memory — ⚑ name + path ONLY (B7); no ``seeded`` field."""

    name: str
    # ⚑ The member's REAL workspace, and the ONE place the registry records it: the
    # external dir for a connect, ``workspaces/<name>`` for an in-tree member.
    source_path: Path


@dataclass
class Workset:
    """In-memory representation of a workset.

    ⚑ *name* comes from the caller, which got it from the GLOBAL registry — it is
    never read off disk, because nothing under *root* records it.
    """

    name: str
    root: Path
    projects: list[WorksetProject] = field(default_factory=list)
    is_default: bool = False                 # True = synthesized default workset
    #: RAW ``workset.workspaces`` repoint from the root workset.yaml; ``None`` = unset.
    workspaces_repoint: str | None = None

    # Convenience paths -------------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        # ⚑ KNOWN GAP, deliberately NOT closed here: ``workset.boxes`` IS repointable and
        # detection now resolves it, but this property still composes the default leaf, so
        # a repointed root is FOUND while its box trees are still created and removed under
        # ``<root>/boxes``.  Resolving it changes where boxes LIVE — add/remove/move/delete
        # and ``settings/paths.py`` all compose off this — which is a store-layout change,
        # not a detection fix.  Do not "just resolve it" without that being the task.
        return self.root / BOXES_DIR_NAME

    @property
    def workspaces_dir(self) -> Path:
        # ⚑ RESOLVED, not composed (§3.3: real and USED) — the only one of the five.
        return resolve_workset_dir_key(
            self.root, self.workspaces_repoint, _WORKSPACES_LEAF,
            key=_WORKSPACES_LEAF,
        )

    @property
    def vault_dir(self) -> Path:
        # ⚑ THE SKELETON DIR ONLY — the non-key shared DEFAULT PARENT (see _VAULT_LEAF).
        # 🛑 DO NOT build a vault path off this: ``vault_ro``/``vault_rw`` are repointable
        # keys, so ``vault_dir / "ro"`` answers a key the settings file may have moved.
        # Use ``vault_ro_dir`` / ``vault_rw_dir`` (or ``resolve_workset_vault_pair``).
        return self.root / _VAULT_LEAF

    @property
    def vault_ro_dir(self) -> Path:
        """The resolved ``workset.vault_ro`` — ⚑ RESOLVED, not composed."""
        return resolve_workset_vault_ro(self.root, load_workset_settings_doc(self.root))

    @property
    def vault_rw_dir(self) -> Path:
        """The resolved ``workset.vault_rw`` — ⚑ RESOLVED, not composed."""
        return resolve_workset_vault_rw(self.root, load_workset_settings_doc(self.root))

    @property
    def logs_dir(self) -> Path:
        # ⚑ Same KNOWN GAP as ``projects_dir``: ``workset.logs`` is repointable and the
        # launch seam already honors it (``settings_launch``/``core_defaults``), but this
        # convenience property composes the default leaf.  See ``projects_dir``.
        return self.root / _LOGS_LEAF

    @property
    def settings_path(self) -> Path:
        """The workset-tier settings file — SETTINGS ONLY, and may not exist."""
        return self.root / WORKSET_META_FILE

    @property
    def registry_path(self) -> Path:
        """The resolved per-workset ``registry.yaml`` — the ``boxes:`` membership, and only that."""
        return workset_registry.resolve_workset_registry_path(
            self.root, load_workset_settings_doc(self.root),
        )


# ---------------------------------------------------------------------------
# Loading a workset: its NAME comes from the caller (who read it out of the global
# registry), its MEMBERS from the root registry.yaml's ``boxes:`` section, and its
# settings from the root workset.yaml.  ⚑ Neither file under the root is read for
# identity — there is none there to read.
# ---------------------------------------------------------------------------

def _load_workset(root: Path, name: str) -> Workset:
    """Build the :class:`Workset` for the globally-registered *name* rooted at *root*."""
    # ⚑ A root still carrying a RETIRED identity table refuses here, with the named
    # cure — it is the load path, not detection, that a 1.6/1.7 user reaches first
    # (their workset IS globally registered, so detection resolves it fine).
    refuse_retired_workset_identity(root)
    settings_doc = load_workset_settings_doc(root)
    registry_path = workset_registry.resolve_workset_registry_path(root, settings_doc)
    # ⚑ Members come from ``boxes:``, which is the WHOLE of what that file holds, and
    # the path is recorded there exactly once.
    projects = [
        WorksetProject(name=box_name, source_path=Path(box_path))
        for box_name, box_path in workset_registry.load_workset_boxes(
            registry_path,
        ).items()
    ]
    return Workset(
        name=name, root=root, projects=projects,
        workspaces_repoint=_workset_path_repoint(settings_doc, _WORKSPACES_LEAF),
    )


def refuse_retired_workset_identity(root: Path) -> None:
    """RAISE when *root*'s workset.yaml still carries a RETIRED workset IDENTITY table.

    ⚑ DETECTED ONLY SO IT CAN BE DIAGNOSED.  v1.8.0 is a clean break: there is no compat
    read and no auto-migration.  Reading past this table is exactly the silent failure —
    1.8.0 takes the file for ordinary settings, drops the table with a generic warning
    and never looks at the `projects` list beside it, so a legacy workset's members stop
    resolving with nothing printed to say why.  BOTH retired spellings are caught:
    1.6.0/1.7.x wrote ``workset.meta``, and the unreleased 1.8.0 tree briefly wrote
    ``meta.workset``.
    """
    data = load_workset_settings_doc(root)
    if data is None:
        return
    workset_tbl = data.get("workset")
    meta_tbl = data.get("meta")
    if isinstance(workset_tbl, Mapping) and isinstance(workset_tbl.get("meta"), Mapping):
        retired = "workset.meta"
        tail = (
            "the top-level `workset:` table is still where this workset's own SETTINGS "
            "live (`workset.bindings`, `workset.workspaces`, `workset.auth`, …), so "
            "delete only the `meta:` table from inside it"
        )
    elif isinstance(meta_tbl, Mapping) and isinstance(meta_tbl.get("workset"), Mapping):
        retired = "meta.workset"
        tail = (
            "the top-level `meta:` table holds nothing a workset root may set, so "
            "delete the whole of it"
        )
    else:
        return
    path = root / WORKSET_META_FILE
    raise LegacyWorksetIdentityError(
        f"'{retired}' is a RETIRED location for a named workset's identity table "
        f"and is still the shape of {path}.\n"
        f"THE RULE: a workset has NO identity table on disk under its root. Its name "
        f"lives in ONE place — the `worksets:` section of the global registry, which "
        f"maps that name to this directory and is what `kanibako workset list` reads. "
        f"This file carries SETTINGS ONLY, is sparse, and may be absent entirely; "
        f"MEMBERSHIP lives in {root / 'registry.yaml'} as flat `boxes:` entries, "
        f"`name: path`. kanibako 1.6.0 and 1.7.x kept the name, a `created` stamp and a "
        f"`projects` list here, so every workset root those releases created carries "
        f"them. Refusing rather than running: 1.8.0 reads this file as ordinary "
        f"settings, so it would drop the table as an unsettable `meta` namespace and "
        f"ignore the `projects` list — your connected boxes would stop resolving with "
        f"nothing printed to say why.\n"
        f"  Fix, BY HAND:\n"
        f"\n"
        f"    1. Each entry of the `projects` LIST becomes one flat entry of the "
        f"`boxes:` section in {root / 'registry.yaml'}, keyed by its `name`, with its "
        f"`source_path` as the value. An entry already there is already correct — leave "
        f"it:\n"
        f"\n"
        f"         boxes:\n"
        f"           <project name>: <its source_path>\n"
        f"\n"
        f"    2. Delete the `{retired}` table from {path} — name, created stamp and "
        f"projects together. NOTHING replaces it: `workset create` already registered "
        f"this workset under its name in the global registry, and `created` is not "
        f"recorded anywhere in 1.8.0.\n"
        f"\n"
        f"  Everything else in {path} stays put: {tail}. If nothing is left, delete the "
        f"file outright — a workset root no longer needs one. kanibako 1.8.0 ships no "
        f"automatic migration for this — see MIGRATION.md §2.43."
    )


# ---------------------------------------------------------------------------
# Global worksets registry: the ``worksets`` section of ``config.registry``.
# ⚑⚑ THE ONE PLACE A WORKSET'S NAME IS RECORDED — its identity, not an index of it.
# ⚑ ONE section in ONE file; ``register_name``/``unregister_name`` are the SOLE writers.
# ---------------------------------------------------------------------------

def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    section = registry_store.load_section(std.registry, "worksets")
    return {name: Path(root) for name, root in section.items()}


# ---------------------------------------------------------------------------
# The workset SKELETON — ⚑⚑ ONE definition with TWO consumers: ``create_workset``
# STAMPS these dirs and :func:`is_workset_skeleton` TESTS for them.  They are a
# matched pair by construction; a second, hand-copied list of the same leaf names
# would let the stamp and the test drift, and a drifted test stops finding worksets
# that are really there — which is how the ancestor walk's NAMED arm went wrong once
# already ([R139]).
# ---------------------------------------------------------------------------

def _workset_skeleton_dirs(root: Path) -> tuple[Path, ...]:
    """The four dirs a workset root is made of — ⚑ three RESOLVED, ``vault`` alone literal."""
    # ⚑⚑ THE RESOLVED DIRS ARE THE LOCATOR (system-design, NAMED arm of "Detect =
    # ancestor-walk").  ``boxes``, ``workspaces`` and ``logs`` are all declared,
    # repointable workset keys, so the locator must be what each one RESOLVES to.
    # Testing the literal leaf instead made a root that repointed ``workset.boxes`` or
    # ``workset.logs`` INVISIBLE to detection: the walk looked for ``<root>/boxes`` and
    # ``<root>/logs``, which the repoint is precisely what removes.
    # ⚑ ``vault`` is the one literal, and correctly so — no key names it (see _VAULT_LEAF).
    # ⚑ ONE read feeds all three resolutions; reading workset.yaml per key would open a
    # window for the three to disagree about the same file.  At create time *root* has no
    # workset.yaml yet, so the read yields None and every leaf is its default — the same
    # four dirs the pre-refactor literals made.
    settings_doc = load_workset_settings_doc(root)
    return (
        resolve_workset_boxes(root, settings_doc),
        resolve_workset_workspaces(root, settings_doc),
        root / _VAULT_LEAF,
        resolve_workset_logs(root, settings_doc),
    )


def is_workset_skeleton(root: Path) -> bool:
    """True when *root* carries the WHOLE skeleton — ⚑ the NAMED-root detection primitive.

    ⚑ Presence-only, and it names nothing.  A workset root records no name anywhere
    (its identity is the global registry's ``worksets:`` entry), so this answers only
    *"is a workset here"* — [R139]: detection and naming are two questions, and
    answering one does not answer the other.  ``_is_standalone_meta_dir`` is the same
    shape for the same reason.
    ⚑ ALL FOUR are required: any one of them alone is an ordinary directory name.
    ⚑ Three of the four are RESOLVED through their workset keys, so this finds a root
    that has repointed ``workset.boxes``, ``workset.workspaces`` or ``workset.logs``.
    """
    return all(subdir.is_dir() for subdir in _workset_skeleton_dirs(root))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_workset(
    name: str, root: Path, std: StandardPaths, force: bool = False,
) -> Workset:
    """Create a new workset directory structure and register it globally."""
    if not name:
        raise WorksetError("Workset name must not be empty.")

    if is_reserved_workset_name(name):
        raise WorksetError(
            f"Workset name '{name}' is reserved and cannot be used. The names "
            f"{', '.join(sorted(RESERVED_WORKSET_NAMES))} are reserved sentinels "
            "for the primary and standalone partitions. Choose another name."
        )

    # ⚑ Same-kind uniqueness (D-B3): refuse, never auto-suffix.  --force NEVER bypasses this.
    registry = _load_registry(std)
    if name in registry:
        raise WorksetError(
            f"Workset name '{name}' is already in use (registered at "
            f"{registry[name]}). Workset names must be unique; choose a "
            "different name."
        )

    # ⚑ Cross-kind guard: a colliding PRIMARY BOX name would shadow this workset in
    # bare-name resolution.  Refuse unless *force*, BEFORE any on-disk side effect.
    if not force:
        from kanibako.settings.paths import load_primary_boxes
        if name in load_primary_boxes(std.primary_workset):
            raise WorksetError(
                f"Workset name '{name}' is already in use by a primary box. "
                f"Box and workset names are separate namespaces, but this bare "
                f"name would then be shadowed by the box in bare-name "
                f"resolution. Re-run with --force to use this name anyway."
            )

    root = root.resolve()
    if root.exists():
        raise WorksetError(f"Workset root already exists: {root}")

    # Multi-step: disk skeleton, then the ONE global registration.  A crash between
    # them would orphan dirs, so unwind in reverse: all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # ⚑ Skeleton = the FOUR dirs of :func:`_workset_skeleton_dirs`, which is also
        # what detection tests for; auth/channels are created lazily elsewhere.  NO file
        # is written at all — no workset.yaml (a workset root need not have one) and
        # no registry.yaml (a workset with no members has no membership to record).
        root.mkdir(parents=True)
        unwind.push(lambda: shutil.rmtree(root, ignore_errors=True))
        # ⚑ The bare ``mkdir()`` (no parents) is safe BECAUSE of the line above and the
        # ``root.exists()`` refusal before it: *root* was just created empty, so it has no
        # workset.yaml, so all three resolved leaves fall back to their defaults and every
        # path here is exactly one level under *root*.  ⚑ A repoint can never reach this
        # call — reaching it would need a workset.yaml inside a root that did not exist a
        # moment ago.  Do NOT paper over a future violation with ``parents=True``: that
        # would silently stamp a skeleton somewhere other than the root being created.
        for subdir_path in _workset_skeleton_dirs(root):
            subdir_path.mkdir()

        ws = Workset(name=name, root=root)

        # ⚑⚑ THE REGISTRATION IS THE CREATION: this line is what makes the directory a
        # workset, because the name→root entry it writes IS the workset's identity.
        # ⚑ ONE section serves BOTH name lookup AND discovery/list — hence one call.
        register_name(std.registry, name, str(root), section="worksets")

        def _drop_workset() -> None:
            unregister_name(std.registry, name, section="worksets")

        unwind.push(_drop_workset)
    except Exception:
        unwind.run()
        raise

    return ws


def load_workset(root: Path, name: str) -> Workset:
    """Load the workset registered as *name* at *root* (raises ``WorksetError`` if absent).

    ⚑ *name* is REQUIRED and comes from the global registry's ``worksets:`` section —
    the caller reached *root* through that mapping, and it is the only record of the
    name there is.
    """
    root = root.resolve()
    if not root.is_dir():
        raise WorksetError(f"Workset root does not exist: {root}")
    return _load_workset(root, name)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` for all registered worksets — ⚑ the default workset is NOT here."""
    return _load_registry(std)


def default_workset(std: StandardPaths) -> Workset:
    """Synthesize the default workset — ⚑ VIRTUAL: no registry write, no identity on disk."""
    from kanibako.settings.paths import load_primary_boxes

    projects_map = load_primary_boxes(std.primary_workset)
    projects = [
        WorksetProject(name=name, source_path=Path(path))
        for name, path in projects_map.items()
    ]

    return Workset(
        name=DEFAULT_WORKSET_ID,
        root=std.primary_workset,
        projects=projects,
        is_default=True,
        # PRIMARY honors a repoint from its own workset.yaml, like a named workset.
        workspaces_repoint=_workset_path_repoint(
            load_workset_settings_doc(std.primary_workset), _WORKSPACES_LEAF,
        ),
    )


def resolve_workset_name(name: str, std: StandardPaths) -> Workset:
    """Resolve a workset *name* to a :class:`Workset` (``default``/``__default__`` → synthesized)."""
    if name in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        return default_workset(std)
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Working set '{name}' is not registered.")
    return load_workset(registry[name], name)


def delete_workset(name: str, std: StandardPaths, *, remove_files: bool = False) -> Path:
    """Unregister a workset and optionally remove its tree; returns the deleted root path."""
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Workset '{name}' is not registered.")

    root = registry[name]

    # Drop the ONE ``worksets`` entry.  Idempotent: a missing entry is a no-op.
    unregister_name(std.registry, name, section="worksets")

    # ⚑ Irreversible step LAST: only after the registry is clean.
    if remove_files and root.is_dir():
        import shutil

        # ⚑⚑ BOX TREES FIRST (J-7): a whole-root rmtree hits the root-owned 555 canon
        # skeleton and leaves the workset half-deleted AFTER its registry entry is gone.
        from kanibako.runtime.container import remove_box_tree

        boxes_dir = root / BOXES_DIR_NAME
        if boxes_dir.is_dir():
            for box_tree in sorted(boxes_dir.iterdir()):
                if box_tree.is_dir() and not box_tree.is_symlink():
                    remove_box_tree(box_tree)
        shutil.rmtree(root)

    return root


def add_project(
    ws: Workset,
    name: str,
    source_path: Path,
    std: StandardPaths | None = None,
    force: bool = False,
) -> WorksetProject:
    """Add a project to a workset; an EXTERNAL *source_path* (with *std*) is CONNECTED instead."""
    for p in ws.projects:
        if p.name == name:
            raise WorksetError(
                f"Project '{name}' already exists in workset '{ws.name}'."
            )

    resolved_source = source_path.resolve()

    # Determine whether the source is external (outside the workset root).
    is_external = False
    if std is not None:
        try:
            resolved_source.relative_to(ws.root.resolve())
        except ValueError:
            is_external = True

    # ⚑ Validate up front: every EXTERNAL refusal fires BEFORE any directory is created.
    # Internal sources and std-less callers (e.g. migrate) skip this block entirely.
    if std is not None and is_external:
        target_root = ws.root.resolve()
        for other_name, other_root in _load_registry(std).items():
            other_root = Path(other_root).resolve()
            if other_root == target_root:
                continue
            try:
                resolved_source.relative_to(other_root)
            except ValueError:
                continue
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it lives inside workset "
                f"'{other_name}' ({other_root}). It would be shadowed by "
                "in-tree detection and mis-resolve. Move it outside that "
                "workset, or connect it to that workset instead."
            )

        from kanibako.launch import box_resolve

        # ⚑ D3-mode #1: an in-place standalone MARKER is the box's authoritative
        # self-declaration; connecting it would be a silent "steal" + dual registration.
        # The guard ALONE fixes it — with no ``boxes:`` entry, resolution finds the marker.
        if not force and box_resolve.standalone_settings_present(resolved_source):
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it is a standalone box "
                "(in-place marker present). Connecting it would absorb a box "
                "that declares itself standalone. Re-run with --force to connect "
                "it anyway (it becomes a workset box), or convert it explicitly "
                "first."
            )

        existing = box_resolve.find_connected_external_box(resolved_source, std)
        if existing is not None:
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it is already connected "
                f"as project '{existing.box_name}' in workset "
                f"'{existing.workset_name}'. Disconnect it first."
            )

    # ⚑⚑ THE ONE RECORDED PATH: an EXTERNAL connect records the source dir itself; an
    # in-tree member records ``workspaces/<name>``, which is the dir created below and
    # the only one it ever mounts.  Recording the caller's *source_path* for an in-tree
    # connect wrote a path the box never ran on.
    recorded_workspace = resolved_source if is_external else ws.workspaces_dir / name

    # Multi-step: the external case touches a symlink + the box dirs before the
    # durable membership write.  Unwind in reverse so the connect is all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # Box dir always real.  exist_ok keeps it idempotent; unwind only rmtrees
        # what we may have created.
        proj_box = ws.projects_dir / name
        existed_box = proj_box.exists()
        proj_box.mkdir(parents=True, exist_ok=True)
        if not existed_box:
            unwind.push(lambda: shutil.rmtree(proj_box, ignore_errors=True))

        # Vault nests ro/rw ABOVE the box name, matching PRIMARY and STANDALONE.
        # ⚑ Unwind removes the per-box LEAVES only — never the shared ro/rw parents.
        # ⚑ The two arms are RESOLVED keys, so the per-box leaf joins the RESOLVED arm.
        vault_ro_base, vault_rw_base = resolve_workset_vault_pair(ws.root)
        vault_ro_proj = vault_ro_base / name
        vault_rw_proj = vault_rw_base / name
        existed_vault_ro = vault_ro_proj.exists()
        existed_vault_rw = vault_rw_proj.exists()
        vault_ro_proj.mkdir(parents=True, exist_ok=True)
        vault_rw_proj.mkdir(parents=True, exist_ok=True)
        if not existed_vault_ro:
            unwind.push(lambda: shutil.rmtree(vault_ro_proj, ignore_errors=True))
        if not existed_vault_rw:
            unwind.push(lambda: shutil.rmtree(vault_rw_proj, ignore_errors=True))

        if is_external:
            # ⚑ workspaces/{name} is a discoverability SYMLINK — never mounted.
            # is_external implies std is not None, but mypy can't track that.
            assert std is not None
            ws.workspaces_dir.mkdir(parents=True, exist_ok=True)
            link = ws.workspaces_dir / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(resolved_source)
                unwind.push(
                    lambda: link.unlink() if link.is_symlink() else None
                )

            # ⚑ --force absorb: MOVE the registration — a box lives in EXACTLY ONE
            # registry.  The in-place marker STAYS (intrinsic identity), so disconnect
            # re-imports it as standalone: a clean round-trip.
            from kanibako.launch import box_resolve as _box_resolve

            if force and _box_resolve.standalone_settings_present(
                resolved_source
            ):
                from kanibako.project import registry_store

                std_name = registry_store.standalone_name_for_root(
                    std.registry, resolved_source
                )
                if std_name is not None:
                    dropped_name: str = std_name
                    dropped_root = registry_store.load_standalone(std.registry)[
                        dropped_name
                    ]
                    registry_store.unregister_standalone(
                        std.registry, dropped_name
                    )

                    def _restore_standalone() -> None:
                        registry_store.register_standalone(
                            std.registry, dropped_name, Path(dropped_root)
                        )

                    unwind.push(_restore_standalone)
        else:
            # Internal (or no std): a real workspace directory.
            ws_dir = ws.workspaces_dir / name
            existed_ws = ws_dir.exists()
            ws_dir.mkdir(parents=True, exist_ok=True)
            if not existed_ws:
                unwind.push(lambda: shutil.rmtree(ws_dir, ignore_errors=True))

        # ⚑ Durable registry write LAST, so a failure leaves no orphaned record.
        # ⚑⚑ ONE WRITE FOR EVERY MEMBER, in-tree and external alike: the P7/D10
        # ``boxes: {name → path}`` entry IS the membership record — sparse create
        # (P8b/Option A) writes no workset.yaml, and box_resolve reads this row for
        # BOTH identity and the workspace override.  Idempotent (overwrites a move).
        # ⚑⚑ The J2 ``op: connect`` bracket lives in ``workset_cmd.run_connect``, NOT
        # here — this is ALSO the membership seam for move/convert/duplicate, which
        # must not emit a ``connect`` entry.
        from kanibako.settings.paths import (
            _register_workset_box_membership,
            _unregister_workset_box_membership,
        )

        _register_workset_box_membership(ws.root, name, recorded_workspace)
        unwind.push(lambda: _unregister_workset_box_membership(ws.root, name))

        proj = WorksetProject(name=name, source_path=recorded_workspace)
        ws.projects.append(proj)
        unwind.push(lambda: _detach_project(ws, name))
    except Exception:
        unwind.run()
        raise

    return proj


def _detach_project(ws: Workset, name: str) -> None:
    """Drop *name* from the in-memory project list (compensating action)."""
    ws.projects[:] = [p for p in ws.projects if p.name != name]


def remove_project(
    ws: Workset, name: str, *, remove_files: bool = False,
    std: StandardPaths | None = None,  # noqa: ARG001 - caller parity with add_project
) -> WorksetProject:
    """Remove a project from a workset — ⚑ NEVER touches the user's external source dir.

    ⚑ *std* is accepted and unused: the membership drop is now unconditional, so it no
    longer needs the global registry to tell an external record from an in-tree one.
    """
    target = None
    for p in ws.projects:
        if p.name == name:
            target = p
            break
    if target is None:
        raise WorksetError(
            f"Project '{name}' not found in workset '{ws.name}'."
        )

    # ⚑⚑ ORDER IS THE REVERSE OF add_project: clean the symlink BEFORE the durable
    # write, so the registry removal is the LAST durable step and a crash mid-cleanup
    # leaves a RE-RUNNABLE state, not a locked-out external path.
    import shutil

    # ⚑ Unlink regardless of remove_files so the discoverability symlink never dangles
    # (removes only the LINK, never the external target).
    link = ws.workspaces_dir / name
    if link.is_symlink():
        link.unlink()

    # ⚑⚑ Durable registry removal LAST, and UNCONDITIONAL: the ``boxes:`` row is the
    # member's ONE record, so an in-tree member must lose it too.  Dropping only
    # EXTERNAL rows orphaned an in-tree disconnect's entry, and the orphan then tripped
    # the workspace-uniqueness refusal — locking that workspace out of its own workset
    # under any name, with no way back short of hand-editing registry.yaml.
    from kanibako.settings.paths import _unregister_workset_box_membership

    _unregister_workset_box_membership(ws.root, name)
    ws.projects.remove(target)

    if remove_files:
        # ⚑ Per-box vault LEAVES only — never the shared ro/rw parents.
        # ⚑⚑ RESOLVED, and it MUST match ``add_project``: deleting the composed default
        # while the box's real vault sits at the repoint leaves the user's data orphaned
        # AND removes a directory the box never used.
        vault_ro_base, vault_rw_base = resolve_workset_vault_pair(ws.root)
        targets = (
            ws.projects_dir / name,
            ws.workspaces_dir / name,
            vault_ro_base / name,
            vault_rw_base / name,
        )
        # ⚑ THE BOX TREE NEEDS THE UNSHARE ESCALATION (J-7): rmtree raises on the 555
        # canon skeleton EVEN WHEN THE CALLER OWNS IT.  Workspace/vault are ordinary
        # user content and stay on the plain path.
        from kanibako.runtime.container import remove_box_tree

        box_tree = ws.projects_dir / name
        for proj_dir in targets:
            if proj_dir.is_symlink():
                # Defensive: only the link is removed, never its target.
                proj_dir.unlink()
            elif proj_dir.is_dir():
                if proj_dir == box_tree:
                    remove_box_tree(proj_dir)
                else:
                    shutil.rmtree(proj_dir)

    return target
