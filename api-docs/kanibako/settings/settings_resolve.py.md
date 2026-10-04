# `src/kanibako/settings/settings_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_resolve.py.md`.


## Variables

```
GUEST_HOME = '/home/agent'
GUEST_UID = 1000
GUEST_GID = 1000
GUEST_WORKSPACE_RELPATH = 'workspace'
GUEST_VAULT_RELPATH = 'vault'
GUEST_VAULT_RO_RELPATH = f'{GUEST_VAULT_RELPATH}/ro'
GUEST_VAULT_RW_RELPATH = f'{GUEST_VAULT_RELPATH}/rw'
GUEST_WORKSPACE = f'{GUEST_HOME}/{GUEST_WORKSPACE_RELPATH}'
GUEST_VAULT_RO = f'{GUEST_HOME}/{GUEST_VAULT_RO_RELPATH}'
GUEST_VAULT_RW = f'{GUEST_HOME}/{GUEST_VAULT_RW_RELPATH}'
BOX_PINNED_ROOT_RELPATH = '.kanibako'
BOX_PINNED_STATE_RELPATH = f'{BOX_PINNED_ROOT_RELPATH}/state'
MAX_REF_DEPTH = 64
UNSET = _Unset()
DEFAULT_TERM = 'xterm'
_VAR_NAME_RE = re.compile('[A-Za-z_][A-Za-z0-9_]*')
_REF_SEG = f'[{SEGMENT_CHAR_CLASS}{CANONICAL_SEP}]+'
_REF_NAME_RE = re.compile(f'{_REF_SEG}(?:\\.{_REF_SEG})*')
_EXPR_SIGNIFICANT: frozenset[str] = frozenset('\\$@~')
```

## Functions
```
def expand_guest_home(value: str) -> str
def literal_expr(text: str) -> str
def literal_map(values: Mapping[str, str]) -> dict[str, str]
def is_verbatim_text(path: Sequence[str]) -> bool
def split_bind(value: str) -> tuple[str, str | None]
def unpack_bind(value: object) -> tuple[str, str, str | None]
def unpack_bind_entry(value: object) -> tuple[str, str | None]
def normalize_bind_dest(dest: str) -> str
def refuse_dest_spelled_twice(raw: Mapping[str, Any], *, category: str, where: str | None=None) -> None
def refuse_unrooted_source(src: str, category: str, dest: str, *, where: str | None=None) -> None
def refuse_scalar_at_table_key(key: str, value: Any, *, where: str | None=None) -> None
def check_bind_map(raw: Mapping[str, Any], *, category: str, where: str | None=None) -> None
def check_bind_tables(tables: Mapping[str, Any], *, root: str, scope: str, contained: Sequence[str], where: str | None) -> None
def match_var(expr: str, i: int) -> tuple[str, int]
def match_ref(expr: str, i: int) -> tuple[str, int]
def expand_expr(expr: str, *, space: Literal['host', 'guest'], ctx: ResolveCtx, lookup: Callable[[str, tuple[str, ...]], str], chain: tuple[str, ...]=(), defer_env: bool=False) -> str
def resolve_var(name: str, ctx: ResolveCtx) -> str | _Unset
def resolve_value(key: str, *, levels: list[LevelView], ctx: ResolveCtx, lookup: Callable[[str, tuple[str, ...]], str]) -> ResolvedValue | _Unset
def _host_term() -> str
def _host_colorterm() -> str | None
def _unescape(s: str) -> str
def _check_node_binds(table: Mapping[str, Any], *, where: str | None) -> None
def _check_dest_map(raw: Mapping[str, Any], *, category: str, where: str | None) -> None
def _in_file(where: str | None) -> str
def _scan_var_span(expr: str, i: int) -> tuple[str, int]
def _expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]
def _resolve_var(name: str, ctx: ResolveCtx) -> str
def _expand_ref(expr: str, i: int, lookup: Callable[[str, tuple[str, ...]], str], chain: tuple[str, ...]) -> tuple[str, int]
def _no_lookup(ref: str, chain: tuple[str, ...]) -> str
```

## Classes

```
class SettingsError(KanibakoError):

@dataclass(frozen=True)
class ResolveCtx:
    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)
    term: str = field(default_factory=_host_term)
    colorterm: str | None = field(default_factory=_host_colorterm)

@dataclass(frozen=True)
class LevelView:
    name: str
    values: Mapping[str, object]
    defaults: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class ResolvedValue:
    value: object
    level: str
    is_default: bool = False
    terminal: bool = False

class _Unset:
    __slots__ = ()

    def __repr__(self) -> str
```
