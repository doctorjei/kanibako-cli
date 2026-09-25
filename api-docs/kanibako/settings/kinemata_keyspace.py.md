# `src/kanibako/settings/kinemata_keyspace.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_SEG = '[A-Za-z_][A-Za-z0-9_]*'
_IDENTIFIER_EDGE = '[A-Za-z0-9_.\\-]'
_RECOGNIZED_NOT_KEYS = ('renamed',)
_SPEC_FIELD = 'spec'
_PARAMETRIC_FIELD = 'parametric'
_PARAMETRIC_KEYS_FIELD = 'parametric_keys'
_FALLBACK_FIELD = 'fallback'
_ALLOWLIST_FIELD = 'allowlist'
_PREF_SECTION = 'pref'
_TREE = Path(__file__).resolve().parents[3]
```

## Functions
```
def in_tree_agents(tree: Path) -> frozenset[str]
def _clauses(row: Any) -> tuple[str, ...]
def _static_head(template: str) -> str
```

## Classes

```
class KeyspaceRegistry:
    match_mode = 'strings'
    suffixes: tuple[str, ...] | None = ('.py',)
    machinery: tuple[str, ...] = ()
    mentions_are_uses = True

    def __init__(self, *, closed: bool=False, name: str='keyspace', **options: Any) -> None

    def entries(self) -> Iterator[Entry]
    def line(self, entry: Entry) -> str
    def declared(self, identifier: str) -> bool
    def resolve(self, identifier: str) -> tuple[str, ...]
    def detect(self, text: str) -> list[str]
    def candidates(self, text: str) -> list[str]

    @staticmethod
    def _build_interiors(keys: dict[str, Any], not_keys: dict[str, Any]) -> set[str]
    @staticmethod
    def _read_categories(categories: dict[str, Any]) -> tuple[list[str], set[str], set[str]]
    @staticmethod
    def _read_core_nodes(keys: dict[str, Any], tier_head: str) -> frozenset[str]
    def _instantiate_scopes(self, scopes: list[str]) -> list[str]
    @staticmethod
    def _read_tier_rule(keys: dict[str, Any]) -> tuple[str, str]
    def _shape_admits(self, match: re.Match[str]) -> bool
    def _tail_ok(self, tail: str, *, node: str | None) -> bool
    def _category_tail(self, segs: list[str]) -> bool
    def _compile(self, template: str, scope_alt: str, tier: bool) -> re.Pattern[str]
    def _build_ns_shapes(self, keys: dict[str, Any]) -> list[re.Pattern[str]]
    def _cross_prefixes(self) -> set[str]
    def _scope_alt(self) -> str
    def _build_key_shapes(self, keys: dict[str, Any]) -> None
    def _build_section_shapes(self, not_keys: dict[str, Any], category_defaults: dict[str, Any], plugin: dict[str, Any]) -> None
    def _read_namespace_shapes(self, plugin: dict[str, Any]) -> list[_Shape]
    def _split_scope(self, segs: list[str]) -> int
    def _read_allowlist(self, pref: dict[str, Any]) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]
    def _pref_declared(self, target: str) -> bool
    def _cross_declared(self, identifier: str) -> bool
    def _declared_plain(self, identifier: str) -> bool
    @cached_property
    def _detector(self) -> re.Pattern[str]
    def _ref_names(self, text: str) -> Iterator[str]
    def __post_init_check__(self) -> None

class _Shape:
    def __init__(self, pattern: re.Pattern[str], spec: tuple[str, ...]) -> None

    def match(self, identifier: str) -> re.Match[str] | None
```
