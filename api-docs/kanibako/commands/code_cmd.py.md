# `src/kanibako/commands/code_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/code_cmd.py.md`.


## Variables

```
_CODE_SHIM_MSG = "Error: the 'code' found on your PATH is VS Code's in-container remote shim.\n  You appear to be running 'kanibako code' INSIDE a container that a VS Code client is attached to; this 'code' would open windows on the ATTACHING desktop, not here.\n  Run 'kanibako code' from the host instead."
_MISSING_CODE_MSG = "Error: the VS Code 'code' CLI was not found on your PATH.\n  Install VS Code and add its 'code' command to PATH (Command Palette: 'Shell Command: Install code command in PATH').\n  You also need the Dev Containers extension."
```

## Functions
```
def add_code_parser(subparsers: argparse._SubParsersAction) -> None
def run_code(args: argparse.Namespace) -> int
def _attach_uri(container_name: str, context: str | None=None) -> str
def _resolve_code_cli() -> str | None
def _extension_for_agent(agent_name: str, project_path) -> str | None
def _resolve_box_agent_node(runtime, std, proj, container_name: str) -> str | None
def _resolve_box_vscode_extension(agent_name: str | None, proj) -> str | None
def _resolve_box_image(runtime, proj, container_name: str) -> str | None
def _seed_attached_config(runtime, std, proj, container_name: str) -> None
def _wire_docker_path(wrapper_path) -> int | None
def _seed_remote_attached_config(engine, container_name: str) -> None
def _run_code_remote(args: argparse.Namespace, dest: str) -> int
```

## Classes

```
class _CodeShimError(Exception):
```
