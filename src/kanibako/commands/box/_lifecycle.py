"""Shared transactional engine for project lifecycle operations.

This module houses the single routine behind ``remap`` / ``move`` / ``convert``
(and their combos).  It splits a project's identity into two axes:

- **location** — where the workspace files physically live (the ``location``
  field of :class:`TargetSpec`).
- **ownership** — which mode/workset owns the project (the ``ownership`` field).

The public surface is:

- :class:`ProjectState` — a uniform descriptor of an existing project.
- :func:`resolve_lifecycle_target` — resolve a path/name to a ``ProjectState``.
- :class:`TargetSpec` — what the caller wants changed (location + ownership).
- :func:`execute_lifecycle` — apply a ``TargetSpec`` to a ``ProjectState``
  transactionally (canonical 5-step order with an unwind stack).

This module also houses the ``run_remap`` / ``run_move`` / ``run_convert`` CLI
entry points and the std-aware :func:`copy_into_workset` helper used by
``box duplicate``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kanibako.config import (
    KanibakoConfig,
    read_project_meta,
    write_project_meta,
)
from kanibako.errors import ProjectError, WorksetError
from kanibako.names import assign_name, unregister_name
from kanibako.paths import (
    ProjectLayout,
    BoxMode,
    ProjectPaths,
    StandardPaths,
    WorksetSpec,
    _ensure_human_vault_symlink,
    _find_workset_for_path,
    _remove_human_vault_symlink,
    _remove_project_vault_symlink,
    detect_project_mode,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.utils import project_hash, write_project_gitignore
from kanibako.workset import (
    Workset,
    _find_connected_project,
    add_project,
    list_worksets,
    load_workset,
    remove_project,
)


# ---------------------------------------------------------------------------
# Sentinels for TargetSpec
# ---------------------------------------------------------------------------

class _Sentinel:
    """A named sentinel that reprs cleanly (for spec/error messages)."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return self._name


#: ``location`` sentinel — keep the workspace where it is (no file move).
INPLACE = _Sentinel("INPLACE")
#: ``location`` sentinel — move the workspace *into* the target workset
#: (``{ws}/workspaces/<name>``).  Only valid with a workset ownership target.
BARE_INTO_WS = _Sentinel("BARE_INTO_WS")
#: ``ownership`` sentinel — keep the current owner/mode.
UNCHANGED = _Sentinel("UNCHANGED")


# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------

@dataclass
class ProjectState:
    """Uniform descriptor of an existing, resolved project.

    *owner* is the canonical ownership token: ``"primary"``, ``"standalone"``,
    or ``"workset:<name>"``.  *ws* is the loaded :class:`Workset` when the owner
    is a workset (else ``None``).  *is_external* is True when the live workspace
    lives outside the owning workset's root (a connected-external project).
    """

    owner: str
    mode: BoxMode
    name: str
    workspace_path: Path
    metadata_path: Path
    shell_path: Path
    vault_ro: Path
    vault_rw: Path
    is_external: bool = False
    ws: Workset | None = None
    layout: ProjectLayout = ProjectLayout.default
    enable_vault: bool = True
    group_auth: bool = True

    @property
    def is_workset(self) -> bool:
        return self.mode == BoxMode.named


@dataclass
class TargetSpec:
    """What a lifecycle operation should change.

    *location* is one of :data:`INPLACE`, :data:`BARE_INTO_WS`, or a concrete
    ``Path`` destination.  *ownership* is one of :data:`UNCHANGED`,
    ``"default"``, ``"standalone"``, or a workset name (plain string, NOT
    prefixed with ``workset:``).  *name* optionally renames the project at the
    destination (defaults to the existing name).
    """

    location: Path | _Sentinel = INPLACE
    ownership: str | _Sentinel = UNCHANGED
    name: str | None = None
    #: ``remap`` semantics — the workspace has ALREADY moved on disk; record the
    #: new ``location`` (path + hash + markers) WITHOUT copying or deleting any
    #: files.  Only meaningful with a concrete ``Path`` location.
    records_only: bool = False


def owner_token(mode: BoxMode, ws_name: str | None = None) -> str:
    """Build a canonical owner token from a mode (+ workset name)."""
    if mode == BoxMode.named:
        if not ws_name:
            raise ValueError("workset owner requires a workset name")
        return f"workset:{ws_name}"
    return mode.value


def _ownership_to_mode(ownership: str) -> tuple[BoxMode, str | None]:
    """Map a TargetSpec ownership value to ``(mode, workset_name | None)``.

    A non-default/standalone string is treated as a workset name.
    """
    if ownership == "default":
        return BoxMode.primary, None
    if ownership == "standalone":
        return BoxMode.standalone, None
    return BoxMode.named, ownership


# ---------------------------------------------------------------------------
# resolve_lifecycle_target
# ---------------------------------------------------------------------------

def resolve_lifecycle_target(
    old: str | None,
    std: StandardPaths,
    config: KanibakoConfig | None = None,
) -> ProjectState:
    """Resolve an existing project (by path or name) to a :class:`ProjectState`.

    *old* may be a path or a registered project/workset-relative name; ``None``
    means the current working directory.  Builds on the existing detectors and
    resolvers; honors ``meta["workspace"]`` overrides (external-connected
    projects) so the descriptor reflects the *live* workspace location.

    Raises :class:`ProjectError` / :class:`WorksetError` when no project is
    found.
    """
    import os

    if config is None:
        from kanibako.config import config_file_path, load_config
        from kanibako.paths import xdg
        config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))

    raw = old or os.getcwd()
    # Front-door (mirrors resolve_any_project): a bare token (no path separator)
    # that doesn't exist in cwd may be a registered project/workset name.  This
    # is essential for ``remap``/``convert`` when the folder has already moved,
    # so the on-disk path is stale but the name still resolves.
    raw_name = raw
    named_workset = False
    if raw and "/" not in raw and not Path(raw).exists():
        from kanibako.paths import resolve_name
        try:
            resolved, kind = resolve_name(std.data_path, raw, cwd=Path.cwd())
            if kind in ("project", "workset"):
                # Update `raw` for BOTH kinds (mirrors resolve_any_project): a
                # bare workset name resolves to the workset ROOT, which
                # detect_project_mode must see -- without this the name
                # path-ifies to cwd/<name> and resolution fails misleadingly.
                raw = resolved
                named_workset = kind == "workset"
        except ProjectError:
            pass
    if named_workset:
        # Lifecycle ops (remap/move/convert) act on a single project box; a
        # workset is not one.  Reject with an actionable message.
        raise WorksetError(
            f"'{raw_name}' is a workset, not a single project box. "
            f"Name a project inside it (e.g. '{raw_name}/<project>') or run the "
            f"command from a project workspace under that workset."
        )
    # Qualified ``workset/project`` addressing (mirrors resolve_any_project): a
    # token with a separator that is NOT an existing path may be a qualified
    # name -- the form the bare-workset rejection above suggests.  Resolve it to
    # the project's workspace so detect_project_mode sees a single box.  A real
    # relative path that happens not to exist is left untouched (falls through
    # to the path-ify below, failing exactly as before).
    if raw and "/" in raw and not Path(raw).exists():
        from kanibako.names import resolve_qualified_name
        try:
            project_workspace, _ws_name = resolve_qualified_name(
                std.data_path, raw,
            )
            raw = project_workspace
        except ProjectError:
            pass
    raw_path = Path(raw).resolve()

    detection = detect_project_mode(raw_path, std, config)

    if detection.mode == BoxMode.named:
        return _resolve_workset_state(raw_path, std, config)
    if detection.mode == BoxMode.standalone:
        proj = resolve_standalone_project(
            std, config, str(detection.project_root), initialize=False,
        )
        return _state_from_paths("standalone", proj, ws=None)

    # default mode
    root = detection.project_root
    if not root.is_dir():
        # The workspace dir is gone (e.g. the user moved the folder before
        # running `remap`).  resolve_project requires the dir to exist, so fall
        # back to building the state from the registered metadata alone.
        fallback = _default_state_from_meta(root, std)
        if fallback is not None:
            return fallback
    proj = resolve_project(
        std, config, project_dir=str(root), initialize=False,
    )
    if not proj.metadata_path.is_dir():
        raise ProjectError(f"No project data found for {root}")
    return _state_from_paths("primary", proj, ws=None)


def _default_state_from_meta(
    workspace: Path, std: StandardPaths,
) -> ProjectState | None:
    """Build a default-mode :class:`ProjectState` from registered metadata.

    Used by ``remap`` when the recorded workspace directory no longer exists on
    disk: the project is still registered in ``names.yaml`` (path -> name) and
    its metadata lives in ``boxes/<name>``.  Returns ``None`` when no such
    registration is found, so the caller can raise the normal error.
    """
    from kanibako.names import read_names

    names = read_names(std.data_path)
    name: str | None = None
    for n, p in names["projects"].items():
        if Path(p).resolve() == workspace.resolve():
            name = n
            break
    if name is None:
        return None
    metadata_path = std.boxes / name
    meta = read_project_meta(metadata_path / "project.yaml")
    if not meta:
        return None
    layout = ProjectLayout(meta["layout"]) if meta.get("layout") else ProjectLayout.default
    shell_path = Path(meta["shell"]) if meta.get("shell") else metadata_path / "shell"
    vault_ro = Path(meta["vault_ro"]) if meta.get("vault_ro") else metadata_path / "vault" / "ro"
    vault_rw = Path(meta["vault_rw"]) if meta.get("vault_rw") else metadata_path / "vault" / "rw"
    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=name,
        workspace_path=workspace.resolve(), metadata_path=metadata_path,
        shell_path=shell_path, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None, layout=layout,
        enable_vault=bool(meta.get("enable_vault", True)),
        group_auth=bool(meta.get("group_auth", True)),
    )


def _resolve_workset_state(
    raw_path: Path, std: StandardPaths, config: KanibakoConfig,
) -> ProjectState:
    """Resolve a workset project (internal or external-connected) to a state."""
    ws: Workset | None = None
    proj_name: str | None = None
    try:
        ws, proj_name = _find_workset_for_path(raw_path, std)  # type: ignore[assignment]
    except WorksetError:
        ws, proj_name = None, None
    if ws is None or proj_name is None:
        hit = _find_connected_project(raw_path, std)
        if hit is not None:
            ws, proj_name = hit
    if ws is None or proj_name is None:
        raise WorksetError(f"No workset project found for path: {raw_path}")

    proj = resolve_workset_project(
        WorksetSpec.from_workset(ws), proj_name, std, config, initialize=False,
    )
    # External when the live workspace is outside the workset root.
    is_external = True
    try:
        proj.project_path.resolve().relative_to(ws.root.resolve())
        is_external = False
    except ValueError:
        is_external = True
    return _state_from_paths(
        owner_token(BoxMode.named, ws.name), proj, ws=ws,
        is_external=is_external,
    )


def _state_from_paths(
    owner: str,
    proj: ProjectPaths,
    *,
    ws: Workset | None,
    is_external: bool = False,
) -> ProjectState:
    return ProjectState(
        owner=owner,
        mode=proj.mode,
        name=proj.name or proj.project_path.name,
        workspace_path=proj.project_path,
        metadata_path=proj.metadata_path,
        shell_path=proj.shell_path,
        vault_ro=proj.vault_ro_path,
        vault_rw=proj.vault_rw_path,
        is_external=is_external,
        ws=ws,
        layout=proj.layout,
        enable_vault=proj.enable_vault,
        group_auth=proj.group_auth,
    )


# ---------------------------------------------------------------------------
# Std-aware workset copy helper for ``box duplicate``
# ---------------------------------------------------------------------------

def copy_into_workset(
    ws: Workset,
    proj_name: str,
    metadata_path: Path,
    shell_path: Path,
    source_path: Path,
    source_mode: BoxMode,
    *,
    copy_workspace: bool,
    std: StandardPaths,
) -> None:
    """Re-root a project into *ws* — the std-aware copy path for ``duplicate``.

    The duplicate is always an INTERNAL workset project: it gets a real
    ``workspaces/<name>`` directory, never an external symlink/redirect back to
    the source.  Duplicate makes a *copy*, not a *connection*; an external
    connection (1:1 in ``connected.yaml``) is what ``connect`` is for, and a bare
    duplicate of an already-connected source is refused up front in
    ``run_duplicate``.

    *std* is threaded to :func:`kanibako.workset.add_project` so its up-front
    guards run (and to keep a single std-aware registration path); because the
    registration target is the in-tree workspace dir, ``add_project`` always
    creates a real directory and writes no external markers — which also avoids
    the source-into-symlink ``copytree`` collision that registering the external
    *source* path would cause.

    *copy_workspace* controls whether the source tree is copied into the new
    internal workspace (``True``) or it is left as an empty skeleton dir (a
    *bare* duplicate, ``False``).  *metadata_path* / *shell_path* are the SOURCE
    project's dirs to copy from.
    """
    # Register the in-tree workspace dir (INTERNAL): add_project then makes a real
    # directory rather than connecting to (and symlinking at) the source.
    add_project(ws, proj_name, ws.workspaces_dir / proj_name, std)

    # Failure-consistency: a crash AFTER add_project (which registers the project
    # in workset.yaml + creates per-project dirs) but DURING the copies below
    # would otherwise strand a registered-but-incomplete project.  Roll the
    # registration + partial dirs back on any failure, then re-raise.
    # remove_project(remove_files=True, std=...) is idempotent and removes only
    # workset-side dirs (never the user's external source).
    try:
        dst_project = ws.projects_dir / proj_name
        shutil.copytree(
            metadata_path, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
            dirs_exist_ok=True,
        )

        if shell_path.is_dir():
            dst_shell = dst_project / "shell"
            shutil.copytree(shell_path, dst_shell, dirs_exist_ok=True)

        if copy_workspace:
            dst_workspace = ws.workspaces_dir / proj_name
            ignore = None
            if source_mode == BoxMode.standalone:
                ignore = shutil.ignore_patterns(".kanibako", "kanibako")
            shutil.copytree(source_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)
    except BaseException:
        try:
            remove_project(ws, proj_name, remove_files=True, std=std)
        except Exception:  # noqa: BLE001 - best-effort rollback
            pass
        raise


# ---------------------------------------------------------------------------
# Unwind stack
# ---------------------------------------------------------------------------

@dataclass
class _Unwind:
    """A LIFO stack of compensating actions for failure-consistency.

    Each pushed action is a zero-arg callable that reverses a forward step.
    On :meth:`run`, actions execute in reverse order; individual failures are
    swallowed (best-effort restore) so one bad unwind does not mask the rest.
    """

    actions: list[Callable[[], None]] = field(default_factory=list)
    cleanups: list[Callable[[], None]] = field(default_factory=list)

    def push(self, action: Callable[[], None]) -> None:
        self.actions.append(action)

    def on_success(self, action: Callable[[], None]) -> None:
        """Register an action to run only when the whole op succeeds.

        Used for scratch (e.g. an unwind stash) that must survive until the
        operation completes but be discarded on success.
        """
        self.cleanups.append(action)

    def run(self) -> None:
        while self.actions:
            action = self.actions.pop()
            try:
                action()
            except Exception:  # noqa: BLE001 - best-effort restore
                pass

    def finish(self) -> None:
        """Run success cleanups (best-effort)."""
        for action in self.cleanups:
            try:
                action()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

STUBBORN_INPLACE_MSG = (
    "Stubbornly refusing to convert in-place from within a workset; add "
    "`--move` or `--move <path>` to relocate."
)


def _resolve_target_workset(
    name: str, std: StandardPaths,
) -> Workset:
    registry = list_worksets(std)
    if name not in registry:
        raise WorksetError(f"Workset '{name}' not found.")
    return load_workset(registry[name])


def _validate(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    force: bool,
    cwd: Path,
) -> dict:
    """Validate the requested operation up front; return resolved plan facts.

    Refuses early (raises ProjectError/WorksetError) so steps 2-5 only run on a
    sound request → zero partial state.  Returns a dict carrying the resolved
    target mode / target workset / destination so :func:`execute_lifecycle`
    need not re-derive them.
    """
    # --- resolve ownership target ---
    if spec.ownership is UNCHANGED:
        target_mode = state.mode
        target_ws_name = (
            state.ws.name if state.ws is not None else None
        )
    else:
        target_mode, target_ws_name = _ownership_to_mode(spec.ownership)  # type: ignore[arg-type]

    target_ws: Workset | None = None
    if target_mode == BoxMode.named:
        if target_ws_name is None:
            raise WorksetError("Workset target requires a workset name.")
        if spec.ownership is UNCHANGED and state.ws is not None:
            target_ws = state.ws
        else:
            target_ws = _resolve_target_workset(target_ws_name, std)

    # --- bare-into-ws only valid with a workset target ---
    if spec.location is BARE_INTO_WS and target_mode != BoxMode.named:
        raise ProjectError(
            "bare --move (into the workset) requires a workset target."
        )

    new_name = spec.name or state.name

    # --- compute destination path (location axis) ---
    dest: Path | None = None
    if spec.location is BARE_INTO_WS:
        assert target_ws is not None
        dest = (target_ws.workspaces_dir / new_name).resolve()
    elif isinstance(spec.location, Path):
        dest = spec.location.resolve()

    relocating = dest is not None

    # --- no-op guard ---
    no_owner_change = (
        spec.ownership is UNCHANGED
        or (target_mode == state.mode
            and (target_mode != BoxMode.named
                 or (state.ws is not None and target_ws is not None
                     and state.ws.name == target_ws.name)))
    )
    no_rename = new_name == state.name
    if not relocating and no_owner_change and no_rename:
        raise ProjectError(
            "Nothing to do: target equals the current location, owner, and name."
        )

    # --- workset -> workset with INTERNAL workspace requires relocation ---
    is_ws_to_ws = (
        state.mode == BoxMode.named
        and target_mode == BoxMode.named
        and not no_owner_change
    )
    if is_ws_to_ws and not state.is_external and not relocating:
        raise ProjectError(STUBBORN_INPLACE_MSG)

    # --- destination not already occupied ---
    # ``remap`` is records-only: the files are presumed ALREADY at *dest*, so we
    # do not require it to be empty (and a no-op same-path remap is fine).
    if relocating and dest is not None and not spec.records_only:
        if dest.resolve() == state.workspace_path.resolve():
            raise ProjectError(
                f"Destination is the project's current location: {dest}"
            )
        if dest.exists():
            raise ProjectError(f"Destination already exists: {dest}")

    # --- membership guard: refuse landing inside a workset the project is
    #     not (becoming) a member of ---
    # ``relocating`` is exactly ``dest is not None`` (set above); test dest
    # directly so mypy narrows away the None for the .resolve() below.
    landing = dest if dest is not None else state.workspace_path
    owning_ws_root: Path | None = None
    if target_mode == BoxMode.named and target_ws is not None:
        owning_ws_root = target_ws.root.resolve()
    for ws_name, ws_root in list_worksets(std).items():
        ws_root = Path(ws_root).resolve()
        if owning_ws_root is not None and ws_root == owning_ws_root:
            continue
        try:
            landing.resolve().relative_to(ws_root)
        except ValueError:
            continue
        raise ProjectError(
            f"Refusing to land the project inside workset '{ws_name}' "
            f"({ws_root}) — it is not (being made) a member of that workset. "
            "Use `--workset {ws_name}` to make it a member, or choose a "
            "destination outside that workset."
        )

    # --- CWD-inside-<old> guard (move is copytree+rmtree, not rename) ---
    # ``remap`` removes nothing, so it cannot strand the shell — skip the guard.
    if relocating and not state.is_external and not spec.records_only:
        old = state.workspace_path.resolve()
        cwd_r = cwd.resolve()
        inside = cwd_r == old
        if not inside:
            try:
                cwd_r.relative_to(old)
                inside = True
            except ValueError:
                inside = False
        if inside and not force:
            raise ProjectError(
                f"Refusing to move: your shell's current directory is inside "
                f"the project being moved ({old}). Moving would strand your "
                f"shell on a removed directory. cd out first, or pass --force "
                f"(then run: cd {dest})."
            )

    # --- name not taken in target workset ---
    if (
        target_mode == BoxMode.named
        and target_ws is not None
        and not (state.ws is not None and target_ws.name == state.ws.name
                 and new_name == state.name)
    ):
        for p in target_ws.projects:
            if p.name == new_name:
                raise WorksetError(
                    f"Project '{new_name}' already exists in workset "
                    f"'{target_ws.name}'."
                )

    return {
        "target_mode": target_mode,
        "target_ws": target_ws,
        "target_ws_name": target_ws_name,
        "dest": dest,
        "relocating": relocating,
        "no_owner_change": no_owner_change,
        "new_name": new_name,
    }


# ---------------------------------------------------------------------------
# execute_lifecycle — the shared transactional routine
# ---------------------------------------------------------------------------

def execute_lifecycle(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: KanibakoConfig | None = None,
    *,
    force: bool = False,
    confirm: Callable[[], bool] | None = None,
) -> ProjectState:
    """Apply *spec* to *state* transactionally and return the new state.

    Canonical 5-step order (see the redesign DESIGN):

      1. Validate everything up front (refuse early → zero partial state).
      2. Move files (if relocating).
      3. Update location records / markers.
      4. Apply ownership / mode change (re-root metadata/shell/vault; registry;
         names; rewrite ``project.yaml`` mode + paths).
      5. Clean up old (never the user's external source dir).

    Steps 2-5 push compensating actions onto an unwind stack; on ANY exception
    the stack runs in reverse to restore a consistent state, then re-raises.
    *confirm*, if given, is called after validation; returning False aborts
    cleanly (no changes) by raising :class:`ProjectError`.
    """
    import os

    if config is None:
        from kanibako.config import config_file_path, load_config
        from kanibako.paths import xdg
        config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))

    cwd = Path(os.getcwd())
    plan = _validate(state, spec, std, config, force=force, cwd=cwd)

    if confirm is not None and not confirm():
        raise ProjectError("Aborted by user.")

    unwind = _Unwind()
    try:
        new_state = _run_steps(state, spec, std, config, plan, unwind)
    except Exception:
        unwind.run()
        raise
    unwind.finish()
    return new_state


def _run_steps(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: KanibakoConfig,
    plan: dict,
    unwind: _Unwind,
) -> ProjectState:
    target_mode: BoxMode = plan["target_mode"]
    target_ws: Workset | None = plan["target_ws"]
    dest: Path | None = plan["dest"]
    relocating: bool = plan["relocating"]
    new_name: str = plan["new_name"]

    # ------------------------------------------------------------------
    # STEP 2 — Move files (only when relocating a real workspace tree).
    # For workset->workset re-roots the *workspace* is not what relocates
    # here when external; for internal ws->ws the move IS required and was
    # validated.  Standard moves copytree the workspace to dest.
    # ------------------------------------------------------------------
    records_only: bool = spec.records_only
    new_workspace = state.workspace_path
    if records_only and dest is not None:
        # ``remap``: the files are presumed already at *dest*; record the new
        # location without copying or removing anything.
        new_workspace = dest
    elif relocating and dest is not None and not state.is_external:
        src = state.workspace_path
        shutil.copytree(src, dest)
        unwind.push(lambda: shutil.rmtree(dest, ignore_errors=True))
        new_workspace = dest
    elif relocating and dest is not None and state.is_external:
        # External source: the "workspace" is the user's external dir; we never
        # move it.  A relocation request on an external project means re-point
        # to a new external/internal location is out of Phase-1 scope beyond
        # ws->ws repoint (handled in ownership). Treat dest as the new recorded
        # location only when it is the destination of an internalizing move.
        new_workspace = dest

    # ------------------------------------------------------------------
    # STEP 4 (ownership) is interleaved with STEP 3 (markers) because the
    # destination metadata roots depend on the target owner.  We compute the
    # new metadata/shell/vault dirs for the target owner, copy them, write the
    # rewritten project.yaml (with correct mode + workspace override + hash +
    # markers), then update registry/names, then clean up the old side.
    # ------------------------------------------------------------------
    new_state = _apply_ownership_and_markers(
        state, std, config, unwind,
        target_mode=target_mode,
        target_ws=target_ws,
        new_name=new_name,
        new_workspace=new_workspace,
        relocating=relocating,
        dest=dest,
    )

    # ------------------------------------------------------------------
    # STEP 5 — Clean up old workspace (only for a real, internal move).
    # NEVER delete a user's external source directory.
    # ------------------------------------------------------------------
    if not records_only and relocating and dest is not None and not state.is_external:
        old_ws = state.workspace_path
        if old_ws.resolve() != dest.resolve() and old_ws.is_dir():
            # Irreversible-ish; but we copied first and recorded everything, so
            # this is the last step.  Keep a backup move for unwind safety.
            shutil.rmtree(old_ws, ignore_errors=True)

    return new_state


def _apply_ownership_and_markers(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    unwind: _Unwind,
    *,
    target_mode: BoxMode,
    target_ws: Workset | None,
    new_name: str,
    new_workspace: Path,
    relocating: bool,
    dest: Path | None,
) -> ProjectState:
    """Re-root metadata/shell/vault into the target owner + rewrite markers.

    Handles every transition by copying the source metadata into the target
    owner's metadata root, writing a fresh ``project.yaml`` (mode + paths +
    workspace override + hash), updating registry/names, and removing the old
    owner's metadata.  Returns the resulting :class:`ProjectState`.
    """
    if target_mode == BoxMode.named:
        return _to_workset(
            state, std, config, unwind,
            target_ws=target_ws,  # type: ignore[arg-type]
            new_name=new_name,
            new_workspace=new_workspace,
            relocating=relocating,
            dest=dest,
        )
    if target_mode == BoxMode.standalone:
        return _to_standalone(
            state, std, config, unwind,
            new_name=new_name, new_workspace=new_workspace,
        )
    return _to_default(
        state, std, config, unwind,
        new_name=new_name, new_workspace=new_workspace,
    )


# -- per-target-mode ownership steps ---------------------------------------

def _copy_metadata(
    src_metadata: Path,
    src_shell: Path,
    dst_metadata: Path,
    *,
    shell_into_metadata: bool,
    unwind: _Unwind,
) -> Path:
    """Copy metadata (minus lock+shell) and shell into *dst_metadata*.

    Returns the destination shell path.  Pushes an rmtree of *dst_metadata*
    onto *unwind*.
    """
    shutil.copytree(
        src_metadata, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
        dirs_exist_ok=True,
    )
    unwind.push(lambda: shutil.rmtree(dst_metadata, ignore_errors=True))

    if shell_into_metadata:
        dst_shell = dst_metadata / "shell"
    else:
        dst_shell = dst_metadata / "shell"
    if src_shell.is_dir():
        shutil.copytree(src_shell, dst_shell, dirs_exist_ok=True)
    return dst_shell


def _remove_old_metadata(state: ProjectState, std: StandardPaths, config: KanibakoConfig) -> None:
    """Remove the source project's metadata/shell + vault symlinks.

    Standalone source: removes the in-tree ``.kanibako`` metadata dir.
    Default source: unregisters the name, removes human-vault symlink, removes
    the boxes metadata dir.
    Workset source: removes the workset registration (std-aware) so external
    markers/symlink/connected.yaml are cleaned; the external source dir is never
    deleted.
    """
    if state.mode == BoxMode.standalone:
        _remove_project_vault_symlink(state.workspace_path)
        if state.metadata_path.is_dir():
            shutil.rmtree(state.metadata_path, ignore_errors=True)
        return

    if state.mode == BoxMode.primary:
        human_vault_dir = std.data_path / config.paths_vault
        _remove_human_vault_symlink(human_vault_dir, state.metadata_path / "vault")
        _remove_project_vault_symlink(state.workspace_path)
        if state.name:
            try:
                unregister_name(std.data_path, state.name)
            except Exception:  # noqa: BLE001
                pass
        if state.metadata_path.is_dir():
            shutil.rmtree(state.metadata_path, ignore_errors=True)
        if state.shell_path.is_dir() and state.shell_path != state.metadata_path / "shell":
            try:
                state.shell_path.relative_to(state.metadata_path)
            except ValueError:
                shutil.rmtree(state.shell_path, ignore_errors=True)
        return

    # workset source
    if state.ws is not None:
        remove_project(state.ws, state.name, remove_files=True, std=std)


def _to_default(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    unwind: _Unwind,
    *,
    new_name: str,
    new_workspace: Path,
) -> ProjectState:
    """Convert/relocate the project so its owner becomes the default workset."""
    project_name = assign_name(std.data_path, str(new_workspace))
    unwind.push(lambda: _safe_unregister(std, project_name))
    dst_metadata = std.boxes / project_name

    dst_shell = _copy_metadata(
        state.metadata_path, state.shell_path, dst_metadata,
        shell_into_metadata=True, unwind=unwind,
    )

    phash = project_hash(str(new_workspace.resolve()))
    layout = state.layout if state.layout != ProjectLayout.simple else ProjectLayout.default
    vault_root = dst_metadata / "vault" if layout == ProjectLayout.robust else new_workspace / "vault"
    vault_ro = vault_root / "ro"
    vault_rw = vault_root / "rw"

    _global_shared = std.data_path / config.paths_shared / "global"
    _local_shared = std.data_path / config.paths_shared
    write_project_meta(
        dst_metadata / "project.yaml",
        mode="primary",
        layout=layout.value,
        workspace=str(new_workspace),
        shell=str(dst_shell),
        vault_ro=str(vault_ro),
        vault_rw=str(vault_rw),
        enable_vault=state.enable_vault,
        group_auth=state.group_auth,
        metadata=str(dst_metadata),
        project_hash=phash,
        global_shared=str(_global_shared),
        local_shared=str(_local_shared),
        name=project_name,
    )

    # Human-vault symlink for robust layout (best-effort).
    if (dst_metadata / "vault").is_dir():
        _ensure_human_vault_symlink(
            std.data_path / config.paths_vault, new_workspace, dst_metadata / "vault",
        )

    _remove_old_metadata(state, std, config)

    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=project_name,
        workspace_path=new_workspace, metadata_path=dst_metadata,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None, layout=layout,
        enable_vault=state.enable_vault, group_auth=state.group_auth,
    )


def _to_standalone(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    unwind: _Unwind,
    *,
    new_name: str,
    new_workspace: Path,
) -> ProjectState:
    """Convert/relocate the project so it becomes standalone (in-tree metadata)."""
    new_workspace.mkdir(parents=True, exist_ok=True)
    dst_metadata = new_workspace / ".kanibako"

    dst_shell = _copy_metadata(
        state.metadata_path, state.shell_path, dst_metadata,
        shell_into_metadata=True, unwind=unwind,
    )

    phash = project_hash(str(new_workspace.resolve()))
    layout = ProjectLayout.simple
    vault_ro = new_workspace / "vault" / "ro"
    vault_rw = new_workspace / "vault" / "rw"

    write_project_meta(
        dst_metadata / "project.yaml",
        mode="standalone",
        layout=layout.value,
        workspace=str(new_workspace),
        shell=str(dst_shell),
        vault_ro=str(vault_ro),
        vault_rw=str(vault_rw),
        enable_vault=state.enable_vault,
        group_auth=state.group_auth,
        metadata=str(dst_metadata),
        project_hash=phash,
        name=new_name,
    )

    write_project_gitignore(new_workspace)
    vault_dir = new_workspace / "vault"
    if vault_dir.is_dir():
        gi = vault_dir / ".gitignore"
        if not gi.exists():
            gi.write_text("rw/\n")

    _remove_old_metadata(state, std, config)

    return ProjectState(
        owner="standalone", mode=BoxMode.standalone, name=new_name,
        workspace_path=new_workspace, metadata_path=dst_metadata,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None, layout=layout,
        enable_vault=state.enable_vault, group_auth=state.group_auth,
    )


def _to_workset(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    unwind: _Unwind,
    *,
    target_ws: Workset,
    new_name: str,
    new_workspace: Path,
    relocating: bool,
    dest: Path | None,
) -> ProjectState:
    """Convert/relocate the project into *target_ws* (std-aware external wiring)."""
    # Is the (new) workspace inside the target workset's tree?
    internal = False
    try:
        new_workspace.resolve().relative_to(target_ws.root.resolve())
        internal = True
    except ValueError:
        internal = False

    # Source path that add_project records + decides external wiring from.
    # For an internal landing we pass the in-tree workspace dir; for external we
    # pass the live external workspace path.
    source_for_add = new_workspace

    # Whether to copy the workspace tree into the workset.
    # - internal landing where the workspace is NOT already the in-tree dir →
    #   copy.  (When relocating into the ws via STEP 2 we already moved the
    #   tree to dest == workspaces/<name>, so don't copy again.)
    # - external → never copy.
    copy_workspace = False
    if internal:
        expected_internal = (target_ws.workspaces_dir / new_name).resolve()
        already_in_place = new_workspace.resolve() == expected_internal
        copy_workspace = not already_in_place

    # workset -> workset re-root: the source workset must release the project
    # BEFORE the target registers it.  The connected.yaml redirect is 1:1, so an
    # external source still mapped to the OLD workset would collide with
    # add_project's "already connected" guard.  We therefore drop the source
    # registration first (clears connected.yaml + the discoverability symlink;
    # NEVER touches the user's external dir), capture a snapshot of the source
    # metadata for unwind, then register with the target.  For an internal
    # ws->ws move the workspace tree was already relocated in STEP 2, so removing
    # the source project (remove_files) only sweeps the leftover skeleton dirs.
    # Where to read the source metadata/shell from when copying into the target.
    # Default: the live source paths.  For a workset source we release the
    # registration FIRST (see below), which deletes those dirs, so we copy from a
    # stash instead.
    metadata_source = state.metadata_path
    shell_source = state.shell_path

    source_is_workset = state.mode == BoxMode.named
    if source_is_workset and state.ws is not None:
        src_ws = state.ws
        src_name = state.name
        src_source_path = state.workspace_path
        # Stash the source metadata (incl. shell) so the forward copy below and
        # the unwind both have a stable source after release.
        import tempfile
        stash = Path(tempfile.mkdtemp(prefix="kanibako-unwind-"))
        stash_boxes = stash / "boxes"
        if state.metadata_path.is_dir():
            shutil.copytree(
                state.metadata_path, stash_boxes,
                ignore=shutil.ignore_patterns(".kanibako.lock"),
                dirs_exist_ok=True,
            )
        metadata_source = stash_boxes
        shell_source = stash_boxes / "shell"
        remove_project(src_ws, src_name, remove_files=True, std=std)

        def _restore_source() -> None:
            add_project(src_ws, src_name, src_source_path, std)
            if stash_boxes.is_dir():
                shutil.copytree(
                    stash_boxes, src_ws.projects_dir / src_name,
                    dirs_exist_ok=True,
                )
            shutil.rmtree(stash, ignore_errors=True)

        unwind.push(_restore_source)
        # Discard the stash on success (kept intact while unwind may need it).
        unwind.on_success(lambda: shutil.rmtree(stash, ignore_errors=True))

    # add_project (std-aware) registers + creates skeleton + (external) markers.
    add_project(target_ws, new_name, source_for_add, std)
    unwind.push(
        lambda: _safe_remove_project(target_ws, new_name, std)
    )

    dst_project = target_ws.projects_dir / new_name
    # Copy metadata (minus lock+shell) into the workset boxes dir.
    shutil.copytree(
        metadata_source, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
        dirs_exist_ok=True,
    )
    dst_shell = dst_project / "shell"
    if shell_source.is_dir():
        shutil.copytree(shell_source, dst_shell, dirs_exist_ok=True)

    if copy_workspace:
        dst_workspace = target_ws.workspaces_dir / new_name
        ignore = None
        if state.mode == BoxMode.standalone:
            ignore = shutil.ignore_patterns(".kanibako", "kanibako")
        shutil.copytree(state.workspace_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)

    # Determine the recorded workspace + hash.
    if internal:
        recorded_workspace = (target_ws.workspaces_dir / new_name)
    else:
        recorded_workspace = new_workspace
    phash = project_hash(str(recorded_workspace.resolve()))

    layout = ProjectLayout.robust
    vault_ro = target_ws.vault_dir / new_name / "ro"
    vault_rw = target_ws.vault_dir / new_name / "rw"
    _global_shared = std.data_path / config.paths_shared / "global"
    _local_shared = target_ws.root / config.paths_shared

    # Rewrite project.yaml.  add_project (external) already wrote a minimal
    # project.yaml with the workspace override; we overwrite with full content.
    write_project_meta(
        dst_project / "project.yaml",
        mode="named",
        layout=layout.value,
        workspace=str(recorded_workspace),
        shell=str(dst_shell),
        vault_ro=str(vault_ro),
        vault_rw=str(vault_rw),
        enable_vault=state.enable_vault,
        group_auth=state.group_auth,
        metadata=str(dst_project),
        project_hash=phash,
        global_shared=str(_global_shared),
        local_shared=str(_local_shared),
        name=new_name,
    )

    # For a workset source the registration was already released above;
    # otherwise clean up the source owner's metadata/markers now.
    if not source_is_workset:
        _remove_old_metadata(state, std, config)

    return ProjectState(
        owner=owner_token(BoxMode.named, target_ws.name),
        mode=BoxMode.named, name=new_name,
        workspace_path=recorded_workspace, metadata_path=dst_project,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=not internal, ws=target_ws, layout=layout,
        enable_vault=state.enable_vault, group_auth=state.group_auth,
    )


# -- small helpers ----------------------------------------------------------

def _safe_unregister(std: StandardPaths, name: str) -> None:
    try:
        unregister_name(std.data_path, name)
    except Exception:  # noqa: BLE001
        pass


def _safe_remove_project(ws: Workset, name: str, std: StandardPaths) -> None:
    try:
        remove_project(ws, name, remove_files=True, std=std)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# CLI entry points: run_remap / run_move / run_convert
# ---------------------------------------------------------------------------

def _ownership_from_args(args) -> str | _Sentinel:
    """Map the uniform target flags (--default/--standalone/--workset) to an
    ownership value, or :data:`UNCHANGED` when none is given.

    The three flags are mutually exclusive (enforced by an argparse mutually
    exclusive group); ``--workset`` carries the workset name.
    """
    if getattr(args, "to_default", False):
        return "default"
    if getattr(args, "to_standalone", False):
        return "standalone"
    ws = getattr(args, "to_workset", None)
    if ws:
        return ws
    return UNCHANGED


def _make_confirm(force: bool, summary: str):
    """Return a ``Callable[[], bool]`` for ``execute_lifecycle``'s *confirm*.

    With *force* the op proceeds without prompting.  Otherwise it prints
    *summary* and prompts; a non-``yes`` answer returns False (engine aborts).
    """
    if force:
        return None

    from kanibako.errors import UserCancelled
    from kanibako.utils import confirm_prompt

    def _confirm() -> bool:
        print(summary)
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except UserCancelled:
            return False
        return True

    return _confirm


def _load_env():
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths, xdg

    config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))
    std = load_std_paths(config)
    return config, std


def _abort_if_locked(state: ProjectState, force: bool) -> bool:
    """Refuse a destructive relocation while a box may be running.

    ``move`` / ``convert`` copy then ``rmtree`` the source workspace, which for a
    running box would delete the live bind-mounted directory out from under it.
    Mirror ``box duplicate``'s lock pre-flight (``_duplicate.py``): if the
    project's ``.kanibako.lock`` is present, warn and abort unless *force* is set.
    Returns True when the caller should abort (and has been warned).
    """
    import sys

    lock_file = state.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this "
            "project. Moving/converting it would copy then DELETE the live "
            "workspace. Stop the box first (kanibako stop), or pass --force.",
            file=sys.stderr,
        )
        if not force:
            print("Aborted.")
            return True
    return False


def run_remap(args) -> int:
    """``box remap <old> [<new>]`` — records-only relocation.

    The folder has already moved on disk; update kanibako's recorded path,
    hash, and markers to reflect the new location.  Does NOT move files and
    never changes ownership.
    """
    import sys

    config, std = _load_env()

    old = getattr(args, "old", None)
    new = getattr(args, "new", None) or "./"
    new_path = Path(new).resolve()

    try:
        state = resolve_lifecycle_target(old, std, config)
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    spec = TargetSpec(location=new_path, ownership=UNCHANGED, records_only=True)
    summary = (
        "Remap project records (no files moved):\n"
        f"  project: {state.name}\n"
        f"     from: {state.workspace_path}\n"
        f"       to: {new_path}"
    )
    try:
        new_state = execute_lifecycle(
            state, spec, std, config,
            force=getattr(args, "force", False),
            confirm=_make_confirm(getattr(args, "force", False), summary),
        )
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Remapped '{new_state.name}' to {new_state.workspace_path}")
    return 0


def run_move(args) -> int:
    """``box move <old> <new>`` (alias ``mv``) — physically relocate files.

    Both paths are required.  An optional target flag
    (--default/--standalone/--workset) also changes ownership.  Refuses an
    external-connected project (its workspace is the user's own directory).
    """
    import sys

    config, std = _load_env()

    old = getattr(args, "old", None)
    new = getattr(args, "new", None)
    if not old or not new:
        print("Error: move requires both <old> and <new>.", file=sys.stderr)
        return 1
    new_path = Path(new).resolve()

    try:
        state = resolve_lifecycle_target(old, std, config)
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if state.is_external:
        print(
            f"Error: '{state.name}' is an external-connected project; its "
            "workspace is your own directory, not managed by kanibako.\n"
            "Use `box remap <old> <new>` to update records if you moved it, "
            "or `box convert` to change ownership.",
            file=sys.stderr,
        )
        return 1

    if _abort_if_locked(state, getattr(args, "force", False)):
        return 2

    ownership = _ownership_from_args(args)
    spec = TargetSpec(
        location=new_path, ownership=ownership, name=getattr(args, "name", None),
    )
    summary = (
        "Move project workspace:\n"
        f"  project: {state.name}\n"
        f"     from: {state.workspace_path}\n"
        f"       to: {new_path}"
    )
    try:
        new_state = execute_lifecycle(
            state, spec, std, config,
            force=getattr(args, "force", False),
            confirm=_make_confirm(getattr(args, "force", False), summary),
        )
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Moved '{new_state.name}' to {new_state.workspace_path}")
    return 0


def run_convert(args) -> int:
    """``box convert [<old>] (--default|--standalone|--workset <ws>) [--move [path]]``.

    Change a project's ownership/mode.  In-place by default for all modes;
    ``--move <path>`` relocates, bare ``--move`` moves into the target workset
    (only valid with ``--workset``).  ``--name`` renames in the target.
    """
    import sys

    config, std = _load_env()

    ownership = _ownership_from_args(args)
    if ownership is UNCHANGED:
        print(
            "Error: convert requires a target "
            "(--default, --standalone, or --workset <ws>).",
            file=sys.stderr,
        )
        return 1

    # --move handling: argparse stores _BARE_MOVE sentinel for bare --move,
    # a path string for --move <path>, and None when absent.
    move_val = getattr(args, "move", None)
    if move_val is None:
        location: Path | _Sentinel = INPLACE
    elif move_val is _BARE_MOVE:
        # Bare --move is only valid with a workset target.
        if not getattr(args, "to_workset", None):
            print(
                "Error: bare `--move` (into the workset) requires "
                "`--workset <ws>`. Use `--move <path>` to relocate elsewhere.",
                file=sys.stderr,
            )
            return 1
        location = BARE_INTO_WS
    else:
        location = Path(move_val).resolve()

    try:
        state = resolve_lifecycle_target(getattr(args, "old", None), std, config)
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Lock pre-flight: convert re-roots by copy then rmtree of the source
    # workspace/metadata, so abort while a box may be running unless --force
    # (mirrors move / duplicate).
    if _abort_if_locked(state, getattr(args, "force", False)):
        return 2

    spec = TargetSpec(
        location=location, ownership=ownership, name=getattr(args, "name", None),
    )
    if location is INPLACE:
        loc_desc = "in place"
    elif location is BARE_INTO_WS:
        loc_desc = "into the workset"
    else:
        loc_desc = f"to {location}"
    summary = (
        "Convert project:\n"
        f"  project: {state.name}\n"
        f"    owner: {state.owner} -> {ownership}\n"
        f" location: {loc_desc}"
    )
    try:
        new_state = execute_lifecycle(
            state, spec, std, config,
            force=getattr(args, "force", False),
            confirm=_make_confirm(getattr(args, "force", False), summary),
        )
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(
        f"Converted '{new_state.name}' to {new_state.owner} "
        f"({new_state.workspace_path})"
    )
    return 0


#: argparse ``const`` sentinel for a bare ``--move`` (no path argument).
_BARE_MOVE = _Sentinel("BARE_MOVE")
