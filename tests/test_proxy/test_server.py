"""Tests for kanibako.proxy.server: mode selection and relaying, against a real socket.

The upstream here is a real loopback ``http.server``, not a patched ``urlopen``.
The properties under test are all wire properties -- a status that survives, a
body that comes back byte-identical, a header that reaches the far side -- and a
mock of the transport cannot fail in the ways the transport can.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from kanibako.proxy.server import MESSAGES_PATH, ProxyConfig, ProxyServer, fixup_request_body

_MODEL = "google/gemma-4-31b-it"
_FIXUP = frozenset({_MODEL})
_TOKEN = "Bearer test-token-never-logged"

_UPSTREAM_SSE = (
  b'event: message_start\ndata: {"type":"message_start"}\n\n'
  b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)

_COMPLETE_RESPONSE = {
  "id": "msg_test",
  "type": "message",
  "role": "assistant",
  "model": _MODEL,
  "content": [{"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"command": "ls"}}],
  "stop_reason": "tool_use",
  "stop_sequence": None,
  "usage": {"input_tokens": 21146, "output_tokens": 29},
}


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
  """Record what arrived, then answer with whatever ``server.reply`` currently holds."""

  protocol_version = "HTTP/1.1"

  def _serve(self):
    declared = int(self.headers.get("Content-Length") or 0)
    self.server.received.append(SimpleNamespace(
      method=self.command,
      path=self.path,
      headers=dict(self.headers.items()),
      body=self.rfile.read(declared) if declared else b"",
    ))
    if self.server.trickle is not None:
      self._trickle()
      return
    status, headers, payload = self.server.reply
    self.send_response(status)
    for name, value in headers.items():
      self.send_header(name, value)
    self.send_header("Content-Length", str(len(payload)))
    self.send_header("Connection", "close")
    self.end_headers()
    self.wfile.write(payload)
    self.close_connection = True

  def _trickle(self):
    """Emit an unbounded event stream one frame at a time, gated on the test.

    No ``Content-Length``: the client reads until close, which is how a real SSE
    response arrives and the only framing under which "did the first event get
    through before the second was written?" is even a question.
    """
    first, second, gate = self.server.trickle
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Connection", "close")
    self.end_headers()
    self.wfile.write(first)
    self.wfile.flush()
    gate.wait(timeout=5)
    self.wfile.write(second)
    self.wfile.flush()
    self.close_connection = True

  do_GET = do_POST = do_DELETE = _serve

  def log_message(self, format, *args):
    """Silence: the fake upstream's access log is noise in a test run."""


@pytest.fixture
def upstream():
  """A loopback gateway whose reply is set per test and whose requests are recorded."""
  httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstreamHandler)
  httpd.received = []
  httpd.reply = (200, {"Content-Type": "application/json"}, b"{}")
  httpd.trickle = None
  thread = threading.Thread(target=httpd.serve_forever, daemon=True)
  thread.start()
  httpd.base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
  yield httpd
  httpd.shutdown()
  thread.join(timeout=5)
  httpd.server_close()


@pytest.fixture
def proxy_for(upstream):
  """Build a started proxy in front of the fake upstream; stopped on teardown."""
  started = []

  def make(fixup_models=frozenset(), upstream_base=None):
    server = ProxyServer(ProxyConfig(
      upstream_base=upstream_base if upstream_base is not None else upstream.base_url,
      fixup_models=fixup_models,
      timeout=10.0,
    ))
    server.start()
    started.append(server)
    return server

  yield make
  for server in started:
    server.stop()


def _post(server, body, path=MESSAGES_PATH, headers=None):
  """POST *body* to the proxy and read the whole answer back."""
  conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
  try:
    conn.request("POST", path, body=body, headers={
      "Content-Type": "application/json",
      "Authorization": _TOKEN,
      **(headers or {}),
    })
    response = conn.getresponse()
    return SimpleNamespace(
      status=response.status,
      headers={name.lower(): value for name, value in response.getheaders()},
      body=response.read(),
    )
  finally:
    conn.close()


def _request_body(model=_MODEL, stream=True):
  return json.dumps({"model": model, "stream": stream, "messages": []}).encode()


class TestFixupRequestBody:
  def test_streaming_call_for_a_fixup_model_is_rewritten(self):
    rewritten = fixup_request_body(MESSAGES_PATH, _request_body(), _FIXUP)
    assert rewritten is not None
    assert rewritten["stream"] is False
    assert rewritten["model"] == _MODEL

  def test_empty_fixup_set_is_passthrough_for_everything(self):
    assert fixup_request_body(MESSAGES_PATH, _request_body(), frozenset()) is None

  def test_model_outside_the_set_is_passthrough(self):
    assert fixup_request_body(MESSAGES_PATH, _request_body(model="other"), _FIXUP) is None

  def test_non_streaming_call_is_passthrough(self):
    assert fixup_request_body(MESSAGES_PATH, _request_body(stream=False), _FIXUP) is None

  def test_other_paths_are_passthrough(self):
    assert fixup_request_body("/v1/models", _request_body(), _FIXUP) is None

  def test_query_string_and_trailing_slash_still_match(self):
    assert fixup_request_body("/v1/messages/?beta=true", _request_body(), _FIXUP) is not None

  def test_unreadable_bodies_are_passthrough(self):
    assert fixup_request_body(MESSAGES_PATH, b"not json", _FIXUP) is None
    assert fixup_request_body(MESSAGES_PATH, b"[1, 2]", _FIXUP) is None
    assert fixup_request_body(MESSAGES_PATH, b"", _FIXUP) is None


class TestPassthrough:
  def test_streaming_is_untouched_for_a_model_not_in_the_fixup_set(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "text/event-stream"}, _UPSTREAM_SSE)
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, _request_body(model="claude-sonnet-4"))

    assert response.status == 200
    assert response.body == _UPSTREAM_SSE
    assert response.headers["content-type"] == "text/event-stream"
    assert json.loads(upstream.received[0].body)["stream"] is True

  def test_events_reach_the_client_before_the_stream_ends(self, upstream, proxy_for):
    # The load-bearing half of "streaming untouched": not just the same bytes,
    # but the same TIMING. A relay that read a fixed block size would hold the
    # first event hostage until the last one arrived.
    first = b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n'
    second = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    gate = threading.Event()
    upstream.trickle = (first, second, gate)
    server = proxy_for(fixup_models=_FIXUP)

    conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
    try:
      conn.request("POST", MESSAGES_PATH, body=_request_body(model="claude-sonnet-4"),
                   headers={"Content-Type": "application/json"})
      response = conn.getresponse()
      assert response.status == 200

      early = response.read1(4096)
      assert early and first.startswith(early), "first event did not arrive on its own"
      gate.set()
      assert early + response.read() == first + second
    finally:
      gate.set()
      conn.close()

  def test_fixup_is_off_by_default(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "text/event-stream"}, _UPSTREAM_SSE)
    server = proxy_for()

    assert _post(server, _request_body()).body == _UPSTREAM_SSE
    assert json.loads(upstream.received[0].body)["stream"] is True

  def test_other_paths_are_forwarded_as_sent(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "application/json"}, b'{"data":[]}')
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, b"", path="/v1/models")

    assert response.body == b'{"data":[]}'
    assert upstream.received[0].path == "/v1/models"

  def test_non_200_passes_through_with_status_and_body_intact(self, upstream, proxy_for):
    # A 429 body is not SSE and parses as "no tool block" -- the fault's own
    # signature. The status is what tells the two apart, so it must survive.
    rate_limited = b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'
    upstream.reply = (429, {"Content-Type": "application/json", "Retry-After": "31"}, rate_limited)
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, _request_body(model="claude-sonnet-4"))

    assert response.status == 429
    assert response.body == rate_limited
    assert response.headers["retry-after"] == "31"


class TestFixup:
  def test_upstream_is_asked_without_streaming(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "application/json"},
                      json.dumps(_COMPLETE_RESPONSE).encode())
    server = proxy_for(fixup_models=_FIXUP)

    _post(server, _request_body())

    sent = json.loads(upstream.received[0].body)
    assert sent["stream"] is False
    assert sent["model"] == _MODEL
    assert sent["messages"] == []

  def test_client_receives_a_synthesized_sse_stream(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "application/json"},
                      json.dumps(_COMPLETE_RESPONSE).encode())
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, _request_body())

    assert response.status == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    events = _parse_sse(response.body)
    assert [event["type"] for event in events] == [
      "message_start",
      "content_block_start",
      "content_block_delta",
      "content_block_stop",
      "message_delta",
      "message_stop",
    ]
    assert events[0]["message"]["content"] == []
    assert events[0]["message"]["usage"] == {"input_tokens": 21146, "output_tokens": 0}
    assert events[1]["content_block"] == {
      "type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {},
    }
    assert json.loads(events[2]["delta"]["partial_json"]) == {"command": "ls"}
    assert events[4]["delta"]["stop_reason"] == "tool_use"
    assert events[4]["usage"] == {"input_tokens": 21146, "output_tokens": 29}

  def test_non_200_on_the_fixup_path_is_relayed_not_synthesized(self, upstream, proxy_for):
    overloaded = b'{"type":"error","error":{"type":"overloaded_error","message":"try later"}}'
    upstream.reply = (529, {"Content-Type": "application/json"}, overloaded)
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, _request_body())

    assert response.status == 529
    assert response.body == overloaded
    assert b"event:" not in response.body

  def test_a_200_that_is_not_json_is_relayed_verbatim(self, upstream, proxy_for):
    upstream.reply = (200, {"Content-Type": "text/plain"}, b"gateway said something else")
    server = proxy_for(fixup_models=_FIXUP)

    response = _post(server, _request_body())

    assert response.status == 200
    assert response.body == b"gateway said something else"


class TestCredentials:
  def test_authorization_reaches_upstream_verbatim_and_is_never_logged(
    self, upstream, proxy_for, caplog,
  ):
    upstream.reply = (200, {"Content-Type": "application/json"},
                      json.dumps(_COMPLETE_RESPONSE).encode())
    server = proxy_for(fixup_models=_FIXUP)

    with caplog.at_level(logging.DEBUG, logger="kanibako"):
      _post(server, _request_body())

    assert upstream.received[0].headers["Authorization"] == _TOKEN
    assert caplog.records, "expected the proxy to log at least something at DEBUG"
    for record in caplog.records:
      assert "test-token-never-logged" not in record.getMessage()
      assert "Authorization" not in record.getMessage()


class TestGatewayFailure:
  def test_unreachable_upstream_answers_502_in_the_anthropic_error_shape(self, proxy_for):
    server = proxy_for(fixup_models=_FIXUP, upstream_base="http://127.0.0.1:1")

    response = _post(server, _request_body())

    assert response.status == 502
    body = json.loads(response.body)
    assert body["type"] == "error"
    assert body["error"]["type"] == "api_error"
    assert "upstream request failed" in body["error"]["message"]


class TestLifecycle:
  def test_stop_before_start_releases_the_socket(self):
    server = ProxyServer(ProxyConfig(upstream_base="http://127.0.0.1:1"))
    assert server.port > 0
    assert server.url == f"http://127.0.0.1:{server.port}"
    server.stop()

  def test_start_is_idempotent(self, proxy_for):
    server = proxy_for()
    server.start()
    server.start()


def _parse_sse(payload: bytes) -> list[dict]:
  """Split an SSE body into its data objects, checking each frame's own shape."""
  events = []
  for frame in payload.decode().split("\n\n"):
    if not frame:
      continue
    name, data = frame.split("\n")
    event = json.loads(data.removeprefix("data: "))
    assert name == f"event: {event['type']}"
    events.append(event)
  return events
