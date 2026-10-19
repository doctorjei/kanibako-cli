# `src/kanibako/utils/files.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_GITIGNORE_ENTRIES = [f'{STANDALONE_META_DIR}/']
```

## Functions
```
def path_hash(project_path: str) -> str
def write_gitignore(project_path: Path) -> None
def gitignore_to_strip(project_path: Path) -> Path | None
def gitignore_holds_only_kanibako(gitignore: Path) -> bool
def strip_gitignore(gitignore: Path) -> bool
def _gitignore_kept_text(gitignore: Path) -> str
```
