# `src/kanibako/settings/store_collapse.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**
Prose for these symbols lives in `llm-docs/kanibako/settings/store_collapse.py.md`.


## Variables

```
MASK: Final[CollapsedBind] = CollapsedBind(None, None)
CLI_PROVENANCE_SCOPE: Final[str] = 'cli'
HOME_DEST: Final[str] = normalize_bind_dest('~')
DERIVED_MOUNT: Final[str] = 'mount'
DERIVED_COPY: Final[str] = 'copy'
DERIVED_MASKED: Final[str] = 'masked'
DERIVED_SUPERSEDED: Final[str] = 'superseded'
DERIVED_AMBIGUOUS: Final[str] = 'ambiguous'
DERIVED_UNCOVERED: Final[str] = 'uncovered'
_KIND_BIND: Final[str] = 'bind'
_KIND_MASK: Final[str] = 'mask'
```

## Types
```
CollapsedBindings = dict[str, CollapsedBind]
CollapsedCopies = list[CollapsedCopy]
DeclaringKeys = dict[str, str]
CollapsedEnvs = dict[str, CollapsedEnv]
_MountKeys = dict[tuple[str, str, str], str]

```

## Functions
```
def collapse_store_shapes(store_shape_set: StoreShapeSet, home_bind: BindEntry, entries: Sequence[CategoryEntry] | None=None) -> CollapsedStore
def fold_opt(opts: str | None, token: str) -> str
def opt_tokens(opts: str | None) -> list[str]
def is_mask(bind: CollapsedBind) -> bool
def collapse_seeded(store_shape_set: StoreShapeSet) -> CollapsedCopies
def collapse_env(entries: list[CategoryEntry], cli_env: Mapping[str, str] | None=None) -> CollapsedEnvs
def is_within(dest: str, root: str) -> bool
def covering_bind(bindings: Mapping[str, CollapsedBind], dest: str) -> str | None
def refuse_uncovered_synced(bindings: CollapsedBindings, copies: CollapsedCopies) -> None
def pair_declarations(declarations: Sequence[Declaration], bindings: Mapping[str, CollapsedBind], copies: Sequence[CollapsedCopy]=()) -> tuple[Derivation, ...]
def derivation_result(row: Any, declared_by: Mapping[str, str] | None=None) -> str
def _mount_declaration_keys(entries: Sequence[CategoryEntry], store_shape_set: StoreShapeSet) -> _MountKeys
def _apply_cli_env(slots: CollapsedEnvs, cli_env: Mapping[str, str] | None) -> None
def _collapse_synced(store_shape_set: StoreShapeSet) -> CollapsedCopies
def _collapse_mounts(store_shape_set: StoreShapeSet, home_bind: BindEntry, mount_keys: _MountKeys) -> tuple[CollapsedBindings, DeclaringKeys]
def _merge_bindings(combined: CollapsedBindings, declared_by: DeclaringKeys, shape: StoreShape, scope: str, mount_keys: _MountKeys) -> None
def _declared_clause(key: str | None) -> str
def _claim(declared_by: DeclaringKeys, dest: str, key: str | None) -> None
def _scope_binds(shape: StoreShape) -> list[tuple[str, BindEntry, str]]
def _scope_masks(shape: StoreShape) -> list[str]
def _segments(dest: str) -> int
def _binds_under(combined: CollapsedBindings, dest: str) -> list[str]
def _masks_over(combined: CollapsedBindings, dest: str) -> list[str]
def _sweep(combined: CollapsedBindings, declared_by: DeclaringKeys, dest: str) -> None
def _refuse_bind_over_bind(combined: CollapsedBindings, declared_by: DeclaringKeys, dest: str, entry: BindEntry, key: str | None) -> None
def _refuse_bind_under_mask(combined: CollapsedBindings, declared_by: DeclaringKeys, dest: str, entry: BindEntry, key: str | None) -> None
def _refuse_mask_on_mask(combined: CollapsedBindings, declared_by: DeclaringKeys, dest: str, key: str | None) -> None
def _refuse_mask_over_home(dest: str, key: str | None) -> None
def _refuse_seed_outside_home(dest: str, entry: BindEntry) -> None
def _refuse_env_twin(arriving: CategoryEntry, held: CollapsedEnv) -> None
def _refuse_mode_contradiction(dest: str, entry: BindEntry, mode: str) -> None
def _pair_one(decl: Declaration, bindings: Mapping[str, CollapsedBind], copies: Sequence[CollapsedCopy], claims: Mapping[tuple[str, str | None], int]) -> Derivation
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
    declared_by: DeclaringKeys = field(default_factory=dict)

class Declaration(NamedTuple):
    key: str
    dest: str
    src: str | None
    delivery: str

class Derivation(NamedTuple):
    declaration: Declaration
    outcome: str
    at: str | None
    bind: CollapsedBind | None = None
    copy: CollapsedCopy | None = None
```
