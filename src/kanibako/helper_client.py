"""Container-side helper client: socket communication with the host hub."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path


class HelperConnection:
    """Persistent connection to the HelperHub for messaging.

    Use for helpers that need to send/receive messages over time.
    For one-shot commands (spawn/stop), use ``send_request()`` instead.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._recv_buf = b""
        self._lock = threading.Lock()

    def connect(self, socket_path: Path, helper_num: int | None = None) -> None:
        """Connect to the hub socket, optionally registering as a helper."""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(socket_path))
        if helper_num is not None:
            resp = self._request({"action": "register", "helper_num": helper_num})
            if resp.get("status") != "ok":
                raise ConnectionError(
                    f"Registration failed: {resp.get('message', 'unknown')}"
                )

    def spawn(self, helper_num: int, model: str | None = None,
              helpers_dir: str | None = None) -> dict:
        """Request the hub to spawn a helper container."""
        req: dict = {"action": "spawn", "helper_num": helper_num}
        if model:
            req["model"] = model
        if helpers_dir:
            req["helpers_dir"] = helpers_dir
        return self._request(req)

    def stop(self, container_name: str) -> dict:
        """Request the hub to stop a helper container."""
        return self._request({
            "action": "stop",
            "container_name": container_name,
        })

    def send(self, to: int, payload: dict) -> dict:
        """Send a message to a specific peer or parent."""
        return self._request({
            "action": "send", "to": to, "payload": payload,
        })

    def broadcast(self, payload: dict) -> dict:
        """Broadcast a message to all connected helpers."""
        return self._request({
            "action": "broadcast", "payload": payload,
        })

    def recv(self, timeout: float | None = None) -> dict | None:
        """Receive an incoming message (blocking).

        Returns the message dict or None on timeout/disconnect.
        """
        if self._sock is None:
            return None
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._recv_buf:
                try:
                    data = self._sock.recv(4096)
                except socket.timeout:
                    return None
                except OSError:
                    return None
                if not data:
                    return None
                self._recv_buf += data
            line, self._recv_buf = self._recv_buf.split(b"\n", 1)
            return json.loads(line)
        finally:
            self._sock.settimeout(old_timeout)

    def close(self) -> None:
        """Close the connection."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _request(self, data: dict) -> dict:
        """Send a request and read the response."""
        if self._sock is None:
            raise ConnectionError("Not connected")
        with self._lock:
            self._sock.sendall(json.dumps(data).encode() + b"\n")
            # Read response from buffer + socket
            while b"\n" not in self._recv_buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed")
                self._recv_buf += chunk
            line, self._recv_buf = self._recv_buf.split(b"\n", 1)
            return json.loads(line)


def send_request(socket_path: Path, request: dict) -> dict:
    """One-shot convenience: connect, send, read response, disconnect.

    For spawn/stop commands that don't need a persistent connection.
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(socket_path))
        s.settimeout(30.0)
        s.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            data = s.recv(4096)
            if not data:
                raise ConnectionError("Connection closed before response")
            buf += data
        line = buf.split(b"\n")[0]
        return json.loads(line)
    finally:
        s.close()
