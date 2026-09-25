# `src/kanibako/settings/settings_launch.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_launch.py.md`.


## Variables

```
SYSTEM_SCALAR_FLOOR: dict[str, object] = {f'system.helpers.{leaf}': value for leaf, value in SPAWN_BUDGET_DEFAULTS.items()}
BOX_HOME_KEY: Final[str] = 'meta.box.home'
SELECTION_KEY = 'system.agent'
_BIND_LEAF_CATEGORIES: frozenset[str] = frozenset({'caches', 'seeded', 'common', 'synced'})
_SCOPES: tuple[str, ...] = SCOPE_CONTAINMENT
_BIND_FLOOR_TAILS: tuple[str, ...] = ('.bindings.ro', '.bindings.rw') + tuple((f'.{c}' for c in sorted(_BIND_LEAF_CATEGORIES)))
_SYSTEM_SHARE_ALLOWED_KEY = 'system.auth.share_allowed'
_BOX_MODES: frozenset[str] = frozenset({'primary', 'named', 'standalone'})
_WORKSET_CHANNEL_LEAVES: frozenset[str] = frozenset({'common', 'chat', 'broadcast', 'share', 'mailboxes', 'share_global'})
_WORKSET_LOCAL_CHANNEL_LEAVES: frozenset[str] = _WORKSET_CHANNEL_LEAVES - {'mailboxes', 'share_global'}
_SETTINGS_FILE_NAMES: Final[str] = "the box's box.yaml, the workset's workset.yaml, the agent's agent.yaml, or the system settings.yaml"
_BOX_ROOT_KEY = 'meta.box.path'
_BOX_STORE_KEY = 'workset.boxes'
```

## Types
```
AuthTier = Literal['workset', 'global', 'box']
_TierFile = tuple[str, Path | None]
_WrittenLevel = tuple[KeyStore, Path | None, KeyStore | None]

```

## Functions
```
def auth_chain_floor(*, mode: str, agent_name: str) -> dict[str, object]
def meta_runtime_floor(*, mode: str, ws_name: str, ws_root_literal: str | None=None) -> dict[str, object]
def meta_agent_path_floor(agent_name: str) -> dict[str, object]
def meta_agent_grammar_floor(agent_name: str, descriptor: 'PluginDescriptor | None') -> dict[str, object]
def meta_identity_floor(*, box_name: str, project_path: str, inbox: str, share_global: str, share_workset: str | None, box_settings: str | None=None, agent_name: str | None=None, agent_real_name: str | None=None, agent_auth_share_support: bool=False) -> dict[str, object]
def workset_anchor_floor(*, mode: str, channelroot: str | None=None, workspaces: str | None=None, workset_channels: Mapping[str, str] | None=None) -> dict[str, object]
def resolve_auth_source(snapshot: KeyStore, *, mode: str | None=None) -> AuthSource
def refuse_read_time_faults(written: Sequence[_WrittenLevel], expanded: KeyStore, *, ctx: ResolveCtx, files: Sequence[_TierFile], subject: ResolveSubject) -> None
def build_launch_snapshot(*, agent_name: str, ctx: ResolveCtx, system_path: Path | None, agent_path: Path | None, workset_path: Path | None, box_path: Path | None, behavior_floor: Mapping[str, object] | None=None, default_categories: Mapping[str, object] | None=None, agent_partial: KeyStore | None=None, agent_state: AgentFileLevel | None=None, persona_values: Mapping[str, str] | None=None, auth_chain: Mapping[str, object] | None=None, meta_runtime: Mapping[str, object] | None=None, meta_identity: Mapping[str, object] | None=None, workset_anchor: Mapping[str, object] | None=None, prefs: 'Sequence[PrefRequest] | None'=None, valid_agents: 'Collection[str] | None'=None, cli_level: Mapping[str, object] | None=None) -> KeyStore
def resolve_selected_agent(*, ctx: ResolveCtx, system_path: Path | None, workset_path: Path | None, box_path: Path | None, prefs: 'Sequence[PrefRequest] | None'=None, valid_agents: 'Collection[str] | None'=None) -> object
def snapshot_leaf(snapshot: KeyStore, dotted: str) -> object
def behavior_pick(snapshot: KeyStore, *, active_agent: str, key: str) -> 'tuple[str | None, object]'
def behavior_slot(snapshot: KeyStore, *, active_agent: str, key: str) -> 'str | None'
def effective_behavior(snapshot: KeyStore, *, active_agent: str, keys: 'list[str] | None'=None) -> dict[str, str]
def meta_agent_grammar(snapshot: KeyStore, *, active_agent: str) -> AgentGrammar
def resolve_box_dest(raw: str, box_ctx: ResolveCtx) -> str
def snapshot_category_entries(snapshot: KeyStore, *, active_agent: str, box_ctx: ResolveCtx, optional_keys: frozenset[str]=frozenset()) -> list[CategoryEntry]
def _is_bind_floor_key(key: str) -> bool
def _read_auth_inputs(snapshot: KeyStore) -> _AuthInputs
def _materialize_auth_active(snapshot: KeyStore) -> None
def _loaded_tiers(files: Sequence[_TierFile]) -> tuple[tuple[str, Path], ...]
def _refuse_retired_spelling(tiers: Sequence[tuple[str, Path]]) -> None
def _refuse_undeclared_snapshot(store: KeyStore, *, files: Sequence[_TierFile], subject: ResolveSubject) -> None
def _path_key_leaves(store: KeyStore) -> list[tuple[str, object]]
def _refuse_ambiguous_path_values(written: Sequence[_WrittenLevel], expanded: KeyStore, *, ctx: ResolveCtx) -> None
def _assert_box_root_resolved(snapshot: KeyStore) -> None
def _materialize_box_agent_mirror(snapshot: KeyStore, *, active_agent: str) -> None
def _mirror_fill(box_node: KeyStore, agent_node: KeyStore) -> None
def _agent_state_partial(level: AgentFileLevel | None) -> KeyStore | None
def _persona_partial(agent_name: str, persona_values: Mapping[str, str] | None) -> KeyStore | None
def _fixed_decl_scope_fn(scope: str)
def _agent_decl_scope_fn(agent_node: object, active_agent: str)
def _agent_pick_node(snapshot: KeyStore, active_agent: str) -> KeyStore
def _overlay_into(base: KeyStore, top: KeyStore) -> None
def _assert_declared_categories(key_prefix: str, node: KeyStore) -> None
def _require_category_node(key_prefix: str, category: str, node: object) -> KeyStore
def _emit_scope_node(collected: list[tuple[tuple[int, str, str], CategoryEntry]], scope_node: KeyStore, *, order: int, scope: str, box_dest_fn, decl_scope_fn, optional_keys: frozenset[str]=frozenset()) -> None
def _emit_bind_map(collected: list[tuple[tuple[int, str, str], CategoryEntry]], map_node: KeyStore, *, order: int, scope: str, category: str, box_dest_fn, decl_scope_fn, optional_keys: frozenset[str]=frozenset()) -> None
def _emit_bind(collected: list[tuple[tuple[int, str, str], CategoryEntry]], order: int, scope: str, category: str, name: str, host_src: str, box_dest_raw: str, opts: str | None, box_dest_fn, *, key_segments: tuple[str, ...], optional_keys: frozenset[str]=frozenset()) -> None
def _no_lookup(ref: str, chain: tuple[str, ...]) -> str
```

## Classes

```
@dataclass(frozen=True)
class AuthSource:
    tier: AuthTier
    global_enabled: bool
    workset_enabled: bool
    global_sync: bool
    workset_source: str | None

    @property
    def creds_shared(self) -> bool

class ResolveSubject(Enum):
    BOX = ('this box', "'kanibako box reset <key>' cannot remove what is not a key, and 'kanibako box show --effective' resolves through this same seam, so it refuses too.")
    WORKSET = ('this working set', "'kanibako workset reset <workset> <key>' cannot remove what is not a key, and 'kanibako workset show --effective' and 'kanibako workset share list --effective' resolve through this same seam, so they refuse too.")

    def __init__(self, what: str, cure_note: str) -> None

class AgentGrammar(NamedTuple):
    mode: dict[str, list[str]]
    exec_fragment: 'list[str] | None'

@dataclass(frozen=True)
class _AuthInputs:
    support: bool
    system_allow: bool
    workset_allow: bool
    global_sync: bool
    global_knob: bool
    workset_knob: bool
```
