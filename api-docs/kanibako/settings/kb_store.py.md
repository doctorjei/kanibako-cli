# `src/kanibako/settings/kb_store.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/kb_store.py.md`.

```python
SCOPE_CONTAINMENT: tuple[str, ...] = ('system', 'agent', 'workset', 'box')

BINDING_DERIVATIONS_NODE: Final[str] = 'binding_derivations'

class Bind(NamedTuple):
    host: str
    box: str
    opts: str | None = None

class BindEntry(NamedTuple):
    src: str
    opts: str | None = None

BindMap = dict[str, BindEntry]

class __Missing__:
    _instance: '__Missing__ | None' = None

    def __new__(cls) -> '__Missing__':
        ...

    def __repr__(self) -> str:
        ...

    def __bool__(self) -> bool:
        ...

__MISSING__: __Missing__ = __Missing__()

StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]
```
