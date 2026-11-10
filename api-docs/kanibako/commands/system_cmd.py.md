# `src/kanibako/commands/system_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/system_cmd.py.md`.


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_info(args: argparse.Namespace) -> int
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
def run_defaults(args: argparse.Namespace) -> int
def _agent_node_error(key: str, *, verb: str, valid_agents: AgentNames | None=None) -> str | None
def _system_target_std(cf)
def _run_system_config(args: argparse.Namespace) -> int
```
