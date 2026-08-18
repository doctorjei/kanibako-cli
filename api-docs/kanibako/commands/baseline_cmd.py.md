# `src/kanibako/commands/baseline_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/baseline_cmd.py.md`.


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_list(args: argparse.Namespace) -> int
def run_verify(args: argparse.Namespace) -> int
def run_install(args: argparse.Namespace) -> int
def _filter_packages(pkgs: list[str], only: list[str] | None, skip: list[str] | None) -> list[str]
def _make_probe(runtime, image: str)
def _verify_image(runtime, image: str, pkgs: list[str]) -> list[tuple[str, str]]
```
