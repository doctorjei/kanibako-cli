# `src/kanibako/log.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
ROTATE_MAX_BYTES = 1048576
```

## Functions
```
def setup_logging(verbose: bool=False, *, timestamps: bool=False) -> None
def get_logger(name: str) -> logging.Logger
def rotate_if_over(path: Path, max_bytes: int, *, root: Path | None=None) -> bool
```
