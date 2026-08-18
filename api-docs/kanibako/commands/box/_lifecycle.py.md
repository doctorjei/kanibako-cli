# `src/kanibako/commands/box/_lifecycle.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/box/_lifecycle.py.md`.


## Variables

```
INPLACE = _Sentinel('INPLACE')
BARE_INTO_WS = _Sentinel('BARE_INTO_WS')
UNCHANGED = _Sentinel('UNCHANGED')
STUBBORN_INPLACE_MSG = 'Stubbornly refusing to convert in-place from within a workset; add `--move` or `--move <path>` to relocate.'
_STANDALONE_ROOT_ARTIFACTS = frozenset({STANDALONE_META_DIR, 'workspace', 'vault', 'settings.yaml', '.kanibako', 'kanibako', '.kanibako.lock'})
_BARE_MOVE = _Sentinel('BARE_MOVE')
```

## Functions
```
def owner_token(mode: BoxMode, ws_name: str | None=None) -> str
def resolve_lifecycle_target(old: str | None, std: StandardPaths, config: KanibakoConfig | None=None) -> ProjectState
def copy_into_workset(ws: Workset, proj_name: str, metadata_path: Path, shell_path: Path, source_path: Path, source_mode: BoxMode, *, copy_workspace: bool, std: StandardPaths) -> None
def execute_lifecycle(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig | None=None, *, force: bool=False, confirm: Callable[[], bool] | None=None) -> ProjectState
def run_remap(args) -> int
def run_move(args) -> int
def run_convert(args) -> int
def _default_rename_name(state: ProjectState, std: StandardPaths, landing_ws: Path, requested_name: str) -> str | None
def _primary_source_own_name(state: ProjectState, std: StandardPaths) -> str | None
def _ownership_to_mode(ownership: str) -> tuple[BoxMode, str | None]
def _default_state_from_meta(workspace: Path, std: StandardPaths) -> ProjectState | None
def _resolve_workset_state(raw_path: Path, std: StandardPaths, config: KanibakoConfig) -> ProjectState
def _state_from_paths(owner: str, proj: ProjectPaths, *, ws: Workset | None, is_external: bool=False) -> ProjectState
def _resolve_target_workset(name: str, std: StandardPaths) -> Workset
def _validate(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig, *, force: bool, cwd: Path) -> dict
def _run_steps(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig, plan: dict, unwind: _Unwind) -> ProjectState
def _apply_ownership_and_markers(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, target_mode: BoxMode, target_ws: Workset | None, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None, requested_name: str='', force: bool=False) -> ProjectState
def _unwind_box_tree(path: Path) -> None
def _copy_metadata(src_metadata: Path, src_shell: Path, dst_metadata: Path, *, shell_into_metadata: bool, home_leaf: str='home', unwind: _Unwind) -> Path
def _deliver_carried_box_settings(state: ProjectState, dst_box_tier: Path) -> None
def _remove_old_metadata(state: ProjectState, std: StandardPaths, config: KanibakoConfig, *, preserve_name: str | None=None) -> None
def _to_default(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str='', force: bool=False) -> ProjectState
def _consolidate_workspace_subdir(root: Path, workspace_subdir: Path, unwind: _Unwind) -> None
def _undo_consolidate(workspace_subdir: Path, root: Path, moved: list[Path]) -> None
def _unconsolidate_workspace_subdir(workspace_subdir: Path, root: Path, unwind: _Unwind) -> None
def _to_standalone(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str='') -> ProjectState
def _to_workset(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, target_ws: Workset, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None) -> ProjectState
def _state_ws_token(state: ProjectState) -> str
def _relocate_channel_partition(old: ProjectState, new: ProjectState, std: StandardPaths) -> None
def _safe_unregister(std: StandardPaths, name: str) -> None
def _safe_register_membership(std: StandardPaths, name: str, workspace: Path) -> None
def _safe_remove_project(ws: Workset, name: str, std: StandardPaths) -> None
def _ownership_from_args(args) -> str | _Sentinel
def _lower_name(args) -> str | None
def _make_confirm(force: bool, summary: str)
def _load_env()
def _abort_if_locked(state: ProjectState, force: bool) -> bool
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
    vault_ro: Path
    vault_rw: Path
    is_external: bool = False
    ws: Workset | None = None
    enable_vault: bool = True

@dataclass
class TargetSpec:
    location: Path | _Sentinel = INPLACE
    ownership: str | _Sentinel = UNCHANGED
    name: str | None = None
    records_only: bool = False

class _Sentinel:
    __slots__ = ('_name',)

    def __init__(self, name: str) -> None

    def __repr__(self) -> str

@dataclass
class _Unwind:
    actions: list[Callable[[], None]] = field(default_factory=list)
    cleanups: list[Callable[[], None]] = field(default_factory=list)

    def push(self, action: Callable[[], None]) -> None
    def on_success(self, action: Callable[[], None]) -> None
    def run(self) -> None
    def finish(self) -> None
```
