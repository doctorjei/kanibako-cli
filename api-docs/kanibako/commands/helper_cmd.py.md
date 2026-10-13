# `src/kanibako/commands/helper_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_PROBE_TIMEOUT = 2.0
_NO_HUB = 'Cannot %s: this box runs no helper hub. A hub runs from a `kanibako start` with helpers allowed and no `--no-helpers` until the box stops; `kanibako start --restart` starts a new one.'
_SPAWN_FAILED = 'Cannot spawn: helper %d did not launch: %s. Its directory is kept, marked failed; `kanibako box helper cleanup %d` removes it.'
_RESPAWN_FAILED = 'Cannot respawn: helper %d did not launch: %s. It stays stopped, the reason in its state.json; fix the cause and retry `kanibako box helper respawn %d`.'
```

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
def _own_helper_number() -> int | None
def _launch(request: dict) -> tuple[str | None, str | None]
def _ro_spawn_config_path(helpers_dir: Path, helper_num: int) -> Path
def _get_existing_helpers(helpers_dir: Path) -> list[int]
def _hub_number() -> tuple[int | None, str | None]
def _hub_stop(state: dict, helper_num: int) -> dict | None
def _subtree(helpers_dir: Path, helper_num: int) -> list[tuple[Path, int]]
def _log_path() -> Path
def _read_log_entries(log_file: Path) -> list[dict]
def _format_log_entry(entry: dict) -> str
def _follow_log(log_file: Path, from_helper: int | None) -> int
```
