# `src/kanibako/project/registry_store.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/registry_store.py.md`.


## Variables

```
_SECTIONS: tuple[str, ...] = ('worksets', 'standalone', 'deregistered', 'rigs', 'image_shells')
_NAME_SECTIONS: frozenset[str] = frozenset({'worksets'})
_SORTED_BLOB_SECTIONS: frozenset[str] = frozenset({'deregistered'})
```

## Functions
```
def load_registry(registry: Path) -> dict[str, dict]
def save_registry(registry: Path, sections: dict[str, dict]) -> None
def load_section(registry: Path, section: str) -> dict
def save_section(registry: Path, section: str, entries: dict) -> None
def load_standalone(registry: Path) -> dict[str, str]
def standalone_box_names(registry: Path) -> set[str]
def register_standalone(registry: Path, box_name: str, root: Path) -> None
def unregister_standalone(registry: Path, box_name: str) -> None
def standalone_name_for_root(registry: Path, root: Path) -> str | None
def load_deregistered(registry: Path) -> dict[str, dict]
def register_deregistered(registry: Path, box_name: str, *, kind: str, workspace: str | None, metadata: str, image: str | None=None, deregistered_at: str | None=None) -> None
def unregister_deregistered(registry: Path, box_name: str) -> bool
def lookup_deregistered(registry: Path, box_name: str) -> dict | None
def list_deregistered(registry: Path) -> dict[str, dict]
def _metadata_definitively_gone(path: str) -> bool
```
