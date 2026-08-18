# `src/kanibako/commands/box/_duplicate.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/box/_duplicate.py.md`.


## Functions
```
def run_duplicate(args: argparse.Namespace) -> int
def _source_is_external(args: argparse.Namespace, std) -> bool
def _run_duplicate_cross_mode(args: argparse.Namespace, std, config) -> int
def _duplicate_to_standalone(src_proj, new_path, std, force)
def _unwind_local_name(std, project_name: str, dst_project: Path) -> None
def _assert_dup_home_free(std, name: str) -> None
def _duplicate_to_local(src_proj, new_path, std, config, force)
def _duplicate_to_workset(args, std, config) -> int
def _duplicate_from_workset(args, source_path, new_path, std, config) -> int
```
