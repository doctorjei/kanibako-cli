# `src/kanibako/settings/core_defaults.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/core_defaults.py.md`.


## Variables

```
CORE_DEFAULTS_FILENAME = 'core-defaults.yaml'
KICKOFF_PACKAGED_PARTS = ('global', 'KICKOFF.md')
ROM_ROOT_PARTS = ('global', 'rom')
CANON_GUEST_ROOT = 'canon'
ROM_COLLECTION_REL = 'COLLECTION.md'
ROM_BIBLE_REL = 'bible'
ROM_CONTENTS_REL = f'{ROM_BIBLE_REL}/ROM_CONTENTS.md'
HANDBOOK_REL = 'handbook'
ROM_GUIDE_REL = 'bible/general/directives/ROM_GENERAL.md'
ROM_BIBLE_CHAPTERS = ('general', 'workset', 'box')
BIBLE_AGENT_CHAPTER = 'agent'
PLUGIN_CHAPTER_MARKER_REL = 'directives/ROM_AGENT.md'
CANON_SEED_DENY_PREFIXES = (f'{CANON_GUEST_ROOT}/COLLECTION.md', f'{CANON_GUEST_ROOT}/{ROM_BIBLE_REL}', f'{CANON_GUEST_ROOT}/{HANDBOOK_REL}')
CANON_ACTIVE_AGENT_TOKEN = '<active>'
HANDBOOK_CHAPTERS = ('general', 'agent', 'workset', 'box')
HANDBOOK_CONTENTS_REL = f'{HANDBOOK_REL}/SYS_CONTENTS.md'
HANDBOOK_FALLBACK_ENTRIES: tuple[tuple[str, str], ...] = (('agent', 'SYS_AGENT.md'), ('workset', 'SYS_WORKSET.md'), ('box', 'SYS_BOX.md'))
HANDBOOK_DIRECTIVES_DIRNAME = 'directives'
UNSHARE_BOX_ROOT_UID = 1
UNSHARE_BOX_ROOT_GID = 1
CANON_SKELETON_DIR_MODE = '555'
CANON_SKELETON_FILE_MODE = '444'
BIND_TABLES = ('channels', 'core', 'kani', 'kickoff', 'canon', 'helpers', 'images')
```

## Types
```
BindArmTable = dict[str, dict[str, tuple[str, ...]]]

```

## Functions
```
def packaged_data_dir(*parts: str) -> Traversable
def vault_mask_default() -> list[str]
def behavior_defaults() -> dict[str, str]
def behavior_default(key: str) -> str
def env_default_categories() -> dict[str, str]
def add_bind(binds: dict[str, Any], category: str, box_dest: str, host_src: str, options: str | None=None, *, scope: str='box') -> None
def channel_default_categories(std: StandardPaths, proj: ProjectPaths) -> BindArmTable
def core_default_categories(std: StandardPaths, proj: ProjectPaths, *, enable_vault: bool, mode: str, guarantee_create: bool=True) -> BindArmTable
def kani_default_categories() -> BindArmTable
def kickoff_box_dest() -> str
def kickoff_guest_dest() -> str
def kickoff_default_categories(descriptor: 'PluginDescriptor | None'=None) -> BindArmTable
def assert_canon_bind_seed_disjoint(bind_dests: Iterable[str], seed_rels: Iterable[str]) -> None
def rom_default_categories() -> BindArmTable
def rom_agent_default_categories(target: 'Target') -> BindArmTable
def canon_optional_bind_keys() -> frozenset[str]
def canon_optional_bind_dests() -> frozenset[str]
def canon_default_categories(std: StandardPaths, agent_name: str | None) -> dict[str, object]
def canon_skeleton_rels() -> tuple[tuple[str, bool], ...]
def materialize_canon_skeleton(shell_path: Path, *, logger: 'logging.Logger | None'=None, quiet: bool=False) -> None
def materialize_canon_skeleton_if_present(shell_path: Path, *, logger: 'logging.Logger | None'=None) -> None
def bind_dest_families() -> dict[str, str]
def helper_bind_dests() -> frozenset[str]
def image_bind_dests() -> frozenset[str]
def helper_default_categories(*, socket_path: Path, log_path: Path) -> BindArmTable
def image_default_categories(*, graph_root: Path | None, storage_conf_path: Path) -> dict[str, object]
def _load_doc() -> dict[str, Any]
def _check_env_key(scope: str, var: str) -> None
def _kickoff_entry() -> dict[str, Any]
def _canon_dest(rel: str) -> str
def _canon_optional_rows() -> list[Any]
def _skeleton_logger() -> 'logging.Logger'
def _protect_canon_skeleton(dirs: list[Path], files: list[Path], log: 'logging.Logger', *, quiet: bool=False) -> None
def _warn_unprotected(root: Path, log: 'logging.Logger', reason: str, agent_owned: bool, quiet: bool=False) -> None
def _table_bind_dests(table: str) -> frozenset[str]
```
