# `src/kanibako/settings/workset_dirkeys.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
WORKSET_PATH_REF = 'meta.workset.path'
WORKSET_EARLY_KEYS: frozenset[str] = frozenset({WORKSPACES_PATH, BOXES_PATH, LOGS_PATH, 'channelroot', 'registry', 'canon', 'template', 'vault_ro', 'vault_rw', *(f'channels.{leaf}' for leaf in DECLARED_WORKSET_CHANNEL_LEAVES)})
```

## Functions
```
def resolve_workset_dir_key(workset_root: Path, repoint: str | None, default_leaf: str, *, key: str, extra_refs: Mapping[str, str] | None=None, where: Path | None=None) -> Path
def early_key_set_error(canonical: str, value: str | None, *, written_file: Path) -> str | None
def _host_ctx() -> ResolveCtx
```
