# `src/kanibako/settings/settings_categories.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_categories.py.md`.

```python
Delivery = Literal['COPY', 'MOUNT', 'ENV']

COPY: Final[Delivery] = 'COPY'

MOUNT: Final[Delivery] = 'MOUNT'

ENV: Final[Delivery] = 'ENV'

SECRET_MOUNT_DIR: Final[str] = '/run/kanibako/secrets'

_BIND_CATEGORIES: Final[tuple[str, ...]] = ('bindings.ro', 'bindings.rw', 'caches', 'seeded', 'common', 'synced')

_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = _BIND_CATEGORIES

_NON_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple((c for c in _BIND_CATEGORIES if c not in _TERMINAL_BIND_CATEGORIES))

SETTABLE_BIND_CATEGORIES: Final[tuple[str, ...]] = ()

ABSTRACT_CATEGORIES: Final[tuple[str, ...]] = ('common', 'caches', 'seeded')

DECLARATION_ROOT_REF: Final[Mapping[str, str]] = {'system': '@config.data', 'agent': '@meta.agent.{agent}.path', 'workset': '@meta.workset.path', 'box': '@meta.box.path'}

_DELIVERY: dict[str, Delivery] = {'masks': MOUNT, 'bindings.ro': MOUNT, 'bindings.rw': MOUNT, 'caches': MOUNT, 'seeded': COPY, 'common': MOUNT, 'synced': COPY, 'env': ENV, 'secret_path': MOUNT}

_AGENT_SCOPE = 'agent\\.[^.]+'

_FILE_SCOPE_ALT = 'system|workset|box'

_NON_TERMINAL_CATEGORY_ALT = '|'.join((c.replace('.', '\\.') for c in _NON_TERMINAL_BIND_CATEGORIES))

RETIRED_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple((c for c in _BIND_CATEGORIES if c not in SETTABLE_BIND_CATEGORIES))

_RETIRED_CATEGORY_ALT = '|'.join((c.replace('.', '\\.') for c in RETIRED_BIND_CATEGORIES))

SCOPE_BIND_KEY_RE = re.compile(f'^(?P<scope>{_FILE_SCOPE_ALT})\\.(?P<category>{_RETIRED_CATEGORY_ALT})\\.(?P<name>.+)$')

AGENT_BIND_KEY_RE = re.compile(f'^agent\\.(?P<node>.+?)\\.(?P<category>{_RETIRED_CATEGORY_ALT})\\.(?P<name>.+)$')

BIND_KEY_RE = re.compile(f'^(?P<scope>{_FILE_SCOPE_ALT}|{_AGENT_SCOPE})\\.(?P<category>{_NON_TERMINAL_CATEGORY_ALT})\\.(?P<name>.+)$' if _NON_TERMINAL_CATEGORY_ALT else '(?!)')

MASK_KEY_RE = re.compile(f'^(?P<scope>system|workset|box|{_AGENT_SCOPE})\\.masks$')

ENV_KEY_RE = re.compile(f'^(?P<scope>system|workset|box|{_AGENT_SCOPE})\\.env\\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$')

SECRET_KEY_RE = re.compile('^(?P<scope>system|agent|workset|box)\\.secret_path\\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$')

SECRET_VAR_RE = re.compile('^[A-Za-z_][A-Za-z0-9_]*$')

_SCOPE_APPLY_ORDER = {'system': 0, 'agent': 1, 'workset': 2, 'box': 3}

CONCRETE_CATEGORIES: Final[tuple[str, ...]] = ('bindings.ro', 'bindings.rw', 'secret_path')

_RULE_CHANGE_RELEASE: Final[str] = '1.8.0'

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
    def key(self) -> str:
        ...

def _bind_options(category: str) -> str:
    ...

def is_read_only(options: str | None) -> bool:
    ...

@dataclass(frozen=True)
class CategoryCollision:
    box_dest: str
    scope: str
    winner_key: str
    loser_keys: tuple[str, ...]

    def message(self) -> str:
        ...

def path_depth(box_dest: str) -> int:
    ...

def gate_credential_delivery(entries: list[CategoryEntry], deliver_creds: bool) -> list[CategoryEntry]:
    ...

@dataclass(frozen=True)
class LaunchDeliveries:
    envs: list[CategoryEntry]
    secrets: list[CategoryEntry]
    agent_dests: frozenset[str]
    narrow_bindings: 'dict[str, object] | None' = None

def secret_path_winners(entries: list[CategoryEntry]) -> list[CategoryEntry]:
    ...

def secret_path_deliveries(entries: list[CategoryEntry]) -> list[CategoryEntry]:
    ...

def launch_deliveries(entries: list[CategoryEntry], *, agent_dests: frozenset[str], narrow_bindings: 'dict[str, object] | None'=None) -> LaunchDeliveries:
    ...

def narrow_table_winners(entries: list[CategoryEntry], dests: frozenset[str]) -> list[CategoryEntry]:
    ...

def raise_binding_vs_binding(box_dest: str, concrete: list[CategoryEntry]) -> NoReturn:
    ...

def raise_extension_onto_occupied(box_dest: str, *, extension: CategoryEntry, base: CategoryEntry) -> NoReturn:
    ...

def _most_specific(entries: list[CategoryEntry]) -> CategoryEntry:
    ...

def _entry_lines(entries: list[CategoryEntry]) -> str:
    ...

def _rule_changed(body: str) -> str:
    ...

def _suppress_then_add(occupant_segments: tuple[str, ...], *, ambiguous: bool=False) -> str:
    ...

def derive_binding_keys(entries: list[CategoryEntry]) -> dict[tuple[str, ...], 'Bind']:
    ...

_EFFECTIVE_DELIVERY_STUB_REASON: Final[str] = "the effective binding / template-source calculation is deliberately unimplemented pending the collapse function; the '--effective' binding-derivations display is disabled until it lands"

def effective_bindings_and_template_sources(snapshot: 'KeyStore') -> Any:
    ...
```
