# `src/kanibako/launch/hygiene.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_WASTE_DIRS = ('.claude/telemetry', '.claude/debug')
_CACHE_WASTE_DIRS = ('.cache/claude', '.cache/sentry', '.cache/@anthropic')
_COMPRESS_AGE_DAYS = 7
```

## Functions
```
def cleanup_shell_dir(shell_dir: Path, dry_run: bool=False, agent_critical_dests: list[tuple[str, str]] | None=None) -> list[str]
def _clean_waste_dirs(shell_dir: Path, dry_run: bool, logger: object) -> list[str]
def _clean_cache_waste(shell_dir: Path, dry_run: bool, logger: object) -> list[str]
def _clean_duplicate_binaries(shell_dir: Path, dry_run: bool, logger: object) -> list[str]
def _find_claude_binaries(shell_dir: Path) -> list[Path]
def _reap_stale_agent_mountpoints(shell_dir: Path, agent_critical_dests: list[tuple[str, str]] | None, dry_run: bool, logger: object) -> list[str]
def _compress_old_logs(shell_dir: Path, dry_run: bool, logger: object) -> list[str]
def _conversation_logs(projects_dir: Path, root: Path) -> list[Path]
def _gzip_file(src: Path, dst: Path, root: Path) -> int
def _is_box_dir(path: Path, root: Path) -> bool
def _box_listdir(path: Path, root: Path) -> list[str]
def _forcing(remove: Callable[[], None], path: Path, root: Path) -> None
def _remove_dir_contents(d: Path, root: Path) -> None
def _remove_tree(d: Path, root: Path) -> None
def _dir_size(d: Path, root: Path) -> int
def _fmt_size(nbytes: int) -> str
```
