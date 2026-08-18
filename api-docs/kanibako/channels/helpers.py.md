# `src/kanibako/channels/helpers.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/channels/helpers.py.md`.


## Variables

```
UNLIMITED_BREADTH = 2 ** 16
DEFAULT_DEPTH = 4
DEFAULT_BREADTH = 4
_INIT_SCRIPT_NAME = 'helper-init.sh'
```

## Functions
```
def effective_breadth(breadth: int) -> int
def parent_of(agent: int, breadth: int) -> int | None
def check_spawn_allowed(budget: SpawnBudget, current_children: int) -> str | None
def child_budget(parent: SpawnBudget) -> SpawnBudget
def resolve_spawn_budget(ro_config: SpawnBudget | None, host_config: SpawnBudget | None, cli_depth: int | None, cli_breadth: int | None) -> SpawnBudget
def read_spawn_config(path: Path) -> SpawnBudget | None
def write_spawn_config(path: Path, budget: SpawnBudget) -> None
def create_helper_dirs(helpers_dir: Path, helper_num: int) -> Path
def create_broadcast_dirs(helpers_dir: Path) -> Path
def create_peer_channels(helpers_dir: Path, new_helper: int, existing_helpers: list[int]) -> None
def link_broadcast(helpers_dir: Path, helper_num: int) -> None
def remove_helper_dirs(helpers_dir: Path, helper_num: int, sibling_helpers: list[int]) -> None
def bundled_init_script() -> Path
def resolve_init_script(parent_scripts_dir: Path | None) -> Path
def _link_peer(helpers_dir: Path, helper_num: int, name: str, target: Path) -> None
```

## Classes

```
@dataclass(frozen=True)
class SpawnBudget:
    depth: int = DEFAULT_DEPTH
    breadth: int = DEFAULT_BREADTH
```
