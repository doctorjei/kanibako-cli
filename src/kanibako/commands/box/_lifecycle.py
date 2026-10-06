"""Shared transactional engine behind ``box remap`` / ``move`` / ``convert``.

**_Terminology_**
- _location_: where the workspace files physically live (:class:`TargetSpec`'s ``location``)
- _ownership_: which mode/workset owns the project (``TargetSpec.ownership``)
- _records-only_: ``remap`` — the files have ALREADY moved; record the new location, copy nothing
- _unwind_: the LIFO stack of compensating actions run in reverse on ANY failure

Public surface: :class:`ProjectState` · :func:`resolve_lifecycle_target` · :class:`TargetSpec` ·
:func:`execute_lifecycle` · the ``run_remap``/``run_move``/``run_convert`` CLI entry points ·
:func:`copy_into_workset` (the std-aware copy path for ``box duplicate``).

⚑ Destructive: step 2 copies; the old workspace is deleted only by
:func:`_retire_old_workspace`, after the whole op succeeded. The step ORDER and the unwind
pushes are load-bearing; see ``llm-docs/kanibako/commands/box/_lifecycle.py.md``.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable

from kanibako.launch.box_identity import validate_box_name
from kanibako.runtime.container import remove_box_tree
from kanibako.settings import bootstrap
from kanibako.settings.core_defaults import materialize_canon_skeleton
from kanibako.settings.workset_dirkeys import EarlyScope, refuse_inherited_per_owner
from kanibako.settings.config import (
    BOX_META_FILE,
    WORKSET_META_FILE,
    BootstrapConfig,
    read_box_enable_vault,
    write_box_enable_vault,
)
from kanibako.identifiers import find_identifier
from kanibako.errors import ProjectError, WorksetError
from kanibako.settings.paths import (
    STANDALONE_META_DIR,
    BoxMode,
    ProjectPaths,
    StandardPaths,
    WorksetSpec,
    _WorksetLike,
    _find_workset_for_path,
    _primary_box_paths,
    _register_workset_box_membership,
    _workset_box_name_for_workspace,
    _workset_box_paths,
    _box_settings_files,
    _default_project_group,
    _early_scope,
    assign_primary_box_name,
    box_metadata_dir,
    box_log_files,
    box_logs_dir_for,
    box_workset_settings_paths,
    check_primary_box_name_free,
    detect_project_mode,
    primary_box_name_for_workspace,
    register_primary_box_name,
    report_retained_store,
    resolve_box_enable_vault,
    resolve_designation,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
    standalone_box_store,
    standalone_logs_dir,
    standalone_store_teardown_plan,
    unregister_primary_box_name,
    write_vault_gitignore,
)
from kanibako.tree_copy import copy_tree_keeping_links, failed_entries
from kanibako.utils import write_project_gitignore
from kanibako.project.workset import (
    Workset,
    add_project,
    ensure_discoverability_link,
    is_in_tree_workspace,
    list_worksets,
    load_workset,
    load_workset_settings_doc,
    refuse_null_workspaces,
    release_project,
    remove_member_store,
    _member_store_bases,
    report_retained_vault,
    report_retained_vaults,
    resolve_workset_boxes,
    resolve_workset_canon,
    resolve_workset_vault_pair,
    resolve_workset_vault_ro,
    resolve_workset_vault_rw,
    resolve_workset_workspaces,
    standalone_vault_teardown,
    _path_in_tree,
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
    # ⚡ ``None`` when the workset nulls that arm — there is no such vault dir.
    vault_ro: Path | None
    vault_rw: Path | None
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
    #: The verb that asked (``"move"`` / ``"convert"``), so a refusal can give advice
    #: in that command's own syntax.  Advice only — no check reads it.
    verb: str | None = None


def owner_token(mode: BoxMode, ws_name: str | None = None) -> str:
    """Build a canonical owner token from a mode (+ workset name)."""
    if mode == BoxMode.named:
        if not ws_name:
            raise ValueError("workset owner requires a workset name")
        return f"workset:{ws_name}"
    return mode.value


def _same_box_name(left: str | None, right: str | None) -> bool:
    """True when two box names are the SAME identifier — compared case-blind (spec §0).

    A one-candidate call into the single comparison carrier, so this file states the
    rule nowhere itself.  An absent name (``None``/``""``) is not an identifier and
    compares exactly, which is what the ``or state.name`` defaults below rely on.
    """
    if not left or not right:
        return left == right
    return find_identifier(left, (right,)) is not None


def _default_rename_name(
    state: ProjectState,
    std: StandardPaths,
    landing_ws: Path,
    requested_name: str,
) -> str | None:
    """The explicit primary-box name a DEFAULT-mode edge would MINT, or ``None``."""
    if not requested_name:
        return None
    existing = _primary_name_at(state, std, landing_ws)
    if existing is not None:
        # ⚑ Landing path already registered ⇒ SAME-PATH edge, no NEW registration minted.
        # ⚑ Case-blind (§0): ``--name FOO`` on a box registered as ``foo`` names the
        # SAME box, so it is the moot reuse below and not a refused in-place rename.
        if not _same_box_name(requested_name, existing):
            raise ProjectError(
                f"In-place rename of a primary (default-mode) box is not "
                f"supported: '{existing}' -> '{requested_name}'. Move the box "
                f"to rename it (e.g. `box move {existing} <new-path> --name "
                f"{requested_name}`), or drop --name to keep the current name."
            )
        # --name equals the current name: a moot reuse, not a rename edge.
        return None
    # ⚑ Case-blind (§0): ``--name FOO`` on a box registered as ``foo`` names the SAME
    # box, so the mint is its STORED spelling. Minting the typed case makes the source's
    # OWN registration read as a same-kind collision at ``_to_default``, refusing a move
    # ``_validate`` has already allowed -- after the tree is copied.
    own = _primary_source_own_name(state, std)
    if own is not None and _same_box_name(requested_name, own):
        return own
    return requested_name


def _primary_name_at(
    state: ProjectState, std: StandardPaths, landing_ws: Path,
) -> str | None:
    """The primary name registered at *landing_ws* for a PRIMARY source, else ``None``.

    ⚑ Not ``None`` is the SAME-PATH edge: the box lands where it is registered.
    """
    if state.mode != BoxMode.primary or not state.name:
        return None
    return primary_box_name_for_workspace(std.primary_workset, str(landing_ws), early=_early_scope(std, BoxMode.primary))


def _primary_source_own_name(
    state: ProjectState, std: StandardPaths,
) -> str | None:
    """The name the SOURCE primary box is CURRENTLY registered under, else ``None``."""
    if state.mode != BoxMode.primary or not state.name:
        return None
    return primary_box_name_for_workspace(
        std.primary_workset, str(state.workspace_path), early=_early_scope(std, BoxMode.primary),
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
    config: BootstrapConfig | None = None,
) -> ProjectState:
    """Resolve an existing project (by path or name) to a :class:`ProjectState`."""
    if config is None:
        from kanibako.settings.config import user_config_file, load_config
        config = load_config(user_config_file())

    raw = resolve_designation(std, old, unknown_name_is_path=True)
    raw_path = Path(raw).resolve()

    detection = detect_project_mode(raw_path, std, config)

    if detection.mode == BoxMode.named:
        # ⚑ A ``workset.workspaces`` path is where a member MAY live; only a ``boxes:``
        # record makes one (spec § Detection & import).  Unrecorded, the primary decides.
        if (primary_box_name_for_workspace(std.primary_workset, str(raw_path),
                                           early=_early_scope(std, BoxMode.primary)) is not None
                and not _workset_records_member_at(raw_path, std)):
            return _resolve_primary_state(raw_path, std, config)
        return _resolve_workset_state(raw_path, std, config)
    if detection.mode == BoxMode.standalone:
        proj = resolve_standalone_project(
            std, config, str(detection.project_root), initialize=False,
        )
        return _state_from_paths("standalone", proj, ws=None, early=_early_scope(std, BoxMode.standalone))

    return _resolve_primary_state(detection.project_root, std, config)


def _workset_records_member_at(resolved: Path, std: StandardPaths) -> bool:
    """Whether a workset records a member at *resolved*: in-tree or an external connect."""
    from kanibako.launch import box_resolve

    if box_resolve.find_connected_external_box(resolved, std) is not None:
        return True
    try:
        ws, _ = _find_workset_for_path(resolved, std)  # type: ignore[assignment]
    except WorksetError:
        return False
    return _workset_box_name_for_workspace(
        ws.root, str(resolved), early=_early_scope(std, BoxMode.named, ws.name)) is not None


def _resolve_primary_state(
    root: Path, std: StandardPaths, config: BootstrapConfig,
) -> ProjectState:
    """Resolve a PRIMARY (default-mode) workspace to a :class:`ProjectState`."""
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
    return _state_from_paths("primary", proj, ws=None, early=_early_scope(std, BoxMode.primary))


def _default_state_from_meta(
    workspace: Path, std: StandardPaths,
) -> ProjectState | None:
    """Build a default-mode :class:`ProjectState` from registered metadata (``remap``)."""
    name = primary_box_name_for_workspace(std.primary_workset, str(workspace), early=_early_scope(std, BoxMode.primary))
    if name is None:
        return None
    # ⚑ P8b: the PRIMARY-membership hit above IS the existence signal — identity no
    # longer self-describes on disk, so there is NO ``project.mode`` presence gate.
    metadata_path = std.boxes / name
    # ⚑ B2b: home/vault are the DEFAULT location only — never a stored per-box override,
    # which would target a different home than the launch binds do (JC-B2b-4).
    shell_path, vault_ro, vault_rw = _primary_box_paths(std, metadata_path, name)
    # ⚑ The GROUP is the PRIMARY workset — the same one ``resolve_project`` derives — so
    # this fallback resolves ``box.enable_vault`` through the SAME CASCADE the launch path
    # uses, so a ``remap`` answers as ``resolve_project`` does whether or not the
    # workspace dir is still on disk.
    box_tier, workset_tier = _box_settings_files(
        BoxMode.primary, metadata_path, _default_project_group(std),
    )
    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=name,
        workspace_path=workspace.resolve(), metadata_path=metadata_path,
        shell_path=shell_path, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=resolve_box_enable_vault(
            std.config_file, box_path=box_tier, workset_path=workset_tier,
        ),
        # ⚑ The AUTHORED half stays a BOX-TIER-ONLY read, no ``default_from`` and NOT the
        # cascade: a merge cannot say which tier carried the leaf, and persisting an
        # inherited default as a box-scope override is what ``carried_box_settings``
        # exists to prevent.
        box_authored_vault=read_box_enable_vault(box_tier),
    )


def _resolve_workset_state(
    raw_path: Path, std: StandardPaths, config: BootstrapConfig,
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
            ws, proj_name = (load_workset(owned.workset_root, owned.workset_name,
                                          early_system=std.early_system),
                             owned.box_name)
    if ws is None or proj_name is None:
        raise WorksetError(f"No workset project found for path: {raw_path}")

    proj = resolve_workset_project(
        WorksetSpec.from_workset(ws), proj_name, std, config, initialize=False,
    )
    recorded = recorded_workspace_for(ws, proj_name, proj.project_path)
    if recorded is None:
        refuse_null_workspaces(ws.root, f"a workspace for '{proj_name}'",
                               early=_early_scope(std, BoxMode.named, ws.name))
    assert recorded is not None  # refused on the line above
    is_external = not is_in_tree_workspace(ws, recorded)
    return _state_from_paths(
        owner_token(BoxMode.named, ws.name), proj, ws=ws,
        early=_early_scope(std, BoxMode.named, ws.name), is_external=is_external,
        workspace=recorded,
    )


def recorded_workspace_for(
    ws: "_WorksetLike", box_name: str, resolved: Path | None,
) -> Path | None:
    """The member's recorded ``source_path`` — its files — else *resolved*.

    Shared by lifecycle, ``box duplicate``, and ``box archive``: a null
    ``workset.workspaces`` nulls the resolved path, never the ``boxes:`` row.
    """
    for member in ws.projects:
        if member.name == box_name:
            return member.source_path
    return resolved


def _state_from_paths(
    owner: str,
    proj: ProjectPaths,
    *,
    ws: Workset | None,
    early: EarlyScope,
    is_external: bool = False,
    workspace: Path | None = None,
) -> ProjectState:
    # ⚑ ``proj.vault_enabled()`` is the RESOLVED value; re-read the BOX TIER alone for what
    # the box authored, so a lifecycle op never persists the workset's default as a
    # box-scope override (see ``ProjectState.box_authored_vault``).
    box_tier, _ = box_workset_settings_paths(proj)
    # ⚑ A named caller passes its recorded *workspace*.
    recorded = workspace if workspace is not None else proj.project_path
    if recorded is None:
        # A standalone root that nulls ``workset.workspaces``: no workspace to move or copy.
        refuse_null_workspaces(proj.metadata_path, f"a workspace for '{proj.name}'",
                               standalone=True, early=early)
    assert recorded is not None  # only a standalone box can lack a workspace
    return ProjectState(
        owner=owner,
        mode=proj.mode,
        name=proj.name or recorded.name,
        workspace_path=recorded,
        metadata_path=proj.metadata_path,
        shell_path=proj.shell_path,
        vault_ro=proj.vault_ro_path,
        vault_rw=proj.vault_rw_path,
        is_external=is_external,
        ws=ws,
        enable_vault=proj.vault_enabled(),
        box_authored_vault=read_box_enable_vault(box_tier),
    )


# ---------------------------------------------------------------------------
# Std-aware workset copy helper for ``box duplicate``
# ---------------------------------------------------------------------------

def _workspace_copy_ignore(
    metadata_root: Path, copied_root: Path, *, mode: BoxMode, early: EarlyScope,
) -> Callable[[str, list[str]], set[str]]:
    """A workspace copy's *ignore*: never carry the box's OWN store into a workspace.

    ⚑⚑ BY PATH, NEVER BY NAME.  ``ignore_patterns("box_data")`` matched that name at ANY
    depth and silently dropped the user's own ``src/box_data/``.  The ONE path excluded is
    the store **as resolved for ITS mode** (:func:`box_metadata_dir`), and only when it
    lies inside *copied_root* (``_path_in_tree``).  A primary's store sits OUTSIDE its
    workspace, so it excludes nothing.  A ``box_data/`` left behind after the store was
    repointed is ordinary content and travels: excluding it, then retiring the source,
    deleted whatever the user kept there.
    """
    store = box_metadata_dir(mode, metadata_root, early=early).resolve()
    copied = copied_root.resolve()
    inside = (store,) if store != copied and _path_in_tree(store, copied) else ()

    def ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory).resolve()
        return {n for path in inside if path.parent == here for n in names
                if here / n == path}

    return ignore


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
    workspace = ws.require_workspaces_dir(f"a workspace for '{proj_name}'") / proj_name
    # ⚑ Taken BEFORE add_project, which adopts an existing leaf: the rollback deletes only
    # the leaves this call created (a ``--bare`` or ``--force`` duplicate adopts on purpose).
    existed = _existing_member_leaves(ws, proj_name)
    # ⚑ Register the IN-TREE workspace dir: a duplicate is always INTERNAL, so add_project
    # makes a real directory instead of symlinking back at the source.
    add_project(ws, proj_name, workspace, std)

    # ⚑ Failure-consistency: a crash after add_project but during the copies would strand a
    # registered-but-incomplete project. Roll registration + created dirs back, then re-raise.
    try:
        dst_project = ws.projects_dir / proj_name
        copy_tree_keeping_links(
            metadata_path, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
            dirs_exist_ok=True,
        )

        if shell_path.is_dir():
            dst_shell = dst_project / "home"
            copy_tree_keeping_links(shell_path, dst_shell, dirs_exist_ok=True)
            # ⚑ The copy carries the canon skeleton's MODES but not its OWNERSHIP — re-assert.
            materialize_canon_skeleton(dst_shell)

        if copy_workspace:
            dst_workspace = workspace
            ignore = None
            if source_mode == BoxMode.standalone:
                ignore = _workspace_copy_ignore(
                    metadata_path, source_path, mode=BoxMode.standalone,
                    early=_early_scope(std, BoxMode.standalone))
            copy_tree_keeping_links(source_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)
    except BaseException:
        _unwind_target_member(ws, proj_name, existed)
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
        """Register an action to run only when the whole op succeeds.

        ⚑ Includes DESTRUCTIVE work — ``_retire_old_workspace`` — not just scratch disposal.
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
    # ⚑ Case-blind (spec §0, ⚑ NAMING RULES), loaded under the REGISTERED spelling.
    stored = find_identifier(name, registry)
    if stored is None:
        raise WorksetError(f"Workset '{name}' not found.")
    return load_workset(registry[stored], stored, early_system=std.early_system)


def _cure_ref(state: ProjectState) -> str:
    """The reference a printed cure reaches *state* by.

    ⚑ A standalone's name is in no registry the route reads, and its nested workspace
    resolves through the path space its root sits in, so it is the box root.
    """
    if state.mode is BoxMode.standalone:
        return str(state.metadata_path)
    return state.name


def _validate(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: BootstrapConfig,
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
        dest = (target_ws.require_workspaces_dir(f"a workspace for '{new_name}'")
                / new_name).resolve()
    elif isinstance(spec.location, Path):
        dest = spec.location.resolve()

    relocating = dest is not None

    if not spec.records_only:
        refuse_inherited_per_owner(
            _state_ws_root(state, std), EarlyScope(std.early_system, _state_ws_token(state)))
        if target_ws is not None:
            refuse_inherited_per_owner(target_ws.root, target_ws.early_scope)
        elif target_mode == BoxMode.primary:
            refuse_inherited_per_owner(std.primary_workset, _early_scope(std, BoxMode.primary))
        else:
            refuse_inherited_per_owner(
                dest or state.workspace_path, _early_scope(std, BoxMode.standalone))

    # --- no-op guard ---
    no_owner_change = (
        spec.ownership is UNCHANGED
        or (target_mode == state.mode
            and (target_mode != BoxMode.named
                 or (state.ws is not None and target_ws is not None
                     and state.ws.name == target_ws.name)))
    )
    no_rename = _same_box_name(new_name, state.name)
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

    # --- an EXTERNAL source cannot be relocated: its "workspace" is the user's own
    #     directory, so a copy would record a path that holds nothing.
    if relocating and state.is_external and not spec.records_only:
        raise ProjectError(
            f"'{state.name}' is an external-connected project; its workspace is "
            "your own directory, not managed by kanibako. Use `box remap <old> "
            "<new>` to update records if you moved it, or `box convert` without "
            "`--move` to change ownership."
        )

    # --- destination not already occupied ---
    # ⚑ ``records_only`` exempt: ``remap``'s files are ALREADY at *dest*, so it must not
    # be required empty (and a no-op same-path remap is fine).
    if relocating and dest is not None and not spec.records_only:
        if dest.resolve() == state.workspace_path.resolve():
            raise ProjectError(
                f"Destination is the project's current location: {dest}"
            )
        if state.workspace_path.resolve() in dest.resolve().parents:
            raise ProjectError(
                f"Destination is inside the project being moved: {dest}"
            )
        if dest.exists():
            raise ProjectError(f"Destination already exists: {dest}")

    # --- an in-tree landing for a NAMED target must be the one leaf the target
    #     records.  Any other in-tree path is copied to by step 2 and copied again to
    #     ``workspaces/<name>`` by step 2b, so the box would record ``workspaces/<name>``
    #     while the user asked for the other path.  ``records_only`` is NOT exempt: it
    #     records that path as the workspace too.
    if target_mode == BoxMode.named and target_ws is not None and dest is not None:
        ws_dir = target_ws.workspaces_dir
        if (ws_dir is not None and is_in_tree_workspace(target_ws, dest)
                and dest != (ws_dir / new_name).resolve()):
            leaf = ws_dir / new_name
            rename = "" if _same_box_name(new_name, state.name) else f" --name {new_name}"
            ref = _cure_ref(state)
            bare = (f"kanibako box convert {ref} --workset {target_ws.name} "
                    f"--move{rename}")
            if spec.records_only:
                advice = (f"Move the files to `{leaf}` and run `kanibako box remap` "
                          "again")
            elif spec.verb == "convert":
                advice = f"Run `{bare}`"
            else:
                advice = (f"Run `kanibako box move {ref} {leaf} --workset "
                          f"{target_ws.name}{rename}` (or `{bare}`)")
            raise ProjectError(
                f"Refusing to record {dest} for a workset member: inside workset "
                f"'{target_ws.name}' a member would live at `{leaf}`, and no other "
                f"in-tree path is the workspace the box records. {advice}, or "
                "choose a destination outside the workset."
            )

    # --- ⚑ an IN-PLACE convert records the box only where it stands: any other leaf
    #     copies the tree in and orphans the source's tree.  A relocation moves it.
    if (target_mode == BoxMode.named and target_ws is not None
            and not relocating and not spec.records_only
            and state.mode is not BoxMode.named):
        landing_leaf = target_ws.workspaces_dir
        if (landing_leaf is not None
                and is_in_tree_workspace(target_ws, state.workspace_path)
                and (landing_leaf / new_name).resolve() != state.workspace_path.resolve()):
            rename = "" if _same_box_name(new_name, state.name) else f" --name {new_name}"
            ref = _cure_ref(state)
            raise ProjectError(
                f"Refusing to convert '{state.name}' in place: a member of workset "
                f"'{target_ws.name}' would live at {(landing_leaf / new_name)}, and "
                f"'{state.name}' already has its workspace at "
                f"{state.workspace_path} — an in-place convert would leave that tree "
                f"behind with no box owning it. Run `kanibako box convert "
                f"{ref} --workset {target_ws.name}{rename} --move` to move it "
                f"there, or `kanibako box move {ref} <path>` to move it out of "
                f"the workset."
            )

    # --- membership guard: refuse landing inside a workset the project is
    #     not (becoming) a member of ---
    landing = dest if dest is not None else state.workspace_path
    owning_ws_root: Path | None = None
    if target_mode == BoxMode.named and target_ws is not None:
        owning_ws_root = target_ws.root.resolve()
    for ws_name, ws_root in list_worksets(std).items():
        ws_root = Path(ws_root).resolve()
        if owning_ws_root is not None and ws_root == owning_ws_root:
            continue
        # ⚑ In-tree: a repointed ``workset.workspaces`` dir, or a link.
        if not is_in_tree_workspace(
                Workset(name=ws_name, root=ws_root, early_system=std.early_system), landing):
            continue
        raise ProjectError(
            f"Refusing to land the project inside workset '{ws_name}' "
            f"({ws_root}) — it is not (being made) a member of that workset. "
            f"Use `--workset {ws_name}` to make it a member, or choose a "
            "destination outside that workset."
        )

    # --- an in-tree landing needs a workspace dir: a null ``workset.workspaces`` has none
    #     (Q96).  Refused HERE, before step 2 moves a tree or a ws->ws source releases. ---
    if target_mode == BoxMode.named and target_ws is not None:
        in_tree = spec.location is BARE_INTO_WS
        if not in_tree:
            try:
                landing.resolve().relative_to(target_ws.root.resolve())
                in_tree = True
            except ValueError:
                pass
        if in_tree:
            refuse_null_workspaces(target_ws.root, f"a workspace for '{new_name}'",
                                   early=target_ws.early_scope)
    # An in-place convert TO standalone sweeps the project's files into the root's
    # ``workset.workspaces`` (``_consolidate_workspace_subdir``); a relocation's *dest* is
    # new, so it carries no null.
    if (target_mode == BoxMode.standalone and dest is None
            and state.mode != BoxMode.standalone):
        refuse_null_workspaces(state.workspace_path, f"a workspace for '{new_name}'",
                               standalone=True, early=_early_scope(std, BoxMode.standalone))

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
    # ⚑ Both compares are case-blind (spec §0): the SELF exemption, so ``--name FOO``
    # on the box already registered as ``foo`` is not read as a collision with itself,
    # and the member scan, so it is not read as free either.  ⚑ The member scan is an
    # ``==`` over already-loaded members rather than a registry lookup, so nothing in
    # ``tests/test_identifier_case_enforcement.py`` sees it — found by reading.
    if (
        target_mode == BoxMode.named
        and target_ws is not None
        and not (state.ws is not None and target_ws.name == state.ws.name
                 and _same_box_name(new_name, state.name))
    ):
        held = find_identifier(new_name, (p.name for p in target_ws.projects))
        if held is not None:
            raise WorksetError(
                f"Project '{held}' already exists in workset "
                f"'{target_ws.name}'."
            )

    # --- an UNREGISTERED leaf of the target's new name is the same collision on disk:
    #     ``add_project`` adopts whatever is already there.  ⚑ ``records_only`` is exempt
    #     (its files ARE meant to be at *dest*), and so is a leaf that IS the source's
    #     own — the same-workset, same-name case releases and re-records it — and so is
    #     the source's own workspace as the landing leaf, kept or vacated alike.
    if (
        not spec.records_only
        and target_mode == BoxMode.named
        and target_ws is not None
    ):
        own: frozenset[Path] = frozenset()
        if (state.ws is not None and target_ws.name == state.ws.name
                and _same_box_name(new_name, state.name)):
            own = frozenset(
                leaf for leaf in _member_leaves(state.ws, state.name)
                if leaf is not None
            )
        landing_leaf = target_ws.workspaces_dir
        if (landing_leaf is not None
                and (landing_leaf / new_name).resolve() == state.workspace_path.resolve()):
            own = own | {state.workspace_path}
        taken = [p for p in sorted(_existing_member_leaves(target_ws, new_name))
                 if p not in own]
        if taken:
            raise ProjectError(
                f"Refusing to land '{new_name}' in workset '{target_ws.name}': "
                f"{taken[0]} already exists and this operation did not create it. "
                "Move it aside, or choose another name."
            )

    # --- same-kind name policy on a DEFAULT-mode --name rename edge (F-7) ---
    # ⚑ Checked UP FRONT so a name refusal costs no file copy.
    requested_name = spec.name or ""
    # The reuse-in-place edges, whose teardown is skipped: the box keeps its own vault.
    vault_reused = (
        state.mode == BoxMode.standalone and target_mode == BoxMode.standalone
        and (dest is None or dest == state.metadata_path.resolve())
    )
    if target_mode == BoxMode.primary:
        landing_ws = dest if dest is not None else state.workspace_path
        mint = _default_rename_name(state, std, landing_ws, requested_name)
        # ⚑ FIX1: a same-name relocate reuses the SOURCE's OWN registration, so it is
        # exempt from the same-kind guard.
        own_name = _primary_source_own_name(state, std)
        # ⚑ The same-path edge reuses the box in place too (``_to_default``).
        landed = _primary_name_at(state, std, landing_ws)
        vault_reused = (
            (landed is not None and landed == state.name)
            or (mint is not None and _same_box_name(mint, own_name))
        )
        if mint is not None and not _same_box_name(mint, own_name):
            check_primary_box_name_free(
                std.primary_workset, mint, str(landing_ws),
                early=_early_scope(std, BoxMode.primary),
            )

    # --- a disabled vault that still holds data would be left behind (Q64) ---
    stranded = [] if vault_reused or state.enable_vault else [
        leaf for leaf, _why in _unreceived_vault_leaves(
            (state.vault_ro, state.vault_rw), (None, None), vault_enabled=False,
        )
    ]
    if stranded and not force:
        raise ProjectError(
            f"Refusing: box.enable_vault is false for '{state.name}', but its vault "
            f"still holds data at {', '.join(map(str, stranded))}. The box carries no "
            "vault, so this would leave that data behind. Move it out first, or pass "
            "--force to proceed and leave it in place."
        )

    return {
        "target_mode": target_mode,
        "target_ws": target_ws,
        "target_ws_name": target_ws_name,
        "dest": dest,
        "relocating": relocating,
        "no_owner_change": no_owner_change,
        "new_name": new_name,
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
    config: BootstrapConfig | None = None,
    *,
    force: bool = False,
    confirm: Callable[[], bool] | None = None,
) -> ProjectState:
    """Apply *spec* to *state* transactionally, in the canonical 5-step order."""
    import os

    if config is None:
        from kanibako.settings.config import user_config_file, load_config
        config = load_config(user_config_file())

    cwd = Path(os.getcwd())
    plan = _validate(state, spec, std, config, force=force, cwd=cwd)

    if confirm is not None and not confirm():
        raise ProjectError("Aborted by user.")

    unwind = _Unwind()
    try:
        new_state = _run_steps(state, spec, std, config, plan, unwind)
    except BaseException:
        # ⚑⚑ ``BaseException``, not ``Exception``: an interrupt after the release would
        # otherwise skip every compensating action and leave the stash in ``$TMPDIR``.
        unwind.run()
        raise
    unwind.finish()
    return new_state


def _run_steps(
    state: ProjectState,
    spec: TargetSpec,
    std: StandardPaths,
    config: BootstrapConfig,
    plan: dict,
    unwind: _Unwind,
) -> ProjectState:
    import sys

    target_mode: BoxMode = plan["target_mode"]
    target_ws: Workset | None = plan["target_ws"]
    dest: Path | None = plan["dest"]
    relocating: bool = plan["relocating"]
    new_name: str = plan["new_name"]
    requested_name: str = plan["requested_name"]

    # --- STEP 2 — Move files (only when relocating a real workspace tree) ---
    records_only: bool = spec.records_only
    new_workspace = state.workspace_path
    if records_only and dest is not None:
        # ``remap``: files presumed already at *dest*; copy and remove nothing.
        new_workspace = dest
        old = state.workspace_path
        if (state.mode is BoxMode.named and not state.is_external and old.is_dir()
                and old.resolve() != dest.resolve()):
            unwind.on_success(lambda: print(
                f"Note: left {old}; remap deletes nothing", file=sys.stderr))
    elif relocating and dest is not None and not state.is_external:
        src = state.workspace_path
        # ⚑⚑ THE PATH-ANCHORED IGNORE, rooted on ``state.metadata_path``, NOT *src*: at
        # the DEFAULT layout a standalone's workspace sits one level below the root that
        # carries ``workset.boxes``, so *src* answered the store as the USER'S
        # ``<workspace>/box_data`` and this move deleted it (R1).
        copy_tree_keeping_links(
            src, dest,
            ignore=_workspace_copy_ignore(
                state.metadata_path, src, mode=state.mode,
                early=EarlyScope(std.early_system, _state_ws_token(state))),
        )
        # ⚑ THE UNWIND OWNS ONLY WHAT THIS MOVE CREATED: the copy refuses an existing dest.
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
    ):
        # ⚑⚑ THE STANDALONE ROOT IS ``metadata_path`` (drift I) — NEVER the workspace's
        # PARENT.  ``workspace_path`` is the RESOLVED ``workset.workspaces``, so under
        # ``workspaces: nested/deep`` its parent is ``<root>/nested`` and under an
        # ABSOLUTE repoint it is not below the root at all.  Every branch here aims a
        # step that MOVES USER DIRECTORIES, so the root is READ, never positioned.
        root = state.metadata_path
        workspace = state.workspace_path
        if target_mode is BoxMode.standalone:
            # ⚑ In-place standalone rename.  ``_to_standalone`` reads this value as the
            # ROOT, so handing it the workspace dir laid a SECOND box inside the first.
            new_workspace = root
        elif workspace.resolve() == root.resolve():
            # ``workspaces: .`` — the live workspace already IS the project dir, which is
            # exactly what every other mode wants.  Nothing to lift.
            new_workspace = root
        elif root.resolve() in workspace.resolve().parents:
            # ⚑ Reverse of drift H: standalone roots the live workspace UNDER the root,
            # every other mode at the project dir — so an in-place convert OUT must lift.
            _unconsolidate_workspace_subdir(workspace, root, unwind)
            new_workspace = root
        else:
            # ⚑ [R144]: an ABSOLUTE ``workset.workspaces`` is a directory the USER named,
            # and emptying it is the irreversible loss.  Nothing needs to move — the mode
            # being converted TO roots its workspace at the project dir, and that dir may
            # be anywhere — so the box simply keeps it.  Reported, because a keep that
            # cannot name the path as the user's is just a leak.
            print(f"Note: left the workspace at {workspace} — workset.workspaces "
                  f"pointed it outside {root}, so it is yours and the box keeps "
                  f"it as its project directory.", file=sys.stderr)
            new_workspace = workspace

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
    )

    # --- STEP 4b — Relocate this box's OWN channel partition (best-effort, D-M10).
    # ⚑ MUST run AFTER identity is finalized (A9): a standalone convert REGENERATES the
    #   box name, so the new address is only readable off ``new_state``. ---
    _relocate_channel_partition(state, new_state, std)

    # --- STEP 5 — Retire the old workspace step 2 copied, ON SUCCESS ONLY.
    # ⚑ Never a user's EXTERNAL source. ---
    if not records_only and relocating and dest is not None and not state.is_external:
        old_ws = state.workspace_path
        unwind.on_success(lambda: _retire_old_workspace(old_ws, dest))
    # ⚑ An external landing whose ``workspaces/<name>`` was held by the leaf just retired
    #   gets its discoverability link now, after the retire (registration order).
    if target_mode is BoxMode.named and target_ws is not None and new_state.is_external:
        link_ws = target_ws

        def _link() -> None:
            ensure_discoverability_link(link_ws, new_name, new_state.workspace_path)

        unwind.on_success(_link)

    return new_state


def _retire_old_workspace(old: Path, landed: Path) -> None:
    """Delete the relocated-from workspace *old*, whose copy landed at *landed*.

    ⚑⚑ An ``_Unwind.on_success`` action ONLY — a failed op never reaches it.  Skips an
    absent *old*, and an *old* that is or holds *landed* (a move into its own subtree;
    ``_validate`` refuses that first).  A symlink is unlinked, never followed.  A failed
    delete prints a Note and stops: no second deleter, rc unchanged.
    """
    import sys

    if not old.exists() and not old.is_symlink():
        return
    old_r = old.resolve()
    landed_r = landed.resolve()
    if old_r == landed_r or old_r in landed_r.parents:
        return
    try:
        if old.is_symlink():
            old.unlink()
            print(f"Note: left {old_r}; it is yours", file=sys.stderr)
        else:
            shutil.rmtree(old)
    except OSError as err:
        print(f"Note: could not remove the old workspace {old}: {err}", file=sys.stderr)


def _apply_ownership_and_markers(
    state: ProjectState,
    std: StandardPaths,
    config: BootstrapConfig,
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
        # ⚑ Standalone is the one mode whose live workspace is NOT the project dir (drift
        # H), so ``new_workspace`` is its ROOT — named as such at the callee, since the two
        # differ by a resolved key and reading one as the other nests a box inside a box.
        return _to_standalone(
            state, std, config, unwind,
            new_name=new_name, root=new_workspace,
            requested_name=requested_name,
        )
    return _to_default(
        state, std, config, unwind,
        new_name=new_name, new_workspace=new_workspace,
        requested_name=requested_name,
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
    copy_tree_keeping_links(
        src_metadata, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
        dirs_exist_ok=True,
    )
    # ⚑ ESCALATING removal, not a plain rmtree: this function lays a canon skeleton below,
    # so a plain rmtree would silently fail to clean up its own destination.
    unwind.push(lambda: _unwind_box_tree(dst_metadata))

    dst_shell = dst_metadata / home_leaf
    if src_shell.is_dir():
        copy_tree_keeping_links(src_shell, dst_shell, dirs_exist_ok=True)
        # ⚑ The copy carries the canon skeleton's 555 MODES but never its OWNERSHIP —
        # re-assert (idempotent; J-7).
        materialize_canon_skeleton(dst_shell)
    return dst_shell


def _deliver_carried_box_settings(
    state: ProjectState, dst_box_tier: Path, *, early: EarlyScope,
) -> None:
    """Write the source box's carried box-scope settings to *dst_box_tier* (M-8)."""
    from kanibako.settings.config import carried_box_settings
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.paths import _box_settings_files

    # ⚑ ``group=None`` is harmless: only the WORKSET tier is derived from the group, and
    # the carry reads the BOX tier alone — ProjectState carries no ProjectGroup anyway.
    src_box, _ = _box_settings_files(state.mode, state.metadata_path, None, early=early)
    carried = carried_box_settings(src_box)
    if carried:
        dump_doc(dst_box_tier, carried)


def _vault_leaf_has_contents(leaf: Path) -> bool:
    """True when *leaf* holds anything — an unreadable leaf counts as non-empty.

    ⚑ An error here must never read as "empty": the carry skips its warnings for
    empty leaves, and silence about an unreadable store would be the wrong default.
    A MISSING leaf holds nothing, so only that error reads as empty.
    """
    try:
        return any(leaf.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _copy_vault_leaf_contents(src: Path, dst: Path | None) -> None:
    """Merge-copy the CONTENTS of vault leaf *src* into leaf *dst*.

    ⚑ The counterpart ``snapshots.py`` copies vault content under the same symlink
    rule; this is the same operation pointed at the relocation destination instead
    of a snapshot dir.  No-ops when *src* holds nothing (missing or not a dir) and when
    *src* and *dst* are the same directory (a reuse-in-place edge, whose teardown
    is skipped — there is nothing to carry).  RAISES on a copy failure: callers
    run this BEFORE the source teardown (except leg 2 of the workset stash and its
    unwind, whose source is the stash), so a failure aborts the relocation with
    the source still whole (and the unwind drops the destination).

    ⚑ Every symlink is copied VERBATIM and never followed
    (:func:`kanibako.tree_copy.copy_tree_keeping_links`), so a dangling link carries
    like any other.  The copy takes every entry it can before it raises one
    ``shutil.Error`` listing the rest; that failure is re-raised as a
    ``ProjectError`` naming the leaf and the entries — the entries are NOT skipped,
    since skipping one would drop it from the store without a word.
    """
    if dst is None or not src.is_dir():
        return
    if src.resolve() == dst.resolve():
        return
    if src.resolve() in dst.resolve().parents:
        # ⚑ Pathological (a destination repointed inside the source leaf): copying
        # would nest the tree into itself, and the teardown below would then delete
        # the just-written destination with the source.  Refuse loudly instead.
        raise ProjectError(
            f"Refusing to carry the vault from {src} into {dst}: the destination "
            f"is inside the source."
        )
    dst.mkdir(parents=True, exist_ok=True)
    try:
        copy_tree_keeping_links(src, dst, dirs_exist_ok=True)
    except shutil.Error as e:
        raise ProjectError(_vault_copy_failure_message(src, dst, e)) from e
    except OSError as e:
        raise ProjectError(
            f"Could not carry the vault contents of {src} to {dst}: {e}\n"
            f"The relocation was aborted."
        ) from e


def _vault_copy_failure_message(src: Path, dst: Path, err: shutil.Error) -> str:
    """Name the vault leaf and each entry ``copytree`` could not copy."""
    listing = failed_entries(err)
    if listing is None:
        return f"Could not carry the vault contents of {src} to {dst}: {err}"
    return (
        f"Could not carry the vault contents of {src} to {dst}; {listing}\n"
        f"The relocation was aborted."
    )


def _vault_carry_pairs(
    state: ProjectState,
    std: StandardPaths,
    dst_ro: Path | None,
    dst_rw: Path | None,
) -> list[tuple[Path, Path]]:
    """The ``(source, destination)`` vault pairs whose contents must be carried.

    ⚑ EVERY DESTINATION LEAF IS CREATED EMPTY and the source leaves are then deleted, so
    the contents must move FIRST or the box's store survives nowhere.  A warning is not a
    substitute for the copy, and neither is a refusal: the vault is a STORE that follows
    the project.

    ⚑ MIRRORS the teardown guards in :func:`_remove_old_metadata`: extends their
    risk model to the copy.
    * primary/named: the box's vault is a per-box LEAF strictly under the source
      arm.  A source NOT strictly under its arm is shared or foreign ground the
      teardown refuses to delete (warn-on-skip); the carry refuses it too, with
      its own warning naming where the contents remain.
    * standalone: the box's vault IS the resolved arm, so the
      :func:`standalone_vault_teardown` removable/retained split governs instead —
      removable arms are carried (teardown deletes them); retained arms stay put
      under the teardown's own note and are NOT duplicated into the new box.
    Same-path pairs (reuse-in-place edges, whose teardown is skipped entirely)
    are dropped silently — there is nothing to carry.
    """
    import sys

    if not state.enable_vault:
        # ⚑ No destination vault exists to carry into: every creator gates the
        # leaves on ``enable_vault``.
        return []
    # ⚑ A NULL SOURCE ARM OR A NULL DESTINATION LEAF IS NOTHING TO CARRY: that slot is
    # left ``None`` rather than removed, so the index still lines up with ``arms`` below.
    sides: list[tuple[Path, Path] | None] = [
        None if src is None or dst is None else (src, dst)
        for src, dst in ((state.vault_ro, dst_ro), (state.vault_rw, dst_rw))
    ]
    if state.mode == BoxMode.standalone:
        # ⚑ STANDALONE ANSWERS FROM ITS OWN TEARDOWN SPLIT, so it needs no arm here —
        # skipping on a missing arm would drop every pair and silently carry nothing.
        removable, _retained = standalone_vault_teardown(state.metadata_path, early=_early_scope(std, BoxMode.standalone))
        removable_roots = {p.resolve() for p in removable}
        out: list[tuple[Path, Path]] = []
        for side in sides:
            if side is None:
                continue
            src, dst = side
            if src.resolve() == dst.resolve():
                continue
            if src.resolve() not in removable_roots:
                # ⚑ Retained: the teardown leaves it in place AND prints where, so
                # the new box starts empty there — say so once, and only when
                # something is actually stored.
                if src.is_dir() and _vault_leaf_has_contents(src):
                    print(
                        f"Note: the new vault starts empty — kept the existing "
                        f"store at {src} in place.",
                        file=sys.stderr,
                    )
                continue
            out.append((src, dst))
        return out
    if state.mode == BoxMode.primary:
        arms: tuple[Path | None, Path | None] = (std.primary_vault_ro, std.primary_vault_rw)
    elif state.mode == BoxMode.named and state.ws is not None:
        arms = resolve_workset_vault_pair(state.ws.root, early=state.ws.early_scope)
    else:  # pragma: no cover - defensive: an unknown mode stays hands-off
        return []
    out = []
    for side, arm in zip(sides, arms):
        # ⚑ A NULL ARM HAS NO PER-BOX LEAF TO CARRY INTO.
        if side is None or arm is None:
            continue
        src, dst = side
        if src.resolve() == dst.resolve():
            continue
        # ⚑ STRICT mirror of the teardown guard below: ``relative_to`` ACCEPTS an
        # equal path, so a leafless arm would take the whole shared dir.  Resolved
        # on both sides (the teardown compares as stored); the model — hands off
        # anything not strictly under the arm — is the same.
        if arm.resolve() not in src.resolve().parents:
            # ⚑ The teardown prints its own leaving-in-place warning for the same
            # path; this one says the contents were not carried.
            if src.is_dir() and _vault_leaf_has_contents(src):
                print(
                    f"Warning: not carrying vault contents from {src} — it is not "
                    f"a per-box directory under {arm}; they remain at {src}.",
                    file=sys.stderr,
                )
            continue
        out.append((src, dst))
    return out


def _carry_vault_contents(
    state: ProjectState,
    std: StandardPaths,
    dst_ro: Path | None,
    dst_rw: Path | None,
) -> None:
    """Carry the source vault's contents into the freshly created destination leaves.

    Runs BEFORE the source teardown on every path that relocates the vault; the
    reuse-in-place edges (whose teardown is skipped) collapse to same-path no-ops
    inside.  A copy failure RAISES, aborting before anything is deleted.
    """
    for src, dst in _vault_carry_pairs(state, std, dst_ro, dst_rw):
        _copy_vault_leaf_contents(src, dst)


def _move_log_back(dst: Path, src: Path) -> None:
    """Undo one carried log file. :func:`shutil.move` returns a path; ``_Unwind`` wants ``None``."""
    shutil.move(dst, src)


def _carry_box_logs(
    state: ProjectState,
    std: StandardPaths,
    unwind: _Unwind,
    *,
    dst_logs: Path | None,
    dst_name: str,
) -> None:
    """Carry the source box's log files to *dst_name* in *dst_logs*, each move undoable.

    ⚑ Same bracket as the vault carry: a rolled-back move leaves the logs where they
    were.  Nothing is derived here — :func:`box_log_files` names both sides.
    Hands off when either side has no dir (a present ``<None>`` ``workset.logs``), when
    the two sides name ONE file — every relocation between two worksets sharing a logs
    dir — and when the destination already holds a log, which stays in place beside the
    source.
    """
    import sys

    src_logs = box_logs_dir_for(
        std, state.mode, state.metadata_path,
        state.ws.root if state.ws is not None else None,
        workset_name=state.ws.name if state.ws is not None else None,
    )
    if src_logs is None or dst_logs is None:
        return
    for src, dst in zip(
        box_log_files(src_logs, state.name), box_log_files(dst_logs, dst_name),
    ):
        if not src.is_file() or src.resolve() == dst.resolve():
            continue
        if dst.exists():
            # ⚑ NEVER clobber an existing dest — a live sibling's log, or the residue
            # of a ``box rm`` without purge, is not this box's to replace.
            print(
                f"Warning: not carrying log {src} — the destination {dst} already "
                f"exists; both are left in place.",
                file=sys.stderr,
            )
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        unwind.push(partial(_move_log_back, dst, src))


#: The ``workset.`` keys the two vault arms resolve from, in the ``(ro, rw)`` order
#: every vault consumer takes them.
_VAULT_ARM_KEYS: tuple[str, str] = ("workset.vault_ro", "workset.vault_rw")


#: Why a relocation keeps a disabled vault's stored data in place (Q64).
_DISABLED_VAULT_WHY = "box.enable_vault is false, so nothing received its contents."


def _unreceived_vault_leaves(
    src_arms: tuple[Path | None, Path | None],
    dst_vault: tuple[Path | None, Path | None],
    leaf_name: str = "",
    *,
    vault_enabled: bool = True,
) -> list[tuple[Path, str]]:
    """The source vault leaves the destination receives nothing into, each with why.

    ⚑⚑ A DESTINATION ARM SET TO ``<None>`` GETS NO LEAF (spec §2a, ALL PROJECTS), and a
    box whose ``box.enable_vault`` is false carries nothing at all
    (:func:`_vault_carry_pairs`), so in both cases the source's contents survive nowhere
    if the teardown deletes its leaf.  Those leaves are RETAINED, and this is the one
    place that decides which: a teardown that deleted a side the destination never
    received destroys the box's store, silently, at rc 0.  A disabled vault's EMPTY leaf
    holds nothing to lose and is not retained.

    *src_arms* is the SOURCE's resolved ``(ro, rw)``; *leaf_name* is the per-box leaf
    a named or primary mode nests under each arm (STANDALONE's vault IS its arm).
    """
    if not vault_enabled:
        return [
            (arm / leaf_name, _DISABLED_VAULT_WHY)
            for arm in src_arms
            if arm is not None and _vault_leaf_has_contents(arm / leaf_name)
        ]
    return [
        (arm / leaf_name, f"{key} is null at the destination, so nothing received its contents.")
        for arm, key, leaf in zip(src_arms, _VAULT_ARM_KEYS, dst_vault)
        if arm is not None and leaf is None
    ]


def _report_unreceived_vaults(kept: list[tuple[Path, str]]) -> None:
    """The retained-leaf Note for each :func:`_unreceived_vault_leaves` entry."""
    for leaf, why in kept:
        report_retained_vault(leaf, why)


def _carried_member_store(
    ws: Workset, name: str, dst_vault: tuple[Path | None, Path | None],
    *, vault_enabled: bool = True,
) -> tuple[tuple[Path, ...], list[tuple[Path, str]]]:
    """(*ws*'s store bases the destination received into, its retained vault leaves).

    ⚑ A base whose arm the destination dropped contributes no leaf to carry into,
    so the source's per-box leaf under it is not this retirement's to delete.  The
    retained leaves include :func:`_nulled_arm_stores`.
    """
    boxes_dir, *arms = _member_store_bases(ws)
    src_arms = resolve_workset_vault_pair(ws.root, early=ws.early_scope)
    unreceived = _unreceived_vault_leaves(
        src_arms, dst_vault, name, vault_enabled=vault_enabled,
    )
    held = {leaf.parent for leaf, _why in unreceived}
    bases = tuple(base for base in (boxes_dir, *arms) if base not in held)
    kept = [entry for entry in unreceived if entry[0].exists()]
    return bases, kept + _nulled_arm_stores(ws, name)


def _nulled_arm_stores(ws: Workset, name: str) -> list[tuple[Path, str]]:
    """(*ws*'s NULL ``workset.vault_*`` arms' per-box leaves still on disk, with why.

    ⚑⚑ A NULL ARM NAMES NO DIR, so both :func:`_member_store_bases` (no base to delete
    under) and :func:`_unreceived_vault_leaves` (no arm to name as kept) skip it.  An arm
    nulled AFTER data was stored under it leaves that data on disk, deleted by nothing
    and named by nothing.  Its leaf sits at the key's DEFAULT location, which is where
    the data was while the arm still resolved.
    """
    out: list[tuple[Path, str]] = []
    for key, resolver, arm in zip(
        _VAULT_ARM_KEYS, (resolve_workset_vault_ro, resolve_workset_vault_rw),
        resolve_workset_vault_pair(ws.root, early=ws.early_scope),
    ):
        if arm is not None:
            continue
        default = resolver(ws.root, None, early=ws.early_scope)
        if default is None:
            continue
        leaf = default / name
        if leaf.exists() or leaf.is_symlink():
            out.append(
                (leaf, f"{key} is null, so it no longer names this store and it is "
                       "yours to remove.")
            )
    return out


def _remove_old_metadata(
    state: ProjectState,
    std: StandardPaths,
    config: BootstrapConfig,
    unwind: _Unwind,
    *,
    dst_vault: tuple[Path | None, Path | None],
    preserve_name: str | None = None,
    preserve_root: Path | None = None,
) -> None:
    """Remove the source project's metadata/shell (+ PRIMARY vault), per source mode.

    ⚑ Each mode's reuse signal is its own IDENTITY on disk: *preserve_name* for a primary
    source (whose metadata dir is named after the box), *preserve_root* for a standalone one
    (whose metadata IS a root, and whose NAME is exactly what an in-place rename changes).
    A destination that reused the source must not then have the source torn out from under
    it — for standalone that is the box's home, settings and vault.

    *dst_vault* is the destination's ``(ro, rw)`` leaves: a side it has NO leaf for
    (:func:`_unreceived_vault_leaves`) is retained here, never deleted.
    """
    import sys

    if state.mode == BoxMode.standalone:
        # ⚑⚑ ``metadata_path`` IS the standalone ROOT (drift I). Remove only the kanibako
        # artifacts inside it — deleting the root would wipe the user's whole project dir
        # AND the already-converted destination.
        root = state.metadata_path
        if preserve_root is not None and preserve_root.resolve() == root.resolve():
            # ⚑⚑ The destination IS this root (an in-place standalone rename): ``box_data/``,
            # the root meta and the vault are the box that was just re-established, and the
            # only stale thing left is the OLD NAME's registry entry.
            from kanibako.project import registry_store
            if state.name:
                try:
                    registry_store.unregister_standalone(std.registry, state.name)
                except Exception:  # noqa: BLE001
                    pass
            return
        # ⚑⚑ RESOLVE THE VAULT BEFORE ANYTHING IS DROPPED — including the registry entry.
        # The root ``workset.yaml`` unlinked below IS the standalone workset tier and the
        # only carrier of a ``workset.vault_*`` repoint, so a later read answers the
        # composed default; and an UNRESOLVABLE repoint raises here, which must happen
        # while the source is still whole and the unwind still has something to restore.
        removable_vault, retained_vault = standalone_vault_teardown(root, early=_early_scope(std, BoxMode.standalone))
        # ⚑⚑ A SIDE THE DESTINATION NEVER RECEIVED IS NOT THIS TEARDOWN'S TO DELETE
        # (a standalone box's vault IS its arm, so the retention question is asked of
        # the arm itself).  A parent of a retained arm leaves *removable* with it: the
        # ``vault/`` skeleton parent is on that list, and ``rmtree`` there would take
        # the store down with the dir.
        kept = _unreceived_vault_leaves(
            (state.vault_ro, state.vault_rw), dst_vault, vault_enabled=state.enable_vault,
        )
        if kept:
            held = {arm.resolve() for arm, _key in kept}
            removable_vault = [
                vault for vault in removable_vault
                if not any(vault.resolve() == arm
                           or vault.resolve() in arm.parents
                           for arm in held)
            ]
        # ⚑ Standalone lives in registry.standalone: drop that entry too,
        # or a standalone→standalone move strands the old name → root mapping.
        from kanibako.project import registry_store
        if state.name:
            try:
                registry_store.unregister_standalone(std.registry, state.name)
            except Exception:  # noqa: BLE001
                pass
        # ⚑ Resolved; the strictly-below split and its reason are
        # :func:`standalone_store_teardown_plan`'s.
        removable_store, retained_store = standalone_store_teardown_plan(
            root, early=_early_scope(std, BoxMode.standalone))
        if removable_store is not None:
            # ⚑ Escalating removal: the root-owned canon skeleton makes a bare rmtree
            # fail with EACCES and leave the old box behind (J-7).
            remove_box_tree(removable_store)
        if retained_store is not None:
            report_retained_store(retained_store, root)
        settings = root / WORKSET_META_FILE
        if settings.is_file():
            settings.unlink()
        for vault in removable_vault:
            if vault.is_dir():
                shutil.rmtree(vault, ignore_errors=True)
        report_retained_vaults(root, retained_vault)
        _report_unreceived_vaults(kept)
        return

    if state.mode == BoxMode.primary:
        # ⚑⚑ L2: on a same-name in-place convert the destination metadata/vault IS the
        # source — dropping them here would delete the box just written.
        reused_in_place = preserve_name is not None and state.name == preserve_name
        if state.name and not reused_in_place:
            try:
                unregister_primary_box_name(std.primary_workset, state.name, early=_early_scope(std, BoxMode.primary))
            except Exception:  # noqa: BLE001
                pass
        if reused_in_place:
            return
        if state.metadata_path.is_dir():
            remove_box_tree(state.metadata_path)
        # ⚑⚑ The PRIMARY vault is NOT under metadata_path: it is a per-box ``<name>`` LEAF
        # under the primary workset's RESOLVED ``@workset.{vault_ro,vault_rw}``.  Remove the
        # leaf only — the arm is shared and holds EVERY box's vault.
        # ⚑ The guard's subject is the ARM: naming it admits exactly the leaves this loop
        # may delete, and a root-subject guard would skip an out-of-root repoint and
        # silently leave the box's real vault behind.
        # ⚑ Containment must be STRICT — ``relative_to`` ACCEPTS an equal path, so a
        # leafless ``vault_dir`` would take every box's vault with it.
        # ⚑ Keyed RESOLVED, as the lookup below is: a symlink anywhere in the vault path
        # would otherwise miss here and send an unreceived leaf to the ``rmtree``.
        unreceived = {leaf.resolve(): why for leaf, why in _unreceived_vault_leaves(
            (state.vault_ro, state.vault_rw), dst_vault, vault_enabled=state.enable_vault,
        )}
        for vault_dir, arm in ((state.vault_ro, std.primary_vault_ro),
                               (state.vault_rw, std.primary_vault_rw)):
            if vault_dir is None or arm is None or not vault_dir.is_dir():
                continue
            if arm not in vault_dir.parents:
                # ⚑ Reported, never silent: a skipped vault is data the user still owns
                # and would otherwise never learn was orphaned.
                print(f"Warning: leaving {vault_dir} in place — it is not a per-box "
                      f"directory under {arm}", file=sys.stderr)
                continue
            why = unreceived.get(vault_dir.resolve())
            if why is not None:
                # ⚑ Foreign ground above is not a per-box leaf at all; this one IS, and
                # the destination holds no copy of it.
                report_retained_vault(vault_dir, why)
                continue
            shutil.rmtree(vault_dir, ignore_errors=True)
        if state.shell_path.is_dir() and state.shell_path != state.metadata_path / "home":
            try:
                state.shell_path.relative_to(state.metadata_path)
            except ValueError:
                remove_box_tree(state.shell_path)
        return

    # Workset source: drop the registration now; the store goes only once the op succeeded
    # (a failed removal part-way would otherwise take the box with it).  ⚑ The workspace
    # leaf is NOT deleted here — a relocation retires it on success (``_retire_old_workspace``).
    if state.ws is not None:
        src_ws, src_name, enabled = state.ws, state.name, state.enable_vault
        release_project(src_ws, src_name)
        unwind.on_success(lambda: _retire_old_store(src_ws, src_name, dst_vault, enabled))


def _retire_old_store(
    ws: Workset, name: str, dst_vault: tuple[Path | None, Path | None],
    vault_enabled: bool = True, *, reraise: bool = False,
) -> None:
    """Delete relocated-from member *name*'s store and name each leaf it leaves.

    A failed removal prints a Note naming every leaf left behind; rc is unchanged.
    With *reraise* the failure propagates instead, for a caller whose unwind
    restores the store.
    """
    bases, kept = _carried_member_store(ws, name, dst_vault, vault_enabled=vault_enabled)
    try:
        remove_member_store(ws, name, bases=bases)
    except OSError as err:
        if reraise:
            raise
        _report_store_leftovers(ws, name, err, keep=kept)
    else:
        _report_store_leftovers(ws, name, keep=kept)
    _report_unreceived_vaults(kept)


def _report_store_leftovers(
    ws: Workset, name: str, err: OSError | None = None, *,
    keep: list[tuple[Path, str]] | None = None,
) -> None:
    """Print a Note naming each of member *name*'s store leaves still on disk.

    ⚑ ``remove_member_store`` does not pass on a ``False`` from ``remove_box_tree``, so
    only the disk says whether the box tree (which may hold credentials) is gone.
    ⚑ *keep* holds the leaves this operation RETAINED on purpose; the retained-vault
    Note already accounts for them, and a second Note would call a deliberate keep a
    failure.
    """
    import sys

    spared = {leaf for leaf, _key in keep or ()}
    left = [p for p in _member_leaves(ws, name)[1:]
            if p is not None and p not in spared and (p.exists() or p.is_symlink())]
    if left:
        why = f": {err}" if err is not None else ""
        print(f"Note: could not remove the old store of '{name}'{why}; left "
              f"{', '.join(map(str, left))}", file=sys.stderr)


def _to_default(
    state: ProjectState,
    std: StandardPaths,
    config: BootstrapConfig,
    unwind: _Unwind,
    *,
    new_name: str,
    new_workspace: Path,
    requested_name: str = "",
) -> ProjectState:
    """Convert/relocate the project so its owner becomes the default workset."""
    # ⚑ ORDER: decide the mint BEFORE the reuse-unregister below, or the same-path reuse
    # case stops being detectable (it is detected BY the source's live registration).
    mint = _default_rename_name(state, std, new_workspace, requested_name)
    # ⚑⚑ L2 / FIX1: a PRIMARY source's own name is STILL registered here, so register/assign
    # would read the source's OWN entry as a same-kind collision. Free it first.
    preserved_name: str | None = None
    if state.mode == BoxMode.primary and state.name:
        existing = _primary_name_at(state, std, new_workspace)
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
            std.primary_workset, mint, new_workspace,
            early=_early_scope(std, BoxMode.primary),
        )
        project_name = mint
    else:
        project_name = assign_primary_box_name(
            std.primary_workset, str(new_workspace), early=_early_scope(std, BoxMode.primary),
        )
    unwind.push(lambda: _safe_unregister(std, project_name))
    dst_metadata = std.boxes / project_name

    # ⚑ Copy from the box METADATA DIR, never ``metadata_path``: for a standalone source
    # those differ (root vs ``box_data/``), and the root would drag workspace+vault into
    # the box dir AND land the source's WORKSET-tier file at the dest's BOX tier (M-8).
    src_meta_dir = box_metadata_dir(state.mode, state.metadata_path,
                                    early=EarlyScope(std.early_system, _state_ws_token(state)))
    # Name reused in place ⇒ the metadata IS already at the destination; copying it
    # would be a failing copy-onto-self.
    if dst_metadata.resolve() == state.metadata_path.resolve():
        dst_shell = state.shell_path
    else:
        dst_shell = _copy_metadata(
            src_meta_dir, state.shell_path, dst_metadata,
            shell_into_metadata=True, unwind=unwind,
        )
        _deliver_carried_box_settings(
            state, dst_metadata / BOX_META_FILE,
            early=EarlyScope(std.early_system, _state_ws_token(state)))

    # Phase 5 fixed PRIMARY table: vault under @config.primary_workset.
    _dst_shell, vault_ro, vault_rw = _primary_box_paths(
        std, dst_metadata, project_name,
    )

    # ⚑ SPARSE (P8b): NO ``project:``/``resolved:`` identity is written — identity lives in
    # the PRIMARY ``boxes:`` membership registered above. Only the non-default
    # ``box.enable_vault`` is persisted, carried so a disabled-vault box stays disabled.
    # ⚑⚑ The BOX-AUTHORED value, never the resolved one: a workset-tier default belongs to
    # the workset and must keep resolving from there (``carried_box_settings``).
    write_box_enable_vault(dst_metadata / BOX_META_FILE, state.box_authored_vault)

    if state.enable_vault:
        # ⚡ A NULL ARM GETS NO LEAF AND NO UNWIND — nothing was made under it.
        for leaf in (vault_ro, vault_rw):
            if leaf is None:
                continue
            leaf.mkdir(parents=True, exist_ok=True)
            unwind.push(partial(shutil.rmtree, leaf, ignore_errors=True))

    # ⚑ THE VAULT CARRY (P1 data loss): the leaves above are created EMPTY and
    # ``_remove_old_metadata`` below deletes the source — contents move first.
    _carry_vault_contents(state, std, vault_ro, vault_rw)
    _carry_box_logs(state, std, unwind, dst_logs=std.primary_logs, dst_name=project_name)

    _remove_old_metadata(
        state, std, config, unwind, dst_vault=(vault_ro, vault_rw),
        preserve_name=preserved_name,
    )

    return ProjectState(
        owner="primary", mode=BoxMode.primary, name=project_name,
        workspace_path=new_workspace, metadata_path=dst_metadata,
        shell_path=dst_shell, vault_ro=vault_ro, vault_rw=vault_rw,
        is_external=False, ws=None,
        enable_vault=state.enable_vault,
        box_authored_vault=state.box_authored_vault,
    )


#: What a standalone root keeps whatever ``workset.*`` says: the ``workset.boxes`` DEFAULT
#: leaf ``box_data/`` (NOT a detection marker — detection reads the root's own
#: ``workset.yaml`` registry null), plus the workset meta, the legacy root box tier (drift
#: I) and the lock.  They STAY at the root when a convert consolidates the rest (drift H).
#: ⚑⚑ EVERY OTHER ARTIFACT AT THE ROOT IS A DECLARED, REPOINTABLE ``workset.*`` DIRECTORY
#: KEY AND IS ANSWERED BY :func:`_standalone_root_artifacts`, NEVER BY A NAME.  A leaf name
#: cannot express ``workset.vault_ro: store/ro`` — the root child is then ``store``, a name
#: no list holds — and cannot express an absolute repoint at all.
_STANDALONE_FIXED_ARTIFACTS = frozenset({
    STANDALONE_META_DIR,   # box_data/ — the ``workset.boxes`` DEFAULT leaf, not a locator
    WORKSET_META_FILE,      # the workset meta (drift I — at the root)
    BOX_META_FILE,          # the box meta (drift I — at the root)
    ".kanibako.lock",       # lock file
})


def _resolve_standalone_workspaces(
    root: Path, doc: Mapping[str, Any] | None, *, early: EarlyScope,
) -> Path:
    """``workset.workspaces`` for a STANDALONE root — the SINGULAR ``workspace`` default.

    Only a convert INTO the root reaches here, and it refused a nulling root first; the
    refusal below keeps a null from ever reading as the default.
    """
    workspaces = resolve_workset_workspaces(root, doc, standalone=True, early=early)
    if workspaces is None:
        refuse_null_workspaces(root, f"a workspace for '{root.name}'", standalone=True,
                               early=early)
    assert workspaces is not None  # refused on the line above
    return workspaces


def _resolve_standalone_boxes(
    root: Path, doc: Mapping[str, Any] | None, *, early: EarlyScope,
) -> Path:
    """``workset.boxes`` for a STANDALONE root — the resolver ``standalone_box_store`` reads
    the key through, so the sweep and the box agree on where the store is."""
    return resolve_workset_boxes(root, doc, standalone=True, early=early)


#: The ``workset.*`` DIRECTORY keys a STANDALONE root materializes UNDER ITSELF, each
#: paired with its resolver — a standalone root is a degenerate workset root
#: (``_standalone_box_paths``), so these are ordinary workset keys, and
#: ``logs``/``template``/``channelroot`` are absent because standalone makes none.
#: ⚑ ``boxes`` is HERE, not only in :data:`_STANDALONE_FIXED_ARTIFACTS`: that set holds a
#: LEAF NAME no key moves, while the STORE answers to ``workset.boxes`` wherever the user
#: put it — a leaf-name filter swept a repointed store into the workspace dir.
_STANDALONE_ROOT_DIR_KEYS = (
    ("workset.workspaces", _resolve_standalone_workspaces),
    ("workset.boxes", _resolve_standalone_boxes),
    ("workset.vault_ro", resolve_workset_vault_ro),
    ("workset.vault_rw", resolve_workset_vault_rw),
    ("workset.canon", resolve_workset_canon),
)


def _standalone_root_artifacts(
    root: Path, *, early: EarlyScope,
) -> list[tuple[str, Path, bool]]:
    """The RESOLVED ``(key, path, repointed)`` directories a standalone *root* owns.

    ⚑⚑ RESOLVED, NEVER A LEAF NAME: a basename filter cannot tell a repointed
    ``workset.vault_ro: store/ro`` from the user's own ``store/``, and would sweep it into
    the workspace dir.  Every key the root resolves is listed, ``workset.canon`` included.

    *repointed* says the resolved path DIFFERS from that key's default leaf, i.e. the user put
    it there.  :func:`_consolidate_workspace_subdir` reports those keeps by name ([R144]: a
    keep that cannot name the path as the user's is just a leak); the default layout keeps
    stay silent, as they always have.

    ⚑ ONE ``workset.yaml`` read feeds every resolution — reading per key opens a window for
    them to disagree about the same document (``_workset_skeleton_dirs``' own reason).

    ⚑ RAISES (``SettingsError``, naming the key and the token) when a repoint will not resolve.
    A root whose layout keys do not answer is a root whose children cannot be told apart, and
    the sweep below MOVES USER DATA.  Refusing costs nothing here: this runs before
    :func:`_to_standalone` has copied or written anything.  Same call order, and for the same
    reason, as ``project/workset.py::standalone_vault_teardown``.
    """
    doc = load_workset_settings_doc(root)
    out: list[tuple[str, Path, bool]] = []
    for key, resolver in _STANDALONE_ROOT_DIR_KEYS:
        resolved = resolver(root, doc, early=early)
        if resolved is None:  # a null key names no dir, so it owns no artifact
            continue
        out.append((key, resolved, resolved != resolver(root, None, early=early)))
    # ⚑ The literal ``vault/`` skeleton parent, on disk only — exactly the tail
    # ``standalone_vault_teardown`` appends, and for its reason: no key names it, the default
    # layout's ``.gitignore`` lives there, and ``_to_standalone`` writes that file itself.
    skeleton = root / bootstrap.VAULT_PATH
    if skeleton.is_dir():
        out.append(("the vault skeleton", skeleton, False))
    return out


def _artifact_claiming(
    child: Path, artifacts: list[tuple[str, Path, bool]],
) -> tuple[str, Path, bool] | None:
    """The artifact *child* IS or CONTAINS, else ``None`` — the ANCESTOR test is the point.

    ⚑ A repoint one level down (``vault_ro: store/ro``) makes the root child ``store``, which
    is not the resolved path but holds it.  An equality-only test keeps ``store/ro`` — a path
    that is not a child of *root* and so was never a candidate — and sweeps ``store``.
    """
    for artifact in artifacts:
        _key, path, _repointed = artifact
        if path == child or child in path.parents:
            return artifact
    return None


def _consolidate_workspace_subdir(
    root: Path,
    workspace_subdir: Path,
    unwind: _Unwind,
    *,
    early: EarlyScope,
) -> None:
    """Move the project's top-level files into the standalone workspace dir (drift H)."""
    import sys

    if not root.is_dir():
        return
    # ⚑ A ``workset.workspaces`` that resolves AT the root means the workspace already IS the
    # root: there is nothing to consolidate, and moving each child onto itself would fail.
    if workspace_subdir.resolve() == root.resolve():
        return

    artifacts = _standalone_root_artifacts(root, early=early)
    movable: list[Path] = []
    for child in root.iterdir():
        if child.name in _STANDALONE_FIXED_ARTIFACTS:
            continue
        claim = _artifact_claiming(child, artifacts)
        if claim is None:
            movable.append(child)
            continue
        key, _path, repointed = claim
        if repointed:
            # ⚑ [R144]: the user put it there, so the keep is REPORTED and names the key —
            # otherwise a directory sitting outside the workspace after a convert that says it
            # consolidated everything is unexplained.  Default-layout keeps stay silent.
            print(f"Note: left {child} at the standalone root — {key} resolves "
                  f"inside it.", file=sys.stderr)
    if not movable:
        return

    workspace_subdir.mkdir(parents=True, exist_ok=True)
    unwind.push(lambda: _undo_consolidate(workspace_subdir, root, movable))
    for child in movable:
        shutil.move(str(child), str(workspace_subdir / child.name))


def _undo_consolidate(
    src_dir: Path, dest_dir: Path, moved: list[Path],
) -> None:
    """Move *moved*'s leaves back from *src_dir* to *dest_dir* — the reversal of either sweep.

    ⚑ *dest_dir* is RE-CREATED first: the unconsolidate direction REMOVES it once emptied
    (with any repoint parents), so a restore would otherwise have nowhere to land and every
    move would fail silently.
    """
    if not moved:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for child in moved:
        src = src_dir / child.name
        if src.exists():
            try:
                shutil.move(str(src), str(dest_dir / child.name))
            except OSError:
                pass


def _unconsolidate_workspace_subdir(
    workspace_subdir: Path,
    root: Path,
    unwind: _Unwind,
) -> None:
    """Lift the standalone workspace dir's contents back up to *root* (inverse of consolidate).

    ⚑ *root* is the standalone ROOT as ``ProjectState`` carries it, never a parent counted
    off *workspace_subdir*: the workspace is the RESOLVED ``workset.workspaces`` and a
    repoint moves it any distance from the root, or out of it entirely.
    """
    if not workspace_subdir.is_dir():
        return
    movable = list(workspace_subdir.iterdir())
    moved: list[Path] = []
    unwind.push(lambda: _undo_consolidate(root, workspace_subdir, moved))
    for child in movable:
        shutil.move(str(child), str(root / child.name))
        moved.append(child)
    # Drop the emptied workspace dir so the converted project keeps no stray one — and with
    # it the now-empty directories a repoint interposed (``workspaces: nested/deep`` leaves
    # ``nested/``, which only ever existed to hold the workspace and is meaningless once the
    # root ``workset.yaml`` that named it is gone).
    # ⚑ ``rmdir`` IS the emptiness test: a parent the user keeps their own files in refuses
    # to go, and the walk stops at the first one that does.
    stale = workspace_subdir
    while stale != root and root in stale.parents:
        try:
            stale.rmdir()
        except OSError:
            break
        stale = stale.parent


def _to_standalone(
    state: ProjectState,
    std: StandardPaths,
    config: BootstrapConfig,
    unwind: _Unwind,
    *,
    new_name: str,
    root: Path,
    requested_name: str = "",
) -> ProjectState:
    """Convert/relocate the project so it becomes standalone (in-tree metadata).

    ⚑⚑ *root* is the standalone ROOT, not the live workspace — this is the ONE target mode
    where the two differ (drift H), and the caller's ``new_workspace`` is therefore the
    root here.  Passing the workspace instead laid a whole second box inside the first.
    """
    from kanibako.project import registry_store
    from kanibako.settings.paths import establish_standalone

    # ⚑ ORDER: consolidate the source's top-level files into the workspace dir FIRST, THEN
    # lay down the kanibako artifacts — otherwise the artifacts get swept in with them.
    root.mkdir(parents=True, exist_ok=True)
    dst_metadata = standalone_box_store(root, early=_early_scope(std, BoxMode.standalone))
    # ⚑ RESOLVED, and it MUST agree with ``resolve_standalone_project``, which reads the same
    # key to answer ``project_path``.  A literal ``root / "workspace"`` is the right answer
    # only until the root carries a ``workset.workspaces`` repoint, and then it fills a
    # directory the box never looks in — two answers for one box.
    workspace_subdir = _resolve_standalone_workspaces(
        root, load_workset_settings_doc(root), early=_early_scope(std, BoxMode.standalone),
    )
    # ⚑ The box METADATA DIR (``box_data/`` for a standalone source) — the ROOT would
    # strand ``<dst>/box_data/box_data/`` on a standalone→standalone move.
    src_meta_dir = box_metadata_dir(state.mode, state.metadata_path,
                                    early=EarlyScope(std.early_system, _state_ws_token(state)))
    # ⚑⚑ REUSED IN PLACE — a standalone box renamed AT ITS OWN ROOT.  Everything below
    # that treats *root* as freshly converted is then wrong twice over.
    reused_in_place = dst_metadata.resolve() == src_meta_dir.resolve()
    if not reused_in_place:
        # ⚑ The sweep exists because the source's project files sit AT the root on an
        # in-place convert.  A root that is ALREADY this box's standalone root does NOT:
        # its files are in the workspace dir and everything left beside them is
        # kanibako's own — starting with the root ``.gitignore`` written below, which
        # the sweep would relocate into the user's workspace.
        _consolidate_workspace_subdir(root, workspace_subdir, unwind, early=_early_scope(std, BoxMode.standalone))
        _copy_metadata(
            src_meta_dir, state.shell_path,
            dst_metadata, shell_into_metadata=True, home_leaf="home", unwind=unwind,
        )
        # ⚑⚑ DO NOT DELETE this file as an "orphan": the box.yaml landing in ``box_data/``
        # IS the destination's BOX TIER (spec §2c — @meta.box.path for standalone IS
        # ``box_data/``). Deleting it discards the box's settings; detection reads the ROOT
        # file (§5), which ``establish_standalone`` writes below.
        _deliver_carried_box_settings(
            state, dst_metadata / BOX_META_FILE,
            early=EarlyScope(std.early_system, _state_ws_token(state)))

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
    # ⚑ The RESOLVED ``workset.vault_rw`` gates this, never the skeleton's existence: the
    # skeleton is the ``ro`` arm's DEFAULT parent, so ``<root>/vault`` can sit on disk while
    # ``vault_rw`` points elsewhere — and then the file's ``rw/`` claims nothing that is
    # there.  ``establish_standalone`` resolved the arm against this root's ``workset.yaml``,
    # repoint included, which is why its return value is what gets passed.
    if vault_rw is not None:  # a null arm has no skeleton to describe
        write_vault_gitignore(root, vault_rw)

    # ⚑ THE VAULT CARRY (P1 data loss) — see ``_to_default``: the destination
    # vault is fresh and the teardown below deletes the source, so contents
    # move first.  A reuse-in-place rename collapses to a same-path no-op.
    _carry_vault_contents(state, std, vault_ro, vault_rw)
    _carry_box_logs(
        state, std, unwind, dst_logs=standalone_logs_dir(root, early=_early_scope(std, BoxMode.standalone)),
        dst_name=box_name,
    )

    _remove_old_metadata(
        state, std, config, unwind, dst_vault=(vault_ro, vault_rw),
        preserve_root=root if reused_in_place else None,
    )

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
    config: BootstrapConfig,
    unwind: _Unwind,
    *,
    target_ws: Workset,
    new_name: str,
    new_workspace: Path,
    relocating: bool,
    dest: Path | None,
) -> ProjectState:
    """Convert/relocate the project into *target_ws* (std-aware external wiring)."""
    internal = is_in_tree_workspace(target_ws, new_workspace)

    # The path add_project records and decides external wiring from.
    source_for_add = new_workspace

    # ⚑ Copy only for an internal landing NOT already in place — STEP 2 may have moved the
    # tree to ``workspaces/<name>`` already. External never copies.
    copy_workspace = False
    in_tree_leaf: Path | None = None
    if internal:
        in_tree_leaf = target_ws.require_workspaces_dir(f"a workspace for '{new_name}'") / new_name
        already_in_place = new_workspace.resolve() == in_tree_leaf.resolve()
        copy_workspace = not already_in_place

    # ⚑ The box METADATA DIR, not ``metadata_path``: for a standalone source those differ
    # (root vs ``box_data/``) — see :func:`box_metadata_dir` (M-8).
    metadata_source = box_metadata_dir(state.mode, state.metadata_path,
                                        early=EarlyScope(std.early_system, _state_ws_token(state)))
    shell_source = state.shell_path

    source_is_workset = state.mode == BoxMode.named
    # ⚑ THE DESTINATION'S OWN LEAVES, RESOLVED ONCE: ``add_project`` below creates them
    # under these arms, the returned state must spell them the same way, and the
    # source-release leg needs them to know which of the SOURCE's vault leaves it
    # received.
    _dst_shell, vault_ro, vault_rw = _workset_box_paths(
        target_ws.projects_dir / new_name,
        *resolve_workset_vault_pair(target_ws.root, early=target_ws.early_scope),
        new_name,
    )
    dst_vault = (vault_ro, vault_rw)
    if source_is_workset and state.ws is not None:
        src_ws = state.ws
        src_name = state.name
        src_source_path = state.workspace_path
        # ⚑⚑ ws->ws: the SOURCE must release BEFORE the target registers (the connection
        # record is 1:1). Release DELETES the source STORE (never its workspace leaf), so
        # stash it first — the forward copy below and the unwind both read the stash.
        import tempfile
        stash = Path(tempfile.mkdtemp(prefix="kanibako-unwind-"))
        stash_boxes = stash / "boxes"
        # ⚑ THE VAULT CARRY, leg 1 of 2: ``remove_member_store`` below deletes the source
        # vault leaves, while the destination leaves only exist after ``add_project`` — so
        # the contents wait out the swap in the stash beside the metadata.
        stash_vault_ro = stash / "vault_ro"
        stash_vault_rw = stash / "vault_rw"
        try:
            if state.metadata_path.is_dir():
                copy_tree_keeping_links(
                    state.metadata_path, stash_boxes,
                    ignore=shutil.ignore_patterns(".kanibako.lock"),
                    dirs_exist_ok=True,
                )
            for _src, _tmp in _vault_carry_pairs(
                state, std, stash_vault_ro, stash_vault_rw,
            ):
                _copy_vault_leaf_contents(_src, _tmp)
        except BaseException:
            # Nothing is released yet: the source is whole and the stash a partial copy.
            _dispose_stash(stash)
            raise
        metadata_source = stash_boxes
        shell_source = stash_boxes / "home"

        def _restore_source() -> None:
            # ⚑ Each step runs even when an earlier one failed, and reports its own
            # failure: ``_Unwind.run`` swallows whatever an action raises.
            import sys

            def _box_tree() -> None:
                if stash_boxes.is_dir():
                    copy_tree_keeping_links(
                        stash_boxes, src_ws.projects_dir / src_name, dirs_exist_ok=True)

            steps: list[tuple[str, Path | None, Callable[[], object]]] = [
                (f"the record of '{src_name}' in workset '{src_ws.name}'",
                 src_source_path,
                 lambda: add_project(src_ws, src_name, src_source_path, std, restoring=True)),
                ("the box tree", src_ws.projects_dir / src_name, _box_tree),
                # ⚑ THE VAULT CARRY, unwind leg (P1 data loss): leg 1 released the source
                # leaves into the stash; no-ops when leg 1 carried nothing.
                ("the read-only vault", state.vault_ro,
                 lambda: _copy_vault_leaf_contents(stash_vault_ro, state.vault_ro)),
                ("the read-write vault", state.vault_rw,
                 lambda: _copy_vault_leaf_contents(stash_vault_rw, state.vault_rw)),
            ]
            clean = True
            for what, where, step in steps:
                try:
                    step()
                except Exception as err:  # noqa: BLE001 - reported, and the rest still run
                    clean = False
                    print(f"Note: could not restore {what} at {where}: {err}",
                          file=sys.stderr)
            if clean:
                _dispose_stash(stash)
            else:
                print(f"Note: kept {stash}; it holds the box's store as it was before "
                      f"the move (it may hold credentials)", file=sys.stderr)

        # ⚑⚑ Pushed BEFORE the release (L1): once it starts, the stash holds the only copy
        # of the store, so a failure part-way through must restore from it, never drop it.
        unwind.push(_restore_source)
        release_project(src_ws, src_name)
        _retire_old_store(
            src_ws, src_name, dst_vault, state.enable_vault, reraise=True,
        )
        # Discard the stash on success (kept intact while unwind may need it).
        unwind.on_success(lambda: _dispose_stash(stash))

    # ⚑ ``force=True``: an absorb INTO a workset must override the standalone-marker
    # connect guard (B2a) — a standalone source still carries its root ``workset.yaml`` marker
    # here, since the marker is removed LATER in the convert. No-op for other modes.
    # ⚑ The unwind deletes a target leaf only when THIS op created it — ``add_project``
    # adopts an existing one, and on a same-workset, same-name relocation the workspace
    # leaf is the source's own, intact workspace.
    existed = _existing_member_leaves(target_ws, new_name)
    add_project(target_ws, new_name, source_for_add, std, force=True)
    unwind.push(lambda: _unwind_target_member(target_ws, new_name, existed))

    dst_project = target_ws.projects_dir / new_name
    # Copy metadata (minus lock+home) into the workset boxes dir.
    copy_tree_keeping_links(
        metadata_source, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home"),
        dirs_exist_ok=True,
    )
    # ⚑ The copy above cannot supply a STANDALONE source's box settings — its box tier is
    # a different file from the root one a pre-P2 box stored them in (M-8).
    _deliver_carried_box_settings(
        state, dst_project / BOX_META_FILE, early=EarlyScope(std.early_system, _state_ws_token(state)))
    dst_shell = dst_project / "home"
    if shell_source.is_dir():
        copy_tree_keeping_links(shell_source, dst_shell, dirs_exist_ok=True)
        # ⚑ The copy carries the canon skeleton's MODES but not its OWNERSHIP (J-7).
        materialize_canon_skeleton(dst_shell)

    if (copy_workspace and in_tree_leaf is not None):
        dst_workspace = in_tree_leaf
        ignore = None
        if state.mode == BoxMode.standalone:
            ignore = _workspace_copy_ignore(
                state.metadata_path, state.workspace_path, mode=BoxMode.standalone,
                early=EarlyScope(std.early_system, _state_ws_token(state)))
        copy_tree_keeping_links(
            state.workspace_path, dst_workspace, ignore=ignore, dirs_exist_ok=True,
        )
        # ⚑ An in-tree workset source's leaf is retired on success, like step 2's copy;
        # other sources keep their tree (an in-place convert deletes nothing).
        if source_is_workset and not state.is_external:
            old_leaf = state.workspace_path
            unwind.on_success(lambda: _retire_old_workspace(old_leaf, dst_workspace))

    # Determine the recorded workspace.
    if in_tree_leaf is not None:
        recorded_workspace = in_tree_leaf
    else:
        # ⚑ EXTERNAL: read the workspace back from the per-workset ``boxes:`` registry
        # add_project just wrote — under sparse create (P8b) the box's box.yaml no
        # longer self-describes, so the D10 connection record is the authority.
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        registry_path = workset_registry.resolve_workset_registry_path(
            target_ws.root, load_doc(target_ws.root / WORKSET_META_FILE),
            early=target_ws.early_scope,
        )
        # ⚑ THE membership accessor, not a second spelling of it: it already compares
        # case-blind (spec §0) and returns the path under the stored key.
        recorded_str = workset_registry.workset_box_path(registry_path, new_name)
        recorded_workspace = (
            Path(recorded_str) if recorded_str else new_workspace
        )
    # ⚑ SPARSE (P8b): NO ``project:``/``resolved:`` identity is written — identity lives in
    # the global name index, workspace in the target workset's ``boxes:`` registry. Only
    # the non-default ``box.enable_vault`` is carried, so a disabled box stays disabled.
    # ⚑⚑ The BOX-AUTHORED value, never the resolved one: the SOURCE workset's default must
    # not follow the box into a DIFFERENT workset as a box-scope override.
    write_box_enable_vault(dst_project / BOX_META_FILE, state.box_authored_vault)

    # ⚑ A workset source ALREADY released above — cleaning up again would double-remove.
    if source_is_workset and state.ws is not None:
        # ⚑ THE VAULT CARRY, leg 2 of 2 (P1 data loss): the destination leaves
        # exist now — land the stashed contents.  No-ops when leg 1 carried nothing.
        # ⚑ The guard MIRRORS leg 1's guard (the *VAULT CARRY, leg 1 of 2* block
        # above): the stash vars are bound only under it, so a named box with no
        # workset (a corrupt state refused elsewhere) must not reach this line.
        _copy_vault_leaf_contents(stash_vault_ro, vault_ro)
        _copy_vault_leaf_contents(stash_vault_rw, vault_rw)
    # ⚑ THE LOG CARRY, beside the vault carry — the destination is registered either way
    # by now, and a released source keeps its logs (``remove_member_store`` deletes the
    # box tree and the vault leaves, never the logs).
    _carry_box_logs(
        state, std, unwind,
        dst_logs=box_logs_dir_for(std, BoxMode.named, dst_project, target_ws.root,
                                  workset_name=target_ws.name),
        dst_name=new_name,
    )
    if not source_is_workset:
        # ⚑ THE VAULT CARRY (P1 data loss) — see ``_to_default``: contents move
        # before the teardown below deletes the source.
        _carry_vault_contents(state, std, vault_ro, vault_rw)
        _remove_old_metadata(state, std, config, unwind, dst_vault=dst_vault)

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
        if src_dir is None or dst_dir is None:
            # ⚑ A NULL PARTITION KEY IS AN ADDRESS THAT DOES NOT EXIST — the same fact as
            # the ``is_dir()`` arm below, so it takes the same way out.  ⛔ NOT IN MY
            # GRANT; named in the report (a reader my first-round census did not reach).
            continue
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
        unregister_primary_box_name(std.primary_workset, name, early=_early_scope(std, BoxMode.primary))
    except Exception:  # noqa: BLE001
        pass


def _safe_register_membership(
    std: StandardPaths, name: str, workspace: Path,
) -> None:
    """Best-effort re-register *name* -> *workspace* in the PRIMARY membership (FIX1)."""
    try:
        _register_workset_box_membership(std.primary_workset, name, workspace, early=_early_scope(std, BoxMode.primary))
    except Exception:  # noqa: BLE001
        pass


def _member_leaves(ws: Workset, name: str) -> tuple[Path | None, Path, Path | None, Path | None]:
    """Member *name*'s four leaves: workspace, box tree, read-only and read-write vault.

    ⚑ The vault arms are RESOLVED and the per-box leaves composed by the one NAMED-mode
    accessor, as :func:`add_project` creates them.  The workspace leaf is ``None`` under
    a null ``workset.workspaces`` — there is no ``workspaces/<name>``.
    """
    box_tree = ws.projects_dir / name
    _shell, vault_ro, vault_rw = _workset_box_paths(
        box_tree, *resolve_workset_vault_pair(ws.root, early=ws.early_scope), name,
    )
    workspaces = ws.workspaces_dir
    return (workspaces / name if workspaces is not None else None, box_tree,
            vault_ro, vault_rw)


def _existing_member_leaves(ws: Workset, name: str) -> frozenset[Path]:
    """Those of :func:`_member_leaves` already on disk (a dangling link counts)."""
    return frozenset(
        p for p in _member_leaves(ws, name)
        if p is not None and (p.exists() or p.is_symlink())
    )


def _unwind_target_member(ws: Workset, name: str, existed: frozenset[Path]) -> None:
    """Undo a target registration: the record, plus each leaf NOT in *existed*.

    ⚑⚑ A leaf that existed before the op is the user's and is never touched.  A link is
    unlinked, never followed; the box tree goes through ``remove_box_tree``.  Whatever
    cannot be removed is reported here, because ``_Unwind.run`` swallows errors.
    """
    import sys

    try:
        release_project(ws, name, keep_link=True)
    except Exception as err:  # noqa: BLE001 - reported; the leaves below still go
        print(f"Note: could not drop the record of '{name}' from workset "
              f"'{ws.name}': {err}", file=sys.stderr)
    workspace, box_tree, vault_ro, vault_rw = _member_leaves(ws, name)
    for leaf in (workspace, box_tree, vault_ro, vault_rw):
        if leaf is None or leaf in existed:
            continue
        try:
            if leaf.is_symlink():
                leaf.unlink()
            elif leaf == box_tree and leaf.is_dir():
                remove_box_tree(leaf)
            elif leaf.is_dir():
                shutil.rmtree(leaf)
        except OSError:
            pass  # reported just below
        if leaf.exists() or leaf.is_symlink():
            print(f"Note: could not remove {leaf}, which this operation created",
                  file=sys.stderr)


def _dispose_stash(stash: Path) -> None:
    """Delete relocation stash *stash* through the box-tree deleter; report a leftover.

    ⚑ The stash holds a copy of the box home, so a leftover may hold credentials.
    """
    import sys

    if (stash.exists() or stash.is_symlink()) and not remove_box_tree(stash):
        print(f"Note: could not remove {stash}; it may hold credentials", file=sys.stderr)


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


def _validated_name(args) -> str | None:
    """Return the user's ``--name`` validated AS TYPED (spec §0), or ``None``."""
    name = getattr(args, "name", None)
    if not name:
        return name
    validate_box_name(name)
    return name


def _make_confirm(force: bool, summary: str):
    """Return a ``Callable[[], bool]`` for ``execute_lifecycle``'s *confirm* (None if forced)."""
    if force:
        return None

    from kanibako.errors import UserCanceled
    from kanibako.utils import confirm_prompt

    def _confirm() -> bool:
        print(summary)
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except UserCanceled:
            return False
        return True

    return _confirm


def _load_env():
    from kanibako.settings.config import user_config_file, load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(user_config_file())
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


def _relocation_failure(err: OSError) -> str:
    """The ``Error:`` line for an ``OSError`` (``shutil.Error`` included) that escaped a relocation.

    A ``shutil.Error`` from a tree copy names each entry it could not copy, as ``_duplicate`` does.
    """
    if isinstance(err, shutil.Error):
        listing = failed_entries(err)
        if listing is not None:
            return f"Error: the relocation failed; {listing}"
    return f"Error: the relocation failed: {err}"


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
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
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
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
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
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
        return 1

    if _abort_if_locked(state, getattr(args, "force", False)):
        return 2

    ownership = _ownership_from_args(args)
    summary = (
        "Move project workspace:\n"
        f"  project: {state.name}\n"
        f"     from: {state.workspace_path}\n"
        f"       to: {new_path}"
    )
    try:
        spec = TargetSpec(
            location=new_path, ownership=ownership, name=_validated_name(args),
            verb="move",
        )
        new_state = execute_lifecycle(
            state, spec, std, config,
            force=getattr(args, "force", False),
            confirm=_make_confirm(getattr(args, "force", False), summary),
        )
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
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
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
        return 1

    # Lock pre-flight: convert re-roots by copy-then-rmtree (mirrors move / duplicate).
    if _abort_if_locked(state, getattr(args, "force", False)):
        return 2

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
        spec = TargetSpec(
            location=location, ownership=ownership, name=_validated_name(args),
            verb="convert",
        )
        new_state = execute_lifecycle(
            state, spec, std, config,
            force=getattr(args, "force", False),
            confirm=_make_confirm(getattr(args, "force", False), summary),
        )
    except (ProjectError, WorksetError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(_relocation_failure(e), file=sys.stderr)
        return 1

    print(
        f"Converted '{new_state.name}' to {new_state.owner} "
        f"({new_state.workspace_path})"
    )
    return 0


#: argparse ``const`` sentinel for a bare ``--move`` (no path argument).
_BARE_MOVE = _Sentinel("BARE_MOVE")
