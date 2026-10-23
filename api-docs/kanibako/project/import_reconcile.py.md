# `src/kanibako/project/import_reconcile.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/import_reconcile.py.md`.


## Types
```
_Answer = TypeVar('_Answer', bound=DetectionResult | None)

```

## Functions
```
def refuse_second_link(registry: Path, root: Path) -> None
def refuse_standalone_spelling(registry: Path, root: Path) -> None
def check_standalone(registry: Path, root: Path, *, journal: Path | None=None, early: 'EarlyScope') -> None
def reconcile(walk: Callable[[], _Answer | Treewalk[_Answer]], std: StandardPaths) -> _Answer
def import_pass(walk: Callable[[], _Answer | Treewalk[_Answer]], std: StandardPaths) -> _Answer
def import_standalone(registry: Path, root: Path, *, journal: Path | None=None, early: 'EarlyScope') -> str | None
def import_named_workset(registry: Path, root: Path, *, journal: Path | None=None) -> str | None
def _alert(mode: str, name: str, path: Path) -> None
def _conflict(mode: str, name: str, new_path: Path, existing_path: str) -> ImportConflictError
@contextmanager
def _journal_register(journal: Path | None, box_path: Path, *, op: str, name: str, mode: str, workset: str | None=None)
def _clear_stale_import(journal: Path | None, box_path: Path) -> None
def _clear_stale_standalone(registry: Path, root: Path, journal: Path | None, early: 'EarlyScope') -> None
def _import_levels(subject: Path, std: StandardPaths) -> bool
```

## Classes

```
class ImportConflictError(KanibakoError):
```
