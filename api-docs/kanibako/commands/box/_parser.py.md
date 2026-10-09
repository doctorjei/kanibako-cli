# `src/kanibako/commands/box/_parser.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/box/_parser.py.md`.


## Variables

```
_MODE_CHOICES = [m.value for m in BoxMode]
_SHOW_ALL_HELP = 'Include every box, not just the running ones'
_MISSING_WORKSPACE = 'missing workspace'
_CREATE_SHAPING_FLAGS = ('name', 'image', 'agent', 'private', 'no_vault')
_CREATE_SUBJECT_FLAGS = ('path', 'standalone', 'allow_home', 'register')
_SHAPING_SET_CURE = {'image': ('box.image=<value>',), 'agent': ('pref.system.agent=<value>',), 'private': ('box.auth.global_enabled=false', 'box.auth.workset_enabled=false'), 'no_vault': ('box.enable_vault=false',)}
_STANDALONE_CREATE_EXEMPT_KEYS: frozenset[str] = frozenset({'registry', 'template'})
_VAULT_LABELS = ('vault ro (your files)', 'vault rw (your files)')
```

## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def precheck_create(args: argparse.Namespace) -> int | None
@_unmakes_its_dirs_on_failure
def run_create(args: argparse.Namespace, outcome: _CreateOutcome) -> int
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
def _check_persona_store_for_create(agent_ref: str) -> str | None
def _create_recovery_refusal(args, std, probe, *, already: bool, pending: dict | None) -> 'str | None'
def _orphaned_primary_box_dir(args, std, probe) -> 'Path | None'
def _named_workset_owning(path: Path, std) -> str | None
def _create_in_workset_space(workset: str, path: Path, *, standalone: bool, by_cwd: bool) -> str
def _plan_workset_member(std, workset: str, name: str, args) -> 'tuple[Workset, str, bool] | None'
def _new_member_undo(ws: Workset, name: str) -> Callable[[], None]
def _new_box_undo(std, probe, *, standalone: bool) -> tuple[Callable[[], None], Callable[[str], None]]
def _claim_create_entry(std, probe, state: dict, *, primary: bool) -> 'dict | None'
def _recovered_standalone_name(std, proj, supplied: str) -> str
def _create_target(args: argparse.Namespace) -> Path
def _mkdir_recording(target: Path, made: list[Path]) -> None
def _unmakes_its_dirs_on_failure(create: Callable[[argparse.Namespace, _CreateOutcome], int]) -> Callable[[argparse.Namespace], int]
def _list_orphans(projects: list, ws_data: list, std, quiet: bool) -> int
def _assert_deletable(path, *, must_be_under: Path | None=None) -> Path
def _primary_purge_plan(std, name: str, metadata_dir: Path) -> list[_PurgeStep]
def _standalone_purge_plan(root: Path, registered_name: str, *, std: StandardPaths, early: EarlyScope) -> list[_PurgeStep]
def _confirm_purge(steps: list[_PurgeStep], *, force: bool) -> bool
def _run_purge_plan(steps: list[_PurgeStep]) -> bool
def _read_box_image(settings_file: Path) -> str | None
def _read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None
def _purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace) -> int
def _resolve_standalone_target(std, config, target: str) -> tuple[str | None, Path | None, bool]
def _rm_standalone(std, box_name: str, root, args: argparse.Namespace, *, registered: bool) -> int
def _composed_standalone_key(root: Path) -> str | None
def _readopt_deregistered(std, name: str, entry: dict) -> int
def _box_register_cure(target: str) -> str
def _box_rm_purge_cure(target: str) -> str
def _retained_box_cures(target: str) -> 'tuple[str, str]'
def _retained_box_cure_lines(target: str) -> str
def _format_credential_age(creds_path: Path) -> str
def _check_container_running(proj) -> tuple[bool, str]
def _resolve_config_subject(std, config, project_dir: str | None)
def _run_box_config(args: argparse.Namespace) -> int
```

## Classes

```
@dataclasses.dataclass
class _CreateOutcome:
    made: list[Path] = dataclasses.field(default_factory=list)
    committed: bool = False
    recover: str = ''

@dataclasses.dataclass(frozen=True)
class _PurgeStep:
    kind: str
    what: str
    path: Path
    anchor: bool = False
    gated: bool = False
```
