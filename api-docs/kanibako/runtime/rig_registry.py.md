# `src/kanibako/runtime/rig_registry.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/rig_registry.py.md`.


## Variables

```
_SECTION = 'rigs'
_INNER_FIELDS: tuple[str, ...] = tuple((f.name for f in fields(RigRecord) if f.name != 'name'))
```

## Functions
```
def registry_path(std: StandardPaths) -> Path
def load_registry(path: Path) -> dict[str, RigRecord]
def save_registry(path: Path, records: dict[str, RigRecord]) -> None
def upsert(path: Path, record: RigRecord) -> None
def remove(path: Path, name: str) -> bool
def get(path: Path, name: str) -> RigRecord | None


@dataclass
class RigRecord:
    name: str
    kind: str
    source: str | None = None
    source_type: str | None = None
    image: str | None = None
    parent: str | None = None
    foundation_source: str | None = None
    reproducible: bool | None = None
    created: str | None = None
    added: str | None = None
```
