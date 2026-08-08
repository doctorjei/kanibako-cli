"""Tests for kanibako.channels.helper_client."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from kanibako.channels.helper_client import HelperConnection, send_request


@pytest.fixture
def echo_server(tmp_path):
    """Start a simple echo server that returns {"status": "ok"} for any request."""
    sock_path = tmp_path / "test.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(4)
    server.settimeout(5.0)
    shutdown = threading.Event()

    def serve():
        while not shutdown.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            t = threading.Thread(target=_handle, args=(conn,), daemon=True)
            t.start()

    def _handle(conn):
        buf = b""
        try:
            while not shutdown.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line)
                    resp = {"status": "ok"}
                    if req.get("action") == "spawn":
                        resp["container_name"] = "kanibako-helper-1-abc"
                    conn.sendall(json.dumps(resp).encode() + b"\n")
        except Exception:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield sock_path

    shutdown.set()
    server.close()
    thread.join(timeout=5.0)


class TestHelperConnection:
    def test_connect_and_register(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server, helper_num=1)
        conn.close()

    def test_spawn(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server)
        resp = conn.spawn(1, model="sonnet")
        assert resp["status"] == "ok"
        assert resp["container_name"] == "kanibako-helper-1-abc"
        conn.close()

    def test_stop(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server)
        resp = conn.stop("kanibako-helper-1-abc", 1)
        assert resp["status"] == "ok"
        conn.close()

    def test_send_message(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server)
        resp = conn.send(1, {"text": "hello"})
        assert resp["status"] == "ok"
        conn.close()

    def test_broadcast(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server)
        resp = conn.broadcast({"text": "all hands"})
        assert resp["status"] == "ok"
        conn.close()

    def test_recv_timeout(self, echo_server):
        conn = HelperConnection()
        conn.connect(echo_server)
        msg = conn.recv(timeout=0.1)
        assert msg is None
        conn.close()

    def test_not_connected_raises(self):
        conn = HelperConnection()
        with pytest.raises(ConnectionError):
            conn.send(1, {"text": "hi"})


class TestSendRequest:
    def test_one_shot_spawn(self, echo_server):
        resp = send_request(echo_server, {
            "action": "spawn", "helper_num": 1,
        })
        assert resp["status"] == "ok"
        assert resp["container_name"] == "kanibako-helper-1-abc"

    def test_one_shot_stop(self, echo_server):
        resp = send_request(echo_server, {
            "action": "stop", "container_name": "foo", "helper_num": 1,
        })
        assert resp["status"] == "ok"

    def test_connection_error(self, tmp_path):
        bad_path = tmp_path / "nonexistent.sock"
        with pytest.raises(Exception):
            send_request(bad_path, {"action": "ping"})


# ---------------------------------------------------------------------------
# FULL-DUPLEX SAFETY: a request and a concurrent recv() share ONE socket.
# ---------------------------------------------------------------------------
#
# The hub multiplexes TWO frame kinds onto a helper's single connection:
#
#   * RESPONSES to that helper's own requests — always carry ``status``
#     (``helper_listener.HelperHub._dispatch`` and the handlers it calls), and
#   * PUSHES routed from a PEER — always carry ``event`` and never ``status``
#     (``_route_message`` / ``_broadcast_message``).
#
# ⚑ A push is written by the SENDING helper's reader thread, so it can land at
# ANY instant — including between our request arriving at the hub and the hub
# writing our response.  The client therefore may not assume "the next frame on
# the wire is my response"; it must ROUTE each frame by shape.  The servers
# below reproduce those wire orders deterministically.


def _serve_one(sock_path, handler):
    """Accept connections on *sock_path* and run *handler(conn)* per connection.

    Returns (shutdown_event, server_socket, thread) for the caller to retire.
    """
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(4)
    server.settimeout(0.25)
    shutdown = threading.Event()

    def serve():
        while not shutdown.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return shutdown, server, thread


@pytest.fixture
def interleaving_server(tmp_path):
    """A hub that emits an unsolicited PUSH immediately BEFORE each response."""
    sock_path = tmp_path / "interleave.sock"

    def handler(conn):
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    json.loads(line)
                    push = {"event": "message", "from": 2, "payload": {"text": "peer"}}
                    conn.sendall(json.dumps(push).encode() + b"\n")
                    conn.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
        except Exception:
            pass
        finally:
            conn.close()

    shutdown, server, thread = _serve_one(sock_path, handler)
    yield sock_path
    shutdown.set()
    server.close()
    thread.join(timeout=5.0)


@pytest.fixture
def slow_server(tmp_path):
    """A hub that waits ``SLOW_DELAY`` seconds before answering each request."""
    sock_path = tmp_path / "slow.sock"

    def handler(conn):
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    json.loads(line)
                    time.sleep(SLOW_DELAY)
                    conn.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
        except Exception:
            pass
        finally:
            conn.close()

    shutdown, server, thread = _serve_one(sock_path, handler)
    yield sock_path
    shutdown.set()
    server.close()
    thread.join(timeout=5.0)


@pytest.fixture
def junk_server(tmp_path):
    """A hub that answers with a frame that is NEITHER a response NOR a push."""
    sock_path = tmp_path / "junk.sock"

    def handler(conn):
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    json.loads(line)
                    conn.sendall(json.dumps({"nonsense": 1}).encode() + b"\n")
        except Exception:
            pass
        finally:
            conn.close()

    shutdown, server, thread = _serve_one(sock_path, handler)
    yield sock_path
    shutdown.set()
    server.close()
    thread.join(timeout=5.0)


@pytest.fixture
def hangup_server(tmp_path):
    """A hub that closes the connection the moment a client connects."""
    sock_path = tmp_path / "hangup.sock"

    def handler(conn):
        conn.close()

    shutdown, server, thread = _serve_one(sock_path, handler)
    yield sock_path
    shutdown.set()
    server.close()
    thread.join(timeout=5.0)


#: The slow hub's answer delay, and the recv() window opened against it.  The
#: WINDOW MUST BE COMFORTABLY SHORTER THAN THE DELAY: the defect being pinned is
#: that recv()'s socket timeout leaks onto a concurrent request, so the request
#: must be in flight while the window is open and must outlive it.
SLOW_DELAY = 1.5
SLOW_RECV_WINDOW = 0.5


class TestFullDuplexSafety:
    def test_request_is_not_handed_an_interleaved_peer_push(self, interleaving_server):
        """A push that precedes the response on the wire must not BE the response.

        Both frames drain from one stream; a client that returns "the next line"
        answers the caller with a PEER'S MESSAGE — silently, since both are dicts.
        """
        conn = HelperConnection()
        conn.connect(interleaving_server)
        try:
            resp = conn.send(2, {"text": "hi"})
            assert resp == {"status": "ok"}
            assert conn.recv(timeout=5.0) == {
                "event": "message", "from": 2, "payload": {"text": "peer"},
            }
        finally:
            conn.close()

    def test_recv_does_not_clobber_an_in_flight_request(self, slow_server):
        """recv()'s timeout window must not shorten a concurrent request's read.

        A short recv() window opens first; the request is issued inside it and
        must still be answered after the window has expired.
        """
        conn = HelperConnection()
        conn.connect(slow_server)
        result: dict = {}

        def opener():
            result["recv"] = conn.recv(timeout=SLOW_RECV_WINDOW)

        def requester():
            try:
                result["resp"] = conn.send(1, {"text": "hi"})
            except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
                result["error"] = exc

        try:
            t_recv = threading.Thread(target=opener, daemon=True)
            t_recv.start()
            time.sleep(SLOW_RECV_WINDOW / 10)
            t_req = threading.Thread(target=requester, daemon=True)
            t_req.start()
            t_req.join(timeout=SLOW_DELAY * 6)
            t_recv.join(timeout=5.0)
            assert not t_req.is_alive(), "the request never completed"
            assert "error" not in result, f"request raised {result['error']!r}"
            assert result["resp"] == {"status": "ok"}
            assert result["recv"] is None
        finally:
            conn.close()

    def test_unroutable_frame_is_refused_by_name(self, junk_server):
        """A frame that is neither shape is REFUSED, not returned as a response."""
        conn = HelperConnection()
        conn.connect(junk_server)
        try:
            with pytest.raises(ConnectionError, match="nonsense"):
                conn.send(1, {"text": "hi"})
        finally:
            conn.close()

    def test_blocking_recv_wakes_on_peer_hangup(self, hangup_server):
        """recv() with NO timeout must return None when the hub goes away."""
        conn = HelperConnection()
        conn.connect(hangup_server)
        result: dict = {}

        def blocker():
            result["recv"] = conn.recv()

        try:
            t = threading.Thread(target=blocker, daemon=True)
            t.start()
            t.join(timeout=10.0)
            assert not t.is_alive(), "recv() did not wake on hangup"
            assert result["recv"] is None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# GUARD: helper_client's import chain must stay free of intra-kanibako imports.
# ---------------------------------------------------------------------------

def test_helper_client_has_zero_intra_kanibako_imports():
    """``helper_client`` and the __init__s run on importing it import NO kanibako.

    WHY THIS GUARD EXISTS — do NOT weaken it without understanding the
    consequence: ``helper_client`` is the CONTAINER-side half of the helper
    subsystem, reached from inside a box where the rest of the kanibako package
    (and the host venv it depends on) need not be importable at all.  Its
    host-side sibling ``helper_listener`` legitimately pulls in the container
    runtime, ``targets.base`` and the settings foundation — the two halves are
    asymmetric BY DESIGN, and the asymmetry is the whole reason
    ``channels/__init__.py`` is deliberately import-free.  An added import here
    (or a facade added to either __init__, which is executed implicitly when
    this module is imported) breaks NOTHING locally and surfaces only as an
    ImportError inside a real box.  This walk fails LOUDLY at that moment.

    Scope, deliberately: only INTRA-KANIBAKO reach is asserted.  The chain's
    third-party freedom is NOT checked here — that is a separate contract, and
    the one module that carries it (box PID-1) has its own guard in
    ``tests/test_box_supervisor.py``.

    The walk is flat rather than recursive, and that is COMPLETE rather than
    weaker: the allowlist is "no kanibako module at all", so the first offending
    import fails the test — there is never a second hop to follow.
    """
    import ast
    import importlib.util
    from pathlib import Path

    target = "kanibako.channels.helper_client"
    # Importing the target implicitly executes its ancestor package __init__s,
    # so their imports are part of the chain and are walked too.
    chain = ["kanibako", "kanibako.channels", target]

    for mod in chain:
        spec = importlib.util.find_spec(mod)
        assert spec is not None and spec.origin, f"cannot locate {mod!r}"
        tree = ast.parse(Path(spec.origin).read_text(), filename=spec.origin)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (
                    f"relative import in {mod!r} (level={node.level}); in-package "
                    "imports are absolute here, and a relative one hides the reach "
                    "this guard walks for"
                )
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert name.split(".")[0] != "kanibako", (
                    f"{mod!r} imports {name!r}: the container-side helper client's "
                    "import chain must reach NOTHING else in kanibako — see this "
                    "test's docstring"
                )
