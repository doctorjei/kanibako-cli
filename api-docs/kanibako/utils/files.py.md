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
def link_write_refusal(path: Path, *internal_roots: Path) -> str | None
def write_gitignore(project_path: Path) -> None
def unrestorable_link_note(path: Path) -> str
def gitignore_to_strip(project_path: Path) -> Path | None
def gitignore_holds_only_kanibako(gitignore: Path) -> bool
def strip_gitignore(gitignore: Path) -> bool
def stat_unless_gone(path: str | os.PathLike[str]) -> os.stat_result | None
def is_dir_unless_gone(path: str | os.PathLike[str]) -> bool
def is_file_unless_gone(path: str | os.PathLike[str]) -> bool
def iterdir_unless_gone(path: Path) -> list[Path] | None
def refuse_unreadable_file(path: str | os.PathLike[str]) -> None
def unreadable_path_error(path: str | os.PathLike[str], exc: OSError) -> ConfigError
def literal_path(value: str | os.PathLike[str]) -> str
def is_home_dir(path: str | os.PathLike[str]) -> bool
def logical_cwd() -> str
def _gitignore_kept_text(gitignore: Path) -> str
```
