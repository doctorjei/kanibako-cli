# `src/kanibako/targets/credsync.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/targets/credsync.py.md`.


## Variables

```
logger = get_logger('credsync')
```

## Functions
```
def seed_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> None
def refresh_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> None
def writeback_cred_files(descriptor: PluginDescriptor, target: Target, *, source_root: Path | None, project_home: Path) -> None
def selected_source_root(auth: AuthSource, *, host_home: Path) -> Path | None
def seed_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path, suppress_oauth: bool=False) -> None
def refresh_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path, suppress_oauth: bool=False) -> None
def writeback_box_credentials(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path, project_home: Path) -> None
def _chmod_600(path: Path) -> None
def _copy_dir(src: Path, dst: Path) -> None
def _sync_workset_dir_from_global(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path) -> None
def _sync_workset_dir_to_global(descriptor: PluginDescriptor, target: Target, *, auth: AuthSource, host_home: Path) -> None
def _create_workset_source_dirs(descriptor: PluginDescriptor, *, auth: AuthSource) -> None
def _cred_descriptor(descriptor: PluginDescriptor, *, suppress_oauth: bool) -> PluginDescriptor
```
