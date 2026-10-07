# `src/kanibako/tree_copy.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def copy_tree_keeping_links(src: Path, dst: Path, *, ignore: Callable[[str, list[str]], Iterable[str]] | None=None, dirs_exist_ok: bool=False, replace_existing: bool=False, keep_root_link: bool=False) -> None
def removed_root_of(target: str, removed: Collection[Path]) -> Path | None
def lay_root_link(src: Path, dst: Path, *, removed: Collection[Path]=(), relocated: Mapping[Path, Path] | None=None) -> bool
def failed_entries(err: shutil.Error) -> str | None
def _copy_root_link(src: Path, dst: Path, *, dirs_exist_ok: bool, replace_existing: bool) -> None
def _relocated_target(target: str, relocated: Mapping[Path, Path]) -> str | None
def _is_under(path: str, root: Path) -> bool
def _refusing_links_in_the_way(src: Path, dst: Path, ignore: Callable[[str, list[str]], Iterable[str]] | None, refused: list[tuple[str, str, str]]) -> Callable[[str, list[str]], set[str]]
def _replaced(src_name: str, dst_name: str, _why: str) -> bool
def _replace_link(path: str, text: str, stat_from: str) -> None
```
