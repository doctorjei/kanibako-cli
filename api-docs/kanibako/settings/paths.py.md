# `src/kanibako/settings/paths.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/paths.py.md`.


## Variables

```
logger = get_logger('paths')
_legacy_primary_settings_warned = False
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}
```

## Types
```
_WorksetProjectRows = list[tuple[str, _WorksetLike, list[tuple[str, str]]]]

```

## Functions
```
@overload
def workset_settings_path(group: _WorksetRooted) -> Path
@overload
def workset_settings_path(group: None) -> None
def workset_settings_path(group: _WorksetRooted | None) -> Path | None
def warn_legacy_primary_settings(std: StandardPaths) -> None
def box_tree_materialized(proj: ProjectPaths) -> bool
def box_metadata_dir(mode: BoxMode, metadata_path: Path) -> Path
def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]
def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path
def xdg(env_var: str, default_suffix: str) -> Path
def host_xdg_map(data_home: Path | None=None) -> dict[str, str]
def resolve_config_paths(set_values: Mapping[str, str], *, data_home: Path, home: Path, xdg_vars: Mapping[str, str] | None=None) -> dict[str, str]
def resolve_system_paths(set_values: Mapping[str, str], *, data_home: Path, home: Path) -> dict[str, Path]
def load_system_config(user_config_path: Path, *, data_home: Path, home: Path) -> dict[str, Path]
def resolve_data_leaf(data_path: Path | None=None, *, config_home: Path | None=None, data_home: Path | None=None) -> str
def load_std_paths(config: KanibakoConfig | None=None) -> StandardPaths
def resolve_project(std: StandardPaths, config: KanibakoConfig, project_dir: str | None=None, *, initialize: bool=False, enable_vault: bool | None=None, name_override: str | None=None, register: bool=True) -> ProjectPaths
def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path
def detect_project_mode(project_dir: Path, std: StandardPaths, config: KanibakoConfig) -> DetectionResult
def load_primary_boxes(primary_workset: Path) -> dict[str, str]
def primary_box_name_for_workspace(primary_workset: Path, workspace: str) -> str | None
def check_primary_box_name_free(primary_workset: Path, registry: Path, name: str, workspace: str, *, force: bool=False) -> None
def pick_primary_box_name(primary_workset: Path, registry: Path, workspace: str, boxes_dir: Path | None=None) -> str
def register_primary_box_name(primary_workset: Path, registry: Path, name: str, workspace: Path | str, *, force: bool=False) -> None
def register_primary_box_name_if_absent(primary_workset: Path, registry: Path, name: str, workspace: Path | str, *, force: bool=False) -> None
def assign_primary_box_name(primary_workset: Path, registry: Path, workspace: Path | str, boxes_dir: Path | None=None) -> str
def unregister_primary_box_name(primary_workset: Path, name: str) -> None
def resolve_workset_project(ws: WorksetSpec, project_name: str, std: StandardPaths, config: KanibakoConfig, *, initialize: bool=False, enable_vault: bool | None=None) -> ProjectPaths
def iter_projects(std: StandardPaths, config: KanibakoConfig) -> list[tuple[Path, Path | None]]
def iter_workset_projects(std: StandardPaths, config: KanibakoConfig) -> _WorksetProjectRows
def resolve_any_project(std: StandardPaths, config: KanibakoConfig, project_dir: str | None=None, *, initialize: bool=False, register: bool=True, name_override: str | None=None) -> ProjectPaths
def resolve_box_target(std: StandardPaths, config: KanibakoConfig, value: str | None=None, *, initialize: bool=False, register: bool=True, warn: bool=True) -> ProjectPaths
def establish_standalone(std: StandardPaths, root: Path, *, enable_vault: bool, name: str='', register: bool=True) -> tuple[str, Path, Path, Path]
def resolve_standalone_project(std: StandardPaths, config: KanibakoConfig, project_dir: str | None=None, *, initialize: bool=False, enable_vault: bool | None=None, name: str='', register: bool=True) -> ProjectPaths
def _default_project_group(std: StandardPaths) -> ProjectGroup
def _standalone_settings_files(root: Path) -> tuple[Path, Path]
def _box_settings_files(mode: BoxMode, metadata_path: Path, group: 'ProjectGroup | None') -> tuple[Path, Path | None]
def _fallback_runtime_dir(var_name: str) -> Path
def _runtime_base_usable(base: Path) -> bool
def _spec_default_xdg_map(data_home: Path | None) -> dict[str, str]
def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]
def _primary_box_paths(std: StandardPaths, metadata_path: Path, box_name: str) -> tuple[Path, Path, Path]
def _workset_box_paths(metadata_path: Path, vault_base: Path, box_name: str) -> tuple[Path, Path, Path]
def _standalone_box_paths(root: Path) -> tuple[Path, Path, Path]
def _bootstrap_shell(shell_path: Path) -> None
def _upgrade_shell(shell_path: Path) -> None
def _init_common(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path, vault_rw_path: Path, project_path: Path, *, enable_vault: bool=True) -> None
def _init_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path, vault_rw_path: Path, project_path: Path, *, enable_vault: bool=True) -> None
def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None
def _is_standalone_meta_dir(root: Path) -> bool
def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None
def _workset_box_name_for_workspace(ws_root: Path, workspace: str) -> str | None
def _workset_box_workspace_for_name(ws_root: Path, box_name: str) -> str | None
def _register_workset_box_membership(ws_root: Path, box_name: str, workspace: Path) -> None
def _unregister_workset_box_membership(ws_root: Path, box_name: str) -> None
def _primary_name_domain(primary_workset: Path, registry: Path) -> set[str]
def _init_workset_project(std: StandardPaths, metadata_path: Path, shell_path: Path) -> None
def _find_workset_for_path(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]
def _resolve_workset_or_connected(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]
def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths
def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths
def _flag_missing_vault(proj: ProjectPaths) -> ProjectPaths
def _init_standalone_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path, vault_rw_path: Path, project_path: Path, *, enable_vault: bool=True) -> None


class BoxMode(Enum):
    primary = 'primary'
    named = 'named'
    standalone = 'standalone'

class DetectionResult(NamedTuple):
    mode: BoxMode
    project_root: Path

@dataclass
class StandardPaths:
    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path
    config_file: Path
    data_path: Path
    state_path: Path
    cache_path: Path
    data: Path
    backup: Path
    agents: Path
    channels: Path
    template: Path
    canon: Path
    settings: Path
    primary_workset: Path
    registry: Path
    journal: Path
    cache: Path
    runtime: Path
    channels_common: Path
    channels_chat: Path
    channels_broadcast: Path
    channels_mailboxes: Path
    channels_share: Path
    boxes: Path
    primary_vault_ro: Path
    primary_vault_rw: Path
    primary_logs: Path

@dataclass(frozen=True)
class ProjectGroup:
    name: str
    root: Path
    is_default: bool
    local_shared_base: Path

class _WorksetRooted(Protocol):
    @property
    def root(self) -> Path

@dataclass
class ProjectPaths:
    project_path: Path
    project_hash: str
    metadata_path: Path
    shell_path: Path
    vault_ro_path: Path
    vault_rw_path: Path
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    enable_vault: bool = field(default=True)
    name: str = field(default='')
    group: ProjectGroup | None = field(default=None)

class _WorksetLike(Protocol):
    name: str
    root: Path
    is_default: bool

    @property
    def projects_dir(self) -> Path
    @property
    def workspaces_dir(self) -> Path
    @property
    def vault_dir(self) -> Path
    @property
    def logs_dir(self) -> Path
    @property
    def projects(self) -> Sequence[_WorksetProjectLike]

class _WorksetProjectLike(Protocol):
    @property
    def name(self) -> str
    @property
    def source_path(self) -> Path

@dataclass(frozen=True)
class WorksetSpec:
    name: str
    root: Path
    projects_dir: Path
    workspaces_dir: Path
    vault_dir: Path
    project_names: tuple[str, ...]
    is_default: bool = False

    @classmethod
    def from_workset(cls, ws: _WorksetLike) -> WorksetSpec
```
