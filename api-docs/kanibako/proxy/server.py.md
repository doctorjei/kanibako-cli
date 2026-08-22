# `src/kanibako/proxy/server.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/proxy/server.py.md`.


## Variables

```
logger = get_logger('proxy.server')
MESSAGES_PATH = '/v1/messages'
_RELAY_CHUNK = 8192
_HOP_BY_HOP = frozenset({'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection', 'te', 'trailer', 'transfer-encoding', 'upgrade'})
_REQUEST_DROP = _HOP_BY_HOP | {'host', 'content-length'}
```

## Functions
```
def fixup_request_body(path: str, body: bytes, fixup_models: frozenset[str]) -> dict[str, Any] | None
```

## Classes

```
@dataclass(frozen=True)
class ProxyConfig:
    upstream_base: str
    fixup_models: frozenset[str] = frozenset()
    timeout: float = 600.0

class ProxyServer:
    def __init__(self, config: ProxyConfig, host: str='127.0.0.1', port: int=0) -> None

    @property
    def host(self) -> str
    @property
    def port(self) -> int
    @property
    def url(self) -> str
    def start(self) -> None
    def stop(self) -> None
    def serve_forever(self) -> None

    def __enter__(self) -> ProxyServer
    def __exit__(self, *_exc: object) -> None

class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl)

class _AnthropicProxyHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'kanibako-proxy'
    sys_version = ''
    do_GET = do_POST = do_DELETE = _handle

    def __init__(self, *args: Any, config: ProxyConfig, **kwargs: Any) -> None

    def log_message(self, format: str, *args: Any) -> None

    def _handle(self) -> None
    def _passthrough(self, body: bytes) -> None
    def _fixup(self, rewritten: dict[str, Any]) -> None
    def _forward(self, body: bytes, *, decode_body: bool) -> Any
    def _send_relayed_headers(self, status: int, headers: Message) -> None
    def _send_stream_headers(self) -> None
    def _relay_whole(self, status: int, headers: Message, raw: bytes) -> None
    def _relay_gateway_failure(self, exc: Exception) -> None
    def _end_headers_and_close(self) -> None
    def _read_body(self) -> bytes
```
