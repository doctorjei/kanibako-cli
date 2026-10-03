# `src/kinemata_views.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
REF_WORKSET_PATH = '@meta.workset.path'
_DELIVERY_HEADS = frozenset({'bindings', 'masks', 'caches', 'seeded', 'synced', 'common', 'env', 'secret_path'})
_FIXED_SCOPES = frozenset({'config', 'system', 'workset', 'box'})
_CLI_TYPED = frozenset({'bool', 'int', 'path'})
_ROOT_ATTRIBUTE = {'primary': 'primary_workset', 'named': 'group_root', 'standalone': 'metadata_path'}
```

## Functions
```
def standalone_is_null(entry: Any) -> bool
def standalone_is_placeholder(entry: Any) -> bool
def never_settable_path(entry: Any) -> bool
def fixed_scope_key(entry: Any) -> bool
def cli_typed_key(entry: Any) -> bool
def cli_routed_key(entry: Any) -> bool
def bootstrap_path_row(entry: Any) -> bool
def every_mode_cell(floors: Any) -> dict[str, Any]
def ref_token_project(mode: str, *, workset_name: str, box_name: str) -> Any
def ref_token_standard_paths(mode: str) -> Any
def guest_bind_arm(binds: Any, arm: str) -> list[tuple[str, tuple[str, ...]]]
def sentinel_helper_binds() -> Any
def box_address_floor(mode: str) -> dict[str, Any]
@contextlib.contextmanager
def recording_launch_snapshots() -> Any
def existing_box_termini(std: Any, config_file: Any, proj: Any, target: Any, node: str) -> list[tuple[str, Any, Any]]
def fresh_launch_snapshots() -> Any
def snapshot_paths(snapshot: Any) -> Any
def declares_no_floor_value(entry: Any) -> bool
def system_value_row(entry: Any) -> bool
def spec_table(section: str, columns: tuple[str, ...]) -> list[dict[str, str]]
def system_settings_rows() -> list[tuple[str, str, str]]
def _standalone_arm(entry: Any) -> tuple[bool, Any]
def _root_or_decoy(mode: str, attribute: str) -> Any
def _keyspec_extract() -> Any
```
