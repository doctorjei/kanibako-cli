# `src/kanibako/settings/workset_dirkeys.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
WORKSET_PATH_REF = 'meta.workset.path'
WORKSET_EARLY_KEYS: frozenset[str] = frozenset({WORKSPACES_PATH, BOXES_PATH, LOGS_PATH, 'channelroot', 'registry', 'canon', 'template', 'vault_ro', 'vault_rw', *(f'channels.{leaf}' for leaf in DECLARED_WORKSET_CHANNEL_LEAVES)})
_USABLE_REFS = f"'@{WORKSET_PATH_REF}' (this workset's root) or another workset early key ('@workset.boxes', '@workset.channelroot', …)"
```

## Functions
```
def early_repoint(workset_root: Path, workset_settings: Mapping[str, Any] | None, key: str) -> tuple[str | None | _Unset, Path]
def resolve_workset_dir_key(workset_root: Path, repoint: str | None, default_leaf: str, *, key: str, where: Path | None=None, standalone: bool | None=None, workset_settings: Mapping[str, Any] | None=None) -> Path
def early_key_set_error(canonical: str, value: str | None, *, written_file: Path) -> str | None
def _stored_repoint(doc: Mapping[str, Any] | None, key: str) -> str | None | _Unset
def _host_ctx() -> ResolveCtx
def _expand_early(workset_root: Path, doc: Mapping[str, Any] | None, value: str, *, key: str, standalone: bool | None, chain: tuple[str, ...]) -> str
def _referent_value(workset_root: Path, doc: Mapping[str, Any] | None, referent: str, *, key: str, standalone: bool | None, chain: tuple[str, ...]) -> str
def _mode_default(referent: str, *, key: str, standalone: bool | None) -> str
```
