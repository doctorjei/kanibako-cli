# `src/kanibako/commands/vault_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/vault_cmd.py.md`.


## Functions
```
def add_vault_subparser(parent_sub: argparse._SubParsersAction) -> None
def run_snapshot(args: argparse.Namespace) -> int
def run_list(args: argparse.Namespace) -> int
def run_restore(args: argparse.Namespace) -> int
def run_prune(args: argparse.Namespace) -> int
def _add_vault_subcommands(p: argparse.ArgumentParser) -> None
def _resolve_vault_rw(project_dir: str | None)
def _human_size(nbytes: int) -> str
```
