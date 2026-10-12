# `src/kanibako/targets/credsync.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = get_logger('credsync')
_BOX_FILE_MAX_BYTES = 1 << 20
_BOX_DIR_MAX_BYTES = 4 << 20
_BOX_DIR_MAX_ENTRIES = 256
```

## Functions
```
def seed_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> None
def refresh_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> None
def writeback_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> bool
def selected_source_root(auth: AuthSource, *, host_home: Path) -> Path | None
def seed_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path, suppress_oauth: bool=False) -> None
def refresh_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path, suppress_oauth: bool=False) -> None
def writeback_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path) -> bool
def _chmod_600(path: Path) -> None
def _copy_dir(src: Path, dst: Path) -> None
def _warn_link_race(exc: boxfs.SymlinkRefusedError) -> None
def _box_file_stat(path: Path, root: Path) -> os.stat_result | None
def _file_into_box(src: Path, dst: Path, project_home: Path) -> None
def _transform_into_box(spec: CredFileSpec, target: Target, src: Path | None, dst: Path, project_home: Path) -> None
def _dir_into_box(src: Path, dst: Path, project_home: Path) -> None
def _dir_from_box(src: Path, dst: Path, project_home: Path) -> None
def _seed_one(spec: CredFileSpec, target: Target, src: Path | None, dst: Path, project_home: Path) -> None
def _refresh_one(spec: CredFileSpec, target: Target, src: Path, dst: Path, project_home: Path) -> None
def _writeback_one(spec: CredFileSpec, target: Target, project_home: Path, src: Path, dst: Path) -> None
def _box_dir_too_big(src: Path) -> bool
def _read_box_file(root: Path, rel: str, action: str='credential writeback') -> tuple[bytes, os.stat_result] | None
def _replace_store_file(spec: CredFileSpec, target: Target, data: bytes, box_stat: os.stat_result, dst: Path) -> None
def _write_backup(dst: Path, old: bytes) -> None
def _sync_workset_dir_from_global(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path) -> None
def _sync_workset_dir_to_global(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path) -> bool
def _box_from_source(primitive: Callable[..., None], descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path) -> None
def _create_workset_source_dirs(descriptor: PluginDescriptor, *, auth: AuthSource) -> None
def _cred_descriptor(descriptor: PluginDescriptor, *, suppress_oauth: bool) -> PluginDescriptor
```
