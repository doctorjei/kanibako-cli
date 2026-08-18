# `src/kanibako/settings/config_dest.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_dest.py.md`.


## Variables

```
_NOUN, _SCOPED, _CATEGORY = ('noun', 'scoped', 'category')
```

## Functions
```
def check_agent_node(node: str) -> 'NodeRouteRefusal | None'
@overload
def noun_settings_file(config_path: Path, settings_path: 'Path | None') -> Path
@overload
def noun_settings_file(config_path: None, settings_path: 'Path | None') -> 'Path | None'
def noun_settings_file(config_path: 'Path | None', settings_path: 'Path | None') -> 'Path | None'
def _agent_node_route(node: str, tail: str, agents_root: 'Path | None') -> 'AgentFileSlot | NodeRouteRefusal | None'
def _persona_agent_target(canonical: str, agents_root: 'Path | None') -> 'AgentFileSlot | str | None'
def _node_bind_target(canonical: str, agents_root: 'Path | None') -> 'AgentFileSlot | None'
def _node_secret_target(canonical: str, agents_root: 'Path | None') -> 'AgentFileSlot | None'
def _category_segments(canonical: str) -> tuple[str, ...]
def _key_slot(canonical: str) -> 'tuple[tuple[str, ...], str, str] | None'
def _dest(canonical: str, *, command_scope: 'object | None', config_path: 'Path | None', settings_path: 'Path | None') -> 'DestRoute | None'
def _write_dest(canonical: str, *, command_scope: 'object | None'=None, config_path: 'Path | None', settings_path: 'Path | None'=None) -> 'DestRoute | None'
def _read_dest(canonical: str, *, command_scope: 'object | None'=None, config_path: 'Path | None', settings_path: 'Path | None'=None) -> 'DestRoute | None'


@dataclass(frozen=True)
class NodeRouteRefusal:
    reason: str
    detail: str = ''

@dataclass(frozen=True)
class DestRoute:
    path: 'Path | None'
    sections: tuple[str, ...]
    leaf: str

    @property
    def file(self) -> Path
```
