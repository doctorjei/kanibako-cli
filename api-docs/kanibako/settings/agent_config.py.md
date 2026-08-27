# `src/kanibako/settings/agent_config.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_config.py.md`.


## Variables

```
IDENTITY_KEYS = frozenset({'name', 'run_args'})
AGENT_CATEGORY_DIRNAME: Final[Mapping[str, str]] = {category: category for category in ABSTRACT_CATEGORIES}
_SELF_RESOLVING_TOKENS: Final[tuple[str, ...]] = ('~', '$', '@')
```

## Functions
```
def agents_dir(data_path: Path, paths_agents: str='agents') -> Path
def store_dirname(node: str) -> str
def agent_settings_path(agents_root: Path, agent_id: str) -> Path
def agent_category_dirname(category: str) -> str
def agent_category_root(agents_root: Path, agent: str, category: str) -> Path
def category_root_ref(scope: str, category: str, *, agent: str | None=None) -> str
def agent_category_root_ref(agent: str, category: str) -> str
def is_self_resolving(src: str) -> bool
def root_relative_source(src: str, root_ref: str) -> str
def agent_config_path(data_path: Path, agent_id: str, paths_agents: str='agents') -> Path
```

## Classes

```
@dataclass
class AgentConfig:
    name: str = ''
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str | None] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    secret_path: dict[str, str | None] = field(default_factory=dict)
    transform_settings: dict = field(default_factory=dict)
    category_tables: dict[str, dict] = field(default_factory=dict)
```
