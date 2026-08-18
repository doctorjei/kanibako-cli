# `src/kanibako/browser_state.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/browser_state.py.md`.


## Variables

```
logger = get_logger('browser_state')
```

## Functions
```
def state_path(data_path: Path) -> Path
def load_state(data_path: Path) -> BrowserState
def save_state(data_path: Path, state: BrowserState) -> None
def to_playwright_context(state: BrowserState) -> dict
def from_playwright_context(context: dict) -> BrowserState


@dataclass
class BrowserState:
    cookies: list[dict] = field(default_factory=list)
    origins: list[dict] = field(default_factory=list)
    updated_at: float = 0.0
```
