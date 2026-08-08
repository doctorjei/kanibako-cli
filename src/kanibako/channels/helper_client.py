"""Container-side helper client: socket communication with the host hub."""

from __future__ import annotations

import json
import queue
import socket
import threading
from pathlib import Path
from typing import Any

#: Posted to BOTH queues when the reader thread retires, so a caller blocked on
#: either wakes instead of waiting for a frame that can no longer arrive.  ⚑ A
#: taker RE-POSTS it: it must still be there for every later and repeated call.
_CLOSED = object()

#: Bound on ``close()``: how long to wait for the reader to notice the shutdown.
#: It is a daemon thread, so an expired wait costs tidiness, never a hang.
_READER_JOIN = 5.0


def _route_frame(line: bytes, responses: queue.Queue[Any],
                 inbox: queue.Queue[Any]) -> str | None:
    """Put ONE wire frame on the queue its shape names; error text if it has none.

    The two shapes are the HUB's, not this module's invention: every response
    ``HelperHub._dispatch`` returns carries a ``status`` member, and every push
    ``_route_message``/``_broadcast_message`` writes carries an ``event`` member
    and no ``status``.  A frame with neither is UNDECLARED, and an undeclared
    variant is refused by name rather than guessed at — guessing is precisely how
    a peer's message ends up handed to a caller as that caller's response.
    """
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        return f"hub sent a frame that is not JSON: {line!r}"
    if not isinstance(frame, dict):
        return f"hub sent a frame that is not an object: {frame!r}"
    if "event" in frame:
        inbox.put(frame)
    elif "status" in frame:
        responses.put(frame)
    else:
        return ("hub sent a frame that is neither a response ('status') nor a "
                f"push ('event'): {frame!r}")
    return None


class HelperConnection:
    """Persistent connection to the HelperHub for messaging.

    FULL DUPLEX, and that is the whole design constraint.  The hub multiplexes
    two frame kinds onto the one socket — responses to this client's requests,
    and pushes routed from a peer (see ``_route_frame``).  A push is written by
    the SENDING helper's reader thread on the host, so it can land at any
    instant, including between a request reaching the hub and the hub writing
    that request's response.  "The next line on the wire is my response" is
    therefore false, and reading it as true silently answers a caller with
    someone else's message.

    So the socket has exactly ONE owner: a reader thread started by
    ``connect()``.  It alone reads the socket and holds the frame buffer, and it
    routes each frame by shape to ``recv()``'s inbox or to a waiting request.
    Callers wait on a QUEUE, never on the socket, and there is no shared buffer
    left to guard.  ``_lock`` consequently guards one thing only: at most one
    request outstanding at a time, which is what makes a single response queue
    unambiguous.

    ⚑ ``recv()`` takes NO lock and touches no socket state.  That is deliberate
    and load-bearing: a blocking ``recv()`` that held the lock (or that retimed
    the shared socket) would stall or break a concurrent request — the very
    concurrency this class advertises.

    For one-shot commands (spawn/stop), use ``send_request()`` instead.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._responses: queue.Queue[Any] = queue.Queue()
        self._inbox: queue.Queue[Any] = queue.Queue()
        self._closed_reason = "Connection closed"

    def connect(self, socket_path: Path, helper_num: int | None = None) -> None:
        """Connect to the hub socket, optionally registering as a helper.

        The reader starts BEFORE registering: the registration response arrives
        through it like every other frame.  ⚑ The socket is left BLOCKING — a
        timeout would apply to ``sendall`` as well, and ``close()`` retires the
        reader with ``shutdown()`` instead of a poll.
        """
        if self._sock is not None:
            raise ConnectionError("Already connected; close() first")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(socket_path))
        self._closing.clear()
        self._closed_reason = "Connection closed"
        self._sock = sock
        self._reader = threading.Thread(
            target=self._read_loop, args=(sock, self._responses, self._inbox),
            name="kanibako-helper-client-reader", daemon=True,
        )
        self._reader.start()
        if helper_num is not None:
            resp = self._request({"action": "register", "helper_num": helper_num})
            if resp.get("status") != "ok":
                raise ConnectionError(f"Registration failed: {resp.get('message', 'unknown')}")

    def spawn(self, helper_num: int, model: str | None = None,
              helpers_dir: str | None = None) -> dict:
        """Request the hub to spawn a helper container."""
        req: dict = {"action": "spawn", "helper_num": helper_num}
        if model:
            req["model"] = model
        if helpers_dir:
            req["helpers_dir"] = helpers_dir
        return self._request(req)

    def stop(self, container_name: str, helper_num: int) -> dict:
        """Request the hub to stop a helper container."""
        return self._request({
            "action": "stop", "container_name": container_name, "helper_num": helper_num,
        })

    def send(self, to: int, payload: dict) -> dict:
        """Send a message to a specific peer or parent."""
        return self._request({"action": "send", "to": to, "payload": payload})

    def broadcast(self, payload: dict) -> dict:
        """Broadcast a message to all connected helpers."""
        return self._request({"action": "broadcast", "payload": payload})

    def recv(self, timeout: float | None = None) -> dict | None:
        """Receive an incoming message (blocking); None on timeout/disconnect.

        Waits on the inbox the reader fills — no lock, no socket.  Blocking here
        for as long as the caller likes cannot delay a concurrent request.
        """
        if self._sock is None:
            return None
        try:
            frame = self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if frame is _CLOSED:
            self._inbox.put(_CLOSED)
            return None
        return frame

    def close(self) -> None:
        """Close the connection; idempotent and never raises.

        ⚑ ``shutdown()`` before ``close()`` is load-bearing: closing a Python
        socket that another thread is blocked in does NOT wake that thread — the
        blocked call holds its own reference, so the fd outlives the close —
        whereas a shutdown delivers EOF to it.
        """
        self._closing.set()
        sock, self._sock = self._sock, None
        reader, self._reader = self._reader, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=_READER_JOIN)
        # A later connect() starts from empty queues.  A reader that outlived
        # the join still holds the OLD ones, so anyone blocked on them is still
        # woken, and its sentinels cannot leak into the next connection.
        self._responses = queue.Queue()
        self._inbox = queue.Queue()

    def _read_loop(self, sock: socket.socket, responses: queue.Queue[Any],
                   inbox: queue.Queue[Any]) -> None:
        """Own the socket's read side: split frames on newlines, route each.

        The frame buffer is a LOCAL — there is no shared buffer to guard, which
        is the point.  The queues are PARAMETERS for the same reason (see
        ``close()``).  This thread acquires no lock at all, so nothing a caller
        does can keep it from delivering.

        The socket carries no timeout, so an ``OSError`` here is a real failure
        or the shutdown from ``close()`` — either way the connection is over.
        """
        buf = b""
        reason = "Connection closed"
        try:
            while not self._closing.is_set():
                try:
                    data = sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    problem = _route_frame(line, responses, inbox)
                    if problem is not None:
                        # The stream is no longer understood; reading on would
                        # risk handing a caller a frame that is not theirs.
                        reason = problem
                        return
        finally:
            self._closed_reason = reason
            responses.put(_CLOSED)
            inbox.put(_CLOSED)

    def _request(self, data: dict) -> dict:
        """Send a request and wait for ITS response.

        The lock spans send-and-await so at most one request is outstanding —
        that, not mutual exclusion over a buffer, is what makes one response
        queue unambiguous.  It cannot deadlock: ``recv()`` never takes this lock,
        and neither does the reader that fills the queue, so the thread holding
        it is always waiting on something a lock-free thread will post.
        """
        sock = self._sock
        if sock is None:
            raise ConnectionError("Not connected")
        responses = self._responses
        with self._lock:
            sock.sendall(json.dumps(data).encode() + b"\n")
            frame = responses.get()
        if frame is _CLOSED:
            responses.put(_CLOSED)
            raise ConnectionError(self._closed_reason)
        return frame


def send_request(socket_path: Path, request: dict) -> dict:
    """One-shot convenience for spawn/stop commands that need no persistent connection.

    No demultiplexing needed: this connection never registers, and the hub routes
    pushes only to REGISTERED helpers, so the sole frame it can receive is the
    response to the single request it sent.
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
