# `src/kanibako/launch/journal.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/journal.py.md`.


## Variables

```
_ENTRIES = 'entries'
_IMPORT_OPS = ('import', 'connect')
```

## Functions
```
def read_journal(journal_path: Path) -> dict[str, dict]
def write_entry(journal_path: Path, box_path: str | Path, *, op: str, name: str, mode: str, workset: str | None=None, workspace: str | None=None) -> None
def clear_entry(journal_path: Path, box_path: str | Path) -> None
def pending_entry(journal_path: Path, box_path: str | Path) -> dict | None
def pending_create(journal_path: Path, box_path: str | Path) -> dict | None
def pending_create_for_workspace(journal_path: Path, workspace: str | Path) -> dict | None
def pending_import(journal_path: Path, box_path: str | Path) -> dict | None
def _key(box_path: str | Path) -> str
```
