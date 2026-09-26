# `src/kanibako/commands/restore.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _keep_links_filter(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo
def _restore_one(std, config, *, project_dir, archive_file, force, name=None) -> int
def _peek_archive_info(archive_file: Path) -> dict[str, str] | None
def _restore_all(std, config, args) -> int
def _parse_info(info_file: Path) -> dict[str, str]
def _validate_git_state(proj, info: dict[str, str], force: bool) -> int
```
