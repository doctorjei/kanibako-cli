# `src/kanibako/commands/stop.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _writeback_on_stop(runtime, proj, container_name: str, *, std, config) -> None
def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int
def _stop_all(runtime: ContainerRuntime, *, force: bool=False) -> int
```
