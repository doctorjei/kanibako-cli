# `src/kanibako/channels/helper_listener.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/channels/helper_listener.py.md`.


## Variables

```
logger = get_logger('helper_listener')
_LOG_MAX_BYTES = 1048576
```

## Functions
```
def _send_json(conn: socket.socket, data: dict) -> None
def _build_helper_mounts(ctx: HelperContext, helper_num: int, helpers_dir: Path) -> list[Mount]
```

## Classes

```
@dataclass
class HelperContext:
    runtime: ContainerRuntime
    image: str
    container_name_prefix: str
    shell_path: Path
    helpers_dir: Path
    socket_path: Path
    binary_mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] | None = None
    entrypoint: str | None = None
    default_entrypoint: str | None = None
    box_shell: str | None = None
    project_path: Path | None = None
    data_path: Path | None = None
    boxes: Path | None = None
    registry: Path | None = None
    primary_workset: Path | None = None

class HelperHub:
    def __init__(self) -> None

    def start(self, socket_path: Path, context: HelperContext, log: MessageLog | None=None) -> None
    def stop(self) -> None

    def _accept_loop(self) -> None
    def _client_reader(self, conn: socket.socket) -> None
    def _dispatch(self, conn: socket.socket, request: dict, current_helper: int | None) -> tuple[dict | None, int | None]
    def _register(self, helper_num: int, conn: socket.socket) -> None
    def _unregister(self, helper_num: int) -> None
    def _route_message(self, sender: int, recipient: int, payload: dict) -> None
    def _broadcast_message(self, sender: int, payload: dict) -> None
    def _handle_spawn(self, request: dict) -> dict
    def _handle_stop(self, request: dict) -> dict
    def _handle_fork(self, request: dict) -> dict

class MessageLog:
    def __init__(self, log_path: Path) -> None

    def log_message(self, sender: int, recipient: int | str, payload: dict) -> None
    def log_control(self, event: str, helper: int | None=None, **extra: Any) -> None
    def close(self) -> None

    def _write(self, entry: dict) -> None
    def _rotate_if_needed(self) -> None
```
