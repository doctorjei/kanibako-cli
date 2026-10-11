# `src/kanibako/channels/helpers.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
UNLIMITED_BREADTH = 2 ** 16
DEFAULT_DEPTH = SPAWN_BUDGET_DEFAULTS['depth']
DEFAULT_BREADTH = SPAWN_BUDGET_DEFAULTS['breadth']
SPAWN_CONFIG_FILENAME = 'spawn.yaml'
HELPER_NUM_KEY: tuple[str, ...] = ('meta', 'box', 'helper_num')
HELPER_SCRIPTS_RELPATH = 'scripts'
PARENT_SCRIPTS_RELPATH = f'{CANON_GUEST_ROOT}/{NOTEBOOK_SCRIPTS_REL}'
HELPER_CORE_LEAVES: dict[str, str] = {'project_path': 'workspace', 'vault_ro_path': 'vault/ro', 'vault_rw_path': 'vault/rw'}
HELPER_STATE_FILENAME = 'state.json'
INIT_SCRIPT_NAME = 'helper-init.sh'
```

## Functions
```
def effective_breadth(breadth: int) -> int
def parent_of(agent: int, breadth: int) -> int | None
def check_spawn_allowed(budget: SpawnBudget, current_children: int) -> str | None
def child_budget(parent: SpawnBudget) -> SpawnBudget
def resolve_spawn_budget(handed_down: SpawnBudget | None, own_settings: SpawnBudget | None, cli_depth: int | None, cli_breadth: int | None) -> SpawnBudget
def read_spawn_budget(path: Path) -> SpawnBudget | None
def write_spawn_budget(path: Path, budget: SpawnBudget) -> None
def write_helper_number(path: Path, helper_num: int) -> None
def read_helper_number(path: Path) -> int | None
def read_helper_state(helpers_dir: Path, helper_num: int) -> dict
def write_helper_state(helpers_dir: Path, helper_num: int, state: dict) -> None
def mark_helper_stopped(helpers_dir: Path, helper_num: int) -> None
def create_helper_dirs(helpers_dir: Path, helper_num: int) -> Path
def create_broadcast_dirs(helpers_dir: Path) -> Path
def create_peer_channels(helpers_dir: Path, new_helper: int, existing_helpers: list[int]) -> None
def remove_helper_dirs(helpers_dir: Path, helper_num: int, sibling_helpers: list[int]) -> None
def bundled_init_script() -> Path
def resolve_init_script(parent_scripts_dir: Path | None) -> Path
def _table(doc: dict, sections: tuple[str, ...]) -> dict
def _link_peer(helpers_dir: Path, helper_num: int, name: str, target: Path) -> None
```

## Classes

```
@dataclass(frozen=True)
class SpawnBudget:
    depth: int = DEFAULT_DEPTH
    breadth: int = DEFAULT_BREADTH
```
