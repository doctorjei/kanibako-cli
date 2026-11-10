# `src/kanibako/commands/agent_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/agent_cmd.py.md`.


## Variables

```
_log = logging.getLogger(__name__)
```

## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_list(args: argparse.Namespace) -> int
def run_info(args: argparse.Namespace) -> int
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
def run_reauth(args: argparse.Namespace) -> int
def _config_file() -> Path
def _load_std() -> StandardPaths
def _missing_store_error(agent_id: str, path: Path, reserved: str) -> str
def _reserved_tier_refusal_for(args: argparse.Namespace) -> str
def _store_node(store: Path) -> str
def _agent_live_input(resolved: AgentResolution | None) -> tuple[Target | None, Mapping[str, str | None] | None, PersonaCredential | None, str | None]
def _display_record(path: Path, agent_id: str) -> tuple[AgentConfig, AgentResolution, AgentNames | None]
def _agent_file_verdict_after_edit(path: 'Path', agent_id: str, key: str, value: 'object', *, valid_agents: AgentNames | None=None) -> 'str | None'
def _run_agent_config(args: argparse.Namespace) -> int
def _agent_key_gate(agent_id: str, key: str, *, path: 'Path', verb: str, valid_agents: AgentNames | None=None) -> str | None
def _agent_write_vocab_error(agent_id: str, key: str, *, verb: str, valid_agents: AgentNames | None=None) -> str | None
def _label_floor(identity: AgentIdentity, target: Target | None) -> dict[str, object]
def _refuse_null_label(std: StandardPaths, identity: AgentIdentity, *, valid_agents: AgentNames | None, floor: Mapping[str, object], persona_values: Mapping[str, str | None] | None, persona_credential: PersonaCredential | None) -> None
def _default_label_text(identity: AgentIdentity, ref: str) -> str
def _agent_label(std: StandardPaths, resolved: AgentResolution, *, valid_agents: AgentNames | None) -> str
def _category_resets(cfg: AgentConfig) -> dict[str, object]
def _stored_rows(table: 'Mapping[str, object]', prefix: str='') -> list[tuple[str, str]]
def _get_agent_key(cfg: AgentConfig, key: str) -> str | None
def _show_agent_config(cfg: AgentConfig, label: str, *, effective: bool=False) -> int
```
