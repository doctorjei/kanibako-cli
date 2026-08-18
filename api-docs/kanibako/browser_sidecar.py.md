# `src/kanibako/browser_sidecar.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/browser_sidecar.py.md`.


## Variables

```
logger = get_logger('browser_sidecar')
_DEFAULT_IMAGE = 'chromedp/headless-shell:latest'
_CDP_PORT = 9222
_STARTUP_TIMEOUT = 30
_HEALTH_CHECK_INTERVAL = 0.5
```

## Functions
```
def ws_endpoint_for_container(ws_url: str) -> str
```

## Classes

```
@dataclass
class BrowserSidecar:
    runtime: ContainerRuntime
    container_name: str
    image: str = _DEFAULT_IMAGE
    host_port: int = 0
    _started: bool = False

    def start(self) -> str
    def stop(self) -> None

    def _resolve_port(self) -> int
    def _wait_for_endpoint(self, port: int) -> str

class BrowserSidecarError(Exception):
```
