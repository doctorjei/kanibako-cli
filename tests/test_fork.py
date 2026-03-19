"""Tests for kanibako fork command."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.helper_listener import HelperContext, HelperHub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fork_ctx(tmp_path):
    """Create a HelperContext with project_path and data_path for fork tests."""
    project_path = tmp_path / "workspace" / "myapp"
    project_path.mkdir(parents=True)
    (project_path / "main.py").write_text("print('hello')\n")
    (project_path / "src").mkdir()
    (project_path / "src" / "lib.py").write_text("# lib\n")

    data_path = tmp_path / "data"
    data_path.mkdir()
    (data_path / "boxes").mkdir()

    # Set up names.toml with the project registered
    names_toml = data_path / "names.toml"
    names_toml.write_text(
        f'[projects]\nmyapp = "{project_path}"\n\n[worksets]\n'
    )

    # Set up metadata dir (boxes/myapp/)
    meta_dir = data_path / "boxes" / "myapp"
    meta_dir.mkdir()
    shell_dir = meta_dir / "shell"
    shell_dir.mkdir()
    (shell_dir / ".bashrc").write_text("# bashrc\n")
    vault_dir = meta_dir / "vault"
    vault_dir.mkdir()
    (vault_dir / "share-ro").mkdir()
    (meta_dir / "project.toml").write_text("[meta]\nmode = \"local\"\n")
    (meta_dir / ".kanibako.lock").write_text("lock\n")
    helpers_dir_meta = meta_dir / "helpers"
    helpers_dir_meta.mkdir()
    (helpers_dir_meta / "state.json").write_text("{}\n")

    helpers_dir = tmp_path / "shell" / "helpers"
    helpers_dir.mkdir(parents=True)

    runtime = MagicMock()
    runtime.run.return_value = 0

    socket_path = tmp_path / "helper.sock"
    return HelperContext(
        runtime=runtime,
        image="test:latest",
        container_name_prefix="kanibako-myapp",
        shell_path=tmp_path / "shell",
        helpers_dir=helpers_dir,
        socket_path=socket_path,
        binary_mounts=[],
        project_path=project_path,
        data_path=data_path,
    )


@pytest.fixture
def fork_hub(tmp_path, fork_ctx):
    """Start a HelperHub with fork-capable context."""
    sock_path = tmp_path / "helper.sock"
    hub = HelperHub()
    hub.start(sock_path, fork_ctx)
    yield hub, sock_path, fork_ctx
    hub.stop()


def _send(sock_path: Path, request: dict) -> dict:
    """Connect to hub, send request, read response."""
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


# ---------------------------------------------------------------------------
# Socket handler tests (via live hub)
# ---------------------------------------------------------------------------

class TestHandleFork:
    def test_fork_creates_sibling_dir(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "feature1"})
        assert resp["status"] == "ok"
        new_path = Path(resp["path"])
        assert new_path == ctx.project_path.parent / "myapp.feature1"
        assert new_path.is_dir()

    def test_fork_copies_workspace_files(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "copy"})
        assert resp["status"] == "ok"
        new_path = Path(resp["path"])
        assert (new_path / "main.py").read_text() == "print('hello')\n"
        assert (new_path / "src" / "lib.py").read_text() == "# lib\n"

    def test_fork_assigns_new_name(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "named"})
        assert resp["status"] == "ok"
        assert "name" in resp
        # The assigned name should be registered in names.toml
        from kanibako.names import read_names
        names = read_names(ctx.data_path)
        assert resp["name"] in names["projects"]

    def test_fork_copies_metadata_excluding_lock_and_helpers(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "meta"})
        assert resp["status"] == "ok"
        new_name = resp["name"]
        new_meta = ctx.data_path / "boxes" / new_name
        assert new_meta.is_dir()
        # project.toml should be copied
        assert (new_meta / "project.toml").is_file()
        # shell should be copied
        assert (new_meta / "shell" / ".bashrc").is_file()
        # vault should be copied
        assert (new_meta / "vault" / "share-ro").is_dir()
        # lock file should NOT be copied
        assert not (new_meta / ".kanibako.lock").exists()
        # helpers dir should NOT be copied
        assert not (new_meta / "helpers").exists()

    def test_fork_rejects_existing_destination(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        # Create the destination beforehand
        dest = ctx.project_path.parent / "myapp.existing"
        dest.mkdir()
        resp = _send(sock_path, {"action": "fork", "name": "existing"})
        assert resp["status"] == "error"
        assert "already exists" in resp["message"]

    def test_fork_rejects_empty_name(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": ""})
        assert resp["status"] == "error"
        assert "missing" in resp["message"]

    def test_fork_rejects_name_with_slash(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "a/b"})
        assert resp["status"] == "error"
        assert "invalid" in resp["message"]

    def test_fork_rejects_name_with_dot(self, fork_hub):
        hub, sock_path, ctx = fork_hub
        resp = _send(sock_path, {"action": "fork", "name": "a.b"})
        assert resp["status"] == "error"
        assert "invalid" in resp["message"]

    def test_fork_returns_error_when_project_path_not_set(self, tmp_path):
        """Fork fails gracefully when context lacks project_path."""
        runtime = MagicMock()
        helpers_dir = tmp_path / "helpers"
        helpers_dir.mkdir()
        ctx = HelperContext(
            runtime=runtime,
            image="test:latest",
            container_name_prefix="kanibako-test",
            shell_path=tmp_path / "shell",
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "helper.sock",
            project_path=None,
            data_path=None,
        )
        sock_path = tmp_path / "helper.sock"
        hub = HelperHub()
        hub.start(sock_path, ctx)
        try:
            resp = _send(sock_path, {"action": "fork", "name": "test"})
            assert resp["status"] == "error"
            assert "project_path" in resp["message"]
        finally:
            hub.stop()


# ---------------------------------------------------------------------------
# CLI tests (fork_cmd)
# ---------------------------------------------------------------------------

class TestRunFork:
    def test_prints_path_on_success(self, tmp_path, capsys):
        from kanibako.commands.fork_cmd import run_fork
        import argparse

        args = argparse.Namespace(name="test")
        sock = tmp_path / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()
        with patch("kanibako.helper_client.send_request") as mock_send, \
             patch("kanibako.commands.fork_cmd.Path.home", return_value=tmp_path):
            mock_send.return_value = {
                "status": "ok",
                "path": "/home/user/proj.test",
                "name": "proj-test",
            }
            rc = run_fork(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "/home/user/proj.test" in out
        assert "proj-test" in out

    def test_prints_error_on_failure(self, tmp_path, capsys):
        from kanibako.commands.fork_cmd import run_fork
        import argparse

        args = argparse.Namespace(name="bad")
        sock = tmp_path / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()
        with patch("kanibako.helper_client.send_request") as mock_send, \
             patch("kanibako.commands.fork_cmd.Path.home", return_value=tmp_path):
            mock_send.return_value = {
                "status": "error",
                "message": "destination already exists",
            }
            rc = run_fork(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_errors_when_no_socket(self, tmp_path, capsys):
        from kanibako.commands.fork_cmd import run_fork
        import argparse

        args = argparse.Namespace(name="nope")
        with patch("kanibako.commands.fork_cmd.Path.home", return_value=tmp_path):
            rc = run_fork(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "helpers enabled" in err


# ---------------------------------------------------------------------------
# CLI registration tests
# ---------------------------------------------------------------------------

class TestForkCLIRegistration:
    def test_crab_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "crab" in _SUBCOMMANDS

    def test_agent_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "agent" in _SUBCOMMANDS

    def test_fork_parser_registered_under_crab(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        # fork should be recognized as a crab subcommand
        args = parser.parse_args(["crab", "fork", "testname"])
        assert args.command == "crab"
        assert args.crab_command == "fork"
        assert args.name == "testname"

    def test_fork_exempt_from_config_check(self):
        """crab fork should not require kanibako.toml to exist."""
        from kanibako.cli import main
        # Calling crab fork with a missing kanibako.toml should not trigger
        # the "kanibako is not set up" error — it should reach run_fork
        # and fail on the socket check instead.
        with pytest.raises(SystemExit) as exc_info:
            main(["crab", "fork", "test"])
        # Should exit with 1 (no socket), not the config-check error
        assert exc_info.value.code == 1

    def test_fork_via_agent_alias_exempt_from_config_check(self):
        """'agent fork' alias is translated to 'crab fork' and still works."""
        from kanibako.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["agent", "fork", "test"])
        assert exc_info.value.code == 1
