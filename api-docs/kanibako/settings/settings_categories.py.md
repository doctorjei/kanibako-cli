# `src/kanibako/settings/settings_categories.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_categories.py.md`.


## Variables

```
COPY: Final[Delivery] = 'COPY'
MOUNT: Final[Delivery] = 'MOUNT'
ENV: Final[Delivery] = 'ENV'
BARE_RELATIVE_SOURCE_HAZARD: Final[str] = "a relative source resolves against whatever directory kanibako happens to be run from, and for a MOUNT podman reads a source beginning with neither '.' nor '/' as the name of a NAMED VOLUME rather than as a host path at all"
SECRET_MOUNT_DIR: Final[str] = '/run/kanibako/secrets'
SETTABLE_BIND_CATEGORIES: Final[tuple[str, ...]] = ()
ABSTRACT_CATEGORIES: Final[tuple[str, ...]] = ('common', 'caches', 'seeded')
DECLARATION_ROOT_REF: Final[Mapping[str, str]] = {'system': '@config.data', 'agent': '@meta.agent.{agent}.path', 'workset': '@meta.workset.path', 'box': '@meta.box.path'}
CATEGORY_FAMILY_ROOTS: Final[frozenset[str]] = frozenset((name.split('.', 1)[0] for name in _DELIVERY))
RETIRED_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple((c for c in _BIND_CATEGORIES if c not in SETTABLE_BIND_CATEGORIES))
SCOPE_BIND_KEY_RE = re.compile(f'^(?P<scope>{_FILE_SCOPE_ALT})\\.(?P<category>{_RETIRED_CATEGORY_ALT})\\.(?P<name>.+)$')
AGENT_BIND_KEY_RE = re.compile(f'^agent\\.(?P<node>.+?)\\.(?P<category>{_RETIRED_CATEGORY_ALT})\\.(?P<name>.+)$')
BIND_KEY_RE = re.compile(f'^(?P<scope>{_FILE_SCOPE_ALT}|{_AGENT_SCOPE})\\.(?P<category>{_NON_TERMINAL_CATEGORY_ALT})\\.(?P<name>.+)$' if _NON_TERMINAL_CATEGORY_ALT else '(?!)')
MASK_KEY_RE = re.compile(f'^(?P<scope>system|workset|box|{_AGENT_SCOPE})\\.masks$')
ENV_KEY_RE = re.compile(f'^(?P<scope>system|workset|box|{_AGENT_SCOPE})\\.env\\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$')
SECRET_KEY_RE = re.compile('^(?P<scope>system|agent|workset|box)\\.secret_path\\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$')
SECRET_VAR_RE = re.compile('^[A-Za-z_][A-Za-z0-9_]*$')
CONCRETE_CATEGORIES: Final[tuple[str, ...]] = ('bindings.ro', 'bindings.rw', 'secret_path')
SUPPRESS_THEN_ADD: Final[str] = 'To change what occupies a destination you must SUPPRESS the entry you do not want and then declare the one you do. An override is not enough: these are two different KEYS, so both survive the cascade. Set the unwanted key to null in the settings file for its scope (a file may write its own scope and the scopes it contains)'
_BIND_CATEGORIES: Final[tuple[str, ...]] = ('bindings.ro', 'bindings.rw', 'caches', 'seeded', 'common', 'synced')
_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = _BIND_CATEGORIES
_NON_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple((c for c in _BIND_CATEGORIES if c not in _TERMINAL_BIND_CATEGORIES))
_DELIVERY: dict[str, Delivery] = {'masks': MOUNT, 'bindings.ro': MOUNT, 'bindings.rw': MOUNT, 'caches': MOUNT, 'seeded': COPY, 'common': MOUNT, 'synced': COPY, 'env': ENV, 'secret_path': MOUNT}
_AGENT_SCOPE = 'agent\\.[^.]+'
_FILE_SCOPE_ALT = 'system|workset|box'
_NON_TERMINAL_CATEGORY_ALT = '|'.join((c.replace('.', '\\.') for c in _NON_TERMINAL_BIND_CATEGORIES))
_RETIRED_CATEGORY_ALT = '|'.join((c.replace('.', '\\.') for c in RETIRED_BIND_CATEGORIES))
_SCOPE_APPLY_ORDER = {'system': 0, 'agent': 1, 'workset': 2, 'box': 3}
_RULE_CHANGE_RELEASE: Final[str] = '1.8.0'
_REMEDY_WRAP: Final[int] = 80
```

## Types
```
Delivery = Literal['COPY', 'MOUNT', 'ENV']

```

## Functions
```
def is_read_only(options: str | None) -> bool
def path_depth(box_dest: str) -> int
def gate_credential_delivery(entries: list[CategoryEntry], deliver_creds: bool) -> list[CategoryEntry]
def secret_path_winners(entries: list[CategoryEntry]) -> list[CategoryEntry]
def secret_path_deliveries(entries: list[CategoryEntry]) -> list[CategoryEntry]
def launch_deliveries(entries: list[CategoryEntry], *, agent_dests: frozenset[str], narrow_bindings: 'dict[str, object] | None'=None, declared_by: 'dict[str, str] | None'=None) -> LaunchDeliveries
def narrow_table_winners(entries: list[CategoryEntry], dests: frozenset[str]) -> list[CategoryEntry]
def raise_binding_vs_binding(box_dest: str, concrete: list[CategoryEntry]) -> NoReturn
def raise_extension_onto_occupied(box_dest: str, *, extension: CategoryEntry, base: CategoryEntry) -> NoReturn
def derive_binding_keys(entries: list[CategoryEntry]) -> dict[tuple[str, ...], 'Bind']
def declaration_delivery(decl_key: str) -> Delivery
def effective_bindings_and_template_sources(snapshot: 'KeyStore') -> 'tuple[Any, ...]'
def _bind_options(category: str) -> str
def _most_specific(entries: list[CategoryEntry]) -> CategoryEntry
def _entry_lines(entries: list[CategoryEntry]) -> str
def _rule_changed(body: str) -> str
def _suppress_then_add(occupant_segments: tuple[str, ...], *, ambiguous: bool=False) -> str
def _assembly_copy_list(snapshot: 'KeyStore', dotted: str) -> list[Any]
```

## Classes

```
@dataclass(frozen=True)
class CategoryEntry:
    category: str
    scope: str
    box_dest: str
    host_src: str | None
    delivery: Delivery
    options: str
    name: str
    key_segments: tuple[str, ...]
    is_credential: bool = False
    optional: bool = False

    @property
    def key(self) -> str

@dataclass(frozen=True)
class CategoryCollision:
    box_dest: str
    scope: str
    winner_key: str
    loser_keys: tuple[str, ...]

    def message(self) -> str

@dataclass(frozen=True)
class LaunchDeliveries:
    secrets: list[CategoryEntry]
    agent_dests: frozenset[str]
    narrow_bindings: 'dict[str, object] | None' = None
    declared_by: 'dict[str, str]' = field(default_factory=dict)
```
