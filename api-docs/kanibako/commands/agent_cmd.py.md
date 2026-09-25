# `src/kanibako/commands/agent_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


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
def _run_agent_config(args: argparse.Namespace) -> int
def _agent_key_gate(agent_id: str, key: str, *, path: 'Path', verb: str) -> str | None
def _declared_label(agent_id: str) -> str
def _agent_label(std: 'StandardPaths', agent_id: str) -> str
def _stored_rows(table: 'Mapping[str, object]', prefix: str='') -> list[tuple[str, str]]
def _get_agent_key(cfg: AgentConfig, key: str) -> str | None
def _show_agent_config(cfg: AgentConfig, label: str, *, effective: bool=False) -> int
```
