# `src/kanibako/channels/helper_client.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/channels/helper_client.py.md`.


## Variables

```
_CLOSED = object()
_READER_JOIN = 5.0
```

## Functions
```
def send_request(socket_path: Path, request: dict) -> dict
def _route_frame(line: bytes, responses: queue.Queue[Any], inbox: queue.Queue[Any]) -> str | None
```

## Classes

```
class HelperConnection:
    def __init__(self) -> None

    def connect(self, socket_path: Path, helper_num: int | None=None) -> None
    def spawn(self, helper_num: int, model: str | None=None, helpers_dir: str | None=None) -> dict
    def stop(self, container_name: str, helper_num: int) -> dict
    def send(self, to: int, payload: dict) -> dict
    def broadcast(self, payload: dict) -> dict
    def recv(self, timeout: float | None=None) -> dict | None
    def close(self) -> None

    def _read_loop(self, sock: socket.socket, responses: queue.Queue[Any], inbox: queue.Queue[Any]) -> None
    def _request(self, data: dict) -> dict
```
