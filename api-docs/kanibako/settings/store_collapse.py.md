# `src/kanibako/settings/store_collapse.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/store_collapse.py.md`.


## Variables

```
MASK: Final[CollapsedBind] = CollapsedBind(None, None)
CLI_PROVENANCE_SCOPE: Final[str] = 'cli'
HOME_DEST: Final[str] = normalize_bind_dest('~')
```

## Types
```
CollapsedBindings = dict[str, CollapsedBind]
CollapsedCopies = list[CollapsedCopy]
CollapsedEnvs = dict[str, CollapsedEnv]

```

## Functions
```
def collapse_store_shapes(store_shape_set: StoreShapeSet, home_bind: BindEntry) -> CollapsedStore
def fold_opt(opts: str | None, token: str) -> str
def opt_tokens(opts: str | None) -> list[str]
def is_mask(bind: CollapsedBind) -> bool
def collapse_seeded(store_shape_set: StoreShapeSet) -> CollapsedCopies
def collapse_env(entries: list[CategoryEntry], cli_env: Mapping[str, str] | None=None) -> CollapsedEnvs
def is_within(dest: str, root: str) -> bool
def _apply_cli_env(slots: CollapsedEnvs, cli_env: Mapping[str, str] | None) -> None
def _collapse_synced(store_shape_set: StoreShapeSet) -> CollapsedCopies
def _collapse_mounts(store_shape_set: StoreShapeSet, home_bind: BindEntry) -> CollapsedBindings
def _merge_bindings(combined: CollapsedBindings, shape: StoreShape) -> None
def _scope_binds(shape: StoreShape) -> list[tuple[str, BindEntry, str]]
def _scope_masks(shape: StoreShape) -> list[str]
def _segments(dest: str) -> int
def _binds_under(combined: CollapsedBindings, dest: str) -> list[str]
def _masks_over(combined: CollapsedBindings, dest: str) -> list[str]
def _sweep(combined: CollapsedBindings, dest: str) -> None
def _refuse_bind_over_bind(combined: CollapsedBindings, dest: str, entry: BindEntry) -> None
def _refuse_bind_under_mask(combined: CollapsedBindings, dest: str, entry: BindEntry) -> None
def _refuse_mask_on_mask(combined: CollapsedBindings, dest: str) -> None
def _refuse_mask_over_home(dest: str) -> None
def _refuse_seed_outside_home(dest: str, entry: BindEntry) -> None
def _refuse_env_twin(arriving: CategoryEntry, held: CollapsedEnv) -> None
def _refuse_mode_contradiction(dest: str, entry: BindEntry, mode: str) -> None
```

## Classes

```
class CollapsedBind(NamedTuple):
    src: str | None
    opts: str | None

class CollapsedCopy(NamedTuple):
    src: str
    dest: str
    opts: str | None

class CollapsedEnv(NamedTuple):
    value: str
    scope: str
    key: str

@dataclass(frozen=True)
class CollapsedStore:
    bindings: CollapsedBindings
    seeded: CollapsedCopies
    synced: CollapsedCopies
```
