# `src/kanibako/settings/agent_select.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_select.py.md`.


## Variables

```
SELECTION_KEY = 'system.agent'
```

## Functions
```
def launch_resolve_ctx(std, proj, agent_name: 'str | None')
def select_agent(*, std, proj, explicit_agent: 'str | None'=None, project_path: 'Path | None'=None) -> AgentSelection


@dataclass(frozen=True)
class AgentSelection:
    node: str
    source: str

    @property
    def has_agent(self) -> bool
    @property
    def selection_level(self) -> 'dict[str, object] | None'
```
