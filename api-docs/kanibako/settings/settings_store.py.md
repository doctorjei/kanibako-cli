# `src/kanibako/settings/settings_store.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_store.py.md`.

```python
class Bind(NamedTuple):
    host: str
    box: str
    opts: str | None = None

class BindEntry(NamedTuple):
    src: str
    opts: str | None = None

BindMap = dict[str, BindEntry]

StoreValue = Union['KeyStore', Bind, BindEntry, str, int, float, bool, list[str], None]

class ReservedKeyError(KeyError):
    ...

BINDING_DERIVATIONS_NODE: Final[str] = 'binding_derivations'

SCOPE_CONTAINMENT: tuple[str, ...] = ('system', 'agent', 'workset', 'box')

def insert_segments(store: 'KeyStore', segments: 'Sequence[str]', value: Any) -> None:
    ...

def insert_dotted(store: 'KeyStore', dotted: str, value: Any) -> None:
    ...

class _Missing:
    _instance: '_Missing | None' = None

    def __new__(cls) -> '_Missing':
        ...

    def __repr__(self) -> str:
        ...

    def __bool__(self) -> bool:
        ...

_RESERVED_KEY_NAMES: frozenset[str] = frozenset({'get', 'keys', 'values', 'items', 'pop', 'popitem', 'setdefault', 'update', 'clear', 'copy', 'fromkeys'})

_MISSING: _Missing = _Missing()

def _check_key_name(key: Any) -> str:
    ...

def _wrap(value: Any) -> StoreValue:
    ...

class KeyStore(dict):

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        ...

    def __setitem__(self, key: str, value: Any) -> None:
        ...

    def __getattr__(self, name: str) -> StoreValue:
        ...

    def __setattr__(self, name: str, value: Any) -> None:
        ...

    def __delattr__(self, name: str) -> None:
        ...

    def __repr__(self) -> str:
        ...
```
