# `src/kanibako/launch/box_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/box_resolve.py.md`.


## Variables

```
_PRIMARY_WORKSET_NAME = 'default'
```

## Functions
```
def stores_standalone_registry_null(project_dir: Path, *, strict: bool=False) -> bool
def standalone_marker_error(root: Path, name: str) -> str | None
def standalone_marker_repair(root: Path) -> tuple[str, str]
def registered_standalone_marker_error(root: Path, std: StandardPaths) -> str | None
def recorded_workspaces_under(std: StandardPaths, tree: Path, *, standalone: bool=True) -> list[tuple[str, Path]]
def find_connected_external_box(project_dir: Path, std: StandardPaths) -> _OwnedBox | None
def detect_box_mode(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> DetectionResult | None
def detect_box_or_default(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> DetectionResult
def standalone_box_name(box_root: Path, registered_name: str | None) -> str
def held_standalone_stores(registry: Path, own_root: Path) -> dict[str, str]
def unregistered_box_refusal(proj: ProjectPaths, belongs: str, name_cure: str) -> str | None
def resolve_box_identity(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> dict[str, Any] | None
def _enumerate_worksets(std: StandardPaths) -> Iterator[tuple[str, Path, BoxMode]]
def _find_owning_box(project_dir: Path, std: StandardPaths, config: BootstrapConfig, *, workset: str | None=None) -> _OwnedBox | None
def _path_space_workset(project_dir: Path, std: StandardPaths) -> str
```

## Classes

```
class _OwnedBox(NamedTuple):
    workset_name: str
    workset_root: Path
    mode: BoxMode
    box_name: str
    box_path: Path
```
