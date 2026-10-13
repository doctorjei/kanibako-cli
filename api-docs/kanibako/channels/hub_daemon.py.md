# `src/kanibako/channels/hub_daemon.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
log = get_logger('hub_daemon')
HUB_READY = b'ready\n'
READY_TIMEOUT = 15.0
STOP_TIMEOUT = 60.0
MISS_LIMIT = 3
PROBE_TIMEOUT = 10.0
_MODULE = 'kanibako.channels.hub_daemon'
```

## Functions
```
def hub_lock_path(socket_path: Path) -> Path
def decide_hub(*, misses: int, seen_live: bool, waited: float, grace: float, listening: bool) -> HubAction
def stop_hub(socket_path: Path, *, timeout: float=STOP_TIMEOUT) -> bool
def serve(hub: Any, *, is_live: Any, stop: threading.Event, poll: float, grace: float, clock: Any=time.monotonic) -> None
def main(argv: list[str] | None=None) -> int
def _try_lock(fd: IO[str]) -> bool
def _holder_pid(lock_path: Path) -> int | None
def _take_lock(socket_path: Path) -> IO[str] | None
def _read_ready(stream: IO[bytes], timeout: float) -> bool
def _box_live(runtime: Any, container: str) -> bool
def _build_parser() -> argparse.ArgumentParser
```

## Classes

```
class HubAction(Enum):
    SERVE = 'serve'
    REBIND = 'rebind'
    EXIT = 'exit'

class HubProcess:
    def __init__(self) -> None

    def start(self, socket_path: Path, context: Any, *, log_path: Path | None, container: str, stderr_path: Path | None) -> bool
    def release(self) -> None
    def stop(self) -> None

    def _at_exit(self) -> None
```
