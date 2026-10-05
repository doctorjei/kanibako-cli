# `src/kanibako/settings/kb_store.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/kb_store.py.md`.


## Variables

```
SCOPE_CONTAINMENT: tuple[str, ...] = ('system', 'agent', 'workset', 'box')
RESOLUTION_ORDER: tuple[str, ...] = ('config', 'meta.runtime', 'meta.agent', 'meta.workset', 'base', 'system', 'agent', 'workset', 'meta.box', 'box')
BINDING_DERIVATIONS_NODE: Final[str] = 'binding_derivations'
IDENTITY_ANCHORS: Final[dict[str, dict[str, tuple[str, ...]]]] = {'workset': {'primary': ('meta.workset.path', 'meta.workset.name'), 'named': ('meta.workset.path', 'meta.workset.name'), 'standalone': ('meta.workset.path',)}, 'partition': {'primary': ('meta.workset.name',), 'named': ('meta.workset.name',), 'standalone': ('meta.workset.name',)}, 'box': {'primary': ('meta.box.name',), 'named': ('meta.box.name',), 'standalone': ('meta.box.name',)}, 'agent': {'primary': ('meta.agent.<agent>.name',), 'named': ('meta.agent.<agent>.name',), 'standalone': ('meta.agent.<agent>.name',)}}
IDENTITY_PAIRED: Final[dict[str, dict[str, str]]] = {'box': {'primary': 'workset', 'named': 'workset'}}
IDENTITY_IMPLIED: Final[dict[str, dict[str, str]]] = {'box': {'standalone': 'workset'}}
__MISSING__: __Missing__ = __Missing__()
```

## Types
```
BindMap = dict[str, BindEntry]
StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]

```

## Classes

```
class Bind(NamedTuple):
    host: str
    box: str
    opts: str | None = None

class BindEntry(NamedTuple):
    src: str
    opts: str | None = None

class __Missing__:
    _instance: '__Missing__ | None' = None

    def __new__(cls) -> '__Missing__'
    def __repr__(self) -> str
    def __bool__(self) -> bool
```
