# `src/kanibako/commands/box/_parser.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/box/_parser.py.md`.


## Variables

```
_MODE_CHOICES = ['default', 'standalone', 'workset']
```

## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_create(args: argparse.Namespace) -> int
def run_ps(args: argparse.Namespace) -> int
def run_list(args: argparse.Namespace) -> int
def run_rm(args: argparse.Namespace) -> int
def run_register(args: argparse.Namespace) -> int
def run_info(args: argparse.Namespace) -> int
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
def _add_target_group(parser: argparse.ArgumentParser, *, required: bool=False) -> None
def _assert_primary_home_free_for_create(std, name: str) -> None
def _check_persona_store_for_create(agent_ref: str, project_path) -> str | None
def _list_orphans(projects: list, ws_data: list, std, quiet: bool) -> int
def _purge_dir(target: Path) -> bool
def _assert_deletable(path, *, must_be_under: Path | None=None) -> Path
def _teardown_primary_box(std, name: str, metadata_dir: Path) -> bool
def _teardown_standalone_box(root: Path) -> bool
def _read_box_image(settings_file: Path) -> str | None
def _read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None
def _purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace) -> int
def _resolve_standalone_target(std, config, target: str) -> tuple[str | None, Path | None]
def _rm_standalone(std, box_name: str, root, args: argparse.Namespace) -> int
def _readopt_deregistered(std, name: str, entry: dict, *, force: bool) -> int
def _format_credential_age(creds_path: Path) -> str
def _check_container_running(proj) -> tuple[bool, str]
def _resolve_config_subject(std, config, project_dir: str | None)
def _run_box_config(args: argparse.Namespace) -> int
```
