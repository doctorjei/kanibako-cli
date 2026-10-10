# `src/kanibako/commands/box/_lifecycle.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/box/_lifecycle.py.md`.


## Variables

```
INPLACE = _Sentinel('INPLACE')
BARE_INTO_WS = _Sentinel('BARE_INTO_WS')
UNCHANGED = _Sentinel('UNCHANGED')
STUBBORN_INPLACE_MSG = 'Stubbornly refusing to convert in-place from within a workset; add `--move` or `--move <path>` to relocate.'
_VAULT_ARM_KEYS: tuple[str, str] = ('workset.vault_ro', 'workset.vault_rw')
_DISABLED_VAULT_WHY = 'box.enable_vault is false, so nothing received its contents.'
_STANDALONE_FIXED_ARTIFACTS = frozenset({STANDALONE_META_DIR, WORKSET_META_FILE, BOX_META_FILE, '.kanibako.lock'})
_STANDALONE_ROOT_DIR_KEYS = (('workset.workspaces', _resolve_standalone_workspaces), ('workset.boxes', _resolve_standalone_boxes), ('workset.vault_ro', resolve_workset_vault_ro), ('workset.vault_rw', resolve_workset_vault_rw), ('workset.canon', resolve_workset_canon))
_BARE_MOVE = _Sentinel('BARE_MOVE')
```

## Functions
```
def owner_token(mode: BoxMode, ws_name: str | None=None) -> str
def resolve_lifecycle_target(old: str | None, std: StandardPaths, config: BootstrapConfig | None=None) -> ProjectState
def recorded_workspace_for(ws: '_WorksetLike', box_name: str, resolved: Path | None) -> Path | None
def box_bind_sources_or_none(std: StandardPaths, proj: ProjectPaths) -> frozenset[str] | None
def plan_box_mounted_links(name: str, bind_sources: frozenset[str] | None, trees: Collection[Path | None]) -> list[MountedLink]
def repoint_box_mounted_links(links: list[MountedLink], relocated: Mapping[Path, Path], landed: ProjectState) -> None
def copy_into_workset(ws: Workset, proj_name: str, metadata_path: Path, shell_path: Path, source_path: Path, source_mode: BoxMode, *, copy_workspace: bool, std: StandardPaths) -> None
def execute_lifecycle(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig | None=None, *, force: bool=False, confirm: Callable[[], bool] | None=None) -> ProjectState
def note_added_leftovers(path: Path, before: frozenset[str] | None) -> None
def run_remap(args) -> int
def run_move(args) -> int
def run_convert(args) -> int
def _same_box_name(left: str | None, right: str | None) -> bool
def _default_rename_name(state: ProjectState, std: StandardPaths, landing_ws: Path, requested_name: str) -> str | None
def _primary_name_at(state: ProjectState, std: StandardPaths, landing_ws: Path) -> str | None
def _primary_source_own_name(state: ProjectState, std: StandardPaths) -> str | None
def _relocated_own_name(state: ProjectState, std: StandardPaths, landing_ws: Path, mint: str | None) -> str | None
def _workspace_landing(state: ProjectState, dest: Path | None, target_mode: BoxMode, *, records_only: bool) -> Path
def _ownership_to_mode(ownership: str) -> tuple[BoxMode, str | None]
def _workset_records_member_at(resolved: Path, std: StandardPaths) -> bool
def _resolve_primary_state(root: Path, std: StandardPaths, config: BootstrapConfig) -> ProjectState
def _default_state_from_meta(workspace: Path, std: StandardPaths) -> ProjectState | None
def _resolve_workset_state(raw_path: Path, std: StandardPaths, config: BootstrapConfig) -> ProjectState
def _state_from_paths(owner: str, proj: ProjectPaths, *, std: StandardPaths, ws: Workset | None, early: EarlyScope, is_external: bool=False, workspace: Path | None=None) -> ProjectState
def _landed_bind_sources(landed: ProjectState, std: StandardPaths, config: BootstrapConfig) -> frozenset[str] | None
def _box_trees(state: ProjectState) -> tuple[Path | None, ...]
def _workspace_copy_ignore(metadata_root: Path, copied_root: Path, *, mode: BoxMode, early: EarlyScope) -> Callable[[str, list[str]], set[str]]
def _resolve_target_workset(name: str, std: StandardPaths) -> Workset
def _cure_ref(state: ProjectState) -> str
def _name_held_in_target_workset(target_mode: BoxMode | None, target_ws: Workset | None, state: ProjectState, new_name: str) -> str | None
def _validate(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig, *, force: bool, cwd: Path) -> dict
def _run_steps(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig, plan: dict, unwind: _Unwind) -> tuple[ProjectState, _SourceTeardown | None]
def _retire_old_workspace(old: Path, landed: Path) -> None
def _leftover_entries(path: Path, before: Collection[str]=()) -> str
def _entry_names(path: Path) -> frozenset[str] | None
def _arm_leftover_note(unwind: _Unwind, path: Path) -> None
def _apply_ownership_and_markers(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, target_mode: BoxMode, target_ws: Workset | None, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None, requested_name: str='') -> tuple[ProjectState, _SourceTeardown | None]
def _unwind_box_tree(path: Path) -> None
def _unwind_created_root(path: Path) -> None
def _copy_metadata(src_metadata: Path, src_shell: Path, dst_metadata: Path, *, shell_into_metadata: bool, home_leaf: str='home', unwind: _Unwind) -> Path
def _deliver_carried_box_settings(state: ProjectState, dst_box_tier: Path, *, early: EarlyScope) -> None
def _vault_leaf_has_contents(leaf: Path) -> bool
def _copy_vault_leaf_contents(src: Path, dst: Path | None, removed: Collection[Path]=(), relocated: Mapping[Path, Path] | None=None) -> None
def _vault_copy_failure_message(src: Path, dst: Path, err: shutil.Error) -> str
def _vault_carry_pairs(state: ProjectState, std: StandardPaths, dst_ro: Path | None, dst_rw: Path | None) -> list[tuple[Path, Path]]
def _carry_vault_contents(state: ProjectState, std: StandardPaths, dst_ro: Path | None, dst_rw: Path | None, teardown: _SourceTeardown, relocated: Mapping[Path, Path] | None=None) -> None
def _landings(*pairs: tuple[Path, Path]) -> dict[Path, Path] | None
def _move_log_back(dst: Path, src: Path) -> None
def _carry_box_logs(state: ProjectState, std: StandardPaths, unwind: _Unwind, *, dst_logs: Path | None, dst_name: str) -> None
def _unreceived_vault_leaves(src_arms: tuple[Path | None, Path | None], dst_vault: tuple[Path | None, Path | None], leaf_name: str='', *, vault_enabled: bool=True) -> list[tuple[Path, str]]
def _report_unreceived_vaults(kept: list[tuple[Path, str]]) -> None
def _carried_member_store(ws: Workset, name: str, dst_vault: tuple[Path | None, Path | None], *, vault_enabled: bool=True) -> tuple[tuple[Path, ...], list[tuple[Path, str]]]
def _nulled_arm_stores(ws: Workset, name: str) -> list[tuple[Path, str]]
def _plan_source_teardown(state: ProjectState, std: StandardPaths, dst_vault: tuple[Path | None, Path | None], *, preserve_name: str | None=None, new_name: str | None=None, preserve_root: Path | None=None) -> _SourceTeardown
def _restore_standalone_rows(std: StandardPaths, before: Mapping[str, str]) -> None
def _restore_primary_rows(std: StandardPaths, before: Mapping[str, str]) -> None
def _safe_unregister_standalone(std: StandardPaths, name: str) -> None
def _standalone_source_row(std: StandardPaths, name: str, root: Path, new_name: str | None) -> tuple[str, str] | None
def _carry_standalone_canon(teardown: _SourceTeardown, std: StandardPaths, src_root: Path, dst_root: Path) -> _SourceTeardown
def _stash_source_marker(teardown: _SourceTeardown, unwind: _Unwind) -> _SourceTeardown
def _drop_source_row(teardown: _SourceTeardown | None, unwind: _Unwind) -> None
def _finish_relocation(state: ProjectState, new_state: ProjectState, std: StandardPaths, teardown: _SourceTeardown | None, later: list[Callable[[], None]], progress: _TailProgress | None=None) -> None
def _note_interrupted_tail(state: ProjectState, new_state: ProjectState, std: StandardPaths, teardown: _SourceTeardown | None, old_workspaces: list[Path], progress: _TailProgress) -> None
def _holds_any(path: Path, stores: Collection[Path]) -> bool
def _on_disk(paths: Iterable[Path]) -> list[Path]
def _unmoved_partitions(old: ProjectState, new: ProjectState, std: StandardPaths, plan: _SourceTeardown) -> list[str]
def _unmoved_snapshots(old: ProjectState, new: ProjectState) -> list[str]
def _retire_old_store(ws: Workset, name: str, dst_vault: tuple[Path | None, Path | None], vault_enabled: bool=True, *, reraise: bool=False) -> None
def _report_store_leftovers(name: str, left: Collection[Path], why: str='', *, unmoved: Collection[str]=()) -> None
def _to_default(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str='') -> tuple[ProjectState, _SourceTeardown | None]
def _resolve_standalone_workspaces(root: Path, doc: Mapping[str, Any] | None, *, early: EarlyScope) -> Path
def _resolve_standalone_boxes(root: Path, doc: Mapping[str, Any] | None, *, early: EarlyScope) -> Path
def _standalone_root_artifacts(root: Path, *, early: EarlyScope) -> list[tuple[str, Path, bool]]
def _artifact_claiming(child: Path, artifacts: list[tuple[str, Path, bool]]) -> tuple[str, Path, bool] | None
def _consolidate_workspace_subdir(root: Path, workspace_subdir: Path, unwind: _Unwind, *, early: EarlyScope) -> None
def _undo_consolidate(src_dir: Path, dest_dir: Path, moved: list[Path]) -> None
def _move_entry(src: Path, dst: Path) -> None
def _prune_empty_dirs(path: Path, above: Path) -> None
def _unconsolidate_workspace_subdir(workspace_subdir: Path, root: Path, unwind: _Unwind) -> None
def _arm_standalone_root_undo(std: StandardPaths, config: BootstrapConfig, root: Path, workspace_subdir: Path, unwind: _Unwind) -> tuple[Callable[[], None], Callable[[str], None]]
def _to_standalone(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, new_name: str, root: Path) -> tuple[ProjectState, _SourceTeardown | None]
def _to_workset(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, target_ws: Workset, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None) -> tuple[ProjectState, _SourceTeardown | None]
def _state_ws_token(state: ProjectState) -> str
def _state_ws_root(state: ProjectState, std: StandardPaths) -> Path
def _own_partition(state: ProjectState, std: StandardPaths) -> OwnPartition
def _old_partition(old: ProjectState, std: StandardPaths, plan: _SourceTeardown) -> OwnPartition
def _relocate_channel_partition(old: ProjectState, new: ProjectState, std: StandardPaths, plan: _SourceTeardown) -> None
def _relocate_snapshot_store(state: ProjectState, new_state: ProjectState) -> Path | None
def _old_snapshot_store(old: ProjectState, new: ProjectState) -> Path | None
def _safe_unregister(std: StandardPaths, name: str) -> None
def _safe_register_membership(std: StandardPaths, name: str, workspace: Path) -> None
def _member_leaves(ws: Workset, name: str) -> tuple[Path | None, Path, Path | None, Path | None]
def _existing_member_leaves(ws: Workset, name: str) -> dict[Path, frozenset[str] | None]
def _unwind_target_member(ws: Workset, name: str, existed: Mapping[Path, frozenset[str] | None]) -> None
def _dispose_stash(stash: Path) -> None
def _convert_target_flags(args) -> list[str]
def _ownership_from_args(args) -> str | _Sentinel
def _validated_name(args) -> str | None
def _make_confirm(force: bool, summary: str)
def _load_env()
def _abort_if_locked(state: ProjectState, force: bool) -> bool
def _relocation_failure(err: OSError) -> str
```

## Classes

```
@dataclass
class ProjectState:
    owner: str
    mode: BoxMode
    name: str
    workspace_path: Path
    metadata_path: Path
    shell_path: Path
    vault_ro: Path | None
    vault_rw: Path | None
    is_external: bool = False
    ws: Workset | None = None
    enable_vault: bool = True
    box_authored_vault: bool = True
    bind_sources: frozenset[str] | None = None

@dataclass
class TargetSpec:
    location: Path | _Sentinel = INPLACE
    ownership: str | _Sentinel = UNCHANGED
    name: str | None = None
    records_only: bool = False
    verb: str | None = None

class _Sentinel:
    __slots__ = ('_name',)

    def __init__(self, name: str) -> None

    def __repr__(self) -> str

@dataclass
class _Unwind:
    actions: list[Callable[[], object]] = field(default_factory=list)
    cleanups: list[tuple[Callable[[], None], Callable[[], None] | None]] = field(default_factory=list)
    finished: int = 0

    def push(self, action: Callable[[], object]) -> None
    def push_first(self, action: Callable[[], object]) -> None
    def on_success(self, action: Callable[[], None], *, interrupted: Callable[[], None] | None=None) -> None
    def run(self) -> None
    def finish(self) -> None
    def note_interrupted(self) -> None

@dataclass(frozen=True)
class _SourceTeardown:
    name: str
    removed: tuple[Path, ...] = ()
    trees: tuple[Path, ...] = ()
    vaults: tuple[Path, ...] = ()
    vault_deleter: Callable[[Path], object] = remove_path
    marker: Path | None = None
    stash: Path | None = None
    canon: Path | None = None
    gitignore: Path | None = None
    partition: OwnPartition | Exception | None = None
    member: Workset | None = None
    dst_vault: tuple[Path | None, Path | None] = (None, None)
    vault_enabled: bool = True
    old_leaf: tuple[Path, Path] | None = None
    keeps: tuple[Callable[[], None], ...] = ()
    drop: Callable[[], object] | None = None
    restore: Callable[[], object] | None = None

@dataclass
class _TailProgress:
    step: str = ''
    snapshots: list[Path] = field(default_factory=list)
```
