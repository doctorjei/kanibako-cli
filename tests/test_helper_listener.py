"""Tests for kanibako.helper_listener."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kanibako.helper_listener import (
    HelperContext,
    HelperHub,
    MessageLog,
    _build_helper_mounts,
    _parse_helper_num,
    _send_json,
)


@pytest.fixture
def mock_ctx(tmp_path):
    """Create a mock HelperContext with filesystem."""
    helpers_dir = tmp_path / "shell" / "helpers"
    helpers_dir.mkdir(parents=True)

    runtime = MagicMock()
    runtime.run.return_value = 0
    runtime.stop.return_value = True
    runtime.rm.return_value = True

    socket_path = tmp_path / "helper.sock"
    return HelperContext(
        runtime=runtime,
        image="test:latest",
        container_name_prefix="kanibako-helper",
        project_hash="abc12345deadbeef",
        shell_path=tmp_path / "shell",
        helpers_dir=helpers_dir,
        socket_path=socket_path,
        binary_mounts=[],
        env=None,
        entrypoint=None,
    )


@pytest.fixture
def hub_and_sock(tmp_path, mock_ctx):
    """Start a HelperHub and return (hub, socket_path, ctx)."""
    sock_path = tmp_path / "helper.sock"
    hub = HelperHub()
    hub.start(sock_path, mock_ctx)
    yield hub, sock_path, mock_ctx
    hub.stop()


def _connect_and_send(sock_path: Path, request: dict) -> dict:
    """Connect to the hub, send a request, read response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(sock_path))
    s.settimeout(5.0)
    s.sendall(json.dumps(request).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        data = s.recv(4096)
        if not data:
            break
        buf += data
    s.close()
    return json.loads(buf.split(b"\n")[0])


class TestParseHelperNum:
    def test_valid_name(self):
        assert _parse_helper_num("kanibako-helper-3-abc12345") == 3

    def test_valid_name_double_digit(self):
        assert _parse_helper_num("kanibako-helper-42-deadbeef") == 42

    def test_invalid_format(self):
        assert _parse_helper_num("some-other-name") is None

    def test_non_numeric(self):
        assert _parse_helper_num("kanibako-helper-abc-123") is None


class TestSendJson:
    def test_sends_newline_terminated_json(self):
        mock_sock = MagicMock()
        _send_json(mock_sock, {"status": "ok"})
        call_data = mock_sock.sendall.call_args[0][0]
        assert call_data.endswith(b"\n")
        parsed = json.loads(call_data.decode().strip())
        assert parsed == {"status": "ok"}


class TestBuildHelperMounts:
    def test_basic_mounts(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helper_root = helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "peers").mkdir()
        (helper_root / "workspace").mkdir()
        spawn_toml = helper_root / "spawn.toml"
        spawn_toml.write_text("[spawn]\ndepth = 3\n")

        sock = tmp_path / "helper.sock"
        sock.touch()
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_prefix="kanibako-helper",
            project_hash="abc",
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=sock,
            binary_mounts=[],
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        dests = [m.destination for m in mounts]
        assert "/home/agent/peers" in dests
        assert "/home/agent/spawn.toml" in dests

    def test_includes_binary_mounts(self, tmp_path):
        from kanibako.targets.base import Mount
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)

        binary_mount = Mount(Path("/usr/bin/claude"), "/usr/bin/claude", "ro")
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_prefix="kanibako-helper",
            project_hash="abc",
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "helper.sock",
            binary_mounts=[binary_mount],
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert any(m.destination == "/usr/bin/claude" for m in mounts)

    def test_broadcast_mount(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helper_root = helpers_dir / "1"
        helper_root.mkdir(parents=True)
        # Create broadcast dir and link
        all_dir = helpers_dir / "all"
        (all_dir / "rw").mkdir(parents=True)
        (all_dir / "ro").mkdir(parents=True)
        (helper_root / "all").symlink_to(all_dir)

        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_prefix="kanibako-helper",
            project_hash="abc",
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "helper.sock",
            binary_mounts=[],
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert any(m.destination == "/home/agent/all" for m in mounts)

    def test_socket_mount(self, tmp_path):
        """Socket is mounted when it exists on the host."""
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)
        sock = tmp_path / "helper.sock"
        sock.touch()

        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_prefix="kanibako-helper",
            project_hash="abc",
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=sock,
            binary_mounts=[],
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert any(m.destination == "/home/agent/.kanibako/helper.sock" for m in mounts)

    def test_no_socket_mount_when_missing(self, tmp_path):
        """Socket is not mounted when it doesn't exist."""
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)

        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_prefix="kanibako-helper",
            project_hash="abc",
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "nonexistent.sock",
            binary_mounts=[],
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert not any(m.destination == "/home/agent/.kanibako/helper.sock" for m in mounts)


class TestHubSocketProtocol:
    def test_register(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {
            "action": "register", "helper_num": 1,
        })
        assert resp["status"] == "ok"

    def test_unknown_action(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {"action": "bogus"})
        assert resp["status"] == "error"
        assert "unknown" in resp["message"]

    def test_invalid_json(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(sock_path))
        s.settimeout(5.0)
        s.sendall(b"not json\n")
        buf = b""
        while b"\n" not in buf:
            data = s.recv(4096)
            if not data:
                break
            buf += data
        s.close()
        resp = json.loads(buf.split(b"\n")[0])
        assert resp["status"] == "error"
        assert "invalid JSON" in resp["message"]


class TestHubSpawn:
    def test_spawn_success(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        # Create helper dirs on host
        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "share-ro").mkdir(parents=True)
        (helper_root / "vault" / "share-rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
            "helpers_dir": "/home/agent/helpers",
        })
        assert resp["status"] == "ok"
        assert "container_name" in resp
        assert "kanibako-helper-1-" in resp["container_name"]

        # Verify runtime.run was called with detach=True
        ctx.runtime.run.assert_called_once()
        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["detach"] is True
        # Verify helper-init.sh is used as entrypoint with correct cli_args
        assert call_kwargs["entrypoint"] == "/home/agent/playbook/scripts/helper-init.sh"
        assert call_kwargs["cli_args"] == ["1", "claude"]

    def test_spawn_runtime_failure(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        ctx.runtime.run.return_value = 1

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "share-ro").mkdir(parents=True)
        (helper_root / "vault" / "share-rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "error"

    def test_spawn_with_custom_entrypoint(self, hub_and_sock):
        """When ctx.entrypoint is set, it's passed as the agent command."""
        hub, sock_path, ctx = hub_and_sock
        ctx.entrypoint = "/usr/bin/custom-agent"

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "share-ro").mkdir(parents=True)
        (helper_root / "vault" / "share-rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["entrypoint"] == "/home/agent/playbook/scripts/helper-init.sh"
        assert call_kwargs["cli_args"] == ["1", "/usr/bin/custom-agent"]

    def test_spawn_with_model(self, hub_and_sock):
        """Model variant is passed through to cli_args."""
        hub, sock_path, ctx = hub_and_sock

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "share-ro").mkdir(parents=True)
        (helper_root / "vault" / "share-rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
            "model": "sonnet",
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["cli_args"] == ["1", "claude", "--model", "sonnet"]

    def test_spawn_without_model(self, hub_and_sock):
        """No model → no --model flag in cli_args."""
        hub, sock_path, ctx = hub_and_sock

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "share-ro").mkdir(parents=True)
        (helper_root / "vault" / "share-rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["cli_args"] == ["1", "claude"]

    def test_spawn_invalid_helper_num(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": -1,
        })
        assert resp["status"] == "error"


class TestHubStop:
    def test_stop_success(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {
            "action": "stop",
            "container_name": "kanibako-helper-1-abc12345",
        })
        assert resp["status"] == "ok"
        ctx.runtime.stop.assert_called_with("kanibako-helper-1-abc12345")
        ctx.runtime.rm.assert_called_with("kanibako-helper-1-abc12345")

    def test_stop_missing_name(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {"action": "stop"})
        assert resp["status"] == "error"
        assert "missing" in resp["message"]


class TestHubMessaging:
    def test_route_message(self, hub_and_sock):
        """Test that a message sent to a registered helper is delivered."""
        hub, sock_path, ctx = hub_and_sock

        # Connect and register helper 1 (receiver)
        recv_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        recv_sock.connect(str(sock_path))
        recv_sock.settimeout(5.0)
        recv_sock.sendall(json.dumps(
            {"action": "register", "helper_num": 1}
        ).encode() + b"\n")
        # Read register response
        buf = b""
        while b"\n" not in buf:
            buf += recv_sock.recv(4096)
        resp = json.loads(buf.split(b"\n")[0])
        assert resp["status"] == "ok"

        # Connect as helper 0 (sender) and send a message to helper 1
        send_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        send_sock.connect(str(sock_path))
        send_sock.settimeout(5.0)
        send_sock.sendall(json.dumps(
            {"action": "register", "helper_num": 0}
        ).encode() + b"\n")
        # Read register response
        buf2 = b""
        while b"\n" not in buf2:
            buf2 += send_sock.recv(4096)

        # Send message
        send_sock.sendall(json.dumps({
            "action": "send", "to": 1,
            "payload": {"text": "hello helper 1"},
        }).encode() + b"\n")
        # Read send response
        buf3 = b""
        while b"\n" not in buf3:
            buf3 += send_sock.recv(4096)
        send_resp = json.loads(buf3.split(b"\n")[0])
        assert send_resp["status"] == "ok"

        # Read delivered message on receiver
        remaining = buf[buf.index(b"\n") + 1:]  # leftover from register
        recv_buf = remaining
        deadline = time.time() + 5.0
        while b"\n" not in recv_buf and time.time() < deadline:
            try:
                recv_buf += recv_sock.recv(4096)
            except socket.timeout:
                break
        msg = json.loads(recv_buf.split(b"\n")[0])
        assert msg["event"] == "message"
        assert msg["from"] == 0
        assert msg["payload"]["text"] == "hello helper 1"

        recv_sock.close()
        send_sock.close()

    def test_broadcast(self, hub_and_sock):
        """Test broadcast sends to all connected helpers except sender."""
        hub, sock_path, ctx = hub_and_sock

        # Register helper 1
        s1 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s1.connect(str(sock_path))
        s1.settimeout(5.0)
        s1.sendall(json.dumps({"action": "register", "helper_num": 1}).encode() + b"\n")
        buf1 = b""
        while b"\n" not in buf1:
            buf1 += s1.recv(4096)

        # Register helper 2
        s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s2.connect(str(sock_path))
        s2.settimeout(5.0)
        s2.sendall(json.dumps({"action": "register", "helper_num": 2}).encode() + b"\n")
        buf2 = b""
        while b"\n" not in buf2:
            buf2 += s2.recv(4096)

        # Broadcast from helper 0 (unregistered sender, using a third connection)
        sb = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sb.connect(str(sock_path))
        sb.settimeout(5.0)
        sb.sendall(json.dumps(
            {"action": "broadcast", "payload": {"text": "all hands"}},
        ).encode() + b"\n")
        bufb = b""
        while b"\n" not in bufb:
            bufb += sb.recv(4096)

        # Both helpers should receive the broadcast
        remaining1 = buf1[buf1.index(b"\n") + 1:]
        rbuf1 = remaining1
        deadline = time.time() + 5.0
        while b"\n" not in rbuf1 and time.time() < deadline:
            try:
                rbuf1 += s1.recv(4096)
            except socket.timeout:
                break
        msg1 = json.loads(rbuf1.split(b"\n")[0])
        assert msg1["event"] == "message"
        assert msg1["payload"]["text"] == "all hands"

        remaining2 = buf2[buf2.index(b"\n") + 1:]
        rbuf2 = remaining2
        deadline = time.time() + 5.0
        while b"\n" not in rbuf2 and time.time() < deadline:
            try:
                rbuf2 += s2.recv(4096)
            except socket.timeout:
                break
        msg2 = json.loads(rbuf2.split(b"\n")[0])
        assert msg2["event"] == "message"
        assert msg2["payload"]["text"] == "all hands"

        s1.close()
        s2.close()
        sb.close()


class TestHubLifecycle:
    def test_start_and_stop(self, tmp_path, mock_ctx):
        sock_path = tmp_path / "test.sock"
        hub = HelperHub()
        hub.start(sock_path, mock_ctx)
        assert sock_path.exists()
        hub.stop()

    def test_stop_cleans_containers(self, tmp_path, mock_ctx):
        sock_path = tmp_path / "test.sock"
        hub = HelperHub()
        hub.start(sock_path, mock_ctx)
        # Simulate tracked containers
        hub._containers = ["container-1", "container-2"]
        hub.stop()
        assert mock_ctx.runtime.stop.call_count == 2
        assert mock_ctx.runtime.rm.call_count == 2


class TestMessageLog:
    def test_log_message(self, tmp_path):
        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_message(0, 1, {"text": "hello"})
        log.close()

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "message"
        assert entries[0]["from"] == 0
        assert entries[0]["to"] == 1
        assert entries[0]["payload"]["text"] == "hello"
        assert "ts" in entries[0]

    def test_log_control(self, tmp_path):
        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_control("spawn", 1, model="sonnet")
        log.close()

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "control"
        assert entries[0]["event"] == "spawn"
        assert entries[0]["helper"] == 1
        assert entries[0]["model"] == "sonnet"

    def test_log_broadcast(self, tmp_path):
        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_message(0, "all", {"text": "broadcast"})
        log.close()

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert entries[0]["to"] == "all"

    def test_multiple_entries(self, tmp_path):
        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_control("spawn", 1)
        log.log_control("register", 1)
        log.log_message(0, 1, {"text": "hi"})
        log.log_message(1, 0, {"text": "bye"})
        log.log_control("stop", 1)
        log.close()

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(entries) == 5
