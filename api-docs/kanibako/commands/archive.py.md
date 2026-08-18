# `src/kanibako/commands/archive.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/archive.py.md`.


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _archive_one(std, config, proj, *, output_file, args) -> int
def _archive_all(std, config, args) -> int
def _stub_project(metadata_path, project_path, std, config)
```
