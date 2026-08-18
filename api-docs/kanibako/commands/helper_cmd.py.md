# `src/kanibako/commands/helper_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/helper_cmd.py.md`.


## Functions
```
def add_helper_subparsers(p: argparse.ArgumentParser) -> None
def run_spawn(args: argparse.Namespace) -> int
def run_list(args: argparse.Namespace) -> int
def run_stop(args: argparse.Namespace) -> int
def run_cleanup(args: argparse.Namespace) -> int
def run_respawn(args: argparse.Namespace) -> int
def run_send(args: argparse.Namespace) -> int
def run_broadcast(args: argparse.Namespace) -> int
def run_register(args: argparse.Namespace) -> int
def run_log(args: argparse.Namespace) -> int
def _helpers_dir() -> Path
def _socket_path() -> Path
def _check_helpers_enabled() -> bool
def _ro_spawn_config_path(helpers_dir: Path, helper_num: int) -> Path
def _state_path(helpers_dir: Path, helper_num: int) -> Path
def _read_state(helpers_dir: Path, helper_num: int) -> dict
def _write_state(helpers_dir: Path, helper_num: int, state: dict) -> None
def _get_existing_helpers(helpers_dir: Path) -> list[int]
def _next_helper_number(existing: list[int], budget: SpawnBudget) -> int
def _cascade_cleanup(helpers_dir: Path, helper_num: int) -> list[int]
def _log_path() -> Path
def _read_log_entries(log_file: Path) -> list[dict]
def _format_log_entry(entry: dict) -> str
def _follow_log(log_file: Path, from_helper: int | None) -> int
```
