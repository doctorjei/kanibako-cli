# `src/kanibako/project/workset_registry.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/project/workset_registry.py.md`.


## Variables

```
_WORKSET_SECTION = 'workset'
_BOXES_SECTION = 'boxes'
_PROJECTS_SECTION = 'projects'
_SECTIONS = (_WORKSET_SECTION, _BOXES_SECTION, _PROJECTS_SECTION)
_SORTED_SECTIONS = (_BOXES_SECTION, _PROJECTS_SECTION)
```

## Functions
```
def load_workset_boxes(registry_path: Path) -> dict[str, str]
def load_workset_identity(registry_path: Path) -> dict[str, Any] | None
def load_workset_projects(registry_path: Path) -> dict[str, dict[str, Any]]
def save_workset_record(registry_path: Path, *, name: str, created: str, projects: Mapping[str, Mapping[str, Any]]) -> None
def register_workset_box(registry_path: Path, box_name: str, path: Path) -> None
def unregister_workset_box(registry_path: Path, box_name: str) -> None
def workset_box_path(registry_path: Path, box_name: str) -> str | None
def reverse_lookup_workset_box(registry_path: Path, workspace: Path | str) -> str | None
def resolve_workset_registry_path(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def _same_workspace(a: str, b: str) -> bool
def _load_raw(registry_path: Path) -> dict
def _section(full_doc: Mapping[str, Any], section: str) -> dict
def _write_doc(registry_path: Path, full_doc: dict) -> None
def _load_boxes_raw(registry_path: Path) -> tuple[dict, dict[str, str]]
def _write_boxes(registry_path: Path, full_doc: dict, boxes: dict[str, str]) -> None
```
