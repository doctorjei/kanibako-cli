# `src/kanibako/snapshots.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = get_logger('snapshots')
UNSORTED_DIRNAME = '.unsorted'
LAYOUT_MARKER_NAME = '.layout'
LAYOUT_MARKER_VALUE = 'per-box-v1\n'
RESERVED_STORE_KEYS = (UNSORTED_DIRNAME, LAYOUT_MARKER_NAME)
_DEFAULT_MAX_SNAPSHOTS = 5
_LEGACY_TS_RE = re.compile('^\\d{8}T\\d{6}Z$')
```

## Functions
```
def migrate_legacy_versions(vault_rw_path: Path, *, box_name: str, store_exclusive: bool=False) -> dict[str, list[str]]
def detect_snapshot_strategy(vault_path: Path) -> str
def create_snapshot(vault_rw_path: Path, *, box_name: str, strategy: str='hardlink', store_exclusive: bool=False) -> Path | None
def list_snapshots(vault_rw_path: Path, *, box_name: str) -> list[tuple[str, str, int]]
def list_unsorted(vault_rw_path: Path) -> list[tuple[str, str, int]]
def adopt_standalone_store(vault_rw_path: Path, *, box_name: str) -> Path | None
def relocate_snapshot_store(old_vault_rw: Path, new_vault_rw: Path, *, old_box: str, new_box: str) -> Path | None
def restore_snapshot(vault_rw_path: Path, snapshot_name: str, *, box_name: str, store_exclusive: bool=False) -> Path | None
def snapshots_to_prune(vault_rw_path: Path, max_keep: int, *, box_name: str) -> list[Path]
def prune_snapshots(vault_rw_path: Path, max_keep: int=_DEFAULT_MAX_SNAPSHOTS, *, box_name: str) -> int
def auto_snapshot(vault_rw_path: Path, *, box_name: str, strategy: str='hardlink', max_keep: int=_DEFAULT_MAX_SNAPSHOTS, store_exclusive: bool=False) -> Path | None
def _versions_dir(vault_rw_path: Path) -> Path
def _box_store(vault_rw_path: Path, box_name: str) -> Path
def _is_migrated(versions: Path) -> bool
def _write_layout_marker(versions: Path) -> None
def _snapshot_child(versions: Path, name: str) -> Path
def _force_writable_dirs(root: Path) -> None
def _rmtree_force(path: Path) -> None
def _test_reflink(path: Path) -> bool
def _snapshot_reflink(vault_rw_path: Path, versions: Path, ts: str) -> Path
def _snapshot_hardlink(vault_rw_path: Path, versions: Path, ts: str) -> Path
def _unique_snapshot_name(store: Path, ts: str) -> str
def _snapshot_entries(store: Path) -> list[tuple[str, str, int]]
def _find_snapshot_owner(vault_rw_path: Path, snapshot_name: str) -> str | None
```

## Classes

```
class UnsafeSnapshotNameError(KanibakoError):

class ForeignSnapshotError(KanibakoError):

class SnapshotSafetyError(KanibakoError):
```
