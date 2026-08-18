# `src/kanibako/commands/clean.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/clean.py.md`.


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _unregister_purged(std, proj) -> None
def _unregister_purged_primary(std, metadata_path, project_path) -> None
def _warn_undeleted(path) -> None
def _purge_one(std, config, path: str, *, force: bool) -> int
def _purge_all(std, config, *, force: bool) -> int
```
