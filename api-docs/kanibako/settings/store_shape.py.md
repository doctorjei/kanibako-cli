# `src/kanibako/settings/store_shape.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/store_shape.py.md`.

```python
MaskMap = dict[str, bool]

StoreShapeSetMap = dict[str, 'StoreShape']

_ARM: Final[dict[str, str]] = {'bindings.ro': 'ro', 'bindings.rw': 'rw', 'caches': 'rw', 'common': 'rw', 'masks': 'mask', 'seeded': 'seed', 'synced': 'sync'}

_NO_ARM: Final[frozenset[str]] = frozenset({'env', 'secret_path'})

_FOLDED_CONCRETE: Final[tuple[str, ...]] = ('bindings.ro', 'bindings.rw')

_FOLDED_ABSTRACT_MOUNT: Final[tuple[str, ...]] = ('caches', 'common')

@dataclass(frozen=True)
class StoreShape:
    ro: BindMap = field(default_factory=dict)
    rw: BindMap = field(default_factory=dict)
    mask: MaskMap = field(default_factory=dict)
    seed: BindMap = field(default_factory=dict)
    sync: BindMap = field(default_factory=dict)

@dataclass(frozen=True)
class StoreShapeSet:
    shapes: StoreShapeSetMap
    warnings: tuple[CategoryCollision, ...] = ()

    def __getitem__(self, scope: str) -> StoreShape:
        ...

def build_store_shape_set(entries: list[CategoryEntry]) -> StoreShapeSet:
    ...

def build_store_shape(entries: list[CategoryEntry]) -> tuple[StoreShape, list[CategoryCollision]]:
    ...

def _within_scope_survivors(box_dest: str, group: list[CategoryEntry]) -> tuple[list[CategoryEntry], list[CategoryCollision]]:
    ...

def _arm_of(entry: CategoryEntry) -> str | None:
    ...
```
