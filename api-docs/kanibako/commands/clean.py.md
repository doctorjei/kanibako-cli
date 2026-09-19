# `src/kanibako/commands/clean.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


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
