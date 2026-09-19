# `src/kanibako/commands/workset_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/workset_cmd.py.md`.


## Variables

```
_STANDALONE_REFUSAL = "Error: 'workset create --standalone' is refused: standalone is a single box's mode, not a working set's. A standalone box keeps its own state and its own workset-tier settings inside its project directory and belongs to no working set, so a working set cannot have standalone members; mode is detected from the box directory, never stored.\n  For a standalone box:  kanibako box create --standalone [path]\n  For a working set:     re-run without --standalone; boxes created in it or connected to it are 'named' mode."
_NEXT_LAUNCH_REMINDER = 'Shares apply on the next box launch (bind mounts are fixed at container creation; a running box is unaffected).'
_PREVIEW_HOME_SRC: str = "(each box's own home store)"
```

## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_create(args: argparse.Namespace) -> int
def run_list(args: argparse.Namespace) -> int
def run_rm(args: argparse.Namespace) -> int
def run_connect(args: argparse.Namespace) -> int
def run_disconnect(args: argparse.Namespace) -> int
def run_info(args: argparse.Namespace) -> int
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
def run_share_add(args: argparse.Namespace) -> int
def run_share_remove(args: argparse.Namespace) -> int
def run_share_list(args: argparse.Namespace) -> int
def _load_std()
def _workset_config_path(ws) -> Path
def _run_workset_config(args: argparse.Namespace) -> int
def _share_source_display(value: object) -> str
def _resolve_share_workset(name: str)
def _load_share_doc(ws_config: Path) -> dict
def _workset_raw_shares(ws_config: Path) -> dict[tuple[str, str], object]
def _print_effective_shares(ws, std, ws_config: Path) -> int
```
