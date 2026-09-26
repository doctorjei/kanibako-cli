# `src/kanibako/tree_copy.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def copy_tree_keeping_links(src: Path, dst: Path, *, ignore: Callable[[str, list[str]], Iterable[str]] | None=None, dirs_exist_ok: bool=False, replace_existing: bool=False) -> None
def failed_entries(err: shutil.Error) -> str | None
def _refusing_links_in_the_way(src: Path, dst: Path, ignore: Callable[[str, list[str]], Iterable[str]] | None, refused: list[tuple[str, str, str]]) -> Callable[[str, list[str]], set[str]]
def _replaced(src_name: str, dst_name: str, _why: str) -> bool
def _replace_link(path: str, text: str, stat_from: str) -> None
```
