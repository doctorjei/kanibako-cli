# `src/kanibako/launch/box_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/box_resolve.py.md`.


## Variables

```
_PRIMARY_WORKSET_NAME = 'default'
```

## Functions
```
def standalone_settings_present(project_dir: Path) -> bool
def find_connected_external_box(project_dir: Path, std: StandardPaths) -> _OwnedBox | None
def detect_box_mode(project_dir: Path, std: StandardPaths, config: KanibakoConfig) -> DetectionResult | None
def resolve_box_identity(project_dir: Path, std: StandardPaths, config: KanibakoConfig) -> dict[str, Any] | None
def _enumerate_worksets(std: StandardPaths) -> Iterator[tuple[str, Path, BoxMode]]
def _find_owning_box(project_dir: Path, std: StandardPaths, config: KanibakoConfig) -> _OwnedBox | None
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
