# `src/kanibako/commands/upgrade.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/upgrade.py.md`.


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _get_repo_dir() -> Path | None
def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess
def _get_current_commit(repo: Path) -> str | None
def _get_remote_commit(repo: Path) -> str | None
def _get_commit_count_behind(repo: Path) -> int | None
```
