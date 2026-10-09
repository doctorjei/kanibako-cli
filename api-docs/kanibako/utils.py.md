# `src/kanibako/utils.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
WORKSET_SEGMENT_PRIMARY = 'primary'
WORKSET_SEGMENT_STANDALONE = 'standalone'
CONTAINER_NAME_PREFIX = 'kb-'
_GITIGNORE_ENTRIES = ['box_data/']
```

## Functions
```
def cp_if_newer(src: str | os.PathLike, dst: str | os.PathLike) -> bool
def confirm_prompt(message: str) -> None
def deep_merge(base: dict, override: dict) -> dict
def short_hash(full_hash: str, length: int=8) -> str
def renders_no_name(box: str) -> bool
def rename_box_cure(mode: str, path: Path) -> str
def unrenderable_box_name_refusal(box: str, mode: str, path: Path | None, designation: str | None) -> str
def name_segment(segment: str) -> str
def workset_segment(mode: str, group_name: str | None) -> str
def render_container_name(workset: str, box: str, helper_num: int | None=None) -> str | None
def render_socket_identity(box: str, workset: str) -> str | None
def container_name_for_box_name(name: str, workset: str) -> str | None
def container_name_segments(proj: ProjectPaths) -> tuple[str, str]
def container_name_for(proj: ProjectPaths) -> str | None
def legacy_container_names(proj: ProjectPaths) -> tuple[str, ...]
def project_hash(project_path: str) -> str
def literal_path(value: str | os.PathLike[str]) -> str
def logical_cwd() -> str
def write_project_gitignore(project_path: Path) -> None
def project_gitignore_to_strip(project_path: Path) -> Path | None
def gitignore_holds_only_kanibako(gitignore: Path) -> bool
def strip_project_gitignore(gitignore: Path) -> bool
def _gitignore_kept_text(gitignore: Path) -> str
```
