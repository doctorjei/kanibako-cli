# `src/kanibako/auth_browser.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = get_logger('auth_browser')
sync_playwright: Any = None
_AUTHORIZE_TIMEOUT_MS = 30000
_NAVIGATION_TIMEOUT_MS = 30000
```

## Types
```
PWTimeout: type[Exception] = Exception

```

## Functions
```
def refresh_auth(url: str, *, headless: bool=True) -> AuthResult
def auto_refresh_auth(claude_path: str, *, headless: bool=True, login_timeout: float=60, env: dict[str, str] | None=None) -> AuthResult
def _check_playwright() -> bool
def _handle_auth_page(page) -> AuthResult
def _extract_key(page) -> str | None
```

## Classes

```
@dataclass
class AuthResult:
    success: bool
    key: str | None = None
    error: str | None = None
```
