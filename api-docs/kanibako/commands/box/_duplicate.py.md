# `src/kanibako/commands/box/_duplicate.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def run_duplicate(args: argparse.Namespace) -> int
def _refuse_inherited(std, source, target: tuple[Path, EarlyScope]) -> None
def _refuse_derived_destination(new_path: Path) -> int | None
def _refuse_primary_dup_name(args: argparse.Namespace, std, new_path: Path) -> int | None
def _claim_primary_dup_name(std, new_path: Path, name: str | None) -> str
def _refuse_existing_destination(path: Path) -> int
def _local_target(std, mode: BoxMode, new_path: Path) -> tuple[Path, EarlyScope]
def _source_is_external(args: argparse.Namespace, std) -> bool
def _run_duplicate_cross_mode(args: argparse.Namespace, std, config) -> int
def _merge_workspace(src: Path, dst: Path, force: bool, *, share_root_link: bool=False) -> None
def _source_authored_vault(src_proj) -> bool
def _duplicate_to_standalone(src_proj, new_path, std, force, src_enable_vault, carried)
def _unwind_local_name(std, project_name: str, dst_project: Path) -> None
def _assert_dup_home_free(std, name: str) -> None
def _duplicate_to_local(src_proj, new_path, std, config, force, carried, name, workspace_src=None)
def _duplicate_to_workset(args, std, config) -> int
def _duplicate_from_workset(args, source_path, new_path, std, config) -> int
```
