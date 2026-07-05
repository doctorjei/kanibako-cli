"""Working set data model and persistence.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  The layout is:

    {root}/
        settings.yaml             ← workset identity (meta.workset.*) + cascade
                                    settings (the single workset file; mirrors a
                                    box's settings.yaml carrying meta.box.*)
        boxes/{name}/             ← per-project metadata + home
            home/                 ← agent home (mounted as /home/agent)
            settings.yaml          ← per-project config
            .kanibako.lock        ← concurrency lock
        workspaces/{name}/        ← per-project workspace (source tree)
        vault/ro/{name}/    ← per-project read-only vault
        vault/rw/{name}/    ← per-project read-write vault
        logs/{name}.jsonl   ← per-box helper message log

A global registry at ``$XDG_DATA_HOME/kanibako/worksets.yaml`` maps workset
names to root paths so they can be discovered from anywhere.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from kanibako import registry_store
from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import WorksetError
from kanibako.names import read_names, register_name, unregister_name
from kanibako.paths import StandardPaths

# The single per-workset file at the workset root.  It carries the workset
# IDENTITY (under ``meta.workset.*``) AND the workset's cascade settings (box/
# agent/workset.bindings tables), mirroring a box's ``settings.yaml`` which holds
# ``meta.box.*`` alongside its settings.  Same filename as ``BOX_META_FILE`` by
# design — both are the "settings.yaml at the entity's root" convention.
WORKSET_META_FILE = "settings.yaml"


# ---------------------------------------------------------------------------
# Failure-consistency: a tiny LIFO unwind stack for multi-step mutations.
#
# Several public mutators below touch more than one file (a root settings.yaml plus
# the global worksets.yaml/names.yaml registries, or a symlink + settings.yaml +
# a per-workset ``boxes:`` connection record).  Individual writes are torn-file-safe
# (atomic temp + os.replace via config_io.dump_doc), but a crash *between* steps
# could strand a half-applied cross-file state: registry says X while disk says Y,
# an orphan symlink, or an external path locked out by a dangling connection record.
#
# The unwind stack mirrors the lifecycle family's pattern (_lifecycle._Unwind):
# each forward step pushes a compensating action; on any exception we run them
# in reverse (best-effort, swallowing secondary failures) and re-raise, leaving
# the op either fully applied or fully rolled back.  These sequences are short
# (2-5 steps), so this stays deliberately small rather than a generic framework.
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
    """Bracket a ``connect`` (workset-membership) register with a journal entry.

    J2 write-ahead for the register-only CONNECT flow: write ``op: connect``
    entry -> (the membership write, the ``with`` body) -> clear entry.  Connect
    REGISTERS an externally-existing dir into a workset and NEVER seeds (the dir
    already holds the user's content), so this bracket has NO seed step.  The
    entry is cleared immediately after the membership write (HARD INVARIANT:
    registered ==> no pending entry).  If the body raises, the entry is LEFT
    (incomplete) and the exception propagates (``add_project``'s ``_Unwind``
    rolls back the in-process effects).  A None *journal* is a no-op bracket
    (plain write), preserving the pre-J2 behavior.  *box_path* is the host-side
    box dir (``ws.projects_dir / name``), the uniform J1/J2 key.
    """
    if journal is None:
        yield
        return
    from kanibako import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op="connect", name=name, mode="named",
        workset=workset, workspace=workspace,
    )
    yield
    journal_mod.clear_entry(journal, box_path)


# Identity of the synthesized "default" workset (the group of default-mode
# projects).  This workset is virtual — it is never written to disk.
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"

# Sentinel workset names reserved by the three-mode model (TARGET §2c): they
# address the PRIMARY and STANDALONE channel partitions and must never be a
# user-chosen NAMED-workset name.  The legacy ``default``/``__default__`` pair
# (now meaning "primary") is also reserved.  A workset name is a user-typed
# shared-channel address, so collisions here are refused, never auto-resolved.
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
    """A project registered inside a workset.

    The unified per-project record (B7): identity + path ONLY.  There is no
    ``seeded`` field — registry MEMBERSHIP (presence in this list) is itself the
    seed signal (a box was seeded when ``create`` added it; ``connect`` adds the
    record without seeding).  See the keyspace spec §0/§5 "Seed = at
    registration" / "One per-project record".
    """

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

    # Convenience paths -------------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        return self.root / "boxes"

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

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
# settings.yaml (at workset root): identity (meta.workset.*) + cascade settings.
#
# The IDENTITY lives under the ``workset.meta`` table so it never collides with
# the cascade-settings tables in the same file (``box.*``, ``agent.*``,
# ``workset.bindings.*`` — the latter shares the ``workset`` top-level key but a
# different sub-key).  The settings readers (read_categories / read_agent_settings
# / _present_scalar_fields) only recognize their own key shapes, so ``workset.meta``
# is invisible to them; conversely the identity reader below only touches
# ``workset.meta``, preserving any cascade keys a write_*/dump round-trips.
# ---------------------------------------------------------------------------

def _write_workset_toml(ws: Workset) -> None:
    """Persist *ws*'s identity into ``workset.meta`` of the root settings.yaml.

    Reads the existing file first so the workset's cascade settings (box/agent/
    workset.bindings tables, written by ``workset config``/``workset share``) are
    preserved across an identity update — only the ``workset.meta`` table is
    replaced.
    """
    existing = load_doc(ws.toml_path) if ws.toml_path.is_file() else {}
    if not isinstance(existing, dict):
        existing = {}
    workset_tbl = existing.get("workset")
    if not isinstance(workset_tbl, dict):
        workset_tbl = {}
        existing["workset"] = workset_tbl
    workset_tbl["meta"] = {
        "name": ws.name,
        "created": ws.created,
        # Credential sharing is NO LONGER a workset.meta identity key — it is an
        # ordinary settable cascade key (``workset.auth.share_allowed``) stored in
        # the workset's [project]/settings cascade like any other setting.
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
    """Read the workset identity (``workset.meta``) from *root*'s settings.yaml."""
    toml_path = root / WORKSET_META_FILE
    if not toml_path.is_file():
        raise WorksetError(f"No {WORKSET_META_FILE} in {root}")
    meta = read_workset_meta(toml_path)
    if meta is None:
        raise WorksetError(
            f"{WORKSET_META_FILE} in {root} has no 'workset.meta' identity"
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
    return Workset(name=name, root=root, created=created, projects=projects)


def read_workset_meta(path: Path) -> dict | None:
    """Return the ``workset.meta`` identity table from a workset settings.yaml.

    Returns the meta dict, or ``None`` when the file is missing/unreadable or
    carries no ``workset.meta`` table (i.e. *path* is not a NAMED-workset root
    marker).  This is the detection primitive: a directory is a NAMED workset
    root iff its ``settings.yaml`` carries ``workset.meta`` with a name.
    """
    if not path.is_file():
        return None
    try:
        data = load_doc(path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    workset_tbl = data.get("workset")
    if not isinstance(workset_tbl, dict):
        return None
    meta = workset_tbl.get("meta")
    if not isinstance(meta, dict):
        return None
    return meta


# ---------------------------------------------------------------------------
# Global worksets registry: the ``worksets`` section of ``system.registry``.
#
# This is the SAME ``worksets`` section the human-name index (``names.py``) reads
# and writes — the former duplicate ``workset_roots`` section was collapsed onto
# it (2026-06-29f). The name index (``register_name`` / ``unregister_name``) is
# the SOLE WRITER; this read helper serves the discovery / list / lookup paths.
# ---------------------------------------------------------------------------

def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    section = registry_store.load_section(std.registry, "worksets")
    return {name: Path(root) for name, root in section.items()}


# ---------------------------------------------------------------------------
# Connected (external) boxes — D10 per-workset registry (no global index).
#
# An externally-connected box is a NAMED workset's per-workset ``boxes:`` entry
# whose registered PATH is the EXTERNAL directory.  The per-workset registries
# collectively ARE the reverse index (D10 enumerate-and-scan) — resolution goes
# through ``box_resolve.find_connected_external_box``.  The former global
# ``connected:`` section (and ``_find_connected_project``/``_load_connected``/
# ``_write_connected``) are GONE.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_workset(name: str, root: Path, std: StandardPaths) -> Workset:
    """Create a new workset directory structure and register it globally.

    Raises ``WorksetError`` if *root* already exists or name is already
    registered.
    """
    if not name:
        raise WorksetError("Workset name must not be empty.")

    if is_reserved_workset_name(name):
        raise WorksetError(
            f"Workset name '{name}' is reserved and cannot be used. The names "
            f"{', '.join(sorted(RESERVED_WORKSET_NAMES))} are reserved sentinels "
            "for the primary and standalone partitions. Choose another name."
        )

    # A NAMED workset's name is a user-typed shared-channel address, so it must
    # be unique across all registered worksets (D-B3). Refuse on collision —
    # never auto-suffix — and point the user at the existing root.
    registry = _load_registry(std)
    if name in registry:
        raise WorksetError(
            f"Workset name '{name}' is already in use (registered at "
            f"{registry[name]}). Workset names must be unique; choose a "
            "different name."
        )

    root = root.resolve()
    if root.exists():
        raise WorksetError(f"Workset root already exists: {root}")

    # Multi-step: create disk skeleton + root settings.yaml, then the global
    # worksets.yaml registry, then the names.yaml index.  A crash between any two
    # would leave orphan dirs (not in the registry) or a worksets.yaml entry with
    # no names.yaml index (bare-name resolution fails while `workset list`
    # works).  Track forward effects and unwind in reverse on any failure so the
    # create is all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # Create directory skeleton.
        root.mkdir(parents=True)
        unwind.push(lambda: shutil.rmtree(root, ignore_errors=True))
        for subdir in ("boxes", "workspaces", "vault", "logs"):
            (root / subdir).mkdir()

        ws = Workset(
            name=name,
            root=root,
            created=datetime.now(timezone.utc).isoformat(),
        )
        _write_workset_toml(ws)

        # Register in the worksets index (name → root). This single section
        # serves BOTH name-based lookups AND workset discovery/list (the former
        # duplicate ``workset_roots`` section was collapsed onto it).
        register_name(std.registry, name, str(root), section="worksets")

        def _drop_workset() -> None:
            unregister_name(std.registry, name, section="worksets")

        unwind.push(_drop_workset)
    except Exception:
        unwind.run()
        raise

    return ws


def load_workset(root: Path) -> Workset:
    """Load a workset from its root directory.

    Raises ``WorksetError`` if the directory or the root ``settings.yaml``
    (carrying ``workset.meta``) is missing.
    """
    root = root.resolve()
    if not root.is_dir():
        raise WorksetError(f"Workset root does not exist: {root}")
    # Migrate old kanibako/ subdir to boxes/ if needed.
    old_subdir = root / "kanibako"
    new_subdir = root / "boxes"
    if old_subdir.is_dir() and not new_subdir.exists():
        old_subdir.rename(new_subdir)
        import sys
        print(f"Migrated workset: {old_subdir} → {new_subdir}", file=sys.stderr)
    return _load_workset_toml(root)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` for all registered worksets.

    This returns ONLY the on-disk registry; the synthesized default workset is
    never injected here.
    """
    return _load_registry(std)


def default_workset(std: StandardPaths) -> Workset:
    """Synthesize the default workset (the group of default-mode projects).

    The default workset is virtual: its members are the default-mode projects
    in ``names.yaml [projects]``.  It roots at ``@config.primary_workset``
    (spec §2c: PRIMARY ``meta.workset.path``), so its settings/env files derive
    from ``root`` exactly like a named workset's (F4).  Credential sharing is
    now a normal settable cascade key (``workset.auth.share_allowed``),
    resolved through the settings pipeline — NOT a workset field.  This object
    is NEVER persisted to disk (no registry write; the root settings.yaml
    carries only cascade keys, never a ``workset.meta`` identity).
    """
    from kanibako.paths import warn_legacy_primary_settings

    warn_legacy_primary_settings(std)
    projects_map = read_names(std.registry).get("projects", {})
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
    )


def resolve_workset_name(name: str, std: StandardPaths) -> Workset:
    """Resolve a workset *name* to a :class:`Workset`.

    The names ``default`` / ``__default__`` resolve to the synthesized default
    workset; any other name is looked up in the on-disk registry.  Raises
    ``WorksetError`` if the name is not registered.
    """
    if name in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        return default_workset(std)
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Working set '{name}' is not registered.")
    return load_workset(registry[name])


def delete_workset(name: str, std: StandardPaths, *, remove_files: bool = False) -> Path:
    """Unregister a workset and optionally remove its directory tree.

    Returns the root path of the deleted workset.
    Raises ``WorksetError`` if the name is not registered.
    """
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Workset '{name}' is not registered.")

    root = registry[name]

    # Drop the workset from the single ``worksets`` index (the former duplicate
    # ``workset_roots`` half was collapsed away, so there is now ONE registry
    # entry to remove — no list-vs-index split to heal). Idempotent: a missing
    # entry is a no-op.
    unregister_name(std.registry, name, section="worksets")

    # Irreversible step LAST: only after both registry halves are clean.
    if remove_files and root.is_dir():
        import shutil
        shutil.rmtree(root)

    return root


def add_project(
    ws: Workset,
    name: str,
    source_path: Path,
    std: StandardPaths | None = None,
) -> WorksetProject:
    """Add a project to a workset.  Creates per-project subdirectories.

    When *source_path* resolves OUTSIDE the workset root and *std* is provided,
    the project is connected to that external directory: the external dir
    becomes the live workspace.  In that case ``workspaces/{name}`` is created
    as a SYMLINK to the external dir (discoverability only — never mounted), a
    ``workspace`` override is written into the project's ``settings.yaml``, and
    the box is registered in the workset's per-workset registry (``boxes: {name →
    external path}`` — the D10 connection record) so launches from the external
    path resolve back to this workset.  Sources inside the workset tree keep the
    normal behavior (a real ``workspaces/{name}`` directory).

    Raises ``WorksetError`` if a project with *name* already exists.
    """
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

    # Validate up front (failure-consistency): for an EXTERNAL source, refuse
    # BEFORE creating any directories if connecting it would mis-resolve.  An
    # external dir that is itself inside another registered workset's tree would
    # be shadowed by ordinary in-tree location detection; an already-connected
    # dir (or a dir nested under one) is a 1:1-mapping conflict.  Internal
    # sources and std-less callers (e.g. migrate) are unaffected.
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

        from kanibako import box_resolve

        existing = box_resolve.find_connected_external_box(resolved_source, std)
        if existing is not None:
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it is already connected "
                f"as project '{existing.box_name}' in workset "
                f"'{existing.workset_name}'. Disconnect it first."
            )

    # Multi-step mutation (external case touches a symlink + settings.yaml + the
    # per-workset ``boxes:`` connection record + the durable root settings.yaml
    # registry write).  A crash between steps could strand a symlink/record with
    # no settings.yaml entry (external path locked out by a dangling connection
    # record) or vice-versa.  Track forward effects on an unwind stack; on any
    # failure undo in reverse + re-raise so the connect is all-or-nothing.
    # Success behavior is identical to before (same final state).
    import shutil

    unwind = _Unwind()
    try:
        # Create per-project directories (boxes always real).  exist_ok=True keeps
        # this idempotent; only rmtree on unwind dirs we (may have) created.
        proj_box = ws.projects_dir / name
        existed_box = proj_box.exists()
        proj_box.mkdir(parents=True, exist_ok=True)
        if not existed_box:
            unwind.push(lambda: shutil.rmtree(proj_box, ignore_errors=True))

        # Vault ro/rw split nests ABOVE the box name (vault/{ro,rw}/<name>) to
        # match PRIMARY and STANDALONE.  Unwind removes the per-box ro/rw leaves
        # only — never the shared vault/ro or vault/rw parents.
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
            # External: workspaces/{name} is a self-documenting symlink to the
            # external dir (never mounted — the bind-mount uses the workspace
            # override).  Write the override + record the redirect.
            # is_external can only be True when std is not None (set above),
            # but mypy can't track that cross-variable invariant.
            assert std is not None
            ws.workspaces_dir.mkdir(parents=True, exist_ok=True)
            link = ws.workspaces_dir / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(resolved_source)
                unwind.push(
                    lambda: link.unlink() if link.is_symlink() else None
                )

            from kanibako.config import BOX_META_FILE, write_project_meta

            project_toml = ws.projects_dir / name / BOX_META_FILE
            write_project_meta(
                project_toml,
                mode="named",
                workspace=str(resolved_source),
                shell="",
                vault_ro="",
                vault_rw="",
                name=name,
            )

            # P7/D10: record the connection in the TARGET workset's per-workset
            # registry — ``boxes: {name → EXTERNAL path}``.  The per-workset
            # ``boxes:`` entry IS the connection record (the reverse index is the
            # collection of per-workset registries); the former global
            # ``connected:`` index is gone.  Idempotent (overwrites a moved box).
            from kanibako.paths import (
                _register_workset_box_membership,
                _unregister_workset_box_membership,
            )

            _register_workset_box_membership(ws.root, name, resolved_source)
            unwind.push(
                lambda: _unregister_workset_box_membership(ws.root, name)
            )
        else:
            # Internal (or no std): a real workspace directory.
            ws_dir = ws.workspaces_dir / name
            existed_ws = ws_dir.exists()
            ws_dir.mkdir(parents=True, exist_ok=True)
            if not existed_ws:
                unwind.push(lambda: shutil.rmtree(ws_dir, ignore_errors=True))

        # Durable registry write LAST.  If it fails the unwind removes the
        # symlink + the per-workset ``boxes:`` registration + dirs above, leaving
        # no orphaned connection record.
        #
        # J2 lifecycle journal: the write-ahead ``op: connect`` entry that
        # brackets this durable membership write lives in the CONNECT COMMAND
        # (``commands.workset_cmd.run_connect``), NOT here — ``add_project`` is
        # also the membership-write seam for the DEFERRED move/convert/duplicate
        # pipelines (``commands.box._lifecycle``), which must NOT journal a
        # ``connect`` op (their in-process ``_Unwind`` covers them; full
        # journaling is a later block).  Scoping the entry to ``run_connect``
        # ensures only an ACTUAL connect op emits a ``connect`` entry.  The
        # write-ahead order still holds: ``run_connect`` writes the entry BEFORE
        # this call and clears it immediately after the call returns.
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
    """Remove a project from a workset.

    When *std* is provided, the external-connect markers written by
    :func:`add_project` are undone regardless of *remove_files*: the box's
    per-workset ``boxes:`` connection record (D10 — present only when its path is
    EXTERNAL) is dropped and the ``workspaces/{name}`` symlink (external projects)
    is unlinked.  Unlinking a symlink removes ONLY the link — never the user's
    external source directory.  With *remove_files* the workset-side per-project
    directories are removed too (symlinks unlinked, real dirs rmtree'd); the
    external source is left intact.

    Raises ``WorksetError`` if no project with *name* exists.
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

    # Failure-consistency ordering: clean the per-workset connection record + the
    # discoverability symlink BEFORE the durable root settings.yaml registry write, so
    # the registry removal (which makes the project "gone") is the LAST durable
    # step.  A crash mid-cleanup then leaves the project still in settings.yaml —
    # a re-runnable state — instead of removing it from the registry while the
    # ``boxes:`` connection record still points at it (which would lock out the
    # external path).  The cleanups are idempotent: a re-run finds nothing to do.
    import shutil

    # Always undo the external-connect record when we have std, so the per-workset
    # ``boxes:`` entry never outlives the project.  D10: the connection record is
    # the per-workset registry entry whose path is EXTERNAL (outside the workset
    # root); an in-tree box's membership entry is left untouched (as before —
    # only external connects were ever cleaned here).
    if std is not None:
        from kanibako import workset_registry
        from kanibako.paths import _unregister_workset_box_membership

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

    # The workspaces/{name} marker is a SYMLINK for external projects; unlink it
    # (removes only the link, never the external target).  Do this regardless of
    # remove_files so the discoverability symlink never dangles.
    link = ws.workspaces_dir / name
    if link.is_symlink():
        link.unlink()

    # Durable registry removal LAST (after redirect + symlink are clean).
    ws.projects.remove(target)
    _write_workset_toml(ws)

    if remove_files:
        # Vault now nests ro/rw ABOVE the box name (vault/{ro,rw}/<name>), so
        # remove those per-box leaves — never the shared vault/ro or vault/rw
        # parents (they hold every box's vault).
        targets = (
            ws.projects_dir / name,
            ws.workspaces_dir / name,
            ws.vault_dir / "ro" / name,
            ws.vault_dir / "rw" / name,
        )
        for proj_dir in targets:
            if proj_dir.is_symlink():
                # Defensive: only the link is removed, never its target.
                proj_dir.unlink()
            elif proj_dir.is_dir():
                shutil.rmtree(proj_dir)

    return target
