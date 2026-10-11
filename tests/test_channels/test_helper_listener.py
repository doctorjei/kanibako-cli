"""Tests for kanibako.channels.helper_listener."""

from __future__ import annotations

import json
import shutil
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.channels.helper_listener import (
    HelperContext,
    HelperHub,
    MessageLog,
    _build_helper_mounts,
    _send_json,
)
import kanibako.channels.helper_listener as helper_listener
from kanibako.channels.helpers import (
    SpawnBudget,
    read_helper_number,
    read_spawn_budget,
    write_helper_number,
    write_spawn_budget,
)
from kanibako.settings.core_defaults import helper_budget_dest
from kanibako.settings.paths import BoxMode
from kanibako.settings.settings_resolve import normalize_bind_dest
from tests.support.early import early_record


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
        container_name_segments=("primary", "testproj"),
        shell_path=tmp_path / "shell",
        helpers_dir=helpers_dir,
        socket_path=socket_path,
        binary_mounts=[],
        env=None,
        entrypoint=None,
        default_entrypoint="claude",
        early=early_record(tmp_path, mode=BoxMode.primary),
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
        spawn_toml = helper_root / "spawn.yaml"
        spawn_toml.write_text("spawn:\n  depth: 3\n")

        sock = tmp_path / "helper.sock"
        sock.touch()
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=sock,
            binary_mounts=[],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        dests = [m.destination for m in mounts]
        assert "/home/agent/peers" in dests
        assert "/home/agent/spawn.yaml" in dests

    def _budget_ctx(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helper_root = helpers_dir / "1"
        helper_root.mkdir(parents=True)
        sock = tmp_path / "helper.sock"
        sock.touch()
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=sock,
            binary_mounts=[],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )
        return ctx, helpers_dir, helper_root

    def test_the_budget_mount_follows_the_declared_row(self, tmp_path, monkeypatch):
        """Dest and options come from the director's ``helper_budget`` row, not a
        hand-spelled literal: move the row and the helper mount moves with it."""
        import copy

        from kanibako.channels.helpers import SpawnBudget, write_spawn_budget
        from kanibako.settings import core_defaults

        ctx, helpers_dir, helper_root = self._budget_ctx(tmp_path)
        doc = helper_root / "spawn.yaml"
        write_spawn_budget(doc, SpawnBudget(1, 1))
        moved = copy.deepcopy(core_defaults._load_doc())
        for row in moved["helpers"]:
            if row["key"] == "helper_budget":
                row["box_dest"] = "~/x.yaml"
                row["options"] = "ro,z"
        monkeypatch.setattr(core_defaults, "_load_doc", lambda: moved)

        by_dest = {m.destination: m for m in _build_helper_mounts(ctx, 1, helpers_dir)}
        assert "/home/agent/spawn.yaml" not in by_dest
        assert by_dest["/home/agent/x.yaml"].source == doc
        assert by_dest["/home/agent/x.yaml"].options == "ro,z"

    def test_the_chain_hands_each_helper_a_decremented_ro_copy(self, tmp_path):
        """Director 2/1 -> helper 1 gets 1/1, read-only at ~/spawn.yaml -> its own
        child would get 0/1, which refuses."""
        from kanibako.channels.helpers import (
            SpawnBudget,
            check_spawn_allowed,
            child_budget,
            read_spawn_budget,
            write_spawn_budget,
        )

        ctx, helpers_dir, helper_root = self._budget_ctx(tmp_path)
        director = SpawnBudget(depth=2, breadth=1)
        write_spawn_budget(helper_root / "spawn.yaml", child_budget(director))

        by_dest = {m.destination: m for m in _build_helper_mounts(ctx, 1, helpers_dir)}
        budget = by_dest["/home/agent/spawn.yaml"]
        assert budget.options == "ro"
        handed = read_spawn_budget(budget.source)
        assert handed == SpawnBudget(depth=1, breadth=1)
        assert check_spawn_allowed(handed, 0) is None
        assert child_budget(handed) == SpawnBudget(depth=0, breadth=1)
        assert check_spawn_allowed(child_budget(handed), 0) == (
            "spawn depth exhausted (depth=0)"
        )

    def test_home_workspace_and_vault_are_bound_from_the_helper_root(self, tmp_path):
        """Home is the pid-0 foundation (its dest and options are the director's); the
        workspace and vault take dest and options from the declared ``core:`` rows."""
        from kanibako.channels.helpers import create_helper_dirs

        ctx, helpers_dir, helper_root = self._budget_ctx(tmp_path)
        create_helper_dirs(helpers_dir, 1)

        by_dest = {m.destination: m for m in _build_helper_mounts(ctx, 1, helpers_dir)}
        assert (by_dest["/home/agent"].source, by_dest["/home/agent"].options) == (
            helper_root, "Z,U",
        )
        ws = by_dest["/home/agent/workspace"]
        assert (ws.source, ws.options) == (helper_root / "workspace", "Z,U")
        ro = by_dest["/home/agent/vault/ro"]
        assert (ro.source, ro.options) == (helper_root / "vault" / "ro", "ro")
        rw = by_dest["/home/agent/vault/rw"]
        assert (rw.source, rw.options) == (helper_root / "vault" / "rw", "Z,U")
        # The foundation goes first, so every deeper bind lands on top of it.
        assert _build_helper_mounts(ctx, 1, helpers_dir)[0].destination == "/home/agent"

    def test_the_core_binds_follow_the_declared_rows(self, tmp_path, monkeypatch):
        """Move the ``core:`` vault row and the helper's vault bind moves with it."""
        import copy

        from kanibako.channels.helpers import create_helper_dirs
        from kanibako.settings import core_defaults

        ctx, helpers_dir, helper_root = self._budget_ctx(tmp_path)
        create_helper_dirs(helpers_dir, 1)
        moved = copy.deepcopy(core_defaults._load_doc())
        for row in moved["core"]:
            if row["source"] == "vault_ro_path":
                row["box_dest"] = "~/ref"
                row["options"] = "ro,z"
        monkeypatch.setattr(core_defaults, "_load_doc", lambda: moved)

        by_dest = {m.destination: m for m in _build_helper_mounts(ctx, 1, helpers_dir)}
        assert "/home/agent/vault/ro" not in by_dest
        assert by_dest["/home/agent/ref"].source == helper_root / "vault" / "ro"
        assert by_dest["/home/agent/ref"].options == "ro,z"

    def test_includes_binary_mounts(self, tmp_path):
        from kanibako.targets.base import Mount
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)

        binary_mount = Mount(Path("/usr/bin/claude"), "/usr/bin/claude", "ro")
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "helper.sock",
            binary_mounts=[binary_mount],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert any(m.destination == "/usr/bin/claude" for m in mounts)

    def _broadcast_ctx(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helper_root = helpers_dir / "1"
        for leaf in ("workspace", "vault/ro", "vault/rw", "peers"):
            (helper_root / leaf).mkdir(parents=True)
        all_dir = helpers_dir / "all"
        (all_dir / "rw").mkdir(parents=True)
        (all_dir / "ro").mkdir(parents=True)
        (all_dir / "ro" / "startup.sh").write_text("echo hi\n")
        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "helper.sock",
            binary_mounts=[],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )
        return ctx, helpers_dir, helper_root, all_dir

    def _after_stubs(self, helpers_dir, helper_root, ctx):
        """The mounts as the runtime gets them: after the launch's stub step."""
        from kanibako.runtime.container import _precreate_mount_stubs

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        _precreate_mount_stubs(
            helper_root, helper_root / "workspace", mounts, True,
            helper_root / "vault/ro", helper_root / "vault/rw", [],
        )
        return {m.destination: m for m in mounts}

    def test_broadcast_mount_binds_the_real_broadcast_dir(self, tmp_path):
        ctx, helpers_dir, helper_root, all_dir = self._broadcast_ctx(tmp_path)

        by_dest = self._after_stubs(helpers_dir, helper_root, ctx)

        source = by_dest["/home/agent/all"].source
        assert source == all_dir
        assert (source / "ro" / "startup.sh").read_text() == "echo hi\n"

    def test_a_parents_in_box_link_does_not_hide_the_broadcast(self, tmp_path):
        """A parent box wrote ``helpers/<N>/all`` as an IN-BOX absolute path, which does
        not resolve on the host; the hub's mount must not depend on it."""
        ctx, helpers_dir, helper_root, all_dir = self._broadcast_ctx(tmp_path)
        (helper_root / "all").symlink_to("/home/agent/helpers/all")

        by_dest = self._after_stubs(helpers_dir, helper_root, ctx)

        assert "/home/agent/all" in by_dest
        assert (by_dest["/home/agent/all"].source / "ro" / "startup.sh").is_file()

    def test_a_relative_link_is_not_mounted_onto_itself(self, tmp_path):
        """The stub step replaces a link at a mount's destination with an empty dir, so a
        source at the same path would bind that empty dir."""
        ctx, helpers_dir, helper_root, all_dir = self._broadcast_ctx(tmp_path)
        (helper_root / "all").symlink_to("../all")

        by_dest = self._after_stubs(helpers_dir, helper_root, ctx)

        assert (by_dest["/home/agent/all"].source / "ro" / "startup.sh").is_file()

    def test_no_broadcast_mount_without_the_broadcast_dir(self, tmp_path):
        ctx, helpers_dir, helper_root, all_dir = self._broadcast_ctx(tmp_path)
        shutil.rmtree(all_dir)

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)

        assert all(m.destination != "/home/agent/all" for m in mounts)

    def test_socket_mount(self, tmp_path):
        """Socket is mounted when it exists on the host."""
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)
        sock = tmp_path / "helper.sock"
        sock.touch()

        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=sock,
            binary_mounts=[],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert any(m.destination == "/home/agent/.kanibako/state/helper.sock" for m in mounts)

    def test_no_socket_mount_when_missing(self, tmp_path):
        """Socket is not mounted when it doesn't exist."""
        helpers_dir = tmp_path / "helpers"
        (helpers_dir / "1").mkdir(parents=True)

        ctx = HelperContext(
            runtime=MagicMock(),
            image="test:latest",
            container_name_segments=("primary", "testproj"),
            shell_path=tmp_path,
            helpers_dir=helpers_dir,
            socket_path=tmp_path / "nonexistent.sock",
            binary_mounts=[],
            early=early_record(tmp_path, mode=BoxMode.primary),
        )

        mounts = _build_helper_mounts(ctx, 1, helpers_dir)
        assert not any(m.destination == "/home/agent/.kanibako/state/helper.sock" for m in mounts)


class TestRegistrationLifetime:
    def test_a_closing_old_connection_keeps_the_newer_registration(self, hub_and_sock):
        """A respawned helper registers its number on a new connection; the old
        connection closing after that must not unregister the new one."""
        import time

        hub, sock_path, _ = hub_and_sock

        def _register() -> socket.socket:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(sock_path))
            s.settimeout(5.0)
            s.sendall(json.dumps({"action": "register", "helper_num": 1}).encode() + b"\n")
            assert json.loads(s.recv(4096).split(b"\n")[0]) == {"status": "ok"}
            return s

        old = _register()
        new = _register()
        try:
            old.close()
            time.sleep(0.5)  # the old connection's reader sees EOF and runs its cleanup
            assert hub._connections.get(1) is not None
            new.close()
            deadline = time.monotonic() + 5
            while 1 in hub._connections and time.monotonic() < deadline:
                time.sleep(0.02)
            assert 1 not in hub._connections
        finally:
            old.close()
            new.close()


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
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
            "helpers_dir": "/home/agent/helpers",
        })
        assert resp["status"] == "ok"
        assert "container_name" in resp
        assert resp["container_name"] == "kb-primary-testproj-helper-1"

        # Verify runtime.run was called with detach=True
        ctx.runtime.run.assert_called_once()
        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["detach"] is True
        # Verify helper-init.sh is used as entrypoint with correct cli_args
        assert call_kwargs["entrypoint"] == "/home/agent/scripts/helper-init.sh"
        assert call_kwargs["cli_args"] == ["1", "claude"]

    def test_spawn_runtime_failure(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        ctx.runtime.run.return_value = 1

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

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
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["entrypoint"] == "/home/agent/scripts/helper-init.sh"
        assert call_kwargs["cli_args"] == ["1", "/usr/bin/custom-agent"]

    def test_spawn_with_model(self, hub_and_sock):
        """Model variant is passed through to cli_args."""
        hub, sock_path, ctx = hub_and_sock

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

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
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

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

    def test_spawn_shell_uses_resolved_box_shell(self, hub_and_sock):
        """No-agent helper falls back to the resolved box.shell, not /bin/bash.

        When neither entrypoint nor default_entrypoint is set (a no-agent box),
        the spawned helper must honor the box.shell resolution chain that the
        director threaded in as ctx.box_shell -- not a hardcoded /bin/bash.
        """
        hub, sock_path, ctx = hub_and_sock
        ctx.entrypoint = None
        ctx.default_entrypoint = None
        ctx.box_shell = "/bin/zsh"

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["cli_args"] == ["1", "/bin/zsh"]
        assert "/bin/bash" not in call_kwargs["cli_args"]

    def test_spawn_shell_no_box_shell_floors_to_sh(self, hub_and_sock):
        """If box_shell is somehow None, the last-ditch floor is sh, not bash."""
        hub, sock_path, ctx = hub_and_sock
        ctx.entrypoint = None
        ctx.default_entrypoint = None
        ctx.box_shell = None

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["cli_args"] == ["1", "sh"]

    def test_spawn_agent_entrypoint_wins_over_box_shell(self, hub_and_sock):
        """A real-agent helper still uses the agent entrypoint, not box.shell."""
        hub, sock_path, ctx = hub_and_sock
        ctx.entrypoint = None
        ctx.default_entrypoint = "claude"
        ctx.box_shell = "/bin/zsh"  # set but must NOT win

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        resp = _connect_and_send(sock_path, {
            "action": "spawn",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["cli_args"] == ["1", "claude"]

    def test_the_entrypoint_reaches_the_helper_through_its_home_bind(self, hub_and_sock):
        """bifrost hb5: ``helper-init.sh`` not found, exit 127 — no home was bound.

        The entrypoint is a path in the helper's home, so some mount must carry it: the
        home bind, whose source is the helper root the parent copied the script into.
        """
        hub, sock_path, ctx = hub_and_sock
        helper_root = ctx.helpers_dir / "1"
        for leaf in ("workspace", "vault/ro", "vault/rw", "scripts"):
            (helper_root / leaf).mkdir(parents=True)
        (helper_root / "scripts" / "helper-init.sh").write_text("#!/usr/bin/env bash\n")

        resp = _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})
        assert resp["status"] == "ok"

        call_kwargs = ctx.runtime.run.call_args[1]
        entry = call_kwargs["entrypoint"]
        by_dest = {m.destination: m for m in call_kwargs["extra_mounts"]}
        assert by_dest["/home/agent"].source == helper_root
        rel = Path(entry).relative_to("/home/agent")
        assert (by_dest["/home/agent"].source / rel).is_file()


def _helper_tree(root: Path) -> None:
    """The leaves a parent creates for one helper (``create_helper_dirs``)."""
    for leaf in ("workspace", "vault/ro", "vault/rw"):
        (root / leaf).mkdir(parents=True, exist_ok=True)


class TestHubTreeSpawn:
    """keyspec ``meta.box.helper_num``: "unique across the director's helper tree".
    One hub serves the whole tree, so a helper's child lands under THAT helper's home
    (the request names its ``requester``), never on the director's slot."""

    def test_a_helpers_child_launches_under_the_helpers_home(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        child_root = ctx.helpers_dir / "1" / "helpers" / "5"
        _helper_tree(child_root)

        resp = _connect_and_send(sock_path, {
            "action": "spawn", "helper_num": 5, "requester": 1,
            "helpers_dir": "/home/agent/helpers",
        })
        assert resp == {"status": "ok", "container_name": "kb-primary-testproj-helper-5"}
        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["shell_path"] == child_root
        by_dest = {m.destination: m for m in call_kwargs["extra_mounts"]}
        assert by_dest["/home/agent"].source == child_root
        assert call_kwargs["cli_args"][0] == "5"

    def test_a_grandchild_maps_through_two_homes(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        _helper_tree(ctx.helpers_dir / "1" / "helpers" / "5")
        deep = ctx.helpers_dir / "1" / "helpers" / "5" / "helpers" / "21"
        _helper_tree(deep)
        for req in ({"helper_num": 1}, {"helper_num": 5, "requester": 1},
                    {"helper_num": 21, "requester": 5}):
            assert _connect_and_send(sock_path, {"action": "spawn", **req})["status"] == "ok"
        assert ctx.runtime.run.call_args[1]["shell_path"] == deep

    def test_an_unknown_requester_is_refused(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {
            "action": "spawn", "helper_num": 5, "requester": 1,
        })
        assert resp["status"] == "error"
        assert "helper 1" in resp["message"]
        ctx.runtime.run.assert_not_called()

    def test_a_number_already_in_the_tree_is_refused(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        _helper_tree(ctx.helpers_dir / "1" / "helpers" / "1")
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        resp = _connect_and_send(sock_path, {
            "action": "spawn", "helper_num": 1, "requester": 1,
        })
        assert resp["status"] == "error"
        assert "already" in resp["message"]
        assert ctx.runtime.run.call_count == 1

    def test_a_respawn_reuses_its_own_number(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        for _ in range(2):
            assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        assert ctx.runtime.run.call_count == 2


#: A user's own ``~/canon/notebook/scripts/helper-init.sh`` (``resolve_init_script``):
#: it registers nothing and exports nothing, and only execs the agent command.
_CUSTOM_INIT = "#!/usr/bin/env bash\nshift\nexec \"$@\"\n"


def _fake_podman(tmp_path: Path) -> tuple[Path, Path]:
    """A podman stand-in: ``run`` records its argv, ``inspect`` reports the recorded
    ``-e`` pairs as ``.Config.Env``.  Returns ``(binary, record)``."""
    import sys

    rec = tmp_path / "podman-run.argv"
    binary = tmp_path / "fake-podman"
    binary.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        f"  run) shift; printf '%s\\n' \"$@\" > '{rec}'; echo cid ;;\n"
        f"  inspect) exec '{sys.executable}' -c 'import json,sys; a=open(sys.argv[1]).read()"
        f".split(chr(10)); print(json.dumps([a[i+1] for i,x in enumerate(a) if x==\"-e\"]))'"
        f" '{rec}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    binary.chmod(0o755)
    return binary, rec


class TestHubWritesTheHelperNumber:
    """keyspec ``meta.box.helper_num`` (``<int, set at spawn>``): the hub writes each
    helper's number into that helper's spawn document at every launch, and binds the
    document RO at ``~/spawn.yaml``.  Nothing goes in the env (system-design § Delivery
    at launch: "nothing writes a variable after the channel"), so a ``podman exec``
    shell and a user's own init script read the same number."""

    def test_each_helper_gets_its_own_number(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        ctx.env = {"FOO": "bar"}
        one = ctx.helpers_dir / "1"
        five = one / "helpers" / "5"
        for home in (one, five):
            _helper_tree(home)
        write_spawn_budget(one / "spawn.yaml", SpawnBudget(depth=3, breadth=4))
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        assert _connect_and_send(sock_path, {
            "action": "spawn", "helper_num": 5, "requester": 1,
        })["status"] == "ok"
        assert read_helper_number(one / "spawn.yaml") == 1
        assert read_helper_number(five / "spawn.yaml") == 5
        # The parent's budget is kept beside the number.
        assert read_spawn_budget(one / "spawn.yaml") == SpawnBudget(depth=3, breadth=4)
        # The document is bound RO at the spawn-budget row's dest, and the env is the
        # director's snapshot as it stands: no per-helper variable.
        call_kwargs = ctx.runtime.run.call_args[1]
        dest, options = helper_budget_dest()
        by_dest = {m.destination: m for m in call_kwargs["extra_mounts"]}
        doc_mount = by_dest[normalize_bind_dest(dest)]
        assert (doc_mount.source, doc_mount.options) == (five / "spawn.yaml", options)
        assert call_kwargs["env"] == {"FOO": "bar"}
        assert ctx.env == {"FOO": "bar"}

    def test_a_respawn_rewrites_the_same_number(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        one = ctx.helpers_dir / "1"
        _helper_tree(one)
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        write_helper_number(one / "spawn.yaml", 9)  # a stale or tampered value
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
        assert read_helper_number(one / "spawn.yaml") == 1

    def test_an_unwritable_document_is_a_failed_launch(self, hub_and_sock):
        """The number cannot be delivered: an error reply, nothing launched, the number
        not held."""
        hub, sock_path, ctx = hub_and_sock
        one = ctx.helpers_dir / "1"
        _helper_tree(one)
        (one / "spawn.yaml").mkdir()
        resp = _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})
        assert resp["status"] == "error"
        ctx.runtime.run.assert_not_called()
        assert 1 not in hub._homes

    def test_a_custom_init_script_and_an_exec_shell_read_the_right_number(
        self, tmp_path, mock_ctx,
    ):
        """Fake podman.  Helper 1 launches helper 5; then helper 5's own entrypoint (a
        user's script that exports nothing) and an exec-style call with no helper
        variable at all each read 5 from the document the hub wrote."""
        import subprocess
        import sys

        from kanibako.runtime.container import ContainerRuntime

        binary, rec = _fake_podman(tmp_path)
        mock_ctx.runtime = ContainerRuntime(command=str(binary))
        mock_ctx.env = {"FOO": "bar"}
        child = mock_ctx.helpers_dir / "1" / "helpers" / "5"
        for home in (mock_ctx.helpers_dir / "1", child):
            _helper_tree(home)
            (home / "scripts").mkdir()
            (home / "scripts" / "helper-init.sh").write_text(_CUSTOM_INIT)
        hub = HelperHub()
        sock_path = tmp_path / "helper.sock"
        hub.start(sock_path, mock_ctx)
        try:
            assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"
            resp = _connect_and_send(sock_path, {
                "action": "spawn", "helper_num": 5, "requester": 1,
            })
        finally:
            hub.stop()
        assert resp == {"status": "ok", "container_name": "kb-primary-testproj-helper-5"}
        argv = rec.read_text().split("\n")
        box_env = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")
        assert box_env["FOO"] == "bar"
        assert not [k for k in box_env if "HELPER" in k]

        # In the box ``~`` is the helper root (the home bind), so ``~/spawn.yaml`` is
        # the document the hub wrote.
        box_env.update(PATH="/usr/bin:/bin", HOME=str(child),
                       PYTHONPATH=str(Path(helper_listener.__file__).parents[2]))
        number_arg = argv[argv.index("test:latest") + 1]
        probe = ("from kanibako.commands.helper_cmd import _own_helper_number;"
                 "print(_own_helper_number())")
        via_init = subprocess.run(
            ["bash", str(child / "scripts" / "helper-init.sh"), number_arg,
             sys.executable, "-c", probe],
            env=box_env, capture_output=True, text=True, timeout=60,
        )
        assert (via_init.returncode, via_init.stdout.strip()) == (0, "5"), via_init.stderr
        via_exec = subprocess.run(
            [sys.executable, "-c", probe], env=box_env,
            capture_output=True, text=True, timeout=60,
        )
        assert (via_exec.returncode, via_exec.stdout.strip()) == (0, "5"), via_exec.stderr


class TestHubStop:
    def test_stop_success(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {
            "action": "stop",
            "container_name": "kanibako-testproj-helper-1",
            "helper_num": 1,
        })
        assert resp["status"] == "ok"
        ctx.runtime.stop.assert_called_with("kanibako-testproj-helper-1")
        ctx.runtime.rm.assert_called_with("kanibako-testproj-helper-1")

    def test_stop_missing_name(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        resp = _connect_and_send(sock_path, {"action": "stop"})
        assert resp["status"] == "error"
        assert "missing" in resp["message"]

    def test_stop_logs_structured_helper_num(self, mock_ctx):
        """_handle_stop reads helper_num from the request and logs it."""
        hub = HelperHub()
        hub._ctx = mock_ctx
        hub._log = MagicMock()
        resp = hub._handle_stop({
            "action": "stop",
            "container_name": "kanibako-testproj-helper-3",
            "helper_num": 3,
        })
        assert resp["status"] == "ok"
        hub._log.log_control.assert_called_once_with("stop", 3)

    def test_stop_without_helper_num_does_not_log(self, mock_ctx):
        """A stop request without helper_num logs nothing and does not crash."""
        hub = HelperHub()
        hub._ctx = mock_ctx
        hub._log = MagicMock()
        resp = hub._handle_stop({
            "action": "stop",
            "container_name": "kanibako-testproj-helper-3",
        })
        assert resp["status"] == "ok"
        hub._log.log_control.assert_not_called()


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


def _running_state(root: Path, container_name: str) -> None:
    """The state document the in-box ``box helper spawn`` leaves after a launch."""
    (root / "state.json").write_text(json.dumps({
        "status": "running", "model": None, "depth": 0, "breadth": 4, "peers": [],
        "container_name": container_name,
    }))


class TestHubStopMarksHelpersStopped:
    """The hub removes every helper container when the director's session ends, so
    each helper's ``state.json`` must say ``stopped``: ``respawn`` refuses a helper
    whose document still says ``running``."""

    def test_each_launched_helper_is_marked_stopped(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        top = ctx.helpers_dir / "1"
        child = top / "helpers" / "5"
        for root, req in ((top, {"helper_num": 1}),
                          (child, {"helper_num": 5, "requester": 1})):
            _helper_tree(root)
            resp = _connect_and_send(sock_path, {"action": "spawn", **req})
            assert resp["status"] == "ok"
            _running_state(root, resp["container_name"])

        hub.stop()

        for root in (top, child):
            state = json.loads((root / "state.json").read_text())
            assert state["status"] == "stopped"
            assert state["container_name"].endswith(f"-helper-{root.name}")
            assert state["peers"] == []

    def test_a_helper_whose_container_did_not_stop_stays_running(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        resp = _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})
        _running_state(ctx.helpers_dir / "1", resp["container_name"])
        ctx.runtime.stop.side_effect = RuntimeError("podman gone")

        hub.stop()

        state = json.loads((ctx.helpers_dir / "1" / "state.json").read_text())
        assert state["status"] == "running"

    def test_no_state_document_is_invented(self, hub_and_sock):
        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        assert _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})["status"] == "ok"

        hub.stop()

        assert not (ctx.helpers_dir / "1" / "state.json").exists()

    def test_list_and_respawn_agree_after_the_stop(self, hub_and_sock, monkeypatch, capsys):
        import argparse

        from kanibako.commands import helper_cmd

        hub, sock_path, ctx = hub_and_sock
        _helper_tree(ctx.helpers_dir / "1")
        resp = _connect_and_send(sock_path, {"action": "spawn", "helper_num": 1})
        _running_state(ctx.helpers_dir / "1", resp["container_name"])
        hub.stop()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: ctx.shell_path))
        capsys.readouterr()

        assert helper_cmd.run_list(argparse.Namespace()) == 0
        row = capsys.readouterr().out.splitlines()[1].split()
        assert row[:2] == ["1", "stopped"]
        # No hub listens any more, so respawn stops at the hub check, not at the state.
        assert helper_cmd.run_respawn(argparse.Namespace(number=1)) == 1
        assert "not stopped" not in capsys.readouterr().err


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


class TestHelperDefaultCategories:
    """The Phase-B helper-hub bind table (socket + log) the launch seam injects.

    The two hardwired ``_HMount`` appends were extracted into
    ``core_defaults.helper_default_categories``, routed through the category
    resolver.  These tests lock the structured shape: the socket carries the
    EMPTY options 3rd slot ("" — a live unix socket; a Z/U relabel/chown would
    break the shared socket topology) and the dynamic box dests + .exists() gate.
    """

    def _sources(self, tmp_path):
        sock = tmp_path / "helper.sock"
        sock.touch()
        log = tmp_path / "helpers.jsonl"
        log.touch()
        return sock, log

    def test_socket_options_are_empty_string(self, tmp_path):
        # helper_sock binding value is the structured triple with options "" —
        # NOT Z/U/z (the relabel/chown that would break the live socket).
        from kanibako.settings import core_defaults

        sock, log = self._sources(tmp_path)
        cats = core_defaults.helper_default_categories(
            socket_path=sock,
            log_path=log,
        )
        # ⚑ Re-derived for dest-keying (R-3/R-5, P6): the socket's DEST is the
        # terminal arm's map key and the value is ``(host_src, options)``, so the
        # empty-options slot moved from index 2 to index 1. The property pinned is
        # unchanged — an EXPLICIT ``""``, never Z/U/z, and never absent (an absent
        # slot would take the ``rw`` category default, which IS ``Z,U``).
        sock_val = cats["box.bindings.rw"][
            "/home/agent/.kanibako/state/helper.sock"
        ]
        assert sock_val == (str(sock), "")
        assert len(sock_val) == 2 and sock_val[1] == ""

    def test_log_routes_ro(self, tmp_path):
        from kanibako.settings import core_defaults

        sock, log = self._sources(tmp_path)
        cats = core_defaults.helper_default_categories(
            socket_path=sock,
            log_path=log,
        )
        # B2b: helper_log routes through the SPEC'S OWN formula (§2c ALL PROJECTS),
        # expressible since PHASE R added the braced @{...} form — the braces keep
        # the ``.jsonl`` suffix out of the ref name, which the greedy bare form
        # would swallow.  The .exists() gate still keys off the probed log; only the
        # emitted host_src is the @-ref chain.
        # ⚑ Re-derived for dest-keying: the dest is the map key now.
        assert cats["box.bindings.ro"] == {
            "/home/agent/.kanibako/state/helpers.jsonl": (
                "{workset.logs}/{meta.box.name}.jsonl",
                "ro",
            ),
        }

    def test_missing_socket_is_omitted(self, tmp_path):
        # .exists() skip-if-missing gate (parity with the old guarded appends):
        # a missing socket simply omits its key, the log still routes.
        from kanibako.settings import core_defaults

        log = tmp_path / "helpers.jsonl"
        log.touch()
        cats = core_defaults.helper_default_categories(
            socket_path=tmp_path / "nonexistent.sock",
            log_path=log,
        )
        # ⚑ Re-derived: the ``helper_sock`` NAME is retired (R-10), so the old
        # ``"box.bindings.rw.helper_sock" not in cats`` would now pass VACUOUSLY.
        # A skipped socket leaves no ``rw`` arm at all — the socket is the only
        # thing this producer puts there — and the log's dest must still be present.
        assert "box.bindings.rw" not in cats
        assert "/home/agent/.kanibako/state/helpers.jsonl" in (
            cats["box.bindings.ro"]
        )

    def test_budget_routes_ro_through_the_spec_formula(self, tmp_path):
        # Spec §2c ``box.bindings.ro[~/spawn.yaml] | ({meta.box.path}/spawn.yaml)``:
        # an existing budget document emits the spec FORMULA, read-only, at ~/spawn.yaml.
        from kanibako.settings import core_defaults

        sock, log = self._sources(tmp_path)
        budget = tmp_path / "spawn.yaml"
        budget.touch()
        cats = core_defaults.helper_default_categories(
            socket_path=sock, log_path=log, budget_path=budget,
        )
        assert cats["box.bindings.ro"]["/home/agent/spawn.yaml"] == (
            "{meta.box.path}/spawn.yaml",
            "ro",
        )

    def test_absent_budget_emits_no_entry(self, tmp_path):
        # Absent document or no path: nothing was delivered, so no bind (and no
        # empty mountpoint) — unlike ``helper_log``, whose ``None`` is a formula.
        from kanibako.settings import core_defaults

        sock, log = self._sources(tmp_path)
        for budget in (tmp_path / "missing.yaml", None):
            cats = core_defaults.helper_default_categories(
                socket_path=sock, log_path=log, budget_path=budget,
            )
            assert "/home/agent/spawn.yaml" not in cats["box.bindings.ro"], budget

    def test_budget_dest_reads_the_row(self):
        from kanibako.settings import core_defaults

        assert core_defaults.helper_budget_dest() == ("~/spawn.yaml", "ro")
        # Not a HUB dest: every start delivers it through its own narrow resolve.
        assert core_defaults.spawn_budget_bind_dests() == {"/home/agent/spawn.yaml"}
        assert "/home/agent/spawn.yaml" not in core_defaults.helper_bind_dests()

    def test_no_socket_emits_the_budget_row_alone(self, tmp_path):
        # A start with no hub (``socket_path=None``) carries ONLY the budget row: the
        # hub's socket and log rows (the log's ``<None>`` formula too) stay out.
        from kanibako.settings import core_defaults

        _, log = self._sources(tmp_path)
        budget = tmp_path / "spawn.yaml"
        budget.touch()
        cats = core_defaults.helper_default_categories(
            socket_path=None, log_path=log, budget_path=budget,
        )
        assert cats == {
            "box.bindings.ro": {
                "/home/agent/spawn.yaml": ("{meta.box.path}/spawn.yaml", "ro"),
            },
        }

    def test_emitted_mount_options_empty_through_the_narrow_route(self, tmp_path):
        # End-to-end through the resolver: the helper_sock entry survives the NARROW
        # table pass with EMPTY mount options (not the rw default Z,U) — the property
        # the live socket depends on.
        from kanibako.settings import core_defaults
        from kanibako.settings.settings_categories import narrow_table_winners
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx

        sock, log = self._sources(tmp_path)
        cats = core_defaults.helper_default_categories(
            socket_path=sock,
            log_path=log,
        )
        # Resolved through the LIVE narrow route (build_launch_snapshot →
        # snapshot_category_entries → ``narrow_table_winners`` over the helper
        # table's own dests), the same single route the real helper-hub resolve
        # takes. B2b: helper_log routes through
        # ``{workset.logs}/{meta.box.name}.jsonl``, so the floor below STANDS IN FOR
        # ``workset_anchor_floor`` / ``meta_identity_floor`` with LITERALS.
        # ⚑ Not the same thing: the real ``workset_anchor_floor`` materializes
        # ``workset.logs`` as the FORMULA ``@meta.workset.path/logs``, so this test
        # does NOT exercise the anchor chain — it pins the LAST link, that the bind's
        # own @-ref resolves back to the same path the ``.exists()`` gate probed.
        # The anchor chain itself is covered by ``test_categories_live`` and the
        # three-mode before/after probe.
        ctx = ResolveCtx(
            agent_name="claude",
            workset_name="ws",
            host_home="/home/u",
            xdg={},
        )
        floor = {
            "workset.logs": str(log.parent),
            "meta.box.name": log.stem,
            **cats,
        }
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx,
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            default_categories=floor,
        )
        entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
        # ⚑ No ``gate_credential_delivery`` composed above this: the launch seam
        # applies it (cutover step 4), but this floor declares no credential entry
        # at all, so a shared-creds gate here would be a literal no-op over the very
        # list it is handed. The composition is pinned where it MEANS something —
        # ``test_start_assembly.TestTheCredentialGateReachesTheCollapse``.
        winners = narrow_table_winners(entries, core_defaults.helper_bind_dests())
        # ⚑ Re-derived for dest-keying (R-3/R-10): a bindings entry's ``name`` IS
        # its box destination — the ``helper_sock`` name no longer exists anywhere,
        # so selecting by it would raise ``StopIteration`` rather than fail an
        # assertion.
        sock_mount = next(
            e for e in winners
            if e.name == "/home/agent/.kanibako/state/helper.sock"
        )
        assert sock_mount.options == ""
        assert sock_mount.box_dest == (
            "/home/agent/.kanibako/state/helper.sock"
        )
        # And the log bind's @-ref chain resolves back to the probed log path —
        # the formula and the .exists()-gated literal must agree (PHASE R).
        log_mount = next(
            e for e in winners
            if e.name == "/home/agent/.kanibako/state/helpers.jsonl"
        )
        assert log_mount.host_src == str(log)


class TestInitScriptNameExecSite:
    """Pins the exec site to INIT_SCRIPT_NAME."""

    def test_exec_site_entrypoint_follows_init_script_name(self, hub_and_sock):
        """The exec site in helper_listener builds its entrypoint path from INIT_SCRIPT_NAME."""
        hub, sock_path, ctx = hub_and_sock
        ctx.entrypoint = None

        helper_root = ctx.helpers_dir / "1"
        helper_root.mkdir(parents=True)
        (helper_root / "workspace").mkdir()
        (helper_root / "vault" / "ro").mkdir(parents=True)
        (helper_root / "vault" / "rw").mkdir(parents=True)

        with patch.object(helper_listener, "INIT_SCRIPT_NAME", "init-check.sh"):
            _connect_and_send(sock_path, {
                "action": "spawn",
                "helper_num": 1,
                "helpers_dir": str(ctx.helpers_dir),
            })

        call_kwargs = ctx.runtime.run.call_args[1]
        assert call_kwargs["entrypoint"].endswith("/init-check.sh")
