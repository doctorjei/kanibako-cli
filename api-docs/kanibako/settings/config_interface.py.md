# `src/kanibako/settings/config_interface.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_interface.py.md`.


## Functions
```
def parse_config_arg(arg: str | None, *, set_null: bool=False) -> 'tuple[ConfigAction, str, str | None]'
def get_config_value(key: str, *, global_config_path: Path, project_toml: Path | None=None, env_global: Path | None=None, env_project: Path | None=None, system_settings_path: Path | None=None, agents_root: Path | None=None, command_scope: 'ConfigLevel | None'=None, active_agent: str | None=None) -> str | None
def set_config_value(key: str, value: 'str | None', *, config_path: Path, env_path: Path | None=None, system_settings_path: Path | None=None, cascade_system_path: Path | None=None, cascade_agent_path: Path | None=None, cascade_workset_path: Path | None=None, cascade_box_path: Path | None=None, cascade_agent_name: str='', command_scope: ConfigLevel | None=None, agents_root: Path | None=None) -> str
def reset_config_value(key: str, *, config_path: Path, env_path: Path | None=None, system_settings_path: Path | None=None, command_scope: ConfigLevel | None=None, cascade_system_path: Path | None=None, cascade_agent_path: Path | None=None, cascade_workset_path: Path | None=None, cascade_box_path: Path | None=None, cascade_agent_name: str='', agents_root: Path | None=None) -> str
def write_system_value(config_path: Path, leaf: str, value: object) -> None
def reset_all(*, config_path: Path, env_path: Path | None=None, force: bool=False, system_settings_path: Path | None=None, command_scope: 'ConfigLevel | None'=None) -> str
def show_config(*, global_config_path: Path, config_path: Path | None=None, env_global: Path | None=None, env_project: Path | None=None, effective: bool=False, file: Any=None, workset_path: Path | None=None, agent_state: dict[str, str] | None=None, env_resolved: dict[str, str] | None=None, system_settings_path: Path | None=None, category_snapshot: Any=None, category_error: str | None=None) -> int
def _pref_value_error(canonical: str, value: 'str | None', *, config_path: Path, system_path: Path | None, agent_path: Path | None, workset_path: Path | None, box_path: Path | None, agent_name: str) -> str | None
def _yaml_skeleton(target: str) -> list[str]
def _host_xdg_map(data_home: 'Path | None'=None) -> dict[str, str]
def _set_time_ctx(config: 'dict[str, str] | None'=None) -> 'Any'
def _path_tier_split() -> 'tuple[dict[str, str], dict[str, object]]'
def _category_set_lookups(config_path: Path, *, canonical: str, system_path: Path | None=None, agent_path: Path | None=None, workset_path: Path | None=None, box_path: Path | None=None, agent_name: str='')
def _clone_keystore(store: 'Any') -> 'Any'
def _set_leaf(store: 'Any', parts: list, value: object) -> None
def _reset_dest(canonical: str, command_scope: 'ConfigLevel | None', config_path: Path, system_settings_path: 'Path | None') -> DestRoute
def _honest_reset_message(key: str, command_scope: 'ConfigLevel | None', effective: 'tuple[str, str] | None'=None) -> str
def _effective_after_reset(canonical: str, sections: tuple[str, ...], leaf: str, *, agent_name: str, system_path: Path | None, agent_path: Path | None, workset_path: Path | None, box_path: Path | None) -> 'tuple[str, str] | None'
def _count_leaves(node: object) -> int
def _clear_writable_scope_tables(path: Path, command_scope: 'ConfigLevel | None') -> int
```

## Classes

```
class ConfigAction(Enum):
    get = 'get'
    set = 'set'
    show = 'show'
    reset = 'reset'
```
