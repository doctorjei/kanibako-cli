# `src/kanibako/settings/config.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config.py.md`.


## Variables

```
BOX_META_FILE = 'box.yaml'
WORKSET_META_FILE = 'workset.yaml'
AGENT_META_FILE = 'agent.yaml'
SYSTEM_HELPERS_SECTION: 'tuple[str, ...]' = ('system', 'helpers')
_BOOL_TRUE = frozenset({'true', '1', 'yes', 'on'})
_BOOL_FALSE = frozenset({'false', '0', 'no', 'off'})
_DEFAULTS: dict[str, str] = {'box_image': 'ghcr.io/doctorjei/kanibako-oci:latest', 'box_shell': ''}
_LAYER1_TABLE = 'config'
_BOX_SCALAR_FIELDS: dict[str, str] = {'box.image': 'box_image', 'box.share_images': 'box_share_images', 'box.shell': 'box_shell', 'box.enable_vault': 'box_enable_vault'}
```

## Functions
```
def coerce_bool(value: object) -> bool | None
def config_file_path(config_home: Path) -> Path
def user_config_file() -> Path
def bootstrap_config_paths(path: Path) -> dict[str, str]
def system_path_set_values(settings_path: Path) -> dict[str, str]
def config_base_path() -> Path
def settings_base_path() -> Path
def load_config(path: Path) -> BootstrapConfig
def box_scalar_defaults_floor() -> dict[str, object]
def write_global_config(path: Path) -> None
def write_project_config(path: Path, image: str) -> None
def persist_creation_flags(box_settings_path: Path, *, materializing: bool, image: str | None=None, share_images: bool | None=None) -> None
def write_box_enable_vault(path: Path, enable_vault: bool=True) -> None
def read_box_enable_vault(path: Path) -> bool
def carried_box_settings(box_tier: Path) -> dict
def read_workset_kuid(path: Path) -> str
def read_workset_skip_kuid_check(path: Path) -> bool
def write_project_config_key(path: Path, flat_key: str, value: str) -> None
def unset_project_config_key(path: Path, flat_key: str) -> bool
def load_project_overrides(path: Path) -> dict[str, object]
def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]
def agent_settings_of(data: dict, agent_name: str) -> dict[str, str]
def system_settings_path() -> Path
def read_system_agent(system_path: Path | None) -> str | None
def read_system_helpers(settings_path: Path | None) -> dict[str, int]
def read_setup_completed(settings_path: Path | None) -> str | None
def setup_compat_gate(settings_path: Path | None) -> str | None
def resolve_agent(*, explicit_agent: str | None, requested: str | None=None, project_path: Path | None=None) -> str
def write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None
def null_path_keys_error(path: Path, keys: Iterable[str], *, cure: str=ERR_CONFIG_NULL_PATH_CURE, head: 'str | None'=None) -> 'str | None'
def refuses_null_path_key(canonical: str) -> bool
def usable_box_store_value(value: object) -> bool
def refuses_box_store_value(canonical: str, value: object) -> bool
def chain_bad_entries(value: object, bad: Iterable[str], *, stored: 'Callable[[str], object]') -> list[str]
def system_path_ref_error(canonical: str, value: 'str | None') -> 'str | None'
def _layer1_settings_keys(data: dict) -> list[str]
def _scalar_value(value: object) -> object
def _present_scalar_fields(path: Path) -> dict[str, object]
def _typed_box_scalar(defaults: KanibakoConfig, field_name: str, value: object) -> object
def _system_settings_path(global_path: Path) -> Path | None
def _split_config_key(flat_key: str) -> tuple[str, str]
def _flatten_leaves(data: dict, prefix: str='') -> dict[str, object]
def _flatten_dotted(data: dict, prefix: str='') -> dict[str, str]
def _refuse_null_paths(path: Path, table: dict, prefix: str, path_keys: Iterable[str]) -> None
```

## Classes

```
@dataclass
class KanibakoConfig:
    box_image: str = _DEFAULTS['box_image']
    box_shell: str = _DEFAULTS['box_shell']
    box_share_images: bool = False
    box_enable_vault: bool = True

@dataclass(frozen=True)
class BootstrapConfig:
    config_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None
```
