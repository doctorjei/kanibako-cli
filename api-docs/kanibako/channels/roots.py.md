# `src/kanibako/channels/roots.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def make_setup_channel_roots(std: StandardPaths) -> None
def make_workset_channel_roots(std: StandardPaths, name: str, root: Path) -> list[Path]
def make_box_inbox(std: StandardPaths, ws_token: str, ws_root: Path, box_name: str) -> Path | None
def make_proj_inbox(std: StandardPaths, proj: ProjectPaths) -> Path | None
def is_channel_root(std: StandardPaths, proj: ProjectPaths | None, path: Path) -> bool
def outermost_channel_root(std: StandardPaths, proj: ProjectPaths | None, path: Path) -> Path | None
def missing_channel_root_error(std: StandardPaths, proj: ProjectPaths) -> str | None
def refuse_linked_channel_sources(std: StandardPaths, proj: ProjectPaths | None, sources: Iterable[tuple[str, Path, bool]]) -> None
def _system_roots(std: StandardPaths) -> list[tuple[str, Path | None]]
def _workset_roots(std: StandardPaths, ws_token: str, ws_root: Path, *, local: bool=True) -> list[tuple[str, Path | None]]
def _setup_roots(std: StandardPaths) -> list[Path]
def _make_root(path: Path) -> bool
def _box_roots(std: StandardPaths, proj: ProjectPaths | None) -> list[tuple[str, Path | None]]
def _set_command(proj: ProjectPaths, key: str) -> str | None
def _as_owner(path: Path, command: str) -> str
def _blocking_dir(path: Path, outer: Path | None) -> Path
def _link_lines(key: str, link: Path, outer: Path | None, remake: Path | None, user_cure: str | None) -> list[str]
```

## Classes

```
class ChannelLinkError(KanibakoError):
```
