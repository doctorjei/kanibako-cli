# `src/kanibako/settings/agent_representation.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_representation.py.md`.


## Types
```
_V = TypeVar('_V')

```

## Functions
```
def agent_default_partial(descriptor: PluginDescriptor, install: AgentInstall, node_name: str | None=None) -> KeyStore
def agent_env_for_node(table: 'dict[str, str]', *, node_name: str, harness: str) -> 'dict[str, str]'
def agent_categories_for_node(table: 'dict[str, object]', *, node_name: str, harness: str) -> 'dict[str, object]'
def harness_store_root(node: str) -> str
def harness_store_leaf(host_src: object, harness: str) -> str | None
def _rekey_agent_keys(table: 'Mapping[str, _V]', *, node_name: str, harness: str) -> 'dict[str, _V]'
def _reroot_arm(arm: 'dict[str, tuple]', *, node_name: str, harness: str) -> 'dict[str, tuple]'
```
