# `src/kanibako/settings/settings_assemble.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_assemble.py.md`.


## Variables

```
BIND_CATEGORY_TOKENS: frozenset[str] = frozenset({_DEST_KEYED_CATEGORY} | _DEST_KEYED_LEAF_CATEGORIES)
RETIRED_FILE_KEYS: 'dict[tuple[str, ...], str]' = {('box', 'agent'): 'box.agent', ('box', 'agent_name'): 'box.agent_name', ('agent', 'default', 'default_agent'): 'system.default_agent'}
RETIRED_BEHAVIOR_KEYS: 'dict[str, str]' = {'auto_approve': 'access'}
_log = logging.getLogger(__name__)
_DEST_KEYED_CATEGORY = 'bindings'
_BIND_ARMS: tuple[str, str] = ('ro', 'rw')
_DEST_KEYED_LEAF_CATEGORIES: frozenset[str] = frozenset({'caches', 'seeded', 'common', 'synced'})
_AGENT_DEFAULT_SUB = 'default'
_PREF_LEGAL_LEVELS: 'frozenset[str]' = frozenset({'workset', 'box'})
_NO_LEAF: Any = object()
_SELECTION_STORY = "The RULE CHANGED in kanibako 1.8.0: a box no longer names its agent with a key of its own — it REQUESTS one at the key that resolves earlier (`pref.system.agent`, spec §2h), and the system default is now `system.agent` (§2g). Refusing rather than running: kanibako cannot tell which agent you meant, and guessing would launch a DIFFERENT agent and seed that agent's credentials into this box."
_MIRROR_STORY = "The RULE CHANGED in kanibako 1.8.0: a box no longer carries a SETTABLE mirror of its agent's settings — it REQUESTS a tweak with `pref.agent.<agent>.<key>` (spec §2h) and reads the effective value back at the read-only `meta.box.agent.<key>` (§2b). Refusing rather than running: an undeclared key is not read at all, so this box would come up on the agent's UNTWEAKED settings and every override in this table would silently vanish."
_RETIRED_BEHAVIOR_VALUE_MAP: 'dict[str, dict[bool, str]]' = {'auto_approve': {True: 'full', False: 'restricted'}}
_BEHAVIOR_TABLE_SHAPES: 'tuple[tuple[tuple[str, ...], int], ...]' = ((('agent',), 1), (('pref', 'agent'), 1), (ROOT_SECTIONS, 0))
_AGENT_FILE_LEVEL: str = 'agent'
```

## Functions
```
def refuse_retired_keys(raw: Any, *, level: str, path: Path | None, box_name: str | None=None) -> None
def refuse_retired_behavior_keys(raw: Any, *, level: str, path: Path | None, subject: str | None=None, box_name: str | None=None) -> None
def stored_config_entries(raw: Any) -> dict[str, object]
def config_entry_groups(keys: Iterable[str]) -> list[tuple[str, list[str]]]
def refuse_config_table(raw: Any, *, level: str, path: Path | None) -> None
def cascade_view(raw: Any, *, level: str) -> Any
def parse_bind_map(raw: Any, *, category: str='bindings', root_ref: str | None=None) -> KeyStore
def dotted_partial(floor: dict[str, object] | None) -> KeyStore
def assemble_levels(*, agent_name: str, base_path: Path | None=None, system_path: Path | None=None, agent_path: Path | None=None, workset_path: Path | None=None, box_path: Path | None=None, floor: dict[str, object] | None=None) -> list[KeyStore]
def _declaration_root_ref(path: tuple[str, ...], category: str) -> str | None
def _stored_spelling(raw: Any) -> str
def _cure_assignment(sub: str, value: Any) -> str
def _cure_subject(level: str, box_name: str | None) -> str
def _retired_mirror_cure(*, level: str, box_name: str | None, table: 'dict[Any, Any]') -> str
def _retired_key_cure(key: str, *, level: str, value: str, box_name: str | None=None, mirror: 'dict[Any, Any] | None'=None) -> str
def _nested_present(raw: Any, parts: 'tuple[str, ...]') -> Any
def _behavior_leaf_sites(raw: Any, leaf: str) -> 'list[tuple[tuple[str, ...], Any]]'
def _retired_behavior_cure(successor: str, *, level: str, tier: str, subject: str | None, box_name: str | None=None) -> str
def _containing_scopes(file_scope: str) -> frozenset[str]
def _upward_scope_drop_set(file_scope: str) -> frozenset[str]
def _drop_upward_scopes(raw: dict, *, file_scope: str, path: Path | None) -> dict
def _parse_node(value: Any, *, in_binds: bool, dest_keyed: bool=False, at_bindings: bool=False, path: tuple[str, ...]=()) -> Any
def _declared_source(src: str, category: str, dest: str, root_ref: str | None) -> str
def _parse_naming_file(raw: dict, *, file_path: Path | None, key_path: tuple[str, ...]=()) -> KeyStore
def _file_partial(raw: dict, *, path: Path | None=None) -> KeyStore
def _agent_partial(raw: dict, *, sub_key: str, path: Path | None=None, node: str | None=None) -> KeyStore
def _insert_dotted(store: KeyStore, dotted: str, value: Any) -> None
def _overlay(base: KeyStore, top: KeyStore) -> None
```
