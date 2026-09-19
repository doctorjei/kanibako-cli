# `src/kanibako/launch/box_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**
Prose for these symbols lives in `llm-docs/kanibako/launch/box_resolve.py.md`.


## Variables

```
_PRIMARY_WORKSET_NAME = 'default'
```

## Functions
```
def standalone_settings_present(project_dir: Path) -> bool
def find_connected_external_box(project_dir: Path, std: StandardPaths) -> _OwnedBox | None
def detect_box_mode(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> DetectionResult | None
def resolve_box_identity(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> dict[str, Any] | None
def _enumerate_worksets(std: StandardPaths) -> Iterator[tuple[str, Path, BoxMode]]
def _find_owning_box(project_dir: Path, std: StandardPaths, config: BootstrapConfig) -> _OwnedBox | None
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
