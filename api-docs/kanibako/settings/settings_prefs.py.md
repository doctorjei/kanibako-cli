# `src/kanibako/settings/settings_prefs.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_prefs.py.md`.


## Variables

```
PREF_ROOT: Final[str] = 'pref'
PREF_LEGAL_LEVELS: Final[tuple[str, ...]] = ('workset', 'box')
ALLOWLIST: Final[tuple[str, ...]] = ('system.agent', 'agent.*.**')
LOCATOR_CLOSURE: Final[frozenset[str]] = frozenset({'workset.boxes', 'workset.kuid'})
_log = logging.getLogger(__name__)
_LEVEL_ORDER: Final[dict[str, int]] = {'config': 0, 'meta': 1, 'base': 2, 'system': 3, 'agent': 4, 'workset': 5, 'box': 6}
_DISCOVERY: 'dict[str, AgentNames]' = {}
```

## Functions
```
def glob_match(pattern: str, key: str) -> bool
def prefs_from_partial(partial: KeyStore, *, level: str, path: Path | None=None) -> list[PrefRequest]
def collect_prefs(workset_path: Path | None, box_path: Path | None) -> list[PrefRequest]
def refuse_pref_table(raw: Any, *, level: str, path: Path | None) -> Any
def key_reason(target: str, *, valid_agents: Collection[str]) -> str | None
def allowlist_reason(target: str, *, valid_agents: Collection[str], allowlist: Sequence[str]=ALLOWLIST) -> str | None
def forbidden_tier_reason(target: str, *, level: str) -> str | None
def validate_pref(req: PrefRequest, *, valid_agents: Collection[str], allowlist: Sequence[str]=ALLOWLIST) -> str | None
def pref_overlay(requests: Iterable[PrefRequest]) -> KeyStore
def apply_prefs(requests: Sequence[PrefRequest], *, valid_agents: 'Collection[str] | None'=None, allowlist: Sequence[str]=ALLOWLIST) -> tuple[KeyStore, KeyStore]
def reset_discovery_cache() -> None
def default_valid_agents() -> AgentNames
def pref_value(requests: Sequence[PrefRequest], target: str) -> StoreValue | None
def pref_request_for(requests: Sequence[PrefRequest], target: str) -> PrefRequest | None
def pref_entry_keys(req: PrefRequest) -> tuple[str, ...]
def pref_origin(target_key: str, requests: Sequence[PrefRequest]) -> PrefRequest | None
def _flatten_pref_node(node: KeyStore, prefix: tuple[str, ...], *, level: str, path: Path | None) -> list[PrefRequest]
def _needs_agent_discovery(requests: Sequence[PrefRequest]) -> bool
```

## Classes

```
@dataclass(frozen=True)
class PrefRequest:
    target: str
    value: StoreValue
    level: str
    source: Path | None = None

    @property
    def key(self) -> str
    @property
    def where(self) -> str

class AgentNames(Collection[str]):
    def __init__(self, discovered: Collection[str], *, leaves: 'Collection[str] | None'=None, discovery_failed: bool=False) -> None

    def __contains__(self, item: object) -> bool
    def __iter__(self)
    def __len__(self) -> int
    def __repr__(self) -> str
```
