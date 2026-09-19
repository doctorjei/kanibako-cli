# `src/kanibako/browser_state.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Variables

```
logger = get_logger('browser_state')
```

## Functions
```
def state_path() -> Path
def load_state() -> BrowserState
def save_state(state: BrowserState) -> None
def to_playwright_context(state: BrowserState) -> dict
def from_playwright_context(context: dict) -> BrowserState
```

## Classes

```
@dataclass
class BrowserState:
    cookies: list[dict] = field(default_factory=list)
    origins: list[dict] = field(default_factory=list)
    updated_at: float = 0.0
```
