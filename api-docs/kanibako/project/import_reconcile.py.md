# `src/kanibako/project/import_reconcile.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/import_reconcile.py.md`.


## Variables

```
_STANDALONE_BOX_DIR = 'box_data'
```

## Functions
```
def import_standalone(registry: Path, root: Path, *, journal: Path | None=None) -> str | None
def import_named_workset(registry: Path, root: Path, *, journal: Path | None=None) -> str | None
def _alert(mode: str, name: str, path: Path) -> None
def _conflict(mode: str, name: str, new_path: Path, existing_path: str) -> ImportConflictError
@contextmanager
def _journal_register(journal: Path | None, box_path: Path, *, op: str, name: str, mode: str, workset: str | None=None)
def _clear_stale_import(journal: Path | None, box_path: Path) -> None


class ImportConflictError(KanibakoError):
    ...
```
