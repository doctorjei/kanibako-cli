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

from kanibako.box_identity import validate_box_name
from kanibako.config import (
    BOX_META_FILE,
    KanibakoConfig,
    read_box_enable_vault,
    write_box_enable_vault,
)
from kanibako.errors import ProjectError, WorksetError
from kanibako.paths import (
    _STANDALONE_META_DIR,
    BoxMode,
    ProjectPaths,
    StandardPaths,
    WorksetSpec,
    _find_workset_for_path,
    assign_primary_box_name,
    detect_project_mode,
    primary_box_name_for_workspace,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
    unregister_primary_box_name,
)
from kanibako.utils import write_project_gitignore
from kanibako.workset import (
    Workset,
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
    enable_vault: bool = True

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
            resolved, kind = resolve_name(
                std.registry, raw, cwd=Path.cwd(),
                primary_workset=std.primary_workset,
            )
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
                std.registry, raw,
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
    disk: the box is still registered in the PRIMARY ``boxes:`` membership (path
    -> name) and its metadata lives in ``boxes/<name>``.  Returns ``None`` when no
    such registration is found, so the caller can raise the normal error.
    """
    name = primary_box_name_for_workspace(std.primary_workset, str(workspace))
    if name is None:
        return None
    # P8b: PRIMARY membership (the reverse-lookup hit above) IS the existence
    # signal — identity no longer self-describes on disk, so there is no
    # ``project.mode`` presence gate.  ``enable_vault`` is the plain box-scope
    # ``box.enable_vault`` read (decoupled from identity).
    metadata_path = std.boxes / name
    # B2b (Option A, Jei-ruled): the per-box meta["shell"]/["vault_*"] custom-path
    # OVERRIDE is DROPPED here too — to stay CONSISTENT with the launch path
    # (resolve_project), which now derives home/vault SOLELY from the default
    # location. Reading the stored override here would make stop/cleanup/move target
    # a DIFFERENT home than the launch binds (JC-B2b-4). home/vault are the default
    # PRIMARY-box location (boxes/<name>/home + primary vault/{ro,rw}/<name>).
    shell_path = metadata_path / "home"
    vault_ro = std.primary_vault_ro / name
    vault_rw = std.primary_vault_rw / name
    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=name,
        workspace_path=workspace.resolve(), metadata_path=metadata_path,
        shell_path=shell_path, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=read_box_enable_vault(metadata_path / BOX_META_FILE),
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
        from kanibako import box_resolve
        owned = box_resolve.find_connected_external_box(raw_path, std)
        if owned is not None:
            ws, proj_name = load_workset(owned.workset_root), owned.box_name
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
        enable_vault=proj.enable_vault,
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
    connection (1:1 in the per-workset registry) is what ``connect`` is for, and a bare
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
    # in the workset.meta identity + creates per-project dirs) but DURING the copies below
    # would otherwise strand a registered-but-incomplete project.  Roll the
    # registration + partial dirs back on any failure, then re-raise.
    # remove_project(remove_files=True, std=...) is idempotent and removes only
    # workset-side dirs (never the user's external source).
    try:
        dst_project = ws.projects_dir / proj_name
        shutil.copytree(
            metadata_path, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
            dirs_exist_ok=True,
        )

        if shell_path.is_dir():
            dst_shell = dst_project / "home"
            shutil.copytree(shell_path, dst_shell, dirs_exist_ok=True)

        if copy_workspace:
            dst_workspace = ws.workspaces_dir / proj_name
            ignore = None
            if source_mode == BoxMode.standalone:
                ignore = shutil.ignore_patterns(_STANDALONE_META_DIR, ".kanibako", "kanibako")
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
        # The user's EXPLICIT --name (empty when not given).  Unlike ``new_name``
        # (which defaults to the source name), this distinguishes "no --name"
        # from "--name <source-name>" so a standalone target only treats a real
        # --name as a user assertion (R1/R3).
        "requested_name": (spec.name or "").lower(),
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
         names; rewrite ``settings.yaml`` mode + paths).
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
    requested_name: str = plan["requested_name"]

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
    elif (
        not records_only
        and not relocating
        and state.mode is BoxMode.standalone
        and target_mode is not BoxMode.standalone
    ):
        # Reverse of drift H: a standalone source roots its live workspace at the
        # ``<root>/workspace`` SUBDIR, but a non-standalone box roots at the
        # project dir itself.  On an in-place convert OUT of standalone, lift the
        # workspace files back up to the root and root the converted box there
        # (so the project stays at the dir the user is in, not a subdir).
        root = state.workspace_path.parent
        _unconsolidate_workspace_subdir(state.workspace_path, root, unwind)
        new_workspace = root

    # ------------------------------------------------------------------
    # STEP 4 (ownership) is interleaved with STEP 3 (markers) because the
    # destination metadata roots depend on the target owner.  We compute the
    # new metadata/shell/vault dirs for the target owner, copy them, write the
    # rewritten settings.yaml (with correct mode + workspace override + hash +
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
        requested_name=requested_name,
    )

    # ------------------------------------------------------------------
    # STEP 4b — Relocate THIS box's OWN channel partition (best-effort, D-M10).
    # Runs AFTER ownership/identity is finalized (A9): standalone convert
    # regenerates the box name, so we must read the FINAL post-convert name + ws
    # token off ``new_state``.  Best-effort + idempotent — failures warn and the
    # lifecycle continues; not registered on the unwind stack (a partial move is
    # not catastrophic and re-running reconciles).
    # ------------------------------------------------------------------
    _relocate_channel_partition(state, new_state, std)

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
    requested_name: str = "",
) -> ProjectState:
    """Re-root metadata/shell/vault into the target owner + rewrite markers.

    Handles every transition by copying the source metadata into the target
    owner's metadata root, writing a fresh ``settings.yaml`` (mode + paths +
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
            requested_name=requested_name,
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
    home_leaf: str = "home",
    unwind: _Unwind,
) -> Path:
    """Copy metadata (minus lock+home) and shell into *dst_metadata*.

    Returns the destination shell path (``dst_metadata / home_leaf``).  Pushes
    an rmtree of *dst_metadata* onto *unwind*.
    """
    shutil.copytree(
        src_metadata, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
        dirs_exist_ok=True,
    )
    unwind.push(lambda: shutil.rmtree(dst_metadata, ignore_errors=True))

    dst_shell = dst_metadata / home_leaf
    if src_shell.is_dir():
        shutil.copytree(src_shell, dst_shell, dirs_exist_ok=True)
    return dst_shell


def _remove_old_metadata(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    preserve_name: str | None = None,
) -> None:
    """Remove the source project's metadata/shell (+ PRIMARY vault).

    Standalone source: removes the in-tree kanibako artifacts (the ``box_data/``
    marker dir + the root ``settings.yaml`` + ``vault/``) — NOT the project root
    itself (which for standalone IS ``metadata_path``: deleting it would wipe the
    user's whole project dir + the already-converted destination).
    Primary source: unregisters the name, removes the boxes metadata dir and
    the PRIMARY-workset vault dir (which Phase 5 moved out of the workspace).
    Workset source: removes the workset registration (std-aware) so external
    markers / the per-workset connection record are cleaned; the external source
    dir is never deleted.

    (Phase 5 / A7: layouts are gone and the vault is never "hidden" inside the
    workspace, so the human-vault / project-vault discovery symlinks were
    deleted — nothing to clean up here.)
    """
    if state.mode == BoxMode.standalone:
        # Standalone boxes are registered in registry.standalone (Phase 5d), so
        # a standalone source's entry must be dropped too — otherwise a
        # standalone→standalone move strands the old name → root mapping.
        from kanibako import registry_store
        if state.name:
            try:
                registry_store.unregister_standalone(std.registry, state.name)
            except Exception:  # noqa: BLE001
                pass
        # metadata_path is the standalone ROOT (drift I); remove only the
        # kanibako artifacts living in it, never the root itself.
        root = state.metadata_path
        box_data = root / _STANDALONE_META_DIR
        if box_data.is_dir():
            shutil.rmtree(box_data, ignore_errors=True)
        settings = root / BOX_META_FILE
        if settings.is_file():
            settings.unlink()
        vault = root / "vault"
        if vault.is_dir():
            shutil.rmtree(vault, ignore_errors=True)
        return

    if state.mode == BoxMode.primary:
        # L2: when the converted box reuses its own name (same-name in-place
        # convert), the destination metadata/vault IS the source — keep the
        # reused name + its metadata/vault rather than dropping them.
        reused_in_place = preserve_name is not None and state.name == preserve_name
        if state.name and not reused_in_place:
            try:
                unregister_primary_box_name(std.primary_workset, state.name)
            except Exception:  # noqa: BLE001
                pass
        if reused_in_place:
            return
        if state.metadata_path.is_dir():
            shutil.rmtree(state.metadata_path, ignore_errors=True)
        # PRIMARY vault lives under @config.primary_workset/vault/{ro,rw}/<name>
        # (Phase 5), so it is NOT under metadata_path — remove the per-box ro/rw
        # dirs explicitly (NOT their shared parent, which holds every box's
        # vault).
        for vault_dir in (state.vault_ro, state.vault_rw):
            if vault_dir.is_dir():
                try:
                    vault_dir.relative_to(std.primary_workset)
                except ValueError:
                    continue
                shutil.rmtree(vault_dir, ignore_errors=True)
        if state.shell_path.is_dir() and state.shell_path != state.metadata_path / "home":
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
    # L2 (name reuse on same-name convert): a PRIMARY source's own name is still
    # registered at this point, so assign_primary_box_name would see the collision
    # and auto-suffix (foo→foo2), stranding the original entry once _remove_old_
    # metadata later unregisters it. Unregister the source's name FIRST (only for
    # a primary source — other modes aren't in the PRIMARY membership) so the same
    # name is reused; the later _remove_old_metadata unregister then no-ops.
    preserved_name: str | None = None
    if state.mode == BoxMode.primary and state.name:
        existing = primary_box_name_for_workspace(
            std.primary_workset, str(new_workspace),
        )
        if existing is not None:
            # Path unchanged + still registered: free the name so it is reused
            # verbatim rather than suffixed, and mark it preserved so
            # _remove_old_metadata neither re-unregisters it nor deletes the
            # reused metadata.
            preserved_name = existing
            _safe_unregister(std, existing)
    project_name = assign_primary_box_name(
        std.primary_workset, std.registry, str(new_workspace),
    )
    unwind.push(lambda: _safe_unregister(std, project_name))
    dst_metadata = std.boxes / project_name

    # When the name is reused in place, the metadata already lives at the
    # destination — copying would be a (failing) copy-onto-self, so reuse it.
    if dst_metadata.resolve() == state.metadata_path.resolve():
        dst_shell = state.shell_path
    else:
        dst_shell = _copy_metadata(
            state.metadata_path, state.shell_path, dst_metadata,
            shell_into_metadata=True, unwind=unwind,
        )

    # Phase 5 fixed PRIMARY table: vault under @config.primary_workset.
    vault_ro = std.primary_vault_ro / project_name
    vault_rw = std.primary_vault_rw / project_name

    # Sparse move (P8b/Option A): NO ``project:``/``resolved:`` identity is
    # written — the moved box's identity/workspace live in the PRIMARY ``boxes:``
    # membership (re-registered above via assign_primary_box_name).  Persist
    # only the NON-default ``box.enable_vault``, sparsely (carried from the source
    # box's ``state.enable_vault`` so a disabled-vault box stays disabled).
    write_box_enable_vault(dst_metadata / BOX_META_FILE, state.enable_vault)

    if state.enable_vault:
        vault_ro.mkdir(parents=True, exist_ok=True)
        vault_rw.mkdir(parents=True, exist_ok=True)
        unwind.push(lambda: shutil.rmtree(vault_ro, ignore_errors=True))
        unwind.push(lambda: shutil.rmtree(vault_rw, ignore_errors=True))

    _remove_old_metadata(state, std, config, preserve_name=preserved_name)

    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=project_name,
        workspace_path=new_workspace, metadata_path=dst_metadata,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=state.enable_vault,
    )


#: Top-level entries that are kanibako artifacts (NOT workspace content) and so
#: must stay at the standalone root rather than move into the ``workspace/``
#: subdir during a convert (drift H consolidation).
_STANDALONE_ROOT_ARTIFACTS = frozenset({
    _STANDALONE_META_DIR,   # box_data/
    "workspace",            # the subdir we are populating
    "vault",                # vault/{ro,rw}
    "settings.yaml",        # the box meta (drift I — at the root)
    ".kanibako",            # legacy marker dir
    "kanibako",             # legacy marker dir
    ".kanibako.lock",       # lock file
})


def _consolidate_workspace_subdir(
    root: Path,
    workspace_subdir: Path,
    unwind: _Unwind,
) -> None:
    """Move the project's top-level files into the ``workspace/`` subdir.

    Drift H: a standalone box's live workspace is the ``<root>/workspace/``
    SUBDIR, not the root itself.  On an in-place convert the user's project
    files currently sit AT the root; relocate everything that is not a kanibako
    artifact (see :data:`_STANDALONE_ROOT_ARTIFACTS`) into the subdir so the
    bind source matches the resolved layout.  A no-op when there is nothing to
    move (e.g. an empty root or files already consolidated).  Each move is
    pushed onto *unwind* so a later failure restores the original placement.

    *root* always holds the project's current files at this point in the convert
    (in-place: the source dir; relocating: the copy STEP 2 made at *dest*;
    external-in-place: the external dir that is BECOMING the standalone root), so
    consolidation applies uniformly — the result roots the live workspace at the
    ``workspace/`` subdir for every transition.
    """
    if not root.is_dir():
        return

    movable = [
        child for child in root.iterdir()
        if child.name not in _STANDALONE_ROOT_ARTIFACTS
    ]
    if not movable:
        return

    workspace_subdir.mkdir(parents=True, exist_ok=True)
    unwind.push(lambda: _undo_consolidate(workspace_subdir, root, movable))
    for child in movable:
        shutil.move(str(child), str(workspace_subdir / child.name))


def _undo_consolidate(
    workspace_subdir: Path, root: Path, moved: list[Path],
) -> None:
    """Best-effort reversal of :func:`_consolidate_workspace_subdir`."""
    for child in moved:
        src = workspace_subdir / child.name
        if src.exists():
            try:
                shutil.move(str(src), str(root / child.name))
            except OSError:
                pass


def _unconsolidate_workspace_subdir(
    workspace_subdir: Path,
    root: Path,
    unwind: _Unwind,
) -> None:
    """Lift the ``workspace/`` subdir's contents back up to *root*.

    The inverse of :func:`_consolidate_workspace_subdir`: used when converting
    OUT of standalone in place, so the workspace files return to the project
    root (where a non-standalone box roots them) and the now-empty subdir is
    removed.  A no-op when the subdir is absent or empty.  Each move is pushed
    onto *unwind* for rollback on a later failure.
    """
    if not workspace_subdir.is_dir():
        return
    movable = list(workspace_subdir.iterdir())
    moved: list[Path] = []
    unwind.push(lambda: _undo_consolidate(root, workspace_subdir, moved))
    for child in movable:
        shutil.move(str(child), str(root / child.name))
        moved.append(child)
    # Drop the emptied subdir so the converted project does not keep a stray
    # ``workspace/`` dir at its root.
    try:
        workspace_subdir.rmdir()
    except OSError:
        pass


def _to_standalone(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    unwind: _Unwind,
    *,
    new_name: str,
    new_workspace: Path,
    requested_name: str = "",
) -> ProjectState:
    """Convert/relocate the project so it becomes standalone (in-tree metadata).

    A standalone box's identity is the canonical opaque ``<kuid>_<leaf>``
    (matching ``create --standalone`` / ``duplicate --standalone``); standalone
    boxes are NOT named via ``names.yaml`` but registered in
    ``registry.standalone``.  Convert ESTABLISHES the box uniformly via
    :func:`establish_standalone`, which now HONORS an explicit ``--name``
    (``new_name``) through :func:`box_identity.resolve_standalone_name`: a
    verbatim canonical id is used if free (else refused), a non-canonical
    ``--name`` becomes a fresh ``<kuid>_<sanitized name>``, and NO ``--name``
    generates a fresh canonical id from the root basename.  It writes
    ``mode=standalone`` with that identity and registers it in
    ``registry.standalone`` (with an unwind to drop the registration on
    failure).  ``_remove_old_metadata`` purges the source's prior registry entry
    (``names.yaml`` for primary/named) so it does not dangle.

    ``new_name`` is only a *requested* name; the source's name is passed as the
    default when the caller did not pass ``--name`` (i.e. ``new_name ==
    state.name``), in which case it is NOT treated as a user assertion — a fresh
    canonical id is generated instead.
    """
    from kanibako import registry_store
    from kanibako.config import BOX_META_FILE
    from kanibako.paths import establish_standalone

    # *new_workspace* is the standalone ROOT (the project dir).  Drift H+I: the
    # standalone tree roots here with ``settings.yaml`` AT THE ROOT, a
    # ``box_data/`` marker dir (home + helper log), ``vault/{ro,rw}/``, and the
    # live workspace as a ``workspace/`` SUBDIR.  Consolidate the source's
    # current top-level project files into that subdir first (in-place convert
    # leaves them at the root otherwise), THEN lay down the kanibako artifacts.
    root = new_workspace
    root.mkdir(parents=True, exist_ok=True)
    dst_metadata = root / _STANDALONE_META_DIR
    workspace_subdir = root / "workspace"
    _consolidate_workspace_subdir(root, workspace_subdir, unwind)

    dst_shell = _copy_metadata(
        state.metadata_path, state.shell_path, dst_metadata,
        shell_into_metadata=True, home_leaf="home", unwind=unwind,
    )
    # The source settings.yaml copied into box_data/ (when the source's metadata
    # dir held one) is an orphan now that the box meta lives at the ROOT; drop it
    # so detection only ever sees the canonical <root>/settings.yaml (drift I).
    stale = dst_metadata / BOX_META_FILE
    if stale.exists():
        stale.unlink()

    # Establish the standalone identity + meta + registration via the shared
    # core.  ``requested_name`` is the user's explicit ``--name`` (empty when
    # not given); establish_standalone routes it through resolve_standalone_name
    # (honor a free canonical id verbatim, refuse a taken one, else fresh prefix
    # over the supplied string; empty → fresh canonical from the root basename).
    # It writes <root>/settings.yaml (mode=standalone) + registers the box.
    box_name, dst_shell, vault_ro, vault_rw = establish_standalone(
        std, root,
        enable_vault=state.enable_vault,
        name=requested_name,
    )
    unwind.push(
        lambda: registry_store.unregister_standalone(std.registry, box_name)
    )

    workspace_subdir.mkdir(parents=True, exist_ok=True)
    write_project_gitignore(root)
    vault_dir = root / "vault"
    if vault_dir.is_dir():
        gi = vault_dir / ".gitignore"
        if not gi.exists():
            gi.write_text("rw/\n")

    _remove_old_metadata(state, std, config)

    return ProjectState(
        owner="standalone", mode=BoxMode.standalone, name=box_name,
        workspace_path=workspace_subdir, metadata_path=root,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=state.enable_vault,
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
    # BEFORE the target registers it.  The connection record is 1:1, so an
    # external source still mapped to the OLD workset would collide with
    # add_project's "already connected" guard.  We therefore drop the source
    # registration first (clears the per-workset connection record + the
    # discoverability symlink; NEVER touches the user's external dir), capture a
    # snapshot of the source
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
        shell_source = stash_boxes / "home"
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
    # ``force=True``: a convert/move/duplicate INTO a workset is a DELIBERATE
    # absorb, so it must override the standalone-marker connect guard (B2a) — the
    # source of a standalone->workset convert still carries its in-place
    # ``box_data/`` marker at this point (the marker is removed later in the
    # convert).  ``force`` only affects a standalone-marked source (a no-op
    # otherwise), so it is safe for the non-standalone move/duplicate cases.
    add_project(target_ws, new_name, source_for_add, std, force=True)
    unwind.push(
        lambda: _safe_remove_project(target_ws, new_name, std)
    )

    dst_project = target_ws.projects_dir / new_name
    # Copy metadata (minus lock+home) into the workset boxes dir.
    shutil.copytree(
        metadata_source, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
        dirs_exist_ok=True,
    )
    dst_shell = dst_project / "home"
    if shell_source.is_dir():
        shutil.copytree(shell_source, dst_shell, dirs_exist_ok=True)

    if copy_workspace:
        dst_workspace = target_ws.workspaces_dir / new_name
        ignore = None
        if state.mode == BoxMode.standalone:
            ignore = shutil.ignore_patterns(_STANDALONE_META_DIR, ".kanibako", "kanibako")
        shutil.copytree(state.workspace_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)

    # Determine the recorded workspace + hash.
    if internal:
        recorded_workspace = (target_ws.workspaces_dir / new_name)
    else:
        # External: source the workspace from the per-workset ``boxes:`` registry
        # that ``add_project`` just wrote (P8b — the D10 connection record is the
        # authoritative external-workspace source, not the box's settings.yaml
        # identity, which stops self-describing under sparse create).
        from kanibako import workset_registry
        from kanibako.config_io import load_doc

        registry_path = workset_registry.resolve_workset_registry_path(
            target_ws.root, load_doc(target_ws.root / "settings.yaml"),
        )
        recorded_str = workset_registry.load_workset_boxes(registry_path).get(
            new_name
        )
        recorded_workspace = (
            Path(recorded_str) if recorded_str else new_workspace
        )
    vault_ro = target_ws.vault_dir / "ro" / new_name
    vault_rw = target_ws.vault_dir / "rw" / new_name

    # Sparse move (P8b/Option A): NO ``project:``/``resolved:`` identity is
    # written — the moved box's identity lives in the global name index and its
    # workspace in the target workset's ``boxes:`` registry (add_project wrote the
    # external entry; sourced above as ``recorded_workspace``).  Persist only the
    # NON-default ``box.enable_vault``, sparsely (carried from ``state`` so a
    # disabled-vault box stays disabled across the move).
    write_box_enable_vault(dst_project / BOX_META_FILE, state.enable_vault)

    # For a workset source the registration was already released above;
    # otherwise clean up the source owner's metadata/markers now.
    if not source_is_workset:
        _remove_old_metadata(state, std, config)

    return ProjectState(
        owner=owner_token(BoxMode.named, target_ws.name),
        mode=BoxMode.named, name=new_name,
        workspace_path=recorded_workspace, metadata_path=dst_project,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=not internal, ws=target_ws,
        enable_vault=state.enable_vault,
    )


# -- small helpers ----------------------------------------------------------

def _state_ws_token(state: ProjectState) -> str:
    """Return the channel-partition workset-name token for *state*.

    ``__PRIMARY__`` for primary mode, the named workset's name for named mode,
    ``__STANDALONE__`` for standalone.  Mirrors
    :func:`kanibako.channels.workset_name_token` but reads off the lifecycle
    :class:`ProjectState` (mode + loaded ``ws``) rather than a ``ProjectPaths``.
    """
    from kanibako.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE

    if state.mode == BoxMode.standalone:
        return WS_TOKEN_STANDALONE
    if state.mode == BoxMode.named:
        if state.ws is None or not state.ws.name:
            raise ValueError(
                "named box is missing its workset; cannot derive the channel "
                "partition token."
            )
        return state.ws.name
    return WS_TOKEN_PRIMARY


def _relocate_channel_partition(
    old: ProjectState, new: ProjectState, std: StandardPaths,
) -> None:
    """Best-effort relocate THIS box's OWN channel partition (D-M10, §6).

    A box's mailbox + system-scope share are partitioned by
    ``@meta.workset.name`` (and keyed by box name), so a move/convert that
    changes the workset and/or the box name changes its channel address.  This
    moves the OWN ``mailboxes/<ws>/<box>`` and ``share/<ws>/<box>`` dirs from the
    OLD partition (*old* — pre-convert identity) to the NEW one (*new* — the
    FINAL post-convert identity; standalone convert regenerates the box name, so
    this MUST run after identity is finalized, A9/IN-8).

    BEST-EFFORT (D-M10): any failure — missing source, destination already
    present, permission — is WARNED and swallowed; the lifecycle continues.  No
    forwarding marker is left for stale cross-box references to the old address.
    Workset-LOCAL channels (commons/chat) are scope-owned, not box-owned, so they
    are NOT relocated (the box simply stops mounting the old workset's local
    channels and starts mounting the new one's).
    """
    import sys

    from kanibako.channels import own_partition_dirs

    try:
        old_token = _state_ws_token(old)
        new_token = _state_ws_token(new)
    except ValueError as e:  # cannot derive a token → nothing to relocate
        print(f"Warning: skipping channel relocation: {e}", file=sys.stderr)
        return

    # No address change → nothing to move (idempotent no-op).
    if old_token == new_token and old.name == new.name:
        return

    src = own_partition_dirs(std, old_token, old.name)
    dst = own_partition_dirs(std, new_token, new.name)

    for src_dir, dst_dir, label in (
        (src.mailbox, dst.mailbox, "mailbox"),
        (src.share_global, dst.share_global, "share"),
    ):
        try:
            if not src_dir.is_dir():
                continue  # nothing published yet under the old address
            if dst_dir.exists():
                # Idempotent / best-effort: never clobber an existing dest.
                print(
                    f"Warning: channel {label} destination already exists, "
                    f"leaving it in place: {dst_dir}",
                    file=sys.stderr,
                )
                continue
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_dir), str(dst_dir))
        except Exception as e:  # noqa: BLE001 - best-effort (D-M10)
            print(
                f"Warning: could not relocate channel {label} "
                f"({src_dir} -> {dst_dir}): {e}",
                file=sys.stderr,
            )


def _safe_unregister(std: StandardPaths, name: str) -> None:
    try:
        unregister_primary_box_name(std.primary_workset, name)
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


def _lower_name(args) -> str | None:
    """Return the user's ``--name`` folded to lowercase (R2), or ``None``.

    Every box name is lowercase, so a user-supplied ``--name`` is silently
    lowercased on acceptance (no rejection of mixed-case input).  After folding,
    the name is validated against the §Design 8 blocklist (NEW/renamed boxes are
    held to the constraint); an invalid name raises ``ProjectError``.
    """
    name = getattr(args, "name", None)
    if not name:
        return name
    folded = name.lower()
    validate_box_name(folded)
    return folded


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

    from kanibako.commands.flags import resolve_subject_value
    old = resolve_subject_value(getattr(args, "old", None), getattr(args, "box", None))
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

    from kanibako.commands.flags import resolve_subject_value
    old = resolve_subject_value(getattr(args, "old", None), getattr(args, "box", None))
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
        location=new_path, ownership=ownership, name=_lower_name(args),
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

    from kanibako.commands.flags import resolve_subject_value
    subject = resolve_subject_value(
        getattr(args, "old", None), getattr(args, "box", None),
    )
    try:
        state = resolve_lifecycle_target(subject, std, config)
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Lock pre-flight: convert re-roots by copy then rmtree of the source
    # workspace/metadata, so abort while a box may be running unless --force
    # (mirrors move / duplicate).
    if _abort_if_locked(state, getattr(args, "force", False)):
        return 2

    spec = TargetSpec(
        location=location, ownership=ownership, name=_lower_name(args),
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
