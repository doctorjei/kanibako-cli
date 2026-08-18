# `src/kanibako/box_lifecycle.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/box_lifecycle.py.md`.


## Variables

```
VSCODE_SERVER_DIR_MARKERS: tuple[str, ...] = ('.vscode-server', '.vscode-server-insiders', '.vscode-server-oss', '.cursor-server')
```

## Types
```
_Runner = Callable[..., 'subprocess.CompletedProcess[str]']

```

## Functions
```
def is_vscode_server_path_part(part: str) -> bool
def vscode_server_present(proc_cmdlines: Iterable[str]) -> bool
def tmux_terminal_attached(list_clients_output: str) -> bool
def classify_transition(prev: AttachState, cur: AttachState) -> LifecycleEvent
def snapshot_attach_state(session: str, *, run: _Runner=subprocess.run, proc_cmdlines: Iterable[str] | None=None) -> AttachState
def canonical_tmux_session_pid(session: str, *, run: _Runner=subprocess.run) -> int | None
def _collect_proc_cmdlines() -> list[str]
def _tmux_clients_output(session: str, run: _Runner) -> str
```

## Classes

```
@dataclass(frozen=True)
class AttachState:
    vscode_server: bool = False
    tmux_terminal: bool = False

    @property
    def any_attached(self) -> bool

    @property
    def _surfaces(self) -> tuple[bool, ...]

class LifecycleEvent(Enum):
    ATTACH = 'attach'
    DETACH = 'detach'
    NONE = 'none'
```
