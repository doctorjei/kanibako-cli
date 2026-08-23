# `src/kanibako/settings/settings_keyspace.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_keyspace.py.md`.


## Variables

```
KEYSPACE_VERSION: Final[str] = '1.8.0rc'
DECLARED_CONFIG_LEAVES: Final[frozenset[str]] = frozenset({'data', 'settings', 'agents', 'primary_workset', 'registry', 'journal'})
DECLARED_SYSTEM_LEAVES: Final[frozenset[str]] = frozenset({'channelroot', 'template', 'canon', 'backup', 'cache', 'runtime', 'agent', 'setup_completed'})
DECLARED_SYSTEM_CHANNEL_LEAVES: Final[frozenset[str]] = frozenset({'common', 'chat', 'broadcast', 'mailboxes', 'share'})
DECLARED_SYSTEM_AUTH_LEAVES: Final[frozenset[str]] = frozenset({'share_allowed'})
DECLARED_BOX_LEAVES: Final[frozenset[str]] = frozenset({'image', 'share_images', 'images_store', 'canon', 'shell', 'enable_vault'})
DECLARED_BOX_AUTH_LEAVES: Final[frozenset[str]] = frozenset({'global_enabled', 'workset_enabled'})
DECLARED_WORKSET_LEAVES: Final[frozenset[str]] = frozenset({'workspaces', 'boxes', 'logs', 'registry', 'template', 'canon', 'kuid', 'skip_kuid_check', 'vault_ro', 'vault_rw', 'channelroot'})
DECLARED_WORKSET_AUTH_LEAVES: Final[frozenset[str]] = frozenset({'share_allowed', 'global_sync', 'path'})
DECLARED_WORKSET_CHANNEL_LEAVES: Final[frozenset[str]] = frozenset({'common', 'chat', 'broadcast', 'share', 'mailboxes', 'share_global'})
DECLARED_AGENT_LEAVES: Final[frozenset[str]] = frozenset({'access', 'allow_helpers', 'continue_mode', 'bootstrap', 'model', 'run_args', 'transform', 'transform_settings', 'endpoint', 'template', 'canon'})
TABLE_VALUED_AGENT_LEAVES: Final[frozenset[str]] = frozenset({'transform_settings'})
SCALAR_AGENT_LEAVES: Final[frozenset[str]] = DECLARED_AGENT_LEAVES - TABLE_VALUED_AGENT_LEAVES
ACCESS_TIERS: Final[tuple[str, ...]] = ('restricted', 'editing', 'full')
BIND_CATEGORIES: Final[frozenset[str]] = frozenset({'bindings.ro', 'bindings.rw', 'caches', 'seeded', 'common', 'synced'})
TERMINAL_CATEGORY_TAILS: Final[frozenset[tuple[str, ...]]] = frozenset({('masks',), ('bindings', 'ro'), ('bindings', 'rw'), ('caches',), ('seeded',), ('common',), ('synced',)})
DECLARED_META_RUNTIME_LEAVES: Final[frozenset[str]] = frozenset({'ws_root', 'ws_name', 'project_type'})
DECLARED_META_ASSEMBLY_LEAVES: Final[frozenset[str]] = frozenset({'bindings', 'seeded', 'synced', 'env'})
DECLARED_META_WORKSET_LEAVES: Final[frozenset[str]] = frozenset({'path', 'name', 'settings'})
DECLARED_META_BOX_LEAVES: Final[frozenset[str]] = frozenset({'path', 'name', 'mode', 'workspace', 'settings', 'inbox', 'share_global', 'share_workset', 'home', 'container_name', 'helper_num'})
DECLARED_META_BOX_AUTH_LEAVES: Final[frozenset[str]] = frozenset({'workset_path'})
DECLARED_META_AGENT_LEAVES: Final[frozenset[str]] = frozenset({'name', 'path', 'settings', 'mode', 'exec'})
DECLARED_META_AGENT_AUTH_LEAVES: Final[frozenset[str]] = frozenset({'share_support'})
RESERVED_LEAF_NAMES: Final[frozenset[str]] = KeyStore.RESERVED_KEY_NAMES
RETIRING_KEYS: Final[frozenset[str]] = frozenset()
KEYSPACE_ROOTS: Final[frozenset[str]] = frozenset(SCOPE_CONTAINMENT) | {'config', 'meta', 'pref', BINDING_DERIVATIONS_NODE}
RESERVED_NODE_REASON: Final[str] = f'{BINDING_DERIVATIONS_NODE!r} is the RESERVED INTERNAL NODE the spec names in so many words (§0, ABSTRACT declarations): the materialised binding an abstract declaration derives is machinery output, not a settable surface. Its interior is declaration keys and box DESTINATIONS, which are data.'
MAX_STORE_DEPTH: Final[int] = 64
_DUNDER_RE: Final = re.compile('^__.*__$')
_VAR_RE: Final = re.compile('^[A-Za-z_][A-Za-z0-9_]*$')
```

## Functions
```
def access_default() -> str
def is_terminal_category_tail(tail: Sequence[str]) -> bool
def is_terminal_category_key(key: str) -> bool
def leaf_name_reason(leaf: str) -> str | None
def is_valid_agent_segment(segment: str, valid_agents: Collection[str]) -> bool
def key_validity(key: str, *, valid_agents: Collection[str], agent_leaves: 'Collection[str] | None'=None) -> str | None
def render_store_path(segments: Collection[str], key_len: int | None=None) -> str
def classify_store_path(segments: tuple[str, ...], *, oracle: Callable[[str], str | None]) -> Judgement
def container_notes(nodes: Mapping[tuple[str, ...], StoreNode]) -> dict[tuple[str, ...], str]
def walk_store_paths(node: KeyStore[Any], prefix: tuple[str, ...]=()) -> Iterator[tuple[tuple[str, ...], bool]]
def undeclared_store_paths(store: KeyStore[Any], *, oracle: Callable[[str], str | None]) -> list[tuple[tuple[str, ...], Judgement]]
def _category_reason(prefix: str, rest: list[str], *, what: str) -> str | None
def _looks_like_category(rest: list[str]) -> bool
def _scope_reason(scope: str, rest: list[str], *, leaves: frozenset[str], sub_tables: dict[str, frozenset[str]], what: str) -> str | None
def _meta_reason(rest: list[str], valid_agents: Collection[str], leaves: Collection[str]=DECLARED_AGENT_LEAVES) -> str | None
def _bad_agent_reason(name: str, valid_agents: Collection[str]) -> str
def _agent_tail_reason(prefix: str, tail: list[str], leaves: Collection[str]=DECLARED_AGENT_LEAVES) -> str | None
```

## Classes

```
class Verdict:
    DECLARED = 'DECLARED'
    VALUE = 'VALUE'
    DATA_SEGMENT = 'DATA_SEGMENT'
    CONTAINER = 'CONTAINER'
    UNROOTED = 'UNROOTED'
    RESERVED = 'RESERVED'
    UNDECLARED = 'UNDECLARED'

class Judgement(NamedTuple):
    verdict: str
    key: str
    key_len: int
    note: str

class StoreNode(NamedTuple):
    verdict: str
    is_node: bool
```
