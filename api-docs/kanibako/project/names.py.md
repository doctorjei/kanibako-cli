# `src/kanibako/project/names.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = get_logger('names')
_WARNED_PATH_SHADOWS: set[tuple[str, str, str, str]] = set()
```

## Functions
```
def read_names(registry: Path) -> dict[str, dict[str, str]]
def register_name(registry: Path, name: str, path: str, section: str='worksets') -> None
def register_name_if_absent(registry: Path, name: str, path: str, section: str='worksets') -> None
def unregister_name(registry: Path, name: str, section: str='worksets', *, exact: bool=False) -> bool
def lookup_by_path(registry: Path, path: str) -> tuple[str, str] | None
def resolve_name(registry: Path, name: str, cwd: Path | None=None, primary_workset: Path | None=None, *, early_system: EarlySystem, path: str | None=None) -> tuple[str, str]
def resolve_qualified_name(registry: Path, qualified: str, *, early_system: EarlySystem) -> tuple[str, str]
def _load(registry: Path) -> dict[str, dict[str, str]]
def _save(registry: Path, names: dict[str, dict[str, str]]) -> None
def _early(early_system: EarlySystem, workset_name: str) -> EarlyScope
def _workset_member_paths(worksets: dict[str, str], name: str, *, early_system: EarlySystem) -> list[tuple[str, str, str]]
def _warn_standalone_shadow(registry: Path, name: str, path: str) -> str
def _records_member_at(boxes: dict[str, str], path: str) -> bool
def _space_member(names: dict[str, dict[str, str]], name: str, cwd: Path | None, *, early_system: EarlySystem) -> tuple[str | None, dict[str, str] | None]
def _primary_member(name: str, primary_workset: Path | None, *, early_system: EarlySystem) -> str | None
def _refuse_other_space_member(names: dict[str, dict[str, str]], name: str, *, early_system: EarlySystem) -> None
```
