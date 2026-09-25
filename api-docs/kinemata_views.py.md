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
def _standalone_arm(entry: Any) -> tuple[bool, Any]
def _root_or_decoy(mode: str, attribute: str) -> Any
```
