# `src/kanibako/settings/agent_file.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_file.py.md`.


## Variables

```
ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)
_ROOT: Final[str] = 'self'
_MODELED_KEYS = IDENTITY_KEYS | frozenset({'env', 'secret_path', 'transform_settings'})
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = ('bindings', 'caches', 'seeded', 'common', 'synced', 'masks', 'secret_path', 'env')
_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)
_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS
_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({'env', 'secret_path'})
_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - IDENTITY_KEYS
_CATEGORY_PLACEHOLDER: Final[dict[str, tuple[str, str]]] = {'env': ('<VAR>', '<value>'), 'secret_path': ('<VAR>', '<host-path>'), 'bindings': ('ro', '{<box-dest>: [<host-src>]}')}
_DEST_KEYED_PLACEHOLDER: Final[tuple[str, str]] = ('<box-dest>', '[<host-src>]')
```

## Functions
```
def table_value_error(tail: str, *, path: Path, verb: str) -> str | None
def file_spelling(*segments: str) -> str
def slot_for(agents_root: Path, node: str, tail: str) -> AgentFileSlot
def read_leaf(slot: AgentFileSlot) -> str | None
def write_leaf(slot: AgentFileSlot, value: object) -> None
def remove_leaf(slot: AgentFileSlot) -> bool
def clear_overrides(path: Path) -> int
def load(path: Path) -> AgentConfig
def save(path: Path, cfg: AgentConfig) -> None
def level_table(raw: Any, *, sub_key: str, node: str | None=None, path: Path | None=None) -> AgentFileLevel
def state_level(state: 'Mapping[str, str | None] | None', *, node: str) -> AgentFileLevel | None
def _read_address(tail: str) -> tuple[tuple[str, ...], str]
def _write_address(tail: str) -> tuple[tuple[str, ...], str]
def _is_table_valued(tail: str) -> bool
def _nested_agent_cure(category: str | None, sub_key: str, *, var: str, value: str) -> str
def _refused_category(sub_tbl: dict) -> str | None
def _refuse_nested_tables(root_tbl: dict, *, node: str | None, path: Path | None) -> None
def _refuse_undeclared_state(state: 'Mapping[str, str | None]', *, node: str) -> None
```

## Classes

```
@dataclass(frozen=True)
class AgentFileSlot:
    path: Path
    tail: str

@dataclass(frozen=True)
class AgentFileLevel:
    node: str
    table: dict
```
