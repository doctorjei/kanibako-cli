# `src/kanibako/commands/clean.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _unregister_purged(std, proj, *, park: bool=False) -> None
def _unregister_purged_primary(std, metadata_path, project_path) -> None
def _warn_undeleted(path) -> None
def _resolve_snapshot_store(vault_rw, box_name: str | None)
def _remove_snapshot_store(store) -> bool
def _remove_member_vault_leaves(vault_ro, vault_rw) -> bool
def _parked_reentries(std, path: str) -> 'list[tuple[str, dict]]'
def _purge_parked(std, config, parked: 'tuple[str, dict]', *, force: bool) -> int
def _purge_one(std, config, path: str, *, force: bool) -> int
def _report_kept_channels(channels: 'list[tuple[str, Path, str | None]]') -> None
def _purge_by_plan(std, proj, *, force: bool) -> bool | None
def _purge_named_member(std, proj, *, force: bool, channels: 'list[tuple[str, Path, str | None]]') -> bool | None
def _purge_all(std, config, *, force: bool) -> int
```
