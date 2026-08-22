# `src/kanibako/project/names.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/names.py.md`.


## Variables

```
logger = get_logger('names')
```

## Functions
```
def cross_kind_shadow_hatch(name: str) -> str
def read_names(registry: Path) -> dict[str, dict[str, str]]
def register_name(registry: Path, name: str, path: str, section: str='worksets') -> None
def register_name_if_absent(registry: Path, name: str, path: str, section: str='worksets') -> None
def unregister_name(registry: Path, name: str, section: str='worksets') -> bool
def lookup_by_path(registry: Path, path: str) -> tuple[str, str] | None
def resolve_name(registry: Path, name: str, cwd: Path | None=None, primary_workset: Path | None=None) -> tuple[str, str]
def resolve_qualified_name(registry: Path, qualified: str) -> tuple[str, str]
def _load(registry: Path) -> dict[str, dict[str, str]]
def _save(registry: Path, names: dict[str, dict[str, str]]) -> None
def _workset_member_paths(worksets: dict[str, str], name: str) -> list[str]
```
