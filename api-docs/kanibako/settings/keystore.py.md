# `src/kanibako/settings/keystore.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/keystore.py.md`.

```python
V = TypeVar('V')

class ReservedKeyError(KeyError):
    ...

class _Missing:
    _instance: '_Missing | None' = None

    def __new__(cls) -> '_Missing':
        ...

    def __repr__(self) -> str:
        ...

    def __bool__(self) -> bool:
        ...

_MISSING: _Missing = _Missing()

class KeyStore(dict[str, 'V | KeyStore[V]'], Generic[V]):
    RESERVED_KEY_NAMES: frozenset[str] = frozenset({'get', 'keys', 'values', 'items', 'pop', 'popitem', 'setdefault', 'update', 'clear', 'copy', 'fromkeys'})

    def __init__(self: KeyStore[Any], *args: Any, **kwargs: Any) -> None:
        ...

    def insert_segments(self, segments: 'Sequence[str]', value: Any) -> None:
        ...

    @staticmethod
    def __check_key_name(key: Any) -> str:
        ...

    @staticmethod
    def __wrap(value: Any) -> V | KeyStore[V]:
        ...

    def __setitem__(self, key: str, value: Any) -> None:
        ...

    def __getattr__(self, name: str) -> V | KeyStore[V]:
        ...

    def __setattr__(self, name: str, value: Any) -> None:
        ...

    def __delattr__(self, name: str) -> None:
        ...

    def __repr__(self) -> str:
        ...
```
