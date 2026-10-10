# `src/kanibako/project/workset.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/workset.py.md`.


## Variables

```
BOXES_DIR_NAME = bootstrap.BOXES_PATH
DEFAULT_WORKSET_ID = '__default__'
DEFAULT_WORKSET_ALIAS = 'default'
RESERVED_WORKSET_IDENTIFIERS = frozenset({DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS})
WORKSET_PARTITION_TOKENS = frozenset({WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE})
WORKSET_RENDERED_SEGMENTS = frozenset({WORKSET_SEGMENT_PRIMARY, WORKSET_SEGMENT_STANDALONE})
RESERVED_WORKSET_NAMES = RESERVED_WORKSET_IDENTIFIERS | WORKSET_PARTITION_TOKENS | WORKSET_RENDERED_SEGMENTS
_STANDALONE_BOXES_LEAF = bootstrap.STANDALONE_META_DIR
_WORKSPACES_LEAF = bootstrap.WORKSPACES_PATH
_STANDALONE_WORKSPACE_LEAF = bootstrap.WORKSPACE_PATH
_CHANNELROOT_LEAF = bootstrap.CHANNELS_PATH
_LOGS_LEAF = bootstrap.LOGS_PATH
_CANON_LEAF = 'canon'
_TEMPLATE_LEAF = 'template'
_BOXES_REF = f'workset.{BOXES_DIR_NAME}'
_VAULT_LEAF = bootstrap.VAULT_PATH
_VAULT_RO_KEY = 'vault_ro'
_VAULT_RW_KEY = 'vault_rw'
_VAULT_RO_LEAF = f'{_VAULT_LEAF}/{bootstrap.RO_PATH}'
_VAULT_RW_LEAF = f'{_VAULT_LEAF}/{bootstrap.RW_PATH}'
_UNDROPPABLE: set[tuple[Path, str]] = set()
```

## Functions
```
def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None
def resolve_workset_workspaces(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False, early: EarlyScope) -> Path | None
def resolve_workspaces_locator(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path
def workset_workspaces_nulled(workset_root: Path, *, early: EarlyScope) -> bool
def refuse_null_workspaces(workset_root: Path, what: str, *, standalone: bool=False, early: EarlyScope) -> None
def refuse_null_box_workspace(workset_root: Path, workspace: Path | None, box: str, *, standalone: bool, early: EarlyScope) -> None
def resolve_workset_boxes(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False, early: EarlyScope) -> Path
def resolve_workset_logs(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False, early: EarlyScope) -> Path | None
def resolve_workset_channelroot(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path | None
def resolve_workset_canon(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path | None
def resolve_workset_template(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path | None
def resolve_workset_vault_ro(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path | None
def resolve_workset_vault_rw(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, early: EarlyScope) -> Path | None
def resolve_workset_vault_pair(workset_root: Path, *, early: EarlyScope) -> tuple[Path | None, Path | None]
def standalone_vault_teardown(root: Path, *, early: EarlyScope) -> tuple[list[Path], list[Path]]
def standalone_canon_teardown(root: Path, *, early: EarlyScope) -> tuple[Path | None, Path | None]
def report_retained_canon(canon: Path, root: Path, why: str | None=None) -> None
def retained_vault_reason(root: Path, vault: Path) -> str
def report_retained_vault(vault: Path, why: str) -> None
def report_retained_vaults(root: Path, retained: Iterable[Path]) -> None
def is_reserved_workset_name(name: str) -> bool
def refuse_reserved_registered_name(name: str, root: Path, *, early_system: EarlySystem) -> None
def refuse_retired_workset_identity(root: Path) -> None
def find_logs_share(std: StandardPaths, *, value: object, scope: str, target_name: str | None=None, target_root: Path | None=None) -> 'tuple[tuple[str, ...], Path] | None'
def logs_share_refusal(canonical_key: str, value: object, std: StandardPaths, *, force: bool, scope: str, target_name: str | None=None, target_root: Path | None=None) -> str | None
def purge_box_logs(std: StandardPaths, logs_dir: Path | None, box: str, *, workset_root: Path | None) -> list[Path]
def box_logs_to_purge(std: StandardPaths, logs_dir: Path | None, box: str, *, workset_root: Path | None) -> list[Path]
def is_workset_skeleton(root: Path, *, early: EarlyScope) -> bool
def create_workset(name: str, root: Path, std: StandardPaths, *, force_logs_share: bool=False) -> Workset
def load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset
def list_worksets(std: StandardPaths) -> dict[str, Path]
def default_workset(std: StandardPaths) -> Workset
def resolve_workset_name(name: str, std: StandardPaths) -> Workset
def delete_workset(name: str, std: StandardPaths, *, remove_files: bool=False) -> Path
def source_in_tree(ws: Workset, source_path: Path) -> bool
def is_in_tree_workspace(ws: Workset, path: Path) -> bool
def refuse_existing_box(source: Path, std: StandardPaths, *, force: bool=False) -> None
def add_project(ws: Workset, name: str, source_path: Path, std: StandardPaths | None=None, force: bool=False, *, restoring: bool=False) -> WorksetProject
def ensure_discoverability_link(ws: Workset, name: str, target: Path) -> Path | None
def release_project(ws: Workset, name: str, *, keep_link: bool=False) -> WorksetProject
def remove_member_store(ws: Workset, name: str, *, bases: tuple[Path, ...] | None=None) -> None
def remove_project(ws: Workset, name: str, *, remove_files: bool=False, std: StandardPaths | None=None) -> WorksetProject
def _workspaces_null_file(workset_root: Path, *, early: EarlyScope) -> Path | None
def _holds_only_arms(path: Path, arms: set[Path]) -> bool
@contextmanager
def _journal_connect(journal: Path | None, box_path: Path, *, name: str, workset: str | None=None, workspace: str | None=None)
def _load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset
def _load_registry(std: StandardPaths) -> dict[str, Path]
def _logs_walk_targets(std: StandardPaths) -> dict[str, Path]
def _walk_scope(system: EarlySystem, name: str, root: Path) -> EarlyScope
def _shown(name: str) -> str
def _with_logs(doc: Mapping[str, Any] | None, value: object) -> dict
def _logs_share_partners(std: StandardPaths, logs_dir: Path, box: str, *, workset_root: Path | None) -> tuple[str, ...]
def _workset_skeleton_dirs(root: Path, *, early: EarlyScope) -> tuple[Path, ...]
def _path_in_tree(path: Path, root: Path) -> bool
def _strictly_in_tree(path: Path, root: Path) -> bool
def _warn_adopted_vault_leaves(name: str, leaves: Iterable[Path | None]) -> None
def _detach_project(ws: Workset, name: str) -> None
def _find_member(ws: Workset, name: str) -> WorksetProject
def _unfollowed(path: Path) -> Path
def _member_store_bases(ws: Workset) -> tuple[Path, ...]
```

## Classes

```
@dataclass
class WorksetProject:
    name: str
    source_path: Path

@dataclass
class Workset:
    name: str
    root: Path
    projects: list[WorksetProject] = field(default_factory=list)
    is_default: bool = False
    early_system: EarlySystem = field(kw_only=True)

    @property
    def early_scope(self) -> EarlyScope
    @property
    def projects_dir(self) -> Path
    @property
    def workspaces_dir(self) -> Path | None
    def require_workspaces_dir(self, what: str) -> Path
    @property
    def vault_dir(self) -> Path
    @property
    def vault_ro_dir(self) -> Path | None
    @property
    def vault_rw_dir(self) -> Path | None
    @property
    def logs_dir(self) -> Path | None
    @property
    def settings_path(self) -> Path
    @property
    def registry_path(self) -> Path

class StoreRemovalError(OSError):
    def __init__(self, message: str, leaf: Path) -> None

@dataclass
class _Unwind:
    actions: list[Callable[[], object]] = field(default_factory=list)
    cleanups: list[tuple[Callable[[], None], Callable[[], None] | None]] = field(default_factory=list)
    finished: int = 0

    def push(self, action: Callable[[], object]) -> None
    def push_first(self, action: Callable[[], object]) -> None
    def on_success(self, action: Callable[[], None], *, interrupted: Callable[[], None] | None=None) -> None
    def run(self) -> None
    def finish(self) -> None
    def note_interrupted(self) -> None
```
