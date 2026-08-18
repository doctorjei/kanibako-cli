# `src/kanibako/runtime/rig_meta.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/rig_meta.py.md`.


## Variables

```
_FIELD_NAMES: tuple[str, ...] = tuple((f.name for f in fields(RigMeta)))
_ALWAYS: frozenset[str] = frozenset({'name', 'kind', 'reproducible'})
```

## Functions
```
def dump_rig_meta(meta: RigMeta) -> str
def write_rig_meta(meta: RigMeta, path: Path) -> None
def load_rig_meta(source: str | Path) -> RigMeta


@dataclass
class RigMeta:
    name: str
    kind: str = 'extended'
    parent: str | None = None
    foundation_source: str | None = None
    reproducible: bool = False
    created: str | None = None
    recipe: list[str] | None = None
```
