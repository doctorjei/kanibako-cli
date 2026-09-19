# `src/kanibako/settings/settings_keyspace_probe.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
ENV_FLAG: Final[str] = 'KANI_KEYSPACE_PROBE'
ENV_FILE: Final[str] = 'KANI_KEYSPACE_PROBE_FILE'
DEFAULT_PROBE_FILE: Final[str] = '/tmp/kanibako-keyspace-probe.jsonl'
probe_errors: list[str] = []
ANY_AGENT: Final[_AnyAgent] = _AnyAgent()
AGENT_LEAF_MAP: Final[ConcedingLeafMap] = ConcedingLeafMap(_DECLARED_HERE, unread_harnesses(_DECLARED_HERE))
_OFF_TOKENS: Final[frozenset[str]] = frozenset({'', '0', 'off', 'false', 'no'})
_PLUGINS: 'Mapping[str, frozenset[str]] | None' = None
_DECLARED_HERE: Final[_AgentLeafMap] = _AgentLeafMap()
_verdicts: dict[str, KeyJudgement] = {}
```

## Functions
```
def plugin_agent_leaf_map() -> 'Mapping[str, frozenset[str]]'
def declared_keyspace_oracle(path: str) -> KeyJudgement
def keyspace_verdict(path: str) -> KeyJudgement
def probe_enabled() -> bool
def observe(store: KeyStore[Any], *, origin: str) -> None
def _discover() -> 'Mapping[str, frozenset[str]]'
def _note_error(exc: BaseException) -> None
def _probe_file() -> str
```

## Classes

```
class _AnyAgent(Collection[str]):
    def __contains__(self, item: object) -> bool
    def __iter__(self) -> Iterator[str]
    def __len__(self) -> int

class _AgentLeafMap(Mapping[str, 'frozenset[str]']):
    def __getitem__(self, key: str) -> 'frozenset[str]'
    def __iter__(self) -> Iterator[str]
    def __len__(self) -> int
```
