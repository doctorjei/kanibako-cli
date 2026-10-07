# `src/kanibako/settings/agent_file.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_file.py.md`.


## Variables

```
FILE_SCOPE: Final[str] = 'agent'
ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)
_ROOT: Final[str] = 'self'
_CONTAINED: Final[tuple[str, ...]] = contained_scopes(FILE_SCOPE)
_CONTRIBUTED: Final[frozenset[str]] = frozenset({_ROOT, FILE_SCOPE, *_CONTAINED})
_MODELED_KEYS: Final[frozenset[str]] = frozenset({'run_args', 'env', 'secret_path', 'transform_settings'})
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = tuple(sorted(CATEGORY_FAMILY_ROOTS))
_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)
_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS
_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({'env', 'secret_path'})
_SCALAR_WRITABLE_KEYS: Final[frozenset[str]] = frozenset({'run_args'})
_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - _SCALAR_WRITABLE_KEYS
_LIST_VALUED_KEYS: Final[frozenset[str]] = frozenset({'run_args'})
_CATEGORY_PLACEHOLDER: Final[dict[str, tuple[str, str]]] = {'env': ('<VAR>', '<value>'), 'secret_path': ('<VAR>', '<host-path>'), 'bindings': ('ro', '{<box-dest>: [<host-src>]}')}
_DEST_KEYED_PLACEHOLDER: Final[tuple[str, str]] = ('<box-dest>', '[<host-src>]')
_UNSET: Final[object] = object()
```

## Functions
```
def scalar_family_of(tail: str) -> str | None
def table_value_error(tail: str, *, path: Path, verb: str) -> str | None
def file_spelling(*segments: str) -> str
def slot_for(agents_root: Path, node: str, tail: str) -> AgentFileSlot
def argv_words(value: str) -> list[str]
def argv_text(words: Iterable[object]) -> str
def stored_leaf_text(tail: str, value: object) -> str | None
def stored_leaf_shape(tail: str, value: object) -> object
def stored_leaf_display(tail: str, value: object) -> str
def stored_leaf_value(slot: AgentFileSlot) -> object
def read_leaf(slot: AgentFileSlot) -> str | None
def write_leaf(slot: AgentFileSlot, value: object) -> None
def remove_leaf(slot: AgentFileSlot) -> bool
def clear_overrides(path: Path) -> int
def record(level: AgentFileLevel, *, node: str) -> AgentConfig
def save(path: Path, cfg: AgentConfig) -> None
def contributed_tables(raw: Any) -> dict
def scope_view(raw: Any, *, node: str) -> dict
def refuse_node_spelled_twice(table: dict, *, prefix: str, path: Path | None) -> None
def level_table(raw: Any, *, sub_key: str, node: str | None=None, path: Path | None=None) -> AgentFileLevel
def state_level(cfg: 'AgentConfig | None', *, node: str, path: Path | None=None) -> AgentFileLevel | None
def _read_address(tail: str) -> tuple[tuple[str, ...], str]
def _write_address(tail: str) -> tuple[tuple[str, ...], str]
def _is_table_valued(tail: str) -> bool
def _spelled_sections(slot: AgentFileSlot, sections: tuple[str, ...], leaf: str) -> tuple[str, ...]
def _own_node_settings(own: dict, scope: Any, *, node: str) -> dict
def _nested_agent_cure(category: str | None, sub_key: str, *, var: str, value: str) -> str
def _refused_category(sub_tbl: dict) -> str | None
def _nested_table_steps(category: str | None, sub_key: str, *, var: str, value: str, path: Path | None, node: str | None) -> str
def _refuse_nested_tables(root_tbl: dict, *, node: str | None, path: Path | None) -> None
def _refuse_stray_roots(raw: dict, *, node: str | None, path: Path | None) -> None
def _contribution(raw: Any, *, node: str | None, path: Path | None) -> dict
def _refuse_scope_value(tables: dict, token: str, *, path: Path | None) -> None
def _refuse_node_values(tables: dict, *, node: str | None, path: Path | None) -> None
def _refuse_two_spellings(tables: dict, *, node: str | None, path: Path | None) -> None
def _node_identity(segment: Any) -> Any
def _setting_leaves(table: dict, trail: tuple[str, ...]=()) -> dict[tuple[str, ...], str]
def _refuse_undeclared_state(entries: 'Iterable[tuple[str, str, str]]', *, node: str, path: Path | None) -> None
def _node_tables(own: Any, scope: Any, *, node: str) -> 'list[tuple[str, dict, Callable[..., str]]]'
def _scope_spelling(seg: Any, *tail: str) -> str
def _undeclared_entries(own: Any, scope: Any, *, node: str) -> 'Iterator[tuple[str, str, str]]'
def _str_keys(table: dict) -> dict
```

## Classes

```
@dataclass(frozen=True)
class AgentFileSlot:
    path: Path
    tail: str
    node: str
    self_root: bool = True

@dataclass(frozen=True)
class AgentFileLevel:
    node: str
    table: dict
    path: Path | None = None
    scope: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    contained: dict = field(default_factory=dict)
```
