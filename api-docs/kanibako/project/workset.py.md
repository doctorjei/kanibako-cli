# `src/kanibako/project/workset.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/workset.py.md`.


## Variables

```
WORKSET_SETTINGS_FILE = 'settings.yaml'
BOXES_DIR_NAME = 'boxes'
DEFAULT_WORKSET_ID = '__default__'
DEFAULT_WORKSET_ALIAS = 'default'
RESERVED_WORKSET_NAMES = frozenset({DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS, '__PRIMARY__', '__STANDALONE__'})
_WORKSPACES_LEAF = 'workspaces'
_STANDALONE_WORKSPACE_LEAF = 'workspace'
_CHANNELROOT_LEAF = 'channels'
```

## Functions
```
def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None
def resolve_workset_workspaces(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool=False) -> Path
def resolve_workset_channelroot(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def is_reserved_workset_name(name: str) -> bool
def read_workset_identity(root: Path) -> dict | None
def create_workset(name: str, root: Path, std: StandardPaths, force: bool=False) -> Workset
def load_workset(root: Path) -> Workset
def list_worksets(std: StandardPaths) -> dict[str, Path]
def default_workset(std: StandardPaths) -> Workset
def resolve_workset_name(name: str, std: StandardPaths) -> Workset
def delete_workset(name: str, std: StandardPaths, *, remove_files: bool=False) -> Path
def add_project(ws: Workset, name: str, source_path: Path, std: StandardPaths | None=None, force: bool=False) -> WorksetProject
def remove_project(ws: Workset, name: str, *, remove_files: bool=False, std: StandardPaths | None=None) -> WorksetProject
def _workset_path_repoint(workset_settings: Mapping[str, Any] | None, leaf: str) -> str | None
def _apply_workset_dir_repoint(workset_root: Path, repoint: str | None, default_leaf: str) -> Path
@contextmanager
def _journal_connect(journal: Path | None, box_path: Path, *, name: str, workset: str | None=None, workspace: str | None=None)
def _write_workset_identity(ws: Workset) -> None
def _load_workset_identity(root: Path) -> Workset
def _refuse_retired_workset_identity(root: Path) -> None
def _load_registry(std: StandardPaths) -> dict[str, Path]
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
    created: str
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
