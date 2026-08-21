"""Workset data model and persistence — see ``llm-docs/kanibako/project/workset.py.md``.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  Terminology:

* **workset root** — the user-chosen dir; holds ``settings.yaml``, ``boxes/``,
  ``vault/``, ``logs/``, ``registry.yaml``, ``auth/`` and ``channels/``.
* **identity** — the on-disk TOP-LEVEL ``meta.workset`` table in the root
  ``settings.yaml``.  ⚑ It never enters the settings merge: ``meta.*`` is RO, so
  the assembler drops it, and :func:`read_workset_meta` reads the raw file
  instead.  The runtime ``meta.workset.*`` keys (spec §1A) are derived at launch
  and never read from here.  ⚑ 1.6.0/1.7.x wrote it the other way round
  (``workset.meta``); that spelling is RETIRED and now HARD-REFUSES with the
  hand re-nest as its cure — see :func:`_refuse_retired_workset_identity`.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kanibako.project import registry_store
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.errors import LegacyWorksetIdentityError, WorksetError
from kanibako.project.names import register_name, unregister_name
# ⚑ FORWARD edge of a documented cycle: ``settings/paths.py`` breaks it by DEFERRING
# its ``project.workset`` imports into function bodies — do not add a module-scope
# edge back this way.
from kanibako.settings.paths import StandardPaths

# The single per-workset file at the workset root: identity + cascade settings.
WORKSET_META_FILE = "settings.yaml"

# The BOX-tree leaf under a workset root (three use sites, only one has a Workset).
BOXES_DIR_NAME = "boxes"

# Default leaves for the RESOLVED workset dir keys — the spec's per-mode default
# formula ``@meta.workset.path/<leaf>`` spelled ONCE (§3.3: real and USED).
_WORKSPACES_LEAF = "workspaces"
_STANDALONE_WORKSPACE_LEAF = "workspace"
_CHANNELROOT_LEAF = "channels"


# ---------------------------------------------------------------------------
# Resolved workset dir keys (workset.workspaces / workset.channelroot) — the
# no-snapshot seam, mirroring ``workset_registry.resolve_workset_registry_path``.
# ---------------------------------------------------------------------------

def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None:
    """Best-effort read of *root*'s workset ``settings.yaml`` document (``None`` on any failure)."""
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


def _apply_workset_dir_repoint(
    workset_root: Path, repoint: str | None, default_leaf: str,
) -> Path:
    """Apply a workset dir-key *repoint* (or the ``<root>/<default_leaf>`` default)."""
    if repoint:
        expanded = Path(repoint).expanduser()
        if not expanded.is_absolute():
            expanded = workset_root / expanded
        return expanded
    return workset_root / default_leaf


def resolve_workset_workspaces(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, standalone: bool = False,
) -> Path:
    """Return the resolved ``workset.workspaces`` dir (*standalone* selects the singular default)."""
    return _apply_workset_dir_repoint(
        workset_root,
        _workset_path_repoint(workset_settings, _WORKSPACES_LEAF),
        _STANDALONE_WORKSPACE_LEAF if standalone else _WORKSPACES_LEAF,
    )


def resolve_workset_channelroot(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved ``workset.channelroot`` — ⚑ primary/named ONLY; callers gate on mode."""
    return _apply_workset_dir_repoint(
        workset_root,
        _workset_path_repoint(workset_settings, "channelroot"),
        _CHANNELROOT_LEAF,
    )


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
    """A project registered inside a workset — ⚑ identity + path ONLY (B7); no ``seeded`` field."""

    name: str
    source_path: Path   # original project path (for reference / cloning)


@dataclass
class Workset:
    """In-memory representation of a workset."""

    name: str
    root: Path
    created: str                            # ISO 8601, UTC
    projects: list[WorksetProject] = field(default_factory=list)
    is_default: bool = False                 # True = synthesized default workset
    #: RAW ``workset.workspaces`` repoint from the root settings.yaml; ``None`` = unset.
    workspaces_repoint: str | None = None

    # Convenience paths -------------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        return self.root / BOXES_DIR_NAME

    @property
    def workspaces_dir(self) -> Path:
        # ⚑ RESOLVED, not composed (§3.3: real and USED) — the only one of the five.
        return _apply_workset_dir_repoint(
            self.root, self.workspaces_repoint, _WORKSPACES_LEAF,
        )

    @property
    def vault_dir(self) -> Path:
        return self.root / "vault"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def toml_path(self) -> Path:
        return self.root / WORKSET_META_FILE


# ---------------------------------------------------------------------------
# The root settings.yaml: the ``meta.workset`` identity table + cascade settings.
# ⚑ The two coexist because the identity table is the TOP-LEVEL ``meta:`` table and
# ``meta.*`` is RO: the assembler DROPS it from the merge, so it can never shadow a
# cascade key, and the identity readers below load the raw file instead.
# ---------------------------------------------------------------------------

def _write_workset_toml(ws: Workset) -> None:
    """Persist *ws*'s identity into ``meta.workset`` — ⚑ reads the file FIRST to keep cascade keys."""
    existing = load_doc(ws.toml_path) if ws.toml_path.is_file() else {}
    if not isinstance(existing, dict):
        existing = {}
    meta_tbl = existing.get("meta")
    if not isinstance(meta_tbl, dict):
        meta_tbl = {}
        existing["meta"] = meta_tbl
    meta_tbl["workset"] = {
        "name": ws.name,
        "created": ws.created,
        # ⚑ Credential sharing is NOT an identity key — it is ``workset.auth.share_allowed``.
        "projects": [
            {
                "name": proj.name,
                "source_path": str(proj.source_path),
            }
            for proj in ws.projects
        ],
    }
    dump_doc(ws.toml_path, existing)


def _load_workset_toml(root: Path) -> Workset:
    """Read the workset identity (``meta.workset``) from *root*'s settings.yaml."""
    toml_path = root / WORKSET_META_FILE
    if not toml_path.is_file():
        raise WorksetError(f"No {WORKSET_META_FILE} in {root}")
    # ⚑ A root still on the RETIRED ``workset.meta`` spelling raises out of here
    # (:func:`_refuse_retired_workset_identity`) rather than reaching the "no identity"
    # message below — the load path gets the named cure, same as detection.
    meta = read_workset_meta(toml_path)
    if meta is None:
        raise WorksetError(
            f"{WORKSET_META_FILE} in {root} has no 'meta.workset' identity"
        )
    name = meta.get("name")
    if not name:
        raise WorksetError(
            f"{WORKSET_META_FILE} in {root} has no 'name' key"
        )
    created = meta.get("created", "")
    projects = []
    for entry in meta.get("projects", []):
        projects.append(
            WorksetProject(
                name=entry["name"],
                source_path=Path(entry["source_path"]),
            )
        )
    return Workset(
        name=name, root=root, created=created, projects=projects,
        # The repoint comes from the SAME settings.yaml, so workspaces_dir resolves.
        workspaces_repoint=_workset_path_repoint(
            load_workset_settings_doc(root), _WORKSPACES_LEAF,
        ),
    )


def _refuse_retired_workset_identity(path: Path, data: Mapping[str, Any]) -> None:
    """RAISE when *data* still nests the identity under the RETIRED ``workset.meta`` spelling.

    ⚑ DETECTED ONLY SO IT CAN BE DIAGNOSED.  v1.8.0 is a clean break: there is no compat
    read and no auto-migration, because reading past this table is exactly the silent
    failure — a legacy root has no ``meta.workset``, so detection would call it "not a
    workset root" and the ancestor walk would fall through to another mode with no error.
    """
    workset_tbl = data.get("workset")
    if not isinstance(workset_tbl, Mapping):
        return
    if not isinstance(workset_tbl.get("meta"), Mapping):
        return
    raise LegacyWorksetIdentityError(
        f"'workset.meta' is the RETIRED spelling of a named workset's identity table "
        f"and is still the shape of {path}.\n"
        f"The RULE CHANGED in kanibako 1.8.0: the identity table moved into the "
        f"top-level `meta:` namespace and is spelled `meta.workset`. kanibako 1.6.0 and "
        f"1.7.x wrote the old spelling, so every workset root those releases created "
        f"carries it. Refusing rather than running: this table is what MARKS the "
        f"directory as a workset root, and reading past it would resolve the directory "
        f"as an ordinary primary-mode box instead — your workset would stop being a "
        f"workset with no error at all.\n"
        f"  Fix: re-nest the table BY HAND in {path} — lift `meta:` out from under "
        f"`workset:` and make `workset:` its child, with the SAME three entries and "
        f"their values unchanged:\n"
        f"\n"
        f"    meta:\n"
        f"      workset:\n"
        f"        name: ...\n"
        f"        created: ...\n"
        f"        projects: ...\n"
        f"\n"
        f"  Nothing else in the file moves: a top-level `workset:` table is still where "
        f"this workset's own settings live, so leave the rest of it exactly as it is "
        f"and delete only the now-empty `meta:` entry under it. kanibako 1.8.0 ships no "
        f"automatic migration for this — see MIGRATION.md §2.43."
    )


def read_workset_meta(path: Path) -> dict | None:
    """Return the ``meta.workset`` table — ⚑⚑ THE NAMED-workset-root detection primitive."""
    if not path.is_file():
        return None
    try:
        data = load_doc(path)
    except Exception:
        # ⚑ An unreadable/corrupt file is genuinely not a workset root.  The guard wraps
        # the LOAD ONLY, so it can never swallow the retired-spelling refusal below.
        return None
    if not isinstance(data, dict):
        return None
    # ⚑⚑ RETIRED SPELLING FIRST, and unconditionally: a legacy root's `meta.workset` read
    # below returns None, which detection reads as "not a workset root" — the exact silence
    # this refusal exists to replace.
    _refuse_retired_workset_identity(path, data)
    meta_tbl = data.get("meta")
    if not isinstance(meta_tbl, dict):
        return None
    identity = meta_tbl.get("workset")
    if not isinstance(identity, dict):
        return None
    return identity


# ---------------------------------------------------------------------------
# Global worksets registry: the ``worksets`` section of ``config.registry``.
# ⚑ ONE section in ONE file; ``register_name``/``unregister_name`` are the SOLE writers.
# ---------------------------------------------------------------------------

def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    section = registry_store.load_section(std.registry, "worksets")
    return {name: Path(root) for name, root in section.items()}


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

    # Multi-step: disk skeleton + root settings.yaml, then the ONE global registration.
    # A crash between them would orphan dirs, so unwind in reverse: all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # ⚑ Skeleton = FOUR dirs; registry.yaml/auth/channels are created lazily
        # elsewhere.  The workspaces dir routes through the resolver, not a literal.
        root.mkdir(parents=True)
        unwind.push(lambda: shutil.rmtree(root, ignore_errors=True))
        for subdir_path in (
            root / BOXES_DIR_NAME,
            resolve_workset_workspaces(root, None),
            root / "vault",
            root / "logs",
        ):
            subdir_path.mkdir()

        ws = Workset(
            name=name,
            root=root,
            created=datetime.now(timezone.utc).isoformat(),
        )
        _write_workset_toml(ws)

        # ⚑ ONE section serves BOTH name lookup AND discovery/list — hence one call.
        register_name(std.registry, name, str(root), section="worksets")

        def _drop_workset() -> None:
            unregister_name(std.registry, name, section="worksets")

        unwind.push(_drop_workset)
    except Exception:
        unwind.run()
        raise

    return ws


def load_workset(root: Path) -> Workset:
    """Load a workset from its root directory (raises ``WorksetError`` when absent/unmarked)."""
    root = root.resolve()
    if not root.is_dir():
        raise WorksetError(f"Workset root does not exist: {root}")
    # Legacy migration: old kanibako/ subdir → boxes/.
    old_subdir = root / "kanibako"
    new_subdir = root / "boxes"
    if old_subdir.is_dir() and not new_subdir.exists():
        old_subdir.rename(new_subdir)
        import sys
        print(f"Migrated workset: {old_subdir} → {new_subdir}", file=sys.stderr)
    return _load_workset_toml(root)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` for all registered worksets — ⚑ the default workset is NOT here."""
    return _load_registry(std)


def default_workset(std: StandardPaths) -> Workset:
    """Synthesize the default workset — ⚑ VIRTUAL: no registry write, no ``meta.workset`` on disk."""
    from kanibako.settings.paths import load_primary_boxes, warn_legacy_primary_settings

    warn_legacy_primary_settings(std)
    projects_map = load_primary_boxes(std.primary_workset)
    projects = [
        WorksetProject(name=name, source_path=Path(path))
        for name, path in projects_map.items()
    ]

    return Workset(
        name=DEFAULT_WORKSET_ID,
        root=std.primary_workset,
        created="",
        projects=projects,
        is_default=True,
        # PRIMARY honors a repoint from its own settings.yaml, like a named workset.
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
    return load_workset(registry[name])


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

    # Multi-step: the external case touches a symlink + the ``boxes:`` connection record
    # + the durable identity write.  Unwind in reverse so the connect is all-or-nothing.
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
        vault_ro_proj = ws.vault_dir / "ro" / name
        vault_rw_proj = ws.vault_dir / "rw" / name
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

            # ⚑ Sparse create (P8b/Option A): NO settings.yaml is written.  The P7/D10
            # ``boxes: {name → EXTERNAL path}`` entry IS the connection record, and
            # box_resolve reads it for BOTH identity and the workspace override.
            # Idempotent (overwrites a moved box).
            from kanibako.settings.paths import (
                _register_workset_box_membership,
                _unregister_workset_box_membership,
            )

            _register_workset_box_membership(ws.root, name, resolved_source)
            unwind.push(
                lambda: _unregister_workset_box_membership(ws.root, name)
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
        # ⚑⚑ The J2 ``op: connect`` bracket lives in ``workset_cmd.run_connect``, NOT
        # here — this is ALSO the membership seam for move/convert/duplicate, which
        # must not emit a ``connect`` entry.
        proj = WorksetProject(name=name, source_path=resolved_source)
        ws.projects.append(proj)
        unwind.push(lambda: _detach_project(ws, name))
        _write_workset_toml(ws)
    except Exception:
        unwind.run()
        raise

    return proj


def _detach_project(ws: Workset, name: str) -> None:
    """Drop *name* from the in-memory project list (compensating action)."""
    ws.projects[:] = [p for p in ws.projects if p.name != name]


def remove_project(
    ws: Workset, name: str, *, remove_files: bool = False,
    std: StandardPaths | None = None,
) -> WorksetProject:
    """Remove a project from a workset — ⚑ NEVER touches the user's external source dir."""
    target = None
    for p in ws.projects:
        if p.name == name:
            target = p
            break
    if target is None:
        raise WorksetError(
            f"Project '{name}' not found in workset '{ws.name}'."
        )

    # ⚑⚑ ORDER IS THE REVERSE OF add_project: clean the connection record + symlink
    # BEFORE the durable write, so the registry removal is the LAST durable step and a
    # crash mid-cleanup leaves a RE-RUNNABLE state, not a locked-out external path.
    import shutil

    # ⚑ The external test is on the RECORD's path: an in-tree membership entry is
    # left untouched, as before.
    if std is not None:
        from kanibako.project import workset_registry
        from kanibako.settings.paths import _unregister_workset_box_membership

        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
        )
        box_path = workset_registry.workset_box_path(registry_path, name)
        if box_path is not None:
            try:
                Path(box_path).resolve().relative_to(ws.root.resolve())
                is_external_record = False
            except ValueError:
                is_external_record = True
            if is_external_record:
                _unregister_workset_box_membership(ws.root, name)

    # ⚑ Unlink regardless of remove_files so the discoverability symlink never dangles
    # (removes only the LINK, never the external target).
    link = ws.workspaces_dir / name
    if link.is_symlink():
        link.unlink()

    # Durable registry removal LAST (after record + symlink are clean).
    ws.projects.remove(target)
    _write_workset_toml(ws)

    if remove_files:
        # ⚑ Per-box vault LEAVES only — never the shared ro/rw parents.
        targets = (
            ws.projects_dir / name,
            ws.workspaces_dir / name,
            ws.vault_dir / "ro" / name,
            ws.vault_dir / "rw" / name,
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
