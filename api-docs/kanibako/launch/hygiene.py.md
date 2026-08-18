# `src/kanibako/launch/hygiene.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/hygiene.py.md`.


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
def _gzip_file(src: Path, dst: Path) -> None
def _remove_dir_contents(d: Path) -> None
def _dir_size(d: Path) -> int
def _fmt_size(nbytes: int) -> str
```
