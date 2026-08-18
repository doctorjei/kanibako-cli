# `src/kanibako/settings/settings_assemble.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_assemble.py.md`.


## Variables

```
RETIRED_FILE_KEYS: 'dict[tuple[str, ...], str]' = {('box', 'agent_name'): 'box.agent_name', ('agent', 'default', 'default_agent'): 'system.default_agent'}
RETIRED_BEHAVIOR_KEYS: 'dict[str, str]' = {'auto_approve': 'access'}
_log = logging.getLogger(__name__)
_BIND_CATEGORIES: frozenset[str] = frozenset({'bindings', 'caches', 'seeded', 'common', 'synced'})
_DEST_KEYED_CATEGORY = 'bindings'
_BIND_ARMS: tuple[str, str] = ('ro', 'rw')
_DEST_KEYED_LEAF_CATEGORIES: frozenset[str] = frozenset({'caches', 'seeded', 'common', 'synced'})
_AGENT_DEFAULT_SUB = 'default'
_PREF_LEGAL_LEVELS: 'frozenset[str]' = frozenset({'workset', 'box'})
_NO_LEAF: Any = object()
_RETIRED_BEHAVIOR_VALUE_MAP: 'dict[str, dict[bool, str]]' = {'auto_approve': {True: 'full', False: 'restricted'}}
_BEHAVIOR_TABLE_SHAPES: 'tuple[tuple[tuple[str, ...], int], ...]' = ((('agent',), 1), (('pref', 'agent'), 1), (ROOT_SECTIONS, 0))
```

## Functions
```
def refuse_retired_keys(raw: Any, *, level: str, path: Path | None, box_name: str | None=None) -> None
def refuse_retired_behavior_keys(raw: Any, *, level: str, path: Path | None, subject: str | None=None) -> None
def parse_bind_map(raw: Any, *, category: str='bindings') -> KeyStore
def dotted_partial(floor: dict[str, object] | None) -> KeyStore
def assemble_levels(*, agent_name: str, base_path: Path | None=None, system_path: Path | None=None, agent_path: Path | None=None, workset_path: Path | None=None, box_path: Path | None=None, floor: dict[str, object] | None=None) -> list[KeyStore]
def _retired_key_cure(key: str, *, level: str, value: str, box_name: str | None=None) -> str
def _nested_present(raw: Any, parts: 'tuple[str, ...]') -> Any
def _behavior_leaf_sites(raw: Any, leaf: str) -> 'list[tuple[tuple[str, ...], Any]]'
def _retired_behavior_cure(successor: str, *, level: str, tier: str, subject: str | None) -> str
def _containing_scopes(file_scope: str) -> frozenset[str]
def _drop_upward_scopes(raw: dict, *, file_scope: str, path: Path | None) -> dict
def _parse_node(value: Any, *, in_binds: bool, dest_keyed: bool=False, at_bindings: bool=False) -> Any
def _file_partial(raw: dict) -> KeyStore
def _agent_partial(raw: dict, *, sub_key: str, path: Path | None=None, node: str | None=None) -> KeyStore
def _insert_dotted(store: KeyStore, dotted: str, value: Any) -> None
def _overlay(base: KeyStore, top: KeyStore) -> None
```
