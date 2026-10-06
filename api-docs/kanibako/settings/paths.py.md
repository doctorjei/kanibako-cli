# `src/kanibako/settings/paths.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/paths.py.md`.


## Variables

```
logger = get_logger('paths')
SUBSCRIPTED_SYSTEM_PATH_KEYS: frozenset[str] = frozenset({'system.backup', 'system.channelroot', 'system.template', 'system.cache', 'system.state', 'system.runtime'})
STANDALONE_REGISTRY_COMMENT = 'REMOVING THIS WILL BREAK A STANDALONE BOX!'
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}
_RUNTIME_TMP_PREFIX = 'kanibako-runtime-'
_FLOOR_FIELD_ALIASES: dict[str, str] = {'system.channelroot': 'channels'}
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
def box_tree_materialized(proj: ProjectPaths) -> bool
def box_metadata_dir(mode: BoxMode, metadata_path: Path) -> Path
def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]
def resolve_box_enable_vault(global_path: Path, *, box_path: Path, workset_path: Path | None) -> bool
def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path
def xdg(env_var: str, default_suffix: str) -> Path
def user_config_home() -> Path
def spec_default_xdg_map(data_home: Path | None) -> dict[str, str]
def host_xdg_map(data_home: Path | None=None) -> dict[str, str]
def resolve_config_paths(set_values: Mapping[str, str | None], *, data_home: Path, home: Path, xdg_vars: Mapping[str, str] | None=None) -> dict[str, str]
def resolve_system_paths(set_values: Mapping[str, str | None], *, data_home: Path, home: Path) -> dict[str, Path]
def host_config_map(std: StandardPaths) -> dict[str, str]
def system_path_floor(std: StandardPaths) -> dict[str, str | None]
def layer1_set_values(user_config_path: Path) -> dict[str, str]
def load_system_tier(user_config_path: Path, *, data_home: Path, home: Path, tolerate_bad_settings: bool=False) -> tuple[dict[str, Path], EarlySystem]
def load_system_config(user_config_path: Path, *, data_home: Path, home: Path, tolerate_bad_settings: bool=False) -> dict[str, Path]
def resolve_data_path(*, config_home: Path | None=None, data_home: Path | None=None) -> Path
def resolve_state_path(*, config_home: Path | None=None, data_home: Path | None=None) -> Path
def resolve_cache_path(*, config_home: Path | None=None, data_home: Path | None=None) -> Path
def load_std_paths(config: BootstrapConfig | None=None, *, tolerate_bad_settings: bool=False) -> StandardPaths
def resolve_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None=None, *, initialize: bool=False, enable_vault: bool | None=None, name_override: str | None=None, register: bool=True) -> ProjectPaths
def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None
def creds_watcher_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None
def box_log_files(logs_dir: Path, box: str) -> BoxLogFiles
def remove_box_logs(logs_dir: Path | None, box: str) -> list[Path]
def standalone_logs_dir(root: Path, *, early: EarlyScope) -> Path | None
def box_logs_dir_for(std: StandardPaths, mode: BoxMode, metadata_path: Path, ws_root: Path | None, *, workset_name: str | None=None) -> Path | None
def box_logs_location(std: StandardPaths, proj: ProjectPaths) -> tuple[Path | None, str]
def write_vault_gitignore(vault_root: Path, vault_rw_path: Path) -> None
def detect_project_mode(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> DetectionResult
def load_primary_boxes(primary_workset: Path, *, early: EarlyScope) -> dict[str, str]
def primary_box_name_for_workspace(primary_workset: Path, workspace: str, *, early: EarlyScope) -> str | None
def check_primary_box_name_free(primary_workset: Path, name: str, workspace: str, *, early: EarlyScope) -> None
def check_workspace_not_named_box(std: StandardPaths, workspace: str) -> None
def pick_primary_box_name(primary_workset: Path, workspace: str, boxes_dir: Path | None=None, *, early: EarlyScope) -> str
def register_primary_box_name(primary_workset: Path, name: str, workspace: Path | str, *, early: EarlyScope) -> None
def register_primary_box_name_if_absent(primary_workset: Path, name: str, workspace: Path | str, *, early: EarlyScope) -> None
def assign_primary_box_name(primary_workset: Path, workspace: Path | str, boxes_dir: Path | None=None, *, early: EarlyScope) -> str
def unregister_primary_box_name(primary_workset: Path, name: str, *, early: EarlyScope) -> None
def resolve_workset_project(ws: WorksetSpec, project_name: str, std: StandardPaths, config: BootstrapConfig, *, initialize: bool=False, enable_vault: bool | None=None) -> ProjectPaths
def iter_projects(std: StandardPaths, config: BootstrapConfig) -> list[tuple[Path, Path | None]]
def iter_workset_projects(std: StandardPaths, config: BootstrapConfig) -> _WorksetProjectRows
def designation_route(value: str | None, *, name_first: bool=False) -> DesignationRoute
def resolve_designation(std: StandardPaths, value: str | None, *, unknown_name_is_path: bool, name_first: bool=False) -> str
def resolve_any_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None=None, *, initialize: bool=False, register: bool=True, name_override: str | None=None) -> ProjectPaths
def resolve_box_target(std: StandardPaths, config: BootstrapConfig, value: str | None=None, *, initialize: bool=False, register: bool=True, warn: bool=True) -> ProjectPaths
def establish_standalone(std: StandardPaths, root: Path, *, enable_vault: bool, name: str='', register: bool=True) -> tuple[str, Path, Path | None, Path | None]
def resolve_standalone_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None=None, *, initialize: bool=False, enable_vault: bool | None=None, name: str='', register: bool=True) -> ProjectPaths
def _default_project_group(std: StandardPaths) -> ProjectGroup
def _standalone_settings_files(root: Path) -> tuple[Path, Path]
def _box_settings_files(mode: BoxMode, metadata_path: Path, group: '_WorksetRooted | None') -> tuple[Path, Path | None]
def _narrow_box_scalar_cascade(global_path: Path, *, workset_path: Path | None, box_path: Path | None) -> 'KeyStore'
def _fallback_runtime_dir(var_name: str) -> Path
def _runtime_base_usable(base: Path, *, follow_symlinks: bool=True, require_private: bool=False) -> bool
def _refused_null_path_value_error(key: str, default: str, *, referent: 'str | None'=None) -> str
def _refuse_bare_relative(key: str, raw: object, default: str, *, ctx: ResolveCtx, lookup: Callable[[str, tuple[str, ...]], str]) -> None
def _resolve_system_path_keys(set_values: Mapping[str, str | None], keys: Iterable[str], *, data_home: Path, home: Path, xdg_vars: Mapping[str, str]) -> tuple[dict[str, str], dict[str, Path]]
def _resolve_system_tier(set_values: Mapping[str, str | None], *, data_home: Path, home: Path, system_refusal: str | None=None) -> tuple[dict[str, Path], EarlySystem]
def _floor_field(key: str) -> str
def _path_tier_set_values(user_config_path: Path, *, data_home: Path, home: Path, xdg_vars: Mapping[str, str], tolerate_bad_settings: bool=False) -> tuple[dict[str, str | None], str | None]
def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]
def _primary_box_paths(std: StandardPaths, metadata_path: Path, box_name: str) -> tuple[Path, Path | None, Path | None]
def _workset_box_paths(metadata_path: Path, vault_ro_base: Path | None, vault_rw_base: Path | None, box_name: str) -> tuple[Path, Path | None, Path | None]
def _early_scope(std: StandardPaths, mode: BoxMode, workset_name: str | None=None) -> EarlyScope
def _standalone_box_paths(root: Path, *, early: EarlyScope) -> tuple[Path, Path | None, Path | None]
def _bootstrap_shell(shell_path: Path) -> None
def _upgrade_shell(shell_path: Path) -> None
def _init_common(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path, *, enable_vault: bool=True, vault_root: Path) -> None
def _host_path_within(candidate: Path, root: Path) -> bool
def _init_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path, *, enable_vault: bool=True) -> None
def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None
def _is_standalone_meta_dir(root: Path) -> bool
def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None
def _workset_box_name_for_workspace(ws_root: Path, workspace: str, *, early: EarlyScope) -> str | None
def _workset_box_workspace_for_name(ws_root: Path, box_name: str, *, early: EarlyScope) -> str | None
def _register_workset_box_membership(ws_root: Path, box_name: str, workspace: Path, *, early: EarlyScope) -> None
def _unregister_workset_box_membership(ws_root: Path, box_name: str, *, early: EarlyScope) -> None
def _init_workset_project(std: StandardPaths, metadata_path: Path, shell_path: Path) -> None
def _find_workset_for_path(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]
def _resolve_workset_or_connected(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]
def _warn_standalone_shadowed(std: StandardPaths, value: str) -> None
def _resolve_designated_path(std: StandardPaths, config: BootstrapConfig, raw: str, *, initialize: bool, register: bool, name_override: str | None=None) -> ProjectPaths
def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths
def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths
def _flag_missing_vault(proj: ProjectPaths) -> ProjectPaths
def _init_standalone_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path, *, enable_vault: bool=True) -> None
```

## Classes

```
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
    data: Path
    backup: Path
    agents: Path
    channels: Path
    template: Path
    canon: Path | None
    settings: Path
    primary_workset: Path
    registry: Path
    journal: Path
    cache: Path
    state: Path
    runtime: Path
    channels_common: Path | None
    channels_chat: Path | None
    channels_broadcast: Path | None
    channels_mailboxes: Path | None
    channels_share: Path | None
    boxes: Path
    primary_vault_ro: Path | None
    primary_vault_rw: Path | None
    primary_logs: Path | None
    early_system: EarlySystem

@dataclass(frozen=True)
class ProjectGroup:
    name: str
    root: Path
    is_default: bool
    local_shared_base: Path

@dataclass
class ProjectPaths:
    project_path: Path | None
    project_hash: str
    metadata_path: Path
    shell_path: Path
    vault_ro_path: Path | None
    vault_rw_path: Path | None
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    name: str = field(default='')
    group: ProjectGroup | None = field(default=None)
    _config_path: Path | None = field(default=None, repr=False)
    _enable_vault: bool | None = field(default=None, repr=False)

    def vault_enabled(self) -> bool

@dataclass(frozen=True)
class WorksetSpec:
    name: str
    root: Path
    projects_dir: Path
    workspaces_dir: Path | None
    vault_ro_dir: Path | None
    vault_rw_dir: Path | None
    project_names: tuple[str, ...]
    is_default: bool = False

    @classmethod
    def from_workset(cls, ws: _WorksetLike) -> WorksetSpec

class BoxLogFiles(NamedTuple):
    helper: Path
    creds_watcher: Path

class DesignationRoute(Enum):
    CWD = 'cwd'
    PATH = 'path'
    NAME = 'name'
    QUALIFIED = 'qualified'
    INVALID = 'invalid'

class _WorksetRooted(Protocol):
    @property
    def root(self) -> Path

class _WorksetLike(Protocol):
    name: str
    root: Path
    is_default: bool

    @property
    def projects_dir(self) -> Path
    @property
    def workspaces_dir(self) -> Path | None
    @property
    def vault_ro_dir(self) -> Path | None
    @property
    def vault_rw_dir(self) -> Path | None
    @property
    def logs_dir(self) -> Path | None
    @property
    def projects(self) -> Sequence[_WorksetProjectLike]

class _WorksetProjectLike(Protocol):
    @property
    def name(self) -> str
    @property
    def source_path(self) -> Path
```
