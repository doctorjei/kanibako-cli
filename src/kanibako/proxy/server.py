"""The listener: read a client request, pick a mode, forward upstream, relay the result.

A stdlib ``http.server`` on the Anthropic surface.  Claude Code talks to it
exactly as it talks to ``api.anthropic.com``; it talks upstream to an
OpenAI-compatible / LiteLLM gateway.  Only a proxy sits on that wire, which is
why this exists at all: a box's own model calls cross it before any tool runs.

PASSTHROUGH is the default for every model and every path.  FIXUP applies only
where :func:`fixup_request_body` says it does — a ``stream: true`` POST to
``/v1/messages`` for a model in the configured fixup set.

⚑ THE AUTHORIZATION HEADER IS FORWARDED AND OTHERWISE UNTOUCHABLE.  It is never
logged, never printed, never persisted, and never included in an error message —
which is also why :meth:`_AnthropicProxyHandler._relay_gateway_failure` reports
an exception's TYPE and not its text.

⚑ A NON-200 IS RELAYED WITH ITS STATUS AND BODY INTACT, always.  A 429 body is
not SSE and parses as "no tool block" — the same signature as the defect this
module papers over.  A proxy that swallowed the status would make rate limiting
indistinguishable from the bug; that mistake has been made once on this record
and cost a wrong conclusion.

DELIBERATELY NOT HANDLED, so the next reader does not go looking: a
chunked-transfer REQUEST body (clients on this surface send ``Content-Length``),
HTTP/2, and upstream connection reuse.  Every response closes its connection —
see :meth:`_AnthropicProxyHandler._send_relayed_headers`.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from kanibako.log import get_logger
from kanibako.proxy.sse import synthesize_stream

logger = get_logger("proxy.server")

#: The one path FIXUP can apply to.  Everything else on the surface passes through.
MESSAGES_PATH = "/v1/messages"

#: Read size when relaying an upstream stream back to the client.
_RELAY_CHUNK = 8192

# Hop-by-hop headers (RFC 9110 §7.6.1) belong to a single connection and must not
# cross a proxy in either direction.  ``transfer-encoding`` especially: urllib has
# already de-chunked the body by the time we see it, so relaying the header would
# describe a framing that is no longer there.
_HOP_BY_HOP = frozenset({
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
})

# On the way out: ``host`` is re-derived from the upstream URL and
# ``content-length`` is recomputed, because FIXUP rewrites the body.
_REQUEST_DROP = _HOP_BY_HOP | {"host", "content-length"}


@dataclass(frozen=True)
class ProxyConfig:
  """Where the proxy forwards to, and which models get FIXUP instead of PASSTHROUGH.

  ⚑ *fixup_models* is a CONSTRUCTOR ARGUMENT and empty by default — never a
  module global and never a hardcoded model list.  De-streaming costs the client
  its incremental tokens, so it is opt-in per model: an unconfigured proxy is a
  pure passthrough.  Which settings scope eventually supplies the set is an open
  question that does not need answering to run one.
  """

  upstream_base: str                            # scheme://host[:port], no trailing path
  fixup_models: frozenset[str] = frozenset()    # model ids to de-stream
  timeout: float = 600.0                        # seconds; a de-streamed call returns all at once


def fixup_request_body(
  path: str,
  body: bytes,
  fixup_models: frozenset[str],
) -> dict[str, Any] | None:
  """Return the ``stream: false`` rewrite of *body* when FIXUP applies, else ``None``.

  ``None`` means PASSTHROUGH, and it is the answer for everything except a
  ``/v1/messages`` request that asked for ``stream: true`` naming a model in
  *fixup_models*.  Returning the rewritten body rather than a boolean keeps the
  decision and its consequence in one place: no caller can conclude "fixup" and
  then forget to flip the flag.

  Unparseable or non-object bodies pass through untouched — this is a proxy, and
  a body we cannot read is still a body the gateway may understand.
  """
  if not fixup_models:
    return None
  if urlsplit(path).path.rstrip("/") != MESSAGES_PATH:
    return None
  try:
    parsed = json.loads(body)
  except (ValueError, UnicodeDecodeError):
    return None
  if not isinstance(parsed, dict):
    return None
  if parsed.get("stream") is not True:
    return None
  if parsed.get("model") not in fixup_models:
    return None
  rewritten = dict(parsed)
  rewritten["stream"] = False
  return rewritten


class _NoRedirects(urllib.request.HTTPRedirectHandler):
  """Refuse every redirect: urllib would re-send the `Authorization` bearer cross-origin.

  The 3xx is raised as an ``HTTPError`` instead, which this module relays to the
  client with its ``Location`` intact — following it is the CLIENT's decision to
  make, with the client's own credentials.
  """

  def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
    return None


class _AnthropicProxyHandler(BaseHTTPRequestHandler):
  """One request: decide the mode, forward it upstream, relay what comes back."""

  protocol_version = "HTTP/1.1"
  server_version = "kanibako-proxy"
  sys_version = ""

  def __init__(self, *args: Any, config: ProxyConfig, **kwargs: Any) -> None:
    # Set BEFORE super().__init__, which handles the whole request inline.
    self._config = config
    self._headers_sent = False
    super().__init__(*args, **kwargs)

  # -- routing --------------------------------------------------------------

  def _handle(self) -> None:
    """Route one request, and never let an exception escape into the server loop."""
    try:
      body = self._read_body()
      rewritten = fixup_request_body(self.path, body, self._config.fixup_models)
      # ⚑ The PATH only, never ``self.path``: a query string is a place a gateway
      # key can legitimately live, and this line is the one that would copy it
      # into a log file.
      logger.debug(
        "proxy: %s %s %s",
        "PASSTHROUGH" if rewritten is None else "FIXUP",
        self.command,
        urlsplit(self.path).path,
      )
      if rewritten is None:
        self._passthrough(body)
      else:
        self._fixup(rewritten)
    except (BrokenPipeError, ConnectionResetError):
      # The client hung up mid-relay. Nothing to say and nowhere to say it.
      self.close_connection = True
    except Exception as exc:
      self._relay_gateway_failure(exc)

  # The surface is "/v1/messages and friends": every verb it uses is forwarded
  # by the same path, because the mode decision reads the body, not the method.
  do_GET = do_POST = do_DELETE = _handle

  def log_message(self, format: str, *args: Any) -> None:
    """Send the access log to the kanibako logger instead of raw stderr.

    ``BaseHTTPRequestHandler`` logs the request LINE only; no header and no body
    reaches this, which is the property that keeps the bearer token out of it.
    """
    logger.debug("proxy %s", format % args)

  # -- the modes ------------------------------------------------------------

  def _passthrough(self, body: bytes) -> None:
    """Forward verbatim and relay the answer back as it arrives.

    ⚑ ``read1``, NOT ``read``.  ``read(n)`` blocks until it has all *n* bytes or
    the connection ends, so relaying a live SSE stream through it would sit on
    events until 8 KB had piled up — silently converting PASSTHROUGH into a
    slower, worse version of the de-streaming it exists to avoid.  ``read1``
    returns whatever one underlying read yields.
    """
    with self._forward(body, decode_body=False) as upstream:
      self._send_relayed_headers(upstream.status, upstream.headers)
      while True:
        chunk = upstream.read1(_RELAY_CHUNK)
        if not chunk:
          break
        self.wfile.write(chunk)
        self.wfile.flush()

  def _fixup(self, rewritten: dict[str, Any]) -> None:
    """Ask upstream without streaming, then synthesize the stream the client asked for."""
    payload = json.dumps(rewritten).encode("utf-8")
    with self._forward(payload, decode_body=True) as upstream:
      status = upstream.status
      headers = upstream.headers
      raw = upstream.read()

    if status != 200:
      self._relay_whole(status, headers, raw)
      return
    try:
      complete = json.loads(raw)
    except ValueError:
      # A 200 whose body is not JSON is not a message we can de-stream. Relay it
      # as it came rather than invent a stream around it.
      logger.debug("proxy: upstream 200 body was not JSON; relayed verbatim")
      self._relay_whole(status, headers, raw)
      return
    if not isinstance(complete, dict):
      self._relay_whole(status, headers, raw)
      return

    self._send_stream_headers()
    for frame in synthesize_stream(complete):
      self.wfile.write(frame.encode("utf-8"))
      self.wfile.flush()

  # -- upstream -------------------------------------------------------------

  def _forward(self, body: bytes, *, decode_body: bool) -> Any:
    """Send this request upstream and return the response — ``HTTPError`` included.

    ⚑ An ``HTTPError`` IS an HTTP response, so it is RETURNED rather than raised
    past: that is what keeps a 429 a 429 all the way back to the client.

    *decode_body* is the FIXUP case, where the body has to be parsed here and so
    must not arrive compressed; PASSTHROUGH relays bytes it never inspects and
    leaves the client's own ``Accept-Encoding`` negotiation alone.
    """
    url = self._config.upstream_base.rstrip("/") + self.path
    request = urllib.request.Request(url, data=body or None, method=self.command)
    for name, value in self.headers.items():
      lowered = name.lower()
      if lowered in _REQUEST_DROP:
        continue
      if decode_body and lowered == "accept-encoding":
        continue
      request.add_header(name, value)   # Authorization included, verbatim and unlogged
    if decode_body:
      request.add_header("Accept-Encoding", "identity")
    if body:
      request.add_header("Content-Length", str(len(body)))

    opener = urllib.request.build_opener(_NoRedirects)
    try:
      return opener.open(request, timeout=self._config.timeout)  # noqa: S310
    except urllib.error.HTTPError as exc:
      return exc

  # -- writing back ---------------------------------------------------------

  def _send_relayed_headers(self, status: int, headers: Message) -> None:
    """Open the response with the upstream status and its non-hop-by-hop headers.

    ⚑ ``send_response_only``, not ``send_response``: a proxy relays the origin's
    ``Date`` and does not stamp its own ``Server`` over it.

    Every response closes its connection.  Upstream framing (``Content-Length``
    or chunked) does not survive the hop uniformly, and a de-streamed SSE body
    has no length at all; ``Connection: close`` makes read-until-close correct
    for all three instead of leaving one of them to a framing bug.
    """
    self.send_response_only(status)
    for name, value in headers.items():
      if name.lower() in _HOP_BY_HOP:
        continue
      self.send_header(name, value)
    self._end_headers_and_close()

  def _send_stream_headers(self) -> None:
    """Open a synthesized SSE response."""
    self.send_response_only(200)
    self.send_header("Date", self.date_time_string())
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self._end_headers_and_close()

  def _relay_whole(self, status: int, headers: Message, raw: bytes) -> None:
    """Relay a fully-read upstream response — status, headers and body intact."""
    self._send_relayed_headers(status, headers)
    self.wfile.write(raw)

  def _relay_gateway_failure(self, exc: Exception) -> None:
    """Answer 502 in the Anthropic error shape when the upstream call itself failed.

    ⚑ The message names the exception's TYPE and nothing else.  Transport
    exceptions stringify with the URL they were attempting, and a proxy must not
    be the thing that copies a credential-bearing URL into a response body.

    Once the response has begun there is no status left to send, so a mid-relay
    failure only closes the connection — a truncated stream the client can
    detect, rather than a second set of headers it cannot parse.
    """
    logger.debug("proxy: upstream request failed (%s)", type(exc).__name__)
    if self._headers_sent:
      self.close_connection = True
      return
    payload = json.dumps({
      "type": "error",
      "error": {
        "type": "api_error",
        "message": f"kanibako proxy: upstream request failed ({type(exc).__name__})",
      },
    }).encode("utf-8")
    self.send_response_only(502)
    self.send_header("Date", self.date_time_string())
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(payload)))
    self._end_headers_and_close()
    self.wfile.write(payload)

  def _end_headers_and_close(self) -> None:
    """Finish the header block, mark the connection closed, and latch that we replied."""
    self.send_header("Connection", "close")
    self.close_connection = True
    self.end_headers()
    self._headers_sent = True

  # -- reading in -----------------------------------------------------------

  def _read_body(self) -> bytes:
    """Read the client's request body, or ``b""`` when it declared none."""
    declared = self.headers.get("Content-Length")
    if not declared:
      return b""
    try:
      length = int(declared)
    except ValueError:
      return b""
    return self.rfile.read(length) if length > 0 else b""


class ProxyServer:
  """A local listener on the Anthropic surface, forwarding to ``config.upstream_base``.

  Binds on construction so :attr:`url` is answerable before anything serves —
  which is what makes port 0 usable, in a test and in a future launch seam
  alike.  :meth:`start` serves in a daemon thread; :meth:`serve_forever` serves
  in the caller's.

  ⚑ INERT: nothing in kanibako constructs one.  Wiring is a separate decision.
  """

  def __init__(self, config: ProxyConfig, host: str = "127.0.0.1", port: int = 0) -> None:
    self._httpd = ThreadingHTTPServer(
      (host, port), partial(_AnthropicProxyHandler, config=config),
    )
    self._thread: threading.Thread | None = None

  @property
  def host(self) -> str:
    return str(self._httpd.server_address[0])

  @property
  def port(self) -> int:
    return int(self._httpd.server_address[1])

  @property
  def url(self) -> str:
    """The base URL a client should be pointed at."""
    return f"http://{self.host}:{self.port}"

  def start(self) -> None:
    """Serve in a background daemon thread. Idempotent."""
    if self._thread is not None:
      return
    self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
    self._thread.start()

  def stop(self) -> None:
    """Stop serving and release the socket. Idempotent, and safe before :meth:`start`.

    ⚑ ``shutdown()`` is only reachable through :meth:`start`.  It waits on an
    event that ``serve_forever`` sets on its way out, so calling it on a server
    that never served blocks forever.
    """
    if self._thread is not None:
      self._httpd.shutdown()
      self._thread.join(timeout=5)
      self._thread = None
    self._httpd.server_close()

  def serve_forever(self) -> None:
    """Serve in the calling thread until interrupted."""
    self._httpd.serve_forever()

  def __enter__(self) -> ProxyServer:
    self.start()
    return self

  def __exit__(self, *_exc: object) -> None:
    self.stop()
