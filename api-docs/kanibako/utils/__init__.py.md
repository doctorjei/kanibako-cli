# `src/kanibako/utils/__init__.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
UTC_STAMP_COMPACT = '%Y%m%dT%H%M%SZ'
UTC_STAMP_SECONDS = '%Y-%m-%dT%H:%M:%SZ'
WORKSET_SEGMENT_PRIMARY = 'primary'
WORKSET_SEGMENT_STANDALONE = 'standalone'
CONTAINER_NAME_PREFIX = 'kb-'
LABEL_WORKSET = 'kanibako.workset'
LABEL_BOX = 'kanibako.box'
LABEL_WORKSET_KUID = 'kanibako.workset_kuid'
LABEL_HELPER = 'kanibako.helper'
_GITIGNORE_ENTRIES = [f'{STANDALONE_META_DIR}/']
```

## Functions
```
def confirm_prompt(message: str) -> None
def confirm_or_abort(force: bool, message: str, *, announce_abort: bool=True) -> bool
def deep_merge(base: dict, override: dict) -> dict
def utc_stamp(kind: Literal['iso', 'compact', 'seconds']) -> str
def short_hash(full_hash: str, length: int=8) -> str
def renders_no_name(box: str) -> bool
def workset_renders_no_name(workset: str) -> bool
def rename_box_cure(mode: str, path: Path, name: str | None=None, taken: Callable[[str], bool] | None=None, connected: str | None=None) -> str
def legacy_workset_reason(workset: str) -> str
def legacy_workset_cure(workset: str, where: Path) -> str
def workset_segment(mode: str, group_name: str | None) -> str
def render_container_name(workset: str, box: str, helper_num: int | None=None) -> str | None
def render_socket_identity(box: str, workset: str) -> str | None
def container_name_for_box_name(name: str, workset: str) -> str | None
def container_name_segments(proj: ProjectPaths) -> tuple[str, str]
def container_name_for(proj: ProjectPaths) -> str | None
def box_labels(proj: ProjectPaths, helper_num: int | None=None) -> dict[str, str]
def box_container(runtime: ContainerRuntime, proj: ProjectPaths, *, check: bool=False) -> str | None
def box_container_named(runtime: ContainerRuntime, box: str, workset: str, *, check: bool=False) -> str | None
def legacy_container_names(proj: ProjectPaths) -> tuple[str, ...]
def project_hash(project_path: str) -> str
def stat_unless_gone(path: str | os.PathLike[str]) -> os.stat_result | None
def is_dir_unless_gone(path: str | os.PathLike[str]) -> bool
def is_file_unless_gone(path: str | os.PathLike[str]) -> bool
def iterdir_unless_gone(path: Path) -> list[Path] | None
def refuse_unreadable_file(path: str | os.PathLike[str]) -> None
def unreadable_path_error(path: str | os.PathLike[str], exc: OSError) -> ConfigError
def literal_path(value: str | os.PathLike[str]) -> str
def is_home_dir(path: str | os.PathLike[str]) -> bool
def logical_cwd() -> str
def write_project_gitignore(project_path: Path) -> None
def project_gitignore_to_strip(project_path: Path) -> Path | None
def gitignore_holds_only_kanibako(gitignore: Path) -> bool
def strip_project_gitignore(gitignore: Path) -> bool
def _labeled_or_rendered(runtime: ContainerRuntime, labels: dict[str, str], rendered: str, *, check: bool) -> str
def _gitignore_kept_text(gitignore: Path) -> str
```
