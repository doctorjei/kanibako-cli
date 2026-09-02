# `src/kanibako/project/workset.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/workset.py.md`.


## Variables

```
BOXES_DIR_NAME = bootstrap.BOXES_PATH
DEFAULT_WORKSET_ID = '__default__'
DEFAULT_WORKSET_ALIAS = 'default'
RESERVED_WORKSET_NAMES = frozenset({DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS, '__PRIMARY__', '__STANDALONE__'})
_STANDALONE_BOXES_LEAF = bootstrap.STANDALONE_META_DIR
_WORKSPACES_LEAF = bootstrap.WORKSPACES_PATH
_STANDALONE_WORKSPACE_LEAF = bootstrap.WORKSPACE_PATH
_CHANNELROOT_LEAF = bootstrap.CHANNELS_PATH
_LOGS_LEAF = bootstrap.LOGS_PATH
_CANON_LEAF = 'canon'
_TEMPLATE_LEAF = 'template'
_BOX_PATH_REF = 'meta.box.path'
_BOXES_REF = f'workset.{BOXES_DIR_NAME}'
_VAULT_LEAF = bootstrap.VAULT_PATH
_VAULT_RO_KEY = 'vault_ro'
_VAULT_RW_KEY = 'vault_rw'
_VAULT_RO_LEAF = f'{_VAULT_LEAF}/{bootstrap.RO_PATH}'
_VAULT_RW_LEAF = f'{_VAULT_LEAF}/{bootstrap.RW_PATH}'
```

## Functions
```
def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None
def resolve_workset_workspaces(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False) -> Path
def resolve_workset_boxes(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False) -> Path
def resolve_workset_logs(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False) -> Path
def resolve_workset_channelroot(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_canon(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_template(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_vault_ro(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_vault_rw(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_vault_pair(workset_root: Path) -> tuple[Path, Path]
def standalone_vault_teardown(root: Path) -> tuple[list[Path], list[Path]]
def is_reserved_workset_name(name: str) -> bool
def refuse_retired_workset_identity(root: Path) -> None
def is_workset_skeleton(root: Path) -> bool
def create_workset(name: str, root: Path, std: StandardPaths, force: bool=False) -> Workset
def load_workset(root: Path, name: str) -> Workset
def list_worksets(std: StandardPaths) -> dict[str, Path]
def default_workset(std: StandardPaths) -> Workset
def resolve_workset_name(name: str, std: StandardPaths) -> Workset
def delete_workset(name: str, std: StandardPaths, *, remove_files: bool=False) -> Path
def add_project(ws: Workset, name: str, source_path: Path, std: StandardPaths | None=None, force: bool=False) -> WorksetProject
def remove_project(ws: Workset, name: str, *, remove_files: bool=False, std: StandardPaths | None=None) -> WorksetProject
def _workset_path_repoint(workset_settings: Mapping[str, Any] | None, leaf: str) -> str | None
@contextmanager
def _journal_connect(journal: Path | None, box_path: Path, *, name: str, workset: str | None=None, workspace: str | None=None)
def _load_workset(root: Path, name: str) -> Workset
def _load_registry(std: StandardPaths) -> dict[str, Path]
def _workset_skeleton_dirs(root: Path) -> tuple[Path, ...]
def _detach_project(ws: Workset, name: str) -> None
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
    workspaces_repoint: str | None = None

    @property
    def projects_dir(self) -> Path
    @property
    def workspaces_dir(self) -> Path
    @property
    def vault_dir(self) -> Path
    @property
    def vault_ro_dir(self) -> Path
    @property
    def vault_rw_dir(self) -> Path
    @property
    def logs_dir(self) -> Path
    @property
    def settings_path(self) -> Path
    @property
    def registry_path(self) -> Path

class _Unwind:
    def __init__(self) -> None

    def push(self, action: Callable[[], None]) -> None
    def run(self) -> None
```
