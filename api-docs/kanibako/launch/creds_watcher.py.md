# `src/kanibako/launch/creds_watcher.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/creds_watcher.py.md`.


## Variables

```
log = get_logger('creds_watcher')
CREDS_DIRTY_RELPATH = '.kanibako/creds-dirty'
```

## Types
```
_IsRunning = Callable[[], bool]
_FlagReader = Callable[[], bool]
_Writeback = Callable[[], None]
_Sleeper = Callable[[float], None]

```

## Functions
```
def creds_dirty_flag_path(project_home: Path) -> Path
def read_creds_dirty(project_home: Path) -> bool
def clear_creds_dirty(project_home: Path) -> None
@contextlib.contextmanager
def creds_store_lock() -> 'Iterator[None]'
def decide_watch(box_running: bool, dirty: bool) -> WatchAction
def main(argv: list[str] | None=None) -> int
def _build_parser() -> argparse.ArgumentParser
def _single_instance_lock(lock_path: Path) -> 'object | None'
def _resolve_watch_context(box: str | None)


class WatchAction(Enum):
    NONE = 'none'
    WRITEBACK = 'writeback'
    FINAL_WRITEBACK = 'final_writeback'
    EXIT = 'exit'

class CredsWatcher:
    def __init__(self, *, is_running: _IsRunning, read_dirty: _FlagReader, writeback: _Writeback, sleep: _Sleeper=time.sleep, poll_interval: float=2.0) -> None

    def run(self) -> int

    def _safe_writeback(self) -> None
```
