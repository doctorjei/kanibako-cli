# `src/kinemata_views.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
REF_WORKSET_PATH = '@meta.workset.path'
AGENT_TIER_NODES: tuple[str, ...] = ('claude', 'codex', 'goose', 'shell')
SPEC_NULL = '<None>'
SPEC_EMPTY = '{}'
SPEC_SENTINELS = frozenset((spec_notation(value) for value in _SENTINEL_VALUES))
STATED = 'stated'
MEMBERSHIP = 'membership'
NOT_EXPRESSIBLE = 'not-expressible'
ABSENCE_CELLS = (SPEC_NULL, SPEC_EMPTY)
_DELIVERY_HEADS = frozenset({'bindings', 'masks', 'caches', 'seeded', 'synced', 'common', 'env', 'secret_path'})
_FIXED_SCOPES = frozenset({'config', 'system', 'workset', 'box'})
_CLI_TYPED = frozenset({'bool', 'int', 'path'})
_ROOT_ATTRIBUTE = {'primary': 'primary_workset', 'named': 'group_root', 'standalone': 'metadata_path'}
_CONSTRUCT_TIME = frozenset({'<generated at creation>', '<construct-time>', "<the user's real project dir>"})
_AGENT = 'anyagent'
_VALUE_END = re.compile('\\s{2,}')
_BRACES = re.compile('^(?P<head>[^{}]*)\\{(?P<alts>[^{}]+)\\}(?P<tail>[^{}]*)$')
_SENTINEL_VALUES: tuple[object, ...] = (None, True, False, {})
_TREE = Path(__file__).resolve().parents[1]
```

## Functions
```
def standalone_is_null(entry: Any) -> bool
def standalone_is_placeholder(entry: Any) -> bool
def never_settable_path(entry: Any) -> bool
def fixed_scope_key(entry: Any) -> bool
def cli_typed_key(entry: Any) -> bool
def cli_routed_key(entry: Any) -> bool
def bootstrap_path_row(entry: Any) -> bool
def every_mode_cell(floors: Any) -> dict[str, Any]
def literal_text(value: Any) -> Any
def ref_token_project(mode: str, *, workset_name: str, box_name: str) -> Any
def ref_token_standard_paths(mode: str) -> Any
def guest_bind_arm(binds: Any, arm: str) -> list[tuple[str, tuple[str, ...]]]
def sentinel_helper_binds() -> Any
def box_address_floor(mode: str) -> dict[str, Any]
@contextlib.contextmanager
def recording_launch_snapshots() -> Any
def existing_box_termini(std: Any, config_file: Any, proj: Any, target: Any, node: str) -> list[tuple[str, Any, Any]]
def fresh_launch_snapshots() -> Any
def snapshot_paths(snapshot: Any) -> Any
def declares_no_floor_value(entry: Any) -> bool
def system_value_row(entry: Any) -> bool
def default_reaches_anchor(entry: Any) -> bool
def spec_table(section: str, columns: tuple[str, ...]) -> list[dict[str, str]]
def system_settings_rows() -> list[tuple[str, str, str]]
def expand_braces(key: str) -> list[str]
def spec_notation(value: object) -> str
def spec_fence(section: str, marker: str) -> list[str]
def spec_cell_value(cell: str) -> str
def spec_fence_rows(section: str, marker: str) -> list[tuple[str, str]]
def classify_spec_cell(key: str, cell: str) -> str
def node_spec_rows(node: str) -> dict[str, tuple[str, str]]
def node_spec_defaults(node: str) -> dict[str, str]
def node_not_expressible(node: str) -> list[str]
def agent_tier_defaults(node: str) -> dict[str, str]
def core_defaults_file() -> str
def plugin_descriptors() -> list[str]
def owner_rows(source: str) -> list[tuple[str, dict[str, Any]]]
def synced_cells() -> dict[str, dict[str, Any]]
def _standalone_arm(entry: Any) -> tuple[bool, Any]
def _root_or_decoy(mode: str, attribute: str) -> Any
def _mode_arm(row: Any, mode: str) -> Any
def _keyspec_extract() -> Any
def _strip_html_comments(lines: list[str]) -> list[str]
def _node_fence_marker(node: str) -> str
def _is_absent(value: object) -> bool
def _agent_tier_floors(node: str) -> dict[str, str | None]
```

## Classes

```
class AgentStatedDefaults(_AgentRegistry):
    def __init__(self, *, node: str, name: str='agent-stated', **options: object) -> None

    def _rows(self) -> list[dict[str, object]]

class EntryOwners(_ViewRegistry):
    VIEWS = ('all', 'core', 'creds')

    def __init__(self, *, rows: str, name: str='entry-owners', **options: object) -> None

    def _rows(self) -> list[dict[str, object]]

class _ViewRegistry:
    match_mode = 'strings'
    suffixes: tuple[str, ...] | None = None
    machinery: tuple[str, ...] = ()
    mentions_are_uses = True
    budget = 16 * 1024
    line_budget = 160
    boundary = ''

    def __init__(self, *, name: str, **options: object) -> None

    def entries(self) -> list[Any]
    def declared(self, identifier: str) -> bool
    def resolve(self, identifier: str) -> tuple[str, ...]
    def detect(self, text: str) -> list[str]
    def line(self, entry: Any) -> str
    def candidates(self, text: str) -> list[str]
    @property
    def notices(self) -> tuple[str, ...]

    def _rows(self) -> list[dict[str, object]]

class _AgentRegistry(_ViewRegistry):
    def __init__(self, *, node: str, name: str='agent-tier', **options: object) -> None
```
