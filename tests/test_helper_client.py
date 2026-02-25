"""Tests for kanibako.helper_client."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from kanibako.helper_client import HelperConnection, send_request


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
        resp = conn.stop("kanibako-helper-1-abc")
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
            "action": "stop", "container_name": "foo",
        })
        assert resp["status"] == "ok"

    def test_connection_error(self, tmp_path):
        bad_path = tmp_path / "nonexistent.sock"
        with pytest.raises(Exception):
            send_request(bad_path, {"action": "ping"})
