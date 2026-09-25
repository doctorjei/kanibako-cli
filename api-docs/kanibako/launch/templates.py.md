# `src/kanibako/launch/templates.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/templates.py.md`.


## Variables

```
AGENT_TEMPLATE_STORE_REL = 'template'
SCOPE_WHITELISTS: dict[str, tuple[str, ...]] = {'box': ('home', 'canon/handbook'), 'agent': ('template', 'canon/handbook', 'common'), 'workset': ('template', 'canon/handbook')}
copy_resource_tree_if_absent = copy_tree
PACKAGED_BOX_TEMPLATE = 'box'
PACKAGED_WORKSET_TEMPLATE = 'workset'
PACKAGED_AGENT_DEFAULT = 'agent_default'
PACKAGED_HANDBOOK = 'handbook'
AGENT_MOULD_DIRNAME = 'agent'
PLUGIN_STORE_PAYLOAD_DIRNAME = 'base'
_SEED_DEST_HOME = '~/'
_SEED_SRC_HOME = 'box/home'
_SEED_SRC_HANDBOOK = 'box/canon/handbook'
_BOX_TEMPLATE_SKELETON = ('box/home/canon/notebook', 'box/home/canon/workbook', 'box/canon/handbook')
_MOULD_CANON_ROOT = 'canon'
_CANON_CHAPTER_LEAF = HANDBOOK_REL
_HTML_COMMENT_RE = re.compile('<!--.*?-->', re.DOTALL)
_FENCE_RE = re.compile('(^|\\n)(```|~~~)[^\\n]*\\n.*?(\\n\\2[^\\n]*(?=\\n|$))', re.DOTALL)
```

## Functions
```
def agent_template_defaults(agent_id: str | None) -> dict[str, object]
def template_seed_defaults(proj: ProjectPaths, agent_id: str | None) -> dict[str, object]
def stage_layers(dest: Path, layers: list[Path]) -> None
def copy_tree(src: Path, dest: Path, *, overwrite: bool=False, scope: str | WorksetStampScope | None=None, dest_root: Path | None=None, check_only: bool=False) -> None
def packaged_box_home_template() -> Path | None
def ensure_agent_stores(std: StandardPaths, agent_names: 'Iterable[str]') -> list[str]
def check_workset_template(std: StandardPaths, workset_path: Path, *, canon_only: bool=False) -> None
def install_workset_template(std: StandardPaths, workset_path: Path, *, canon_only: bool=False) -> None
def handbook_layer_source_keys(proj: ProjectPaths, agent_id: str | None) -> tuple[str, ...]
def install_box_handbook_template(dest: Path, layer_roots: Iterable[Path]) -> None
def install_packaged_templates(std: StandardPaths, agent_names: list[str], refresh: bool=False) -> None
def walk_shipped_files(root: Path) -> list[tuple[str, Path]]
def packaged_templates_digest(agent_names: list[str]) -> str
def plan_template_refresh(std: StandardPaths, agent_names: list[str]) -> tuple[list[Path], list[Path], list[Path]]
def _scope_rules(scope: str | WorksetStampScope) -> tuple[str, tuple[str, ...]]
def _check_whitelist(store_rel: Path, scope: str | WorksetStampScope) -> None
def _is_contained(target: Path, root: Path) -> bool
def _assert_contained(target: Path, root: Path, *, what: str) -> None
def _packaged_base_template() -> Path | None
def _packaged_shared_bundle() -> Path | None
def _packaged_agent_store(agent_name: str) -> Path | None
def _assert_stamp_leaf_in_root(workset_path: Path, doc: Mapping[str, Any] | None, resolved: Path, leaf: str) -> None
def _workset_stamp_dirs(workset_path: Path, *, canon_only: bool) -> tuple[Path, Path]
def _workset_scope_allowed(workset_path: Path, canon_root: Path, template_root: Path) -> tuple[str, ...]
def _workset_stamp_copy(std: StandardPaths, workset_path: Path, canon_only: bool, canon_root: Path) -> tuple[Path, Path]
def _is_shipped_content(entry: Path) -> bool
def _packaged_manifest_entries(agent_names: list[str]) -> list[tuple[str, bytes]]
def _normalise_markdown(text: str) -> str
def _equivalent(src_file: Path, target: Path) -> bool
```

## Classes

```
@dataclass(frozen=True)
class WorksetStampScope:
    workset_path: Path
    canon_root: Path
    template_root: Path
    name: ClassVar[str] = 'workset'

    def allowed(self) -> tuple[str, ...]
```
