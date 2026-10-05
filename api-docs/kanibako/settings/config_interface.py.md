# `src/kanibako/settings/config_interface.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_interface.py.md`.


## Variables

```
FLOOR_TIER = 'built-in default'
_log = get_logger(__name__)
```

## Functions
```
def parse_config_arg(arg: str | None, *, set_null: bool=False) -> 'tuple[ConfigAction, str, str | None]'
def get_config_value(key: str, *, global_config_path: Path, project_toml: Path | None=None, env_global: Path | None=None, env_project: Path | None=None, system_settings_path: Path | None=None, agents_root: Path | None=None, command_scope: 'ConfigLevel | None'=None, active_agent: str | None=None, cascade_system_path: Path | None=None, cascade_workset_path: Path | None=None, node_store: bool=True) -> str | None
def set_config_value(key: str, value: 'str | None', *, config_path: Path, env_path: Path | None=None, system_settings_path: Path | None=None, cascade_system_path: Path | None=None, cascade_agent_path: Path | None=None, cascade_workset_path: Path | None=None, cascade_box_path: Path | None=None, cascade_agent_name: str='', command_scope: ConfigLevel | None=None, agents_root: Path | None=None, std: Any=None, proj: Any=None, ws: Any=None, target_error: 'str | None'=None, force: bool=False, node_store: bool=True) -> str
def reset_config_value(key: str, *, config_path: Path, env_path: Path | None=None, system_settings_path: Path | None=None, command_scope: ConfigLevel | None=None, cascade_system_path: Path | None=None, cascade_agent_path: Path | None=None, cascade_workset_path: Path | None=None, cascade_box_path: Path | None=None, cascade_agent_name: str='', agents_root: Path | None=None, node_store: bool=True) -> str
def effective_value(canonical: str, sections: tuple[str, ...], leaf: str, *, agent_name: str, system_path: Path | None, agent_path: Path | None, workset_path: Path | None, box_path: Path | None, floor: 'Mapping[str, object] | None'=None, inputs: 'LaunchInputs | None'=None) -> 'tuple[str, str] | None'
def write_system_value(system_settings_path: Path, leaf: str, value: object) -> None
def reset_all(*, config_path: Path, env_path: Path | None=None, force: bool=False, system_settings_path: Path | None=None, command_scope: 'ConfigLevel | None'=None) -> str
def show_config(*, global_config_path: Path, command_scope: ConfigLevel, config_path: Path | None=None, env_global: Path | None=None, env_project: Path | None=None, effective: bool=False, file: Any=None, workset_path: Path | None=None, agent_state: dict[str, str] | None=None, env_resolved: dict[str, str] | None=None, system_settings_path: Path | None=None, category_snapshot: Any=None, category_ctx: Any=None, category_error: str | None=None, category_declared_by: Any=None, category_dest_keys: Any=None, inputs: Any=None, agent_name: str=GENERAL_SLOT, agent_path: Path | None=None) -> int
def _pref_value_error(canonical: str, value: 'str | None', *, config_path: Path, command_scope: 'ConfigLevel | None', system_settings_path: Path | None, system_path: Path | None, agent_path: Path | None, workset_path: Path | None, box_path: Path | None, agent_name: str, set_target: 'LaunchInputs | None') -> str | None
def _yaml_skeleton(target: str) -> list[str]
def _host_xdg_map(data_home: 'Path | None'=None) -> dict[str, str]
def _set_time_ctx(config: 'dict[str, str] | None'=None) -> 'Any'
def _path_tier_split() -> 'tuple[dict[str, str], dict[str, str]]'
def _set_time_target(*, std, proj, ws, agent_name: str, system_path: 'Path | None') -> 'LaunchInputs | None'
def _target_scope_anchors(target: 'LaunchInputs | None', *, agent_path: 'Path | None', agent_name: str) -> dict[str, object]
def _set_time_anchor(anchor_ref: str, *, scope_anchors: 'dict[str, object]', agents_root: 'Path | None') -> 'str | None'
def _bare_relative_path_error(canonical: str, value: 'str | None', *, display_key: str, route_key: str, config_path: Path, system_settings_path: 'Path | None', command_scope: 'ConfigLevel | None', target: 'LaunchInputs | None', agent_path: 'Path | None', agent_name: str, agents_root: 'Path | None') -> 'str | None'
def _unusable_store_root_error(canonical: str, value: 'str | None') -> 'str | None'
def _command_tier_files(cmd: 'Path | None', command_scope: 'ConfigLevel | None', *, system_path: 'Path | None', agent_path: 'Path | None', workset_path: 'Path | None', box_path: 'Path | None') -> 'tuple[Path | None, Path | None, Path | None, Path | None]'
def _dotted_in(node: object, dotted: str) -> object
def _cascade_bad_entries(cmd: 'Path | None', command_scope: 'ConfigLevel | None', *, system_path: 'Path | None', workset_path: 'Path | None', box_path: 'Path | None', edited: 'str | None'=None) -> _BadEntries
def _first_dotted(views: 'list[dict]', dotted: str) -> object
def _overwritten_by(edited: 'str | None', entry: 'tuple[str, ...]') -> bool
def _set_time_snapshot(*, target: 'LaunchInputs | None', agent_name: str, agent_path: 'Path | None', config_path: 'Path | None'=None, command_scope: 'ConfigLevel | None'=None, system_settings_path: 'Path | None'=None, system_path: 'Path | None'=None, workset_path: 'Path | None'=None, box_path: 'Path | None'=None) -> 'tuple[Any, Any]'
def _floor_blind_default(key: str, value: str, candidate: 'Any', command_scope: 'ConfigLevel | None') -> bool
def _category_set_lookups(config_path: Path, *, canonical: str, command_scope: 'ConfigLevel | None'=None, system_settings_path: Path | None=None, system_path: Path | None=None, agent_path: Path | None=None, workset_path: Path | None=None, box_path: Path | None=None, agent_name: str='', target: 'LaunchInputs | None'=None)
def _lenient_expand(snapshot: 'Any', ctx: 'Any', agent_name: str) -> 'tuple[Any, dict[str, str]]'
def _clone_keystore(store: 'Any') -> 'Any'
def _set_leaf(store: 'Any', parts: list, value: object) -> None
def _argv_aware(leaf: str, fallback: 'Callable[[object], str]') -> 'Callable[[object], str]'
def _scalar_family_render(key: str, category: str) -> 'Callable[[object], str]'
def _read_slot(canonical: str, slot: AgentFileSlot) -> str | None
def _node_noun_file_value(canonical: str, slot: AgentFileSlot, noun_file: 'Path | None', command_scope: 'ConfigLevel | None') -> 'str | None'
def _noun_file_slot(slot: AgentFileSlot, noun_file: Path) -> AgentFileSlot
def _system_verb_slot(slot: 'AgentFileSlot | str | None', noun_file: 'Path | None', node_store: bool) -> 'AgentFileSlot | str | None'
def _stored_shape_for(canonical: str, value: object) -> object
def _set_confirmation(display_key: str, value: object) -> str
def _null_path_key_error(canonical: str, value: 'str | None', *, command_scope: 'ConfigLevel | None', config_path: Path, system_settings_path: 'Path | None') -> 'str | None'
def _box_store_value_error(canonical: str, value: 'str | None', *, command_scope: 'ConfigLevel | None', config_path: Path, system_settings_path: 'Path | None') -> 'str | None'
def _null_box_scalar_error(canonical: str, value: 'str | None', *, command_scope: 'ConfigLevel | None', config_path: Path, system_settings_path: 'Path | None') -> 'str | None'
def _reset_dest(canonical: str, command_scope: 'ConfigLevel | None', config_path: Path, system_settings_path: 'Path | None') -> DestRoute
def _honest_reset_message(key: str, command_scope: 'ConfigLevel | None', effective: 'tuple[str, str] | None'=None) -> str
def _clear_writable_tables(path: Path, command_scope: 'ConfigLevel | None') -> dict[str, int]
def _stored_at(stored: object, segments: 'tuple[str, ...]') -> object
def _reset_all_message(count: int, *, undeclared: int, unlisted: int) -> str
def _entry_phrase(count: int, adjective: str) -> str
def _noun_stored_view(path: 'Path | None', command_scope: ConfigLevel) -> dict
def _quiet_drop_announcements(path: 'Path | None', command_scope: 'ConfigLevel | None') -> None
def _undeclared_stored_entries(data: dict) -> dict[tuple[str, ...], tuple[str, str]]
def _misplaced_config_entries(data: dict) -> dict[str, str]
def _dropped_tables_get_reads(path: 'Path | None', command_scope: ConfigLevel) -> list[str]
def _keeps_settings_apart(command_scope: 'ConfigLevel | None') -> bool
def _shown_entries(*, config_path: 'Path | None', settings_path: 'Path | None', command_scope: 'ConfigLevel | None', stored: 'dict | None'=None) -> _ShownEntries
def _persona_node_rows(stored: dict) -> dict[str, tuple[str, str]]
```

## Classes

```
class ConfigAction(Enum):
    get = 'get'
    set = 'set'
    show = 'show'
    reset = 'reset'

class _BadEntries(NamedTuple):
    files: 'list[tuple[Path | None, list[str]]]'
    names: 'list[str]'
    stored: 'Callable[[str], object]'

    def warn_reports(self) -> 'list[str]'
    def chain_block(self) -> str

class _ShownEntries(NamedTuple):
    overrides: 'list[tuple[str, object]]'
    undeclared: 'dict[tuple[str, ...], tuple[str, str]]'
    misplaced: 'dict[str, str]'
    stored: dict
```
