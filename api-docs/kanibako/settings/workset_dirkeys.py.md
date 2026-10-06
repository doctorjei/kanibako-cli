# `src/kanibako/settings/workset_dirkeys.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
WORKSET_PATH_REF = 'meta.workset.path'
WORKSET_NAME_REF = 'meta.workset.name'
WORKSET_EARLY_KEYS: frozenset[str] = frozenset({WORKSPACES_PATH, BOXES_PATH, LOGS_PATH, 'channelroot', 'registry', 'canon', 'template', 'vault_ro', 'vault_rw', *(f'channels.{leaf}' for leaf in DECLARED_WORKSET_CHANNEL_LEAVES)})
_USABLE_REFS = f"'@{WORKSET_PATH_REF}' (this workset's root), '@{WORKSET_NAME_REF}' (its partition name), a system path ('@system.channels.mailboxes', …), or another workset early key ('@workset.boxes', '@workset.channelroot', …)"
```

## Functions
```
def early_repoint(workset_root: Path, workset_settings: Mapping[str, Any] | None, key: str, *, early: EarlyScope) -> tuple[str | None | _Unset, Path]
def refuse_inherited_per_owner(workset_root: Path, early: EarlyScope, *, doc: Mapping[str, Any] | None | _Unset=UNSET) -> None
def early_tier(doc: Mapping[str, Any] | None) -> dict[str, str | None]
def early_system(set_values: Mapping[str, str | None], resolved: Mapping[str, Path], *, system_refusal: str | None=None) -> EarlySystem
def resolve_workset_dir_key(workset_root: Path, repoint: str | None, default_leaf: str, *, key: str, where: Path | None=None, standalone: bool | None=None, workset_settings: Mapping[str, Any] | None=None, early: EarlyScope) -> Path
def early_key_set_error(canonical: str, value: str | None, *, written_file: Path, standalone_reads: bool, early_system: EarlySystem | None, std_error: str | None=None, workset_name: str | None=None) -> str | None
def _stored_repoint(doc: Mapping[str, Any] | None, key: str, *, where: Path | None=None) -> str | None | _Unset
def _refuse_unanchored(workset_settings: Mapping[str, Any] | None, key: str, value: str, *, early: EarlyScope, where: Path | None=None) -> None
def _host_ctx() -> ResolveCtx
def _expand_early(workset_root: Path, doc: Mapping[str, Any] | None, value: str, *, key: str, standalone: bool | None, chain: tuple[str, ...], early: EarlyScope) -> str
def _system_path(ref: str, system: EarlySystem) -> str
def _referent_value(workset_root: Path, doc: Mapping[str, Any] | None, referent: str, *, key: str, standalone: bool | None, chain: tuple[str, ...], early: EarlyScope) -> str
def _declared_default(key: str) -> object
def _mode_default(referent: str, *, key: str, standalone: bool | None) -> str
def _reader_modes(key: str, *, standalone_reads: bool) -> tuple[bool | None, ...]
def _door_scope(early_system: EarlySystem, workset_name: str | None, *, standalone: bool | None) -> EarlyScope
```

## Classes

```
@dataclass(frozen=True)
class EarlySystem:
    tier: dict[str, str | None]
    file: Path
    system_paths: dict[str, str | None]
    system_refusal: str | None = None

class EarlyScope(NamedTuple):
    system: EarlySystem
    workset_name: str
```
