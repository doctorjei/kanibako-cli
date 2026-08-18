# `src/kanibako/settings/settings_views.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_views.py.md`.


## Variables

```
__all__ = ['ViewError', 'bind_map', 'bind_maps', 'derived_bindings', 'env_view', 'masks_set', 'typed_field', 'as_str', 'as_bool', 'as_int', 'as_float', 'as_path', 'as_opt_path', 'as_argv_fragment', 'as_mode_table', 'FiniteView', 'MetaView', 'MetaRuntimeView', 'MetaBoxView', 'MetaWorksetView', 'MetaAgentView']
```

## Types
```
T = TypeVar('T')

```

## Functions
```
def bind_map(node: KeyStore, *, label: str='bindings') -> Mapping[str, BindEntry]
def bind_maps(node: KeyStore, *, label: str='bindings') -> tuple[Mapping[str, BindEntry], Mapping[str, BindEntry]]
def derived_bindings(node: KeyStore, *, label: str='binding_derivations') -> dict[str, Bind]
def env_view(node: KeyStore, *, label: str='env') -> Mapping[str, 'str | int | float | bool']
def masks_set(node: KeyStore, *, label: str='masks') -> set[str]
def as_str(value: Any) -> str
def as_bool(value: Any) -> bool
def as_int(value: Any) -> int
def as_float(value: Any) -> float
def as_path(value: Any) -> Path
def as_opt_path(value: Any) -> Path | None
def as_argv_fragment(value: Any) -> list[str]
def as_mode_table(value: Any) -> dict[str, list[str]]
def _require_node(node: Any, label: str) -> None
def _sub_or_empty(node: KeyStore, key: str) -> KeyStore
```

## Classes

```
class ViewError(Exception):

class typed_field(Generic[T]):
    __slots__ = ('_coerce', '_key', '_name')

    def __init__(self, coerce: Callable[[Any], T], *, key: str | None=None) -> None

    def __set_name__(self, owner: type, name: str) -> None
    def __get__(self, obj: FiniteView | None, owner: type | None=None) -> T

class FiniteView:
    __slots__ = ('_node',)
    _node: KeyStore

    def __init__(self, node: KeyStore) -> None

class MetaView(FiniteView):
    name: str = typed_field(as_str)
    root: Path = typed_field(as_path)

class MetaRuntimeView(FiniteView):
    ws_root: Path = typed_field(as_path)
    project_type: str = typed_field(as_str)

class MetaBoxView(FiniteView):
    mode: str = typed_field(as_str)
    name: str = typed_field(as_str)
    workspace: Path = typed_field(as_path)
    inbox: Path = typed_field(as_path)
    share_global: Path = typed_field(as_path)
    share_workset: 'Path | None' = typed_field(as_opt_path)
    settings: 'Path | None' = typed_field(as_opt_path)

class MetaWorksetView(FiniteView):
    path: Path = typed_field(as_path)
    settings: 'Path | None' = typed_field(as_opt_path)
    name: str = typed_field(as_str)

class MetaAgentView(FiniteView):
    name: str = typed_field(as_str)
    path: str = typed_field(as_str)
    settings: Path = typed_field(as_path)
    mode: 'dict[str, list[str]]' = typed_field(as_mode_table)
    exec: 'list[str]' = typed_field(as_argv_fragment)

class _BindMapView(Mapping[str, BindEntry]):
    __slots__ = ('_node', '_label')

    def __init__(self, node: KeyStore, *, label: str) -> None

    def __getitem__(self, key: str) -> BindEntry
    def __iter__(self) -> Iterator[str]
    def __len__(self) -> int
    def __contains__(self, key: object) -> bool
    def _checked(self, key: str, value: Any) -> BindEntry
    def __repr__(self) -> str

class _EnvView(Mapping[str, 'str | int | float | bool']):
    __slots__ = ('_node',)

    def __init__(self, node: KeyStore) -> None

    def __getitem__(self, key: str) -> str | int | float | bool
    def __iter__(self) -> Iterator[str]
    def __len__(self) -> int
    def __contains__(self, key: object) -> bool
    def _checked(self, key: str, value: Any) -> str | int | float | bool
    def __repr__(self) -> str
```
