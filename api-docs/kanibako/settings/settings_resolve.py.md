# `src/kanibako/settings/settings_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_resolve.py.md`.


## Variables

```
GUEST_HOME = '/home/agent'
GUEST_UID = 1000
GUEST_GID = 1000
BOX_PINNED_ROOT_RELPATH = '.kanibako'
BOX_PINNED_STATE_RELPATH = f'{BOX_PINNED_ROOT_RELPATH}/state'
MAX_REF_DEPTH = 64
UNSET = _Unset()
_VAR_NAME_RE = re.compile('[A-Za-z_][A-Za-z0-9_]*')
_REF_SEG = f'[{SEGMENT_CHAR_CLASS}{CANONICAL_SEP}]+'
_REF_NAME_RE = re.compile(f'{_REF_SEG}(?:\\.{_REF_SEG})*')
```

## Functions
```
def split_bind(value: str) -> tuple[str, str | None]
def unpack_bind(value: object) -> tuple[str, str, str | None]
def unpack_bind_entry(value: object) -> tuple[str, str | None]
def normalize_bind_dest(dest: str) -> str
def match_var(expr: str, i: int) -> tuple[str, int]
def match_ref(expr: str, i: int) -> tuple[str, int]
def expand_expr(expr: str, *, space: Literal['host', 'guest'], ctx: ResolveCtx, lookup: Callable[[str, tuple[str, ...]], str], chain: tuple[str, ...]=(), defer_env: bool=False) -> str
def resolve_value(key: str, *, levels: list[LevelView], ctx: ResolveCtx, lookup: Callable[[str, tuple[str, ...]], str]) -> ResolvedValue | _Unset
def _unescape(s: str) -> str
def _scan_var_span(expr: str, i: int) -> tuple[str, int]
def _expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]
def _resolve_var(name: str, ctx: ResolveCtx) -> str
def _expand_ref(expr: str, i: int, lookup: Callable[[str, tuple[str, ...]], str], chain: tuple[str, ...]) -> tuple[str, int]
def _no_lookup(ref: str, chain: tuple[str, ...]) -> str


class SettingsError(KanibakoError):
    ...

class _Unset:
    __slots__ = ()

    def __repr__(self) -> str

@dataclass(frozen=True)
class ResolveCtx:
    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)

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
```
