# `src/kanibako/utils.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/utils.py.md`.


## Variables

```
_DASH_ESCAPE = '-.'
_GITIGNORE_ENTRIES = ['box_data/']
```

## Functions
```
def cp_if_newer(src: str | os.PathLike, dst: str | os.PathLike) -> bool
def confirm_prompt(message: str) -> None
def short_hash(full_hash: str, length: int=8) -> str
def container_name_for(proj: ProjectPaths) -> str
def project_hash(project_path: str) -> str
def escape_path(path: str) -> str
def write_project_gitignore(project_path: Path) -> None
```
