# `src/kanibako/settings/settings_expand.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_expand.py.md`.


## Variables

```
_ABSENT: _Absent = _Absent()
_PREF_ROOT = 'pref'
```

## Functions
```
@overload
def expand(snapshot: KeyStore, ctx: ResolveCtx) -> KeyStore
@overload
def expand(snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool) -> KeyStore | tuple[KeyStore, dict[str, str]]
def expand(snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool=False) -> KeyStore | tuple[KeyStore, dict[str, str]]
def _is_whole_value_ref(value: str) -> str | None


class _Absent:
    _instance: '_Absent | None' = None

    def __new__(cls) -> '_Absent'
    def __repr__(self) -> str

class _LenientDefect(Exception):
    def __init__(self, reason: str) -> None

class _Expander:
    def __init__(self, snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool=False) -> None

    def run(self) -> KeyStore

    def _expand_node(self, node: KeyStore, *, path: tuple[str, ...]) -> KeyStore
    def _expand_dest_key(self, key: str, value: StoreValue, *, chain: tuple[str, ...]) -> str
    def _expand_leaf(self, value: StoreValue, *, path: tuple[str, ...]) -> StoreValue | _Absent
    def _expand_bind(self, bind: Bind, *, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _expand_bind_entry(self, entry: BindEntry, *, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _expand_str(self, value: str, *, space: str, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _resolve_ref(self, dotted: str, *, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _lookup_raw(self, dotted: str) -> StoreValue | _Absent
    def _expand_embedded(self, value: str, *, space: str, chain: tuple[str, ...]) -> str
    def _lookup_str(self, dotted: str, chain: tuple[str, ...]) -> str
```
