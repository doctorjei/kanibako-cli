"""Shared transactional engine behind ``box remap`` / ``move`` / ``convert``.

**_Terminology_**
- _location_: where the workspace files physically live (:class:`TargetSpec`'s ``location``)
- _ownership_: which mode/workset owns the project (``TargetSpec.ownership``)
- _records-only_: ``remap`` — the files have ALREADY moved; record the new location, copy nothing
- _unwind_: the LIFO stack of compensating actions run in reverse on ANY failure

Public surface: :class:`ProjectState` · :func:`resolve_lifecycle_target` · :class:`TargetSpec` ·
:func:`execute_lifecycle` · the ``run_remap``/``run_move``/``run_convert`` CLI entry points ·
:func:`copy_into_workset` (the std-aware copy path for ``box duplicate``).

⚑ Destructive: steps 2 and 5 copy then ``rmtree``. The step ORDER and the unwind pushes are
load-bearing; see ``llm-docs/kanibako/commands/box/_lifecycle.py.md``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kanibako.launch.box_identity import validate_box_name
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.core_defaults import materialize_canon_skeleton
from kanibako.settings.config import (
    BOX_META_FILE,
    WORKSET_META_FILE,
    KanibakoConfig,
    read_box_enable_vault,
    write_box_enable_vault,
)
from kanibako.errors import ProjectError, WorksetError
from kanibako.settings.paths import (
    STANDALONE_META_DIR,
    BoxMode,
    ProjectPaths,
    StandardPaths,
    WorksetSpec,
    _find_workset_for_path,
    _register_workset_box_membership,
    _box_settings_files,
    _default_project_group,
    assign_primary_box_name,
    box_metadata_dir,
    box_workset_settings_paths,
    check_primary_box_name_free,
    detect_project_mode,
    primary_box_name_for_workspace,
    register_primary_box_name,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
    unregister_primary_box_name,
)
from kanibako.utils import write_project_gitignore
from kanibako.project.workset import (
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
#: ``location`` sentinel — move into ``{ws}/workspaces/<name>``; workset targets only.
BARE_INTO_WS = _Sentinel("BARE_INTO_WS")
#: ``ownership`` sentinel — keep the current owner/mode.
UNCHANGED = _Sentinel("UNCHANGED")


# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------

@dataclass
class ProjectState:
    """Uniform descriptor of an existing, resolved project."""

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
    #: The RESOLVED ``box.enable_vault`` — box tier over the containing workset's default.
    enable_vault: bool = True
    #: ⚑ What the BOX ITSELF authored, ignoring the workset tier — the ONLY value a
    #: lifecycle op may persist at the destination's box tier.  A ``box.*`` key at the
    #: source's workset tier is that workset's OVERRIDABLE DEFAULT
    #: (:func:`kanibako.settings.config.carried_box_settings`), so writing the RESOLVED
    #: value would pin an inherited default as a box-scope override the destination
    #: workset can no longer reach.  Mirrors ``box_authored_vault`` in the resolvers.
    box_authored_vault: bool = True


@dataclass
class TargetSpec:
    """What a lifecycle operation should change (location axis + ownership axis)."""

    location: Path | _Sentinel = INPLACE
    ownership: str | _Sentinel = UNCHANGED
    name: str | None = None
    #: ⚑ ``remap`` semantics — record the new location, copy/delete NOTHING.
    records_only: bool = False


def owner_token(mode: BoxMode, ws_name: str | None = None) -> str:
    """Build a canonical owner token from a mode (+ workset name)."""
    if mode == BoxMode.named:
        if not ws_name:
            raise ValueError("workset owner requires a workset name")
        return f"workset:{ws_name}"
    return mode.value


def _default_rename_name(
    state: ProjectState,
    std: StandardPaths,
    landing_ws: Path,
    requested_name: str,
) -> str | None:
    """The explicit primary-box name a DEFAULT-mode edge would MINT, or ``None``."""
    if not requested_name:
        return None
    if state.mode == BoxMode.primary and state.name:
        existing = primary_box_name_for_workspace(
            std.primary_workset, str(landing_ws),
        )
        if existing is not None:
            # ⚑ Landing path already registered ⇒ SAME-PATH edge, no NEW registration minted.
            if requested_name != existing:
                raise ProjectError(
                    f"In-place rename of a primary (default-mode) box is not "
                    f"supported: '{existing}' -> '{requested_name}'. Move the box "
                    f"to rename it (e.g. `box move {existing} <new-path> --name "
                    f"{requested_name}`), or drop --name to keep the current name."
                )
            # --name equals the current name: a moot reuse, not a rename edge.
            return None
    return requested_name


def _primary_source_own_name(
    state: ProjectState, std: StandardPaths,
) -> str | None:
    """The name the SOURCE primary box is CURRENTLY registered under, else ``None``."""
    if state.mode != BoxMode.primary or not state.name:
        return None
    return primary_box_name_for_workspace(
        std.primary_workset, str(state.workspace_path),
    )


def _ownership_to_mode(ownership: str) -> tuple[BoxMode, str | None]:
    """Map a TargetSpec ownership value to ``(mode, workset_name | None)``."""
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
    """Resolve an existing project (by path or name) to a :class:`ProjectState`."""
    import os

    if config is None:
        from kanibako.settings.config import config_file_path, load_config
        from kanibako.settings.paths import xdg
        config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))

    raw = old or os.getcwd()
    # ⚑ Bare-token front door (mirrors resolve_any_project): ``remap``/``convert`` need it —
    # the folder has already moved, so the path is stale but the NAME still resolves.
    raw_name = raw
    named_workset = False
    if raw and "/" not in raw and not Path(raw).exists():
        from kanibako.settings.paths import resolve_name
        try:
            resolved, kind = resolve_name(
                std.registry, raw, cwd=Path.cwd(),
                primary_workset=std.primary_workset,
            )
            if kind in ("project", "workset"):
                # ⚑ BOTH kinds update `raw`: detect_project_mode must see the workset ROOT.
                raw = resolved
                named_workset = kind == "workset"
        except ProjectError:
            pass
    if named_workset:
        # Lifecycle ops act on a single project box; a workset is not one.
        raise WorksetError(
            f"'{raw_name}' is a workset, not a single project box. "
            f"Name a project inside it (e.g. '{raw_name}/<project>') or run the "
            f"command from a project workspace under that workset."
        )
    # Qualified ``workset/project`` addressing — the form the rejection above suggests.
    if raw and "/" in raw and not Path(raw).exists():
        from kanibako.project.names import resolve_qualified_name
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
        # ⚑ Workspace dir gone (moved before `remap`): resolve_project requires it, so
        # fall back to the registered metadata alone.
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
    """Build a default-mode :class:`ProjectState` from registered metadata (``remap``)."""
    name = primary_box_name_for_workspace(std.primary_workset, str(workspace))
    if name is None:
        return None
    # ⚑ P8b: the PRIMARY-membership hit above IS the existence signal — identity no
    # longer self-describes on disk, so there is NO ``project.mode`` presence gate.
    metadata_path = std.boxes / name
    # ⚑ B2b: home/vault are the DEFAULT location only — never a stored per-box override,
    # which would target a different home than the launch binds do (JC-B2b-4).
    shell_path = metadata_path / "home"
    vault_ro = std.primary_vault_ro / name
    vault_rw = std.primary_vault_rw / name
    # ⚑ The GROUP is the PRIMARY workset — the same one ``resolve_project`` derives — so
    # this fallback resolves ``box.enable_vault`` through the SAME R2 downward default the
    # launch path uses.  Passing ``None`` here made a ``remap`` answer differently
    # depending only on whether the workspace dir was still on disk.
    box_tier, workset_tier = _box_settings_files(
        BoxMode.primary, metadata_path, _default_project_group(std),
    )
    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=name,
        workspace_path=workspace.resolve(), metadata_path=metadata_path,
        shell_path=shell_path, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=read_box_enable_vault(box_tier, default_from=workset_tier),
        box_authored_vault=read_box_enable_vault(box_tier),
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
        from kanibako.launch import box_resolve
        owned = box_resolve.find_connected_external_box(raw_path, std)
        if owned is not None:
            ws, proj_name = (load_workset(owned.workset_root, owned.workset_name),
                             owned.box_name)
    if ws is None or proj_name is None:
        raise WorksetError(f"No workset project found for path: {raw_path}")

    proj = resolve_workset_project(
        WorksetSpec.from_workset(ws), proj_name, std, config, initialize=False,
    )
    # EXTERNAL == the live workspace lies outside the workset root.
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
    # ⚑ ``proj.enable_vault`` is the RESOLVED value; re-read the BOX TIER alone for what
    # the box authored, so a lifecycle op never persists the workset's default as a
    # box-scope override (see ``ProjectState.box_authored_vault``).
    box_tier, _ = box_workset_settings_paths(proj)
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
        box_authored_vault=read_box_enable_vault(box_tier),
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
    """Re-root a project into *ws* — the std-aware copy path for ``duplicate``."""
    # ⚑ Register the IN-TREE workspace dir: a duplicate is always INTERNAL, so add_project
    # makes a real directory instead of symlinking back at the source.
    add_project(ws, proj_name, ws.workspaces_dir / proj_name, std)

    # ⚑ Failure-consistency: a crash after add_project but during the copies would strand a
    # registered-but-incomplete project. Roll registration + partial dirs back, then re-raise.
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
            # ⚑ copytree carries the canon skeleton's MODES but not its OWNERSHIP — re-assert.
            materialize_canon_skeleton(dst_shell)

        if copy_workspace:
            dst_workspace = ws.workspaces_dir / proj_name
            ignore = None
            if source_mode == BoxMode.standalone:
                ignore = shutil.ignore_patterns(STANDALONE_META_DIR)
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
    """A LIFO stack of compensating actions for failure-consistency."""

    actions: list[Callable[[], None]] = field(default_factory=list)
    cleanups: list[Callable[[], None]] = field(default_factory=list)

    def push(self, action: Callable[[], None]) -> None:
        self.actions.append(action)

    def on_success(self, action: Callable[[], None]) -> None:
        """Register an action to run only when the whole op succeeds (scratch disposal)."""
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
    return load_workset(registry[name], name)


def _validate(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    force: bool,
    cwd: Path,
) -> dict:
    """Validate up front; return plan facts. ⚑ EVERY refusal belongs HERE, never in a step."""
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
    # ⚑ ``records_only`` exempt: ``remap``'s files are ALREADY at *dest*, so it must not
    # be required empty (and a no-op same-path remap is fine).
    if relocating and dest is not None and not spec.records_only:
        if dest.resolve() == state.workspace_path.resolve():
            raise ProjectError(
                f"Destination is the project's current location: {dest}"
            )
        if dest.exists():
            raise ProjectError(f"Destination already exists: {dest}")

    # --- membership guard: refuse landing inside a workset the project is
    #     not (becoming) a member of ---
    # ``relocating`` is exactly ``dest is not None``; test dest directly so mypy narrows.
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
    # ⚑ ``records_only`` exempt: ``remap`` removes nothing, so it cannot strand the shell.
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

    # --- cross-kind name policy on a DEFAULT-mode --name rename edge (F-7) ---
    # ⚑ Checked UP FRONT so a name refusal costs no file copy.
    requested_name = (spec.name or "").lower()
    if target_mode == BoxMode.primary:
        landing_ws = dest if dest is not None else state.workspace_path
        mint = _default_rename_name(state, std, landing_ws, requested_name)
        # ⚑ FIX1: a same-name relocate reuses the SOURCE's OWN registration — self-reuse,
        # not a collision, so it is exempt from the same-kind guard.
        if mint is not None and mint != _primary_source_own_name(state, std):
            check_primary_box_name_free(
                std.primary_workset, std.registry, mint, str(landing_ws),
                force=force,
            )

    return {
        "target_mode": target_mode,
        "target_ws": target_ws,
        "target_ws_name": target_ws_name,
        "dest": dest,
        "relocating": relocating,
        "no_owner_change": no_owner_change,
        "new_name": new_name,
        "force": force,
        # ⚑ The EXPLICIT --name (empty when absent) — distinct from ``new_name``, which
        # defaults to the source name. Standalone needs the distinction (R1/R3).
        "requested_name": requested_name,
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
    """Apply *spec* to *state* transactionally, in the canonical 5-step order."""
    import os

    if config is None:
        from kanibako.settings.config import config_file_path, load_config
        from kanibako.settings.paths import xdg
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
    force: bool = plan["force"]

    # --- STEP 2 — Move files (only when relocating a real workspace tree) ---
    records_only: bool = spec.records_only
    new_workspace = state.workspace_path
    if records_only and dest is not None:
        # ``remap``: files presumed already at *dest*; copy and remove nothing.
        new_workspace = dest
    elif relocating and dest is not None and not state.is_external:
        src = state.workspace_path
        shutil.copytree(src, dest)
        unwind.push(lambda: shutil.rmtree(dest, ignore_errors=True))
        new_workspace = dest
    elif relocating and dest is not None and state.is_external:
        # ⚑ EXTERNAL source: the "workspace" is the USER'S OWN dir — never moved, only
        # re-recorded. *dest* is the recorded location of an internalizing move.
        new_workspace = dest
    elif (
        not records_only
        and not relocating
        and state.mode is BoxMode.standalone
        and target_mode is not BoxMode.standalone
    ):
        # ⚑ Reverse of drift H: standalone roots the live workspace at ``<root>/workspace``,
        # every other mode at the project dir — so an in-place convert OUT must lift.
        root = state.workspace_path.parent
        _unconsolidate_workspace_subdir(state.workspace_path, root, unwind)
        new_workspace = root

    # --- STEPS 3+4 — markers INTERLEAVED with ownership: the destination metadata
    #     roots depend on the target owner, so they cannot be separated. ---
    new_state = _apply_ownership_and_markers(
        state, std, config, unwind,
        target_mode=target_mode,
        target_ws=target_ws,
        new_name=new_name,
        new_workspace=new_workspace,
        relocating=relocating,
        dest=dest,
        requested_name=requested_name,
        force=force,
    )

    # --- STEP 4b — Relocate this box's OWN channel partition (best-effort, D-M10).
    # ⚑ MUST run AFTER identity is finalized (A9): a standalone convert REGENERATES the
    #   box name, so the new address is only readable off ``new_state``. ---
    _relocate_channel_partition(state, new_state, std)

    # --- STEP 5 — Clean up the old workspace (real, internal moves only).
    # ⚑ NEVER delete a user's EXTERNAL source directory, and never before step 2's copy. ---
    if not records_only and relocating and dest is not None and not state.is_external:
        old_ws = state.workspace_path
        if old_ws.resolve() != dest.resolve() and old_ws.is_dir():
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
    force: bool = False,
) -> ProjectState:
    """Re-root metadata/shell/vault into the target owner + rewrite markers."""
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
        requested_name=requested_name, force=force,
    )


# -- per-target-mode ownership steps ---------------------------------------

def _unwind_box_tree(path: Path) -> None:
    """Best-effort box-tree removal shaped for ``_Unwind.push`` (discards the bool)."""
    remove_box_tree(path)


def _copy_metadata(
    src_metadata: Path,
    src_shell: Path,
    dst_metadata: Path,
    *,
    shell_into_metadata: bool,
    home_leaf: str = "home",
    unwind: _Unwind,
) -> Path:
    """Copy metadata (minus lock+home) and shell into *dst_metadata*; return the dest shell."""
    shutil.copytree(
        src_metadata, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
        dirs_exist_ok=True,
    )
    # ⚑ ESCALATING removal, not a plain rmtree: this function lays a canon skeleton below,
    # so a plain rmtree would silently fail to clean up its own destination.
    unwind.push(lambda: _unwind_box_tree(dst_metadata))

    dst_shell = dst_metadata / home_leaf
    if src_shell.is_dir():
        shutil.copytree(src_shell, dst_shell, dirs_exist_ok=True)
        # ⚑ copytree carries the canon skeleton's 555 MODES but never its OWNERSHIP —
        # re-assert (idempotent; J-7).
        materialize_canon_skeleton(dst_shell)
    return dst_shell


def _deliver_carried_box_settings(state: ProjectState, dst_box_tier: Path) -> None:
    """Write the source box's carried box-scope settings to *dst_box_tier* (M-8)."""
    from kanibako.settings.config import carried_box_settings
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.paths import _box_settings_files

    # ⚑ ``group=None`` is harmless: only the WORKSET tier is derived from the group, and
    # the carry reads the BOX tier alone — ProjectState carries no ProjectGroup anyway.
    src_box, _ = _box_settings_files(state.mode, state.metadata_path, None)
    carried = carried_box_settings(src_box)
    if carried:
        dump_doc(dst_box_tier, carried)


def _remove_old_metadata(
    state: ProjectState,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    preserve_name: str | None = None,
) -> None:
    """Remove the source project's metadata/shell (+ PRIMARY vault), per source mode."""
    if state.mode == BoxMode.standalone:
        # ⚑ Standalone lives in registry.standalone, not names.yaml: drop that entry too,
        # or a standalone→standalone move strands the old name → root mapping.
        from kanibako.project import registry_store
        if state.name:
            try:
                registry_store.unregister_standalone(std.registry, state.name)
            except Exception:  # noqa: BLE001
                pass
        # ⚑⚑ ``metadata_path`` IS the standalone ROOT (drift I). Remove only the kanibako
        # artifacts inside it — deleting the root would wipe the user's whole project dir
        # AND the already-converted destination.
        root = state.metadata_path
        box_data = root / STANDALONE_META_DIR
        if box_data.is_dir():
            # ⚑ Escalating removal: the root-owned canon skeleton makes a bare rmtree
            # fail with EACCES and leave the old box behind (J-7).
            remove_box_tree(box_data)
        settings = root / WORKSET_META_FILE
        if settings.is_file():
            settings.unlink()
        vault = root / "vault"
        if vault.is_dir():
            shutil.rmtree(vault, ignore_errors=True)
        return

    if state.mode == BoxMode.primary:
        # ⚑⚑ L2: on a same-name in-place convert the destination metadata/vault IS the
        # source — dropping them here would delete the box just written.
        reused_in_place = preserve_name is not None and state.name == preserve_name
        if state.name and not reused_in_place:
            try:
                unregister_primary_box_name(std.primary_workset, state.name)
            except Exception:  # noqa: BLE001
                pass
        if reused_in_place:
            return
        if state.metadata_path.is_dir():
            remove_box_tree(state.metadata_path)
        # ⚑⚑ PRIMARY vault sits at @config.primary_workset/vault/{ro,rw}/<name>, NOT under
        # metadata_path: remove the per-box dirs only — the shared parent holds EVERY box's
        # vault, and the relative_to check below is what keeps the removal inside it.
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
                remove_box_tree(state.shell_path)
        return

    # Workset source: drop the registration; the external source dir is NEVER deleted.
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
    requested_name: str = "",
    force: bool = False,
) -> ProjectState:
    """Convert/relocate the project so its owner becomes the default workset."""
    # ⚑ ORDER: decide the mint BEFORE the reuse-unregister below, or the same-path reuse
    # case stops being detectable (it is detected BY the source's live registration).
    mint = _default_rename_name(state, std, new_workspace, requested_name)
    # ⚑⚑ L2 / FIX1: a PRIMARY source's own name is STILL registered here, so register/assign
    # would read the source's OWN entry as a same-kind collision. Free it first.
    preserved_name: str | None = None
    if state.mode == BoxMode.primary and state.name:
        existing = primary_box_name_for_workspace(
            std.primary_workset, str(new_workspace),
        )
        if existing is not None:
            # SAME-PATH in-place convert: free the name so assign reuses it verbatim.
            preserved_name = existing
            _safe_unregister(std, existing)
        elif mint is not None and mint == _primary_source_own_name(state, std):
            # ⚑ RELOCATING same-name move: this unwind runs BEFORE any later one, so a
            # failed re-register leaves name -> OLD path intact rather than orphaned.
            preserved_name = mint
            old_ws = state.workspace_path
            _safe_unregister(std, mint)
            unwind.push(
                lambda: _safe_register_membership(std, mint, old_ws)
            )
    # Honored --name goes through the per-kind guard; else the auto-suffix path.
    if mint is not None:
        register_primary_box_name(
            std.primary_workset, std.registry, mint, new_workspace, force=force,
        )
        project_name = mint
    else:
        project_name = assign_primary_box_name(
            std.primary_workset, std.registry, str(new_workspace),
        )
    unwind.push(lambda: _safe_unregister(std, project_name))
    dst_metadata = std.boxes / project_name

    # ⚑ Copy from the box METADATA DIR, never ``metadata_path``: for a standalone source
    # those differ (root vs ``box_data/``), and the root would drag workspace+vault into
    # the box dir AND land the source's WORKSET-tier file at the dest's BOX tier (M-8).
    src_meta_dir = box_metadata_dir(state.mode, state.metadata_path)
    # Name reused in place ⇒ the metadata IS already at the destination; copying it
    # would be a failing copy-onto-self.
    if dst_metadata.resolve() == state.metadata_path.resolve():
        dst_shell = state.shell_path
    else:
        dst_shell = _copy_metadata(
            src_meta_dir, state.shell_path, dst_metadata,
            shell_into_metadata=True, unwind=unwind,
        )
        _deliver_carried_box_settings(state, dst_metadata / BOX_META_FILE)

    # Phase 5 fixed PRIMARY table: vault under @config.primary_workset.
    vault_ro = std.primary_vault_ro / project_name
    vault_rw = std.primary_vault_rw / project_name

    # ⚑ SPARSE (P8b): NO ``project:``/``resolved:`` identity is written — identity lives in
    # the PRIMARY ``boxes:`` membership registered above. Only the non-default
    # ``box.enable_vault`` is persisted, carried so a disabled-vault box stays disabled.
    # ⚑⚑ The BOX-AUTHORED value, never the resolved one: a workset-tier default belongs to
    # the workset and must keep resolving from there (``carried_box_settings``).
    write_box_enable_vault(dst_metadata / BOX_META_FILE, state.box_authored_vault)

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
        box_authored_vault=state.box_authored_vault,
    )


#: ⚑ kanibako artifacts (NOT workspace content): they STAY at the standalone root when a
#: convert consolidates everything else into ``workspace/`` (drift H).
_STANDALONE_ROOT_ARTIFACTS = frozenset({
    STANDALONE_META_DIR,   # box_data/
    "workspace",            # the subdir we are populating
    "vault",                # vault/{ro,rw}
    WORKSET_META_FILE,      # the workset meta (drift I — at the root)
    BOX_META_FILE,          # the box meta (drift I — at the root)
    ".kanibako.lock",       # lock file
})


def _consolidate_workspace_subdir(
    root: Path,
    workspace_subdir: Path,
    unwind: _Unwind,
) -> None:
    """Move the project's top-level files into the ``workspace/`` subdir (drift H)."""
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
    """Lift the ``workspace/`` subdir's contents back up to *root* (inverse of consolidate)."""
    if not workspace_subdir.is_dir():
        return
    movable = list(workspace_subdir.iterdir())
    moved: list[Path] = []
    unwind.push(lambda: _undo_consolidate(root, workspace_subdir, moved))
    for child in movable:
        shutil.move(str(child), str(root / child.name))
        moved.append(child)
    # Drop the emptied subdir so the converted project keeps no stray ``workspace/``.
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
    """Convert/relocate the project so it becomes standalone (in-tree metadata)."""
    from kanibako.project import registry_store
    from kanibako.settings.paths import establish_standalone

    # ⚑ ORDER: consolidate the source's top-level files into ``workspace/`` FIRST, THEN lay
    # down the kanibako artifacts — otherwise the artifacts get swept into the subdir.
    root = new_workspace
    root.mkdir(parents=True, exist_ok=True)
    dst_metadata = root / STANDALONE_META_DIR
    workspace_subdir = root / "workspace"
    _consolidate_workspace_subdir(root, workspace_subdir, unwind)

    # ⚑ The box METADATA DIR (``box_data/`` for a standalone source) — the ROOT would
    # strand ``<dst>/box_data/box_data/`` on a standalone→standalone move.
    dst_shell = _copy_metadata(
        box_metadata_dir(state.mode, state.metadata_path), state.shell_path,
        dst_metadata, shell_into_metadata=True, home_leaf="home", unwind=unwind,
    )
    # ⚑⚑ DO NOT DELETE this file as an "orphan": the box.yaml landing in ``box_data/``
    # IS the destination's BOX TIER (spec §2c — @meta.box.path for standalone IS
    # ``box_data/``). Deleting it discards the box's settings; detection reads the ROOT
    # file (§5), which ``establish_standalone`` writes below.
    _deliver_carried_box_settings(state, dst_metadata / BOX_META_FILE)

    # Establish identity + meta + registration through the shared core; it writes
    # ``workset.kuid`` to <root>/workset.yaml and a sparse ``box.enable_vault`` to the box
    # tier, then registers the box.  ⚑ NO mode is persisted anywhere — standalone is
    # detected from the MARKER (that root file beside ``box_data/``), never from a stored key.
    # ⚑⚑ The BOX-AUTHORED value: ``establish_standalone`` writes this straight to the box
    # tier ``_deliver_carried_box_settings`` just laid down, so passing the RESOLVED value
    # would undo that carry and pin the source workset's default on a box that has LEFT it.
    box_name, dst_shell, vault_ro, vault_rw = establish_standalone(
        std, root,
        enable_vault=state.box_authored_vault,
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
        # ⚑ The new root ``workset.yaml`` carries ``workset.kuid`` and nothing else, so the
        # standalone box's RESOLVED value IS what it authored — the source workset's
        # default did not travel.
        enable_vault=state.box_authored_vault,
        box_authored_vault=state.box_authored_vault,
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

    # The path add_project records and decides external wiring from.
    source_for_add = new_workspace

    # ⚑ Copy only for an internal landing NOT already in place — STEP 2 may have moved the
    # tree to ``workspaces/<name>`` already. External never copies.
    copy_workspace = False
    if internal:
        expected_internal = (target_ws.workspaces_dir / new_name).resolve()
        already_in_place = new_workspace.resolve() == expected_internal
        copy_workspace = not already_in_place

    # ⚑ The box METADATA DIR, not ``metadata_path``: for a standalone source those differ
    # (root vs ``box_data/``) — see :func:`box_metadata_dir` (M-8).
    metadata_source = box_metadata_dir(state.mode, state.metadata_path)
    shell_source = state.shell_path

    source_is_workset = state.mode == BoxMode.named
    if source_is_workset and state.ws is not None:
        src_ws = state.ws
        src_name = state.name
        src_source_path = state.workspace_path
        # ⚑⚑ ws->ws: the SOURCE must release BEFORE the target registers (the connection
        # record is 1:1). Release DELETES the source dirs, so stash them first — the
        # forward copy below and the unwind both read the stash, not the live paths.
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

    # ⚑ ``force=True``: an absorb INTO a workset must override the standalone-marker
    # connect guard (B2a) — a standalone source still carries its ``box_data/`` marker
    # here, since the marker is removed LATER in the convert. No-op for other modes.
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
    # ⚑ The copy above cannot supply a STANDALONE source's box settings — its box tier is
    # a different file from the root one a pre-P2 box stored them in (M-8).
    _deliver_carried_box_settings(state, dst_project / BOX_META_FILE)
    dst_shell = dst_project / "home"
    if shell_source.is_dir():
        shutil.copytree(shell_source, dst_shell, dirs_exist_ok=True)
        # ⚑ copytree carries the canon skeleton's MODES but not its OWNERSHIP (J-7).
        materialize_canon_skeleton(dst_shell)

    if copy_workspace:
        dst_workspace = target_ws.workspaces_dir / new_name
        ignore = None
        if state.mode == BoxMode.standalone:
            ignore = shutil.ignore_patterns(STANDALONE_META_DIR)
        shutil.copytree(state.workspace_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)

    # Determine the recorded workspace.
    if internal:
        recorded_workspace = (target_ws.workspaces_dir / new_name)
    else:
        # ⚑ EXTERNAL: read the workspace back from the per-workset ``boxes:`` registry
        # add_project just wrote — under sparse create (P8b) the box's box.yaml no
        # longer self-describes, so the D10 connection record is the authority.
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        registry_path = workset_registry.resolve_workset_registry_path(
            target_ws.root, load_doc(target_ws.root / WORKSET_META_FILE),
        )
        recorded_str = workset_registry.load_workset_boxes(registry_path).get(
            new_name
        )
        recorded_workspace = (
            Path(recorded_str) if recorded_str else new_workspace
        )
    vault_ro = target_ws.vault_dir / "ro" / new_name
    vault_rw = target_ws.vault_dir / "rw" / new_name

    # ⚑ SPARSE (P8b): NO ``project:``/``resolved:`` identity is written — identity lives in
    # the global name index, workspace in the target workset's ``boxes:`` registry. Only
    # the non-default ``box.enable_vault`` is carried, so a disabled box stays disabled.
    # ⚑⚑ The BOX-AUTHORED value, never the resolved one: the SOURCE workset's default must
    # not follow the box into a DIFFERENT workset as a box-scope override.
    write_box_enable_vault(dst_project / BOX_META_FILE, state.box_authored_vault)

    # ⚑ A workset source ALREADY released above — cleaning up again would double-remove.
    if not source_is_workset:
        _remove_old_metadata(state, std, config)

    return ProjectState(
        owner=owner_token(BoxMode.named, target_ws.name),
        mode=BoxMode.named, name=new_name,
        workspace_path=recorded_workspace, metadata_path=dst_project,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=not internal, ws=target_ws,
        enable_vault=state.enable_vault,
        box_authored_vault=state.box_authored_vault,
    )


# -- small helpers ----------------------------------------------------------

def _state_ws_token(state: ProjectState) -> str:
    """Return the channel-partition workset-name token for *state*."""
    from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE

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


def _state_ws_root(state: ProjectState, std: StandardPaths) -> Path:
    """Return ``@meta.workset.path`` for *state* — the root its channel keys live under.

    ⚑ The ``ProjectState`` twin of :func:`channels.workset_root`, which takes a resolved
    ``ProjectPaths`` this path does not have.  Same three arms, deliberately in the same
    order as :func:`_state_ws_token`: the token says WHICH partition, this says which
    ``workset.yaml`` may repoint it, and a relocation needs both for each side.
    """
    if state.mode == BoxMode.standalone:
        # Standalone roots the workset at the project ROOT; ``metadata_path`` IS that
        # root (the workspace is a subdir under it), matching ``channels.workset_root``.
        return state.metadata_path
    if state.mode == BoxMode.named:
        if state.ws is None:
            raise ValueError(
                "named box is missing its workset; cannot derive the workset root."
            )
        return state.ws.root
    return std.primary_workset


def _relocate_channel_partition(
    old: ProjectState, new: ProjectState, std: StandardPaths,
) -> None:
    """Best-effort relocate THIS box's OWN channel partition (D-M10, §6).

    ⚑⚑ EACH SIDE'S PARTITION IS READ THROUGH ITS OWN WORKSET'S KEYS.  Both addresses
    used to be built from ``(std, ws_token)`` alone, which can only produce the
    partition's DEFAULT — while ``box_channel_addresses`` routes through
    ``workset.channels.{mailboxes,share_global}``, so a workset that repoints
    ``mailboxes`` has its boxes MOUNTED at the repointed address.  Moving the default
    directory therefore moved nothing and stranded the box's real mail.
    """
    import sys

    from kanibako.channels.channels import own_partition_dirs

    try:
        old_token = _state_ws_token(old)
        new_token = _state_ws_token(new)
        old_root = _state_ws_root(old, std)
        new_root = _state_ws_root(new, std)
    except ValueError as e:  # cannot derive an address → nothing to relocate
        print(f"Warning: skipping channel relocation: {e}", file=sys.stderr)
        return

    # No address change → nothing to move (idempotent no-op).
    # ⚑ Compared on the TOKEN, not the resolved path: two tokens that repoint to one
    # directory are still two partitions, and the box's own subdir name is what moves.
    if old_token == new_token and old.name == new.name:
        return

    # ⚑ A REPOINT CAN REFUSE (``workset.channels.*`` names the key and raises rather
    # than falling back), and this step runs AFTER the files have already moved. A
    # settings error in a best-effort cleanup must not abort a lifecycle operation
    # that is otherwise complete, so it warns with the key's own message and skips —
    # the same treatment the per-directory move below already gives an OSError.
    try:
        src = own_partition_dirs(std, old_token, old.name, ws_root=old_root)
        dst = own_partition_dirs(std, new_token, new.name, ws_root=new_root)
    except Exception as e:  # noqa: BLE001 - best-effort (D-M10)
        print(
            f"Warning: skipping channel relocation, a channel key did not "
            f"resolve: {e}",
            file=sys.stderr,
        )
        return

    for src_dir, dst_dir, label in (
        (src.mailbox, dst.mailbox, "mailbox"),
        (src.share_global, dst.share_global, "share"),
    ):
        try:
            if not src_dir.is_dir():
                continue  # nothing published yet under the old address
            if dst_dir.exists():
                # ⚑ NEVER clobber an existing dest — this whole step is best-effort.
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


def _safe_register_membership(
    std: StandardPaths, name: str, workspace: Path,
) -> None:
    """Best-effort re-register *name* -> *workspace* in the PRIMARY membership (FIX1)."""
    try:
        _register_workset_box_membership(std.primary_workset, name, workspace)
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
    """Map --default/--standalone/--workset to an ownership value, else :data:`UNCHANGED`."""
    if getattr(args, "to_default", False):
        return "default"
    if getattr(args, "to_standalone", False):
        return "standalone"
    ws = getattr(args, "to_workset", None)
    if ws:
        return ws
    return UNCHANGED


def _lower_name(args) -> str | None:
    """Return the user's ``--name`` folded to lowercase and validated (R2), or ``None``."""
    name = getattr(args, "name", None)
    if not name:
        return name
    folded = name.lower()
    validate_box_name(folded)
    return folded


def _make_confirm(force: bool, summary: str):
    """Return a ``Callable[[], bool]`` for ``execute_lifecycle``'s *confirm* (None if forced)."""
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
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.settings.paths import load_std_paths, xdg

    config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))
    std = load_std_paths(config)
    return config, std


def _abort_if_locked(state: ProjectState, force: bool) -> bool:
    """Refuse a destructive relocation while a box may be running; True ⇒ caller aborts."""
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
    """``box remap <old> [<new>]`` — records-only relocation; moves no files."""
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
    """``box move <old> <new>`` (alias ``mv``) — physically relocate files."""
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
    """``box convert [<old>] (--default|--standalone|--workset <ws>) [--move [path]]``."""
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

    # argparse stores :data:`_BARE_MOVE` for bare --move, a path string for --move <path>.
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

    # Lock pre-flight: convert re-roots by copy-then-rmtree (mirrors move / duplicate).
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
