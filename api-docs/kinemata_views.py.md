# `src/kinemata_views.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_DELIVERY_HEADS = frozenset({'bindings', 'masks', 'caches', 'seeded', 'synced', 'common', 'env', 'secret_path'})
_FIXED_SCOPES = frozenset({'config', 'system', 'workset', 'box'})
_CLI_TYPED = frozenset({'bool', 'int', 'path'})
```

## Functions
```
def standalone_is_absent(entry: Any) -> bool
def never_settable_path(entry: Any) -> bool
def fixed_scope_key(entry: Any) -> bool
def cli_typed_key(entry: Any) -> bool
def cli_routed_key(entry: Any) -> bool
def bootstrap_path_row(entry: Any) -> bool
```
