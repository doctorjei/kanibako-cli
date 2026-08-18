# `src/kanibako/snapshots.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/snapshots.py.md`.


## Variables

```
logger = get_logger('snapshots')
_DEFAULT_MAX_SNAPSHOTS = 5
```

## Functions
```
def detect_snapshot_strategy(vault_path: Path) -> str
def create_snapshot(vault_rw_path: Path, strategy: str='hardlink') -> Path | None
def list_snapshots(vault_rw_path: Path) -> list[tuple[str, str, int]]
def restore_snapshot(vault_rw_path: Path, snapshot_name: str) -> None
def prune_snapshots(vault_rw_path: Path, max_keep: int=_DEFAULT_MAX_SNAPSHOTS) -> int
def auto_snapshot(vault_rw_path: Path, *, strategy: str='hardlink', max_keep: int=_DEFAULT_MAX_SNAPSHOTS) -> Path | None
def _versions_dir(vault_rw_path: Path) -> Path
def _force_writable_dirs(root: Path) -> None
def _rmtree_force(path: Path) -> None
def _test_reflink(path: Path) -> bool
def _snapshot_reflink(vault_rw_path: Path, versions: Path, ts: str) -> Path
def _snapshot_hardlink(vault_rw_path: Path, versions: Path, ts: str) -> Path
```
