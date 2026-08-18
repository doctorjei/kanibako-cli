# `src/kanibako/runtime/baseline.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/baseline.py.md`.


## Variables

```
BASELINE_FILENAME = 'image-baseline.yaml'
```

## Functions
```
def load_baseline() -> dict[str, list[str]]
def packages() -> list[str]
def executables() -> list[tuple[str, str]]
def verify(probe: Callable[[str], bool]) -> list[tuple[str, str]]
def install_command(pkgs: list[str]) -> list[str]
def warn_non_debian() -> None
def _read_doc(path: Path) -> dict[str, list[str]]
def _shipped_default() -> dict[str, list[str]]
def _overlay_paths() -> list[Path]
```
