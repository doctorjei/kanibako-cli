# `src/kanibako/settings/settings_expand.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_expand.py.md`.


## Variables

```
_ABSENT: _Absent = _Absent()
_NOT_IN_CASCADE = "declared in the keyspace, but not in this command's cascade"
_PREF_ROOT = 'pref'
_SEEDED = 'seeded'
```

## Types
```
NullSources = dict[tuple[str, ...], tuple[str, ...]]
RefsRead = dict[tuple[str, ...], frozenset[str]]
DestKeys = dict[tuple[str, ...], str]
Derive = Callable[[str, Callable[[str], object]], object]

```

## Functions
```
@overload
def expand(snapshot: KeyStore, ctx: ResolveCtx, *, null_sources: NullSources | None=None, refs_read: RefsRead | None=None, dest_keys: DestKeys | None=None, derive: Derive | None=None) -> KeyStore
@overload
def expand(snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool, null_sources: NullSources | None=None, refs_read: RefsRead | None=None, dest_keys: DestKeys | None=None, derive: Derive | None=None) -> KeyStore | tuple[KeyStore, dict[str, str]]
def expand(snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool=False, null_sources: NullSources | None=None, refs_read: RefsRead | None=None, dest_keys: DestKeys | None=None, derive: Derive | None=None) -> KeyStore | tuple[KeyStore, dict[str, str]]
def _absent_reason(dotted: str) -> str | None
def _is_whole_value_ref(value: str) -> str | None
def _is_whole_value_var(value: str) -> str | None
def _whole_braced(value: str, kind: str) -> str | None
def _leaf_label(path: tuple[str, ...]) -> str
```

## Classes

```
class _Absent:
    _instance: '_Absent | None' = None

    def __new__(cls) -> '_Absent'
    def __repr__(self) -> str

class _LenientDefect(Exception):
    def __init__(self, reason: str, *, blind: bool=False) -> None

class _ExpandedShapeError(SettingsError):

class _Expander:
    def __init__(self, snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool=False, derive: Derive | None=None) -> None

    def run(self) -> KeyStore

    def _expand_node(self, node: KeyStore, *, path: tuple[str, ...]) -> KeyStore
    def _defect_past_blindness(self, key: str, value: StoreValue, path: tuple[str, ...], *, seed: bool) -> str | None
    def _expand_dest_key(self, key: str, value: StoreValue, *, chain: tuple[str, ...], seed: bool=False) -> str | None
    def _expand_leaf(self, value: StoreValue, *, path: tuple[str, ...]) -> StoreValue | _Absent
    def _refuse_relative_host_src(self, raw: str, expanded: str, *, chain: tuple[str, ...]) -> None
    def _expand_bind(self, bind: Bind, *, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _expand_bind_entry(self, entry: BindEntry, *, chain: tuple[str, ...], null_refs: list[str] | None=None) -> StoreValue | _Absent
    def _expand_str(self, value: str, *, space: str, chain: tuple[str, ...], null_refs: list[str] | None=None) -> StoreValue | _Absent
    def _resolve_whole_value_var(self, name: str) -> StoreValue | _Absent
    def _resolve_ref(self, dotted: str, *, chain: tuple[str, ...], absent_ok: bool=False) -> StoreValue | _Absent
    def _derived(self, dotted: str, *, chain: tuple[str, ...]) -> StoreValue | _Absent
    def _lookup_raw(self, dotted: str) -> StoreValue | _Absent
    def _expand_embedded(self, value: str, *, space: str, chain: tuple[str, ...], none_refs: list[str]) -> str
    def _lookup_str(self, dotted: str, chain: tuple[str, ...], none_refs: list[str]) -> str
```
