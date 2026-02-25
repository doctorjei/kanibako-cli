"""Tests for kanibako helper CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.helper_cmd import (
    _cascade_cleanup,
    _format_log_entry,
    _get_existing_helpers,
    _next_helper_number,
    _read_state,
    _write_state,
    run_broadcast,
    run_cleanup,
    run_list,
    run_log,
    run_register,
    run_respawn,
    run_send,
    run_spawn,
    run_stop,
)
from kanibako.helpers import SpawnBudget, write_spawn_config


@pytest.fixture
def helpers_env(tmp_path, monkeypatch):
    """Set up a temporary helpers environment."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "playbook" / "scripts").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "kanibako.toml"
    config_file.write_text("[container]\nimage = \"test\"\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    return home


class TestGetExistingHelpers:
    def test_empty_dir(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        assert _get_existing_helpers(helpers) == []

    def test_numeric_dirs(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "1").mkdir()
        (helpers / "3").mkdir()
        (helpers / "2").mkdir()
        assert _get_existing_helpers(helpers) == [1, 2, 3]

    def test_ignores_non_numeric(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "1").mkdir()
        (helpers / "all").mkdir()
        (helpers / "channels").mkdir()
        assert _get_existing_helpers(helpers) == [1]

    def test_nonexistent_dir(self, tmp_path):
        assert _get_existing_helpers(tmp_path / "nope") == []


class TestNextHelperNumber:
    def test_first_helper(self):
        assert _next_helper_number([], SpawnBudget()) == 1

    def test_sequential(self):
        assert _next_helper_number([1], SpawnBudget()) == 2
        assert _next_helper_number([1, 2], SpawnBudget()) == 3

    def test_fills_gap(self):
        assert _next_helper_number([1, 3], SpawnBudget()) == 2


class TestRunSpawn:
    def test_basic_spawn(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Spawned helper 1" in out

        # Directory structure created
        helpers = helpers_env / "helpers"
        assert (helpers / "1" / "vault" / "share-ro").is_dir()
        assert (helpers / "1" / "vault" / "share-rw").is_dir()
        assert (helpers / "1" / "workspace").is_dir()
        assert (helpers / "1" / "peers").is_dir()

        # RO spawn config written
        ro_config = helpers / "1" / "spawn.toml"
        assert ro_config.is_file()

        # Broadcast dirs created
        assert (helpers / "all" / "rw").is_dir()
        assert (helpers / "all" / "ro").is_dir()

        # Broadcast linked
        assert (helpers / "1" / "all").is_symlink()

    def test_spawn_multiple(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        run_spawn(args)

        helpers = helpers_env / "helpers"
        assert (helpers / "1").is_dir()
        assert (helpers / "2").is_dir()

        # Peer channels between 1 and 2
        assert (helpers / "1" / "peers" / "1:2-ro").is_symlink()
        assert (helpers / "2" / "peers" / "1:2-rw").is_symlink()

    def test_depth_zero_refused(self, helpers_env, capsys):
        # Write RO config with depth=0
        own_ro = helpers_env / "spawn.toml"
        write_spawn_config(own_ro, SpawnBudget(depth=0, breadth=4))

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 1
        assert "depth" in capsys.readouterr().err

    def test_breadth_exhausted(self, helpers_env, capsys):
        # Write RO config with breadth=1
        own_ro = helpers_env / "spawn.toml"
        write_spawn_config(own_ro, SpawnBudget(depth=4, breadth=1))

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0  # first spawn ok

        rc = run_spawn(args)
        assert rc == 1  # second spawn refused
        assert "breadth" in capsys.readouterr().err

    def test_model_shown_in_output(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(args)
        assert "model: sonnet" in capsys.readouterr().out

    def test_child_gets_decremented_depth(self, helpers_env):
        args = _make_args(depth=3, breadth=4, model=None)
        run_spawn(args)

        from kanibako.helpers import read_spawn_config
        child_config = read_spawn_config(
            helpers_env / "helpers" / "1" / "spawn.toml"
        )
        assert child_config is not None
        assert child_config.depth == 2
        assert child_config.breadth == 4

    def test_init_script_copied(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        init = helpers_env / "helpers" / "1" / "playbook" / "scripts" / "helper-init.sh"
        assert init.is_file()
        assert "#!/usr/bin/env bash" in init.read_text()


class TestHelperState:
    def test_spawn_writes_state(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(args)
        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "spawned"
        assert state["model"] == "sonnet"
        assert state["depth"] == 3  # default 4, decremented to 3

    def test_spawn_no_model(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        state = _read_state(helpers_env / "helpers", 1)
        assert state["model"] is None

    def test_state_records_peers(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        run_spawn(args)
        state2 = _read_state(helpers_env / "helpers", 2)
        assert state2["peers"] == [1]


class TestRunList:
    def test_no_helpers(self, helpers_env, capsys):
        args = _make_args()
        rc = run_list(args)
        assert rc == 0
        assert "No helpers" in capsys.readouterr().out

    def test_lists_spawned_helpers(self, helpers_env, capsys):
        spawn_args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(spawn_args)
        run_spawn(spawn_args)

        capsys.readouterr()  # clear spawn output
        args = _make_args()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "1" in out
        assert "2" in out
        assert "spawned" in out

    def test_list_shows_model(self, helpers_env, capsys):
        spawn_args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(spawn_args)

        capsys.readouterr()
        args = _make_args()
        run_list(args)
        out = capsys.readouterr().out
        assert "sonnet" in out

    def test_list_shows_depth(self, helpers_env, capsys):
        spawn_args = _make_args(depth=2, breadth=4, model=None)
        run_spawn(spawn_args)

        capsys.readouterr()
        args = _make_args()
        run_list(args)
        out = capsys.readouterr().out
        # Child depth is parent depth - 1 = 1
        assert "1" in out


class TestRunStop:
    def test_stop_running_helper(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        rc = run_stop(_make_args(number=1))
        assert rc == 0
        assert "Stopped helper 1" in capsys.readouterr().out

        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "stopped"

    def test_stop_already_stopped(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        rc = run_stop(_make_args(number=1))
        assert rc == 0
        assert "already stopped" in capsys.readouterr().out

    def test_stop_nonexistent(self, helpers_env, capsys):
        rc = run_stop(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err


class TestRunCleanup:
    def test_cleanup_removes_dirs(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        helpers = helpers_env / "helpers"
        assert (helpers / "1").is_dir()

        rc = run_cleanup(_make_args(number=1))
        assert rc == 0
        assert "Cleaned up helper 1" in capsys.readouterr().out
        assert not (helpers / "1").exists()

    def test_cleanup_removes_peer_channels(self, helpers_env, capsys):
        """Cleanup removes peer symlinks from siblings."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        helpers = helpers_env / "helpers"
        # Helper 1 should have peer links to helper 2
        assert any(
            p.is_symlink() for p in (helpers / "1" / "peers").iterdir()
        )

        # Clean up helper 2
        rc = run_cleanup(_make_args(number=2))
        assert rc == 0
        assert not (helpers / "2").exists()

        # Helper 1's peer links to helper 2 should be gone
        remaining_peers = [
            p.name for p in (helpers / "1" / "peers").iterdir()
            if p.is_symlink()
        ]
        for name in remaining_peers:
            assert "2" not in name.split(":")[0] and "2" not in name.split(":")[1].split("-")[0]

    def test_cleanup_nonexistent(self, helpers_env, capsys):
        rc = run_cleanup(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err

    def test_cleanup_cleans_broadcast_link(self, helpers_env, capsys):
        """Cleanup removes the helper's broadcast symlink along with the dir."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        helpers = helpers_env / "helpers"
        assert (helpers / "1" / "all").is_symlink()

        run_cleanup(_make_args(number=1))
        # Entire dir is gone, including the all/ symlink
        assert not (helpers / "1").exists()


class TestRunRespawn:
    def test_respawn_stopped_helper(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        rc = run_respawn(_make_args(number=1))
        assert rc == 0
        assert "Respawned helper 1" in capsys.readouterr().out

        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "respawned"

    def test_respawn_running_refused(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        rc = run_respawn(_make_args(number=1))
        assert rc == 1
        assert "not stopped" in capsys.readouterr().err

    def test_respawn_nonexistent(self, helpers_env, capsys):
        rc = run_respawn(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err


class TestCascadeCleanup:
    def test_cascade_removes_children(self, helpers_env, capsys):
        """Cascade cleanup removes a helper and its nested children."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))

        helpers = helpers_env / "helpers"
        # Simulate nested children: helpers/1/helpers/1 and helpers/1/helpers/2
        child_helpers = helpers / "1" / "helpers"
        (child_helpers / "1").mkdir(parents=True)
        _write_state(child_helpers, 1, {"status": "spawned"})
        (child_helpers / "2").mkdir(parents=True)
        _write_state(child_helpers, 2, {"status": "spawned"})

        capsys.readouterr()
        rc = run_cleanup(_make_args(number=1, cascade=True))
        assert rc == 0
        assert "descendant" in capsys.readouterr().out
        assert not (helpers / "1").exists()

    def test_cascade_multi_level(self, helpers_env):
        """Cascade handles multiple nesting levels."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))

        helpers = helpers_env / "helpers"
        # Create a 3-level deep tree: helpers/1/helpers/1/helpers/1
        level2 = helpers / "1" / "helpers"
        (level2 / "1").mkdir(parents=True)
        level3 = level2 / "1" / "helpers"
        (level3 / "1").mkdir(parents=True)

        removed = _cascade_cleanup(helpers, 1)
        assert len(removed) == 3  # deepest first, then parent, then top
        assert not (helpers / "1").exists()

    def test_no_cascade_leaves_children(self, helpers_env, capsys):
        """Without --cascade, children are left behind (orphaned)."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))

        helpers = helpers_env / "helpers"
        child_helpers = helpers / "1" / "helpers"
        (child_helpers / "1").mkdir(parents=True)

        capsys.readouterr()
        # Cleanup without cascade — the child dir is inside the helper root,
        # so it gets removed by shutil.rmtree anyway (part of the dir tree)
        run_cleanup(_make_args(number=1, cascade=False))
        assert not (helpers / "1").exists()


class TestRunSend:
    def test_send_no_socket(self, helpers_env, capsys):
        """Send fails gracefully when helpers not enabled."""
        rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 1
        assert "not enabled" in capsys.readouterr().err

    def test_send_with_socket(self, helpers_env, capsys):
        """Send succeeds when socket exists and mock returns ok."""
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 0
        assert "sent" in capsys.readouterr().out

    def test_send_failure(self, helpers_env, capsys):
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "no route"}
            rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 1
        assert "failed" in capsys.readouterr().err.lower()


class TestRunBroadcast:
    def test_broadcast_no_socket(self, helpers_env, capsys):
        rc = run_broadcast(_make_args(message="all hands"))
        assert rc == 1
        assert "not enabled" in capsys.readouterr().err

    def test_broadcast_success(self, helpers_env, capsys):
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_broadcast(_make_args(message="all hands"))
        assert rc == 0
        assert "broadcast" in capsys.readouterr().out.lower()


class TestRunRegister:
    def test_register_no_socket(self, helpers_env):
        rc = run_register(_make_args(number=1))
        assert rc == 1

    def test_register_success(self, helpers_env):
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_register(_make_args(number=1))
        assert rc == 0
        m.assert_called_once()
        req = m.call_args[0][1]
        assert req["action"] == "register"
        assert req["helper_num"] == 1

    def test_register_failure(self, helpers_env):
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "bad"}
            rc = run_register(_make_args(number=1))
        assert rc == 1


class TestRunLog:
    def test_log_no_file(self, helpers_env, capsys):
        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 1
        assert "No helper message log" in capsys.readouterr().err

    def test_log_displays_entries(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "helper-messages.jsonl"
        log_file.parent.mkdir(parents=True)
        import json
        entries = [
            {"ts": "2026-02-25T12:35:10Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "Analyze auth."}},
            {"ts": "2026-02-25T12:36:45Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "Found 3 issues."}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "[0 → 1]" in out
        assert "Analyze auth." in out
        assert "[1 → 0]" in out
        assert "Found 3 issues." in out

    def test_log_filter_by_helper(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "helper-messages.jsonl"
        log_file.parent.mkdir(parents=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "a"}},
            {"ts": "T12:01:00Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "b"}},
            {"ts": "T12:02:00Z", "type": "message", "from": 2, "to": 0, "payload": {"text": "c"}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=1, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "b" in out
        assert "c" not in out

    def test_log_last_n(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "helper-messages.jsonl"
        log_file.parent.mkdir(parents=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "first"}},
            {"ts": "T12:01:00Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "second"}},
            {"ts": "T12:02:00Z", "type": "message", "from": 2, "to": 0, "payload": {"text": "third"}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=1))
        assert rc == 0
        out = capsys.readouterr().out
        assert "third" in out
        assert "first" not in out

    def test_log_control_entries(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "helper-messages.jsonl"
        log_file.parent.mkdir(parents=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "control", "event": "spawn", "helper": 1, "model": "sonnet"},
            {"ts": "T12:01:00Z", "type": "control", "event": "register", "helper": 1},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "[spawn]" in out
        assert "model=sonnet" in out
        assert "[register]" in out


class TestFormatLogEntry:
    def test_message_entry(self):
        entry = {"ts": "2026-02-25T12:35:10Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "hello"}}
        result = _format_log_entry(entry)
        assert "12:35:10" in result
        assert "[0 → 1]" in result
        assert "hello" in result

    def test_broadcast_entry(self):
        entry = {"ts": "2026-02-25T12:37:00Z", "type": "message", "from": 0, "to": "all", "payload": {"text": "start"}}
        result = _format_log_entry(entry)
        assert "[0 → *]" in result

    def test_control_entry(self):
        entry = {"ts": "2026-02-25T12:34:56Z", "type": "control", "event": "spawn", "helper": 1, "model": "sonnet"}
        result = _format_log_entry(entry)
        assert "[spawn]" in result
        assert "helper 1" in result


class TestSocketWiring:
    def test_spawn_with_socket(self, helpers_env, capsys):
        """Spawn calls socket client when socket exists."""
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok", "container_name": "kanibako-helper-1-abc"}
            args = _make_args(depth=None, breadth=None, model=None)
            rc = run_spawn(args)

        assert rc == 0
        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"
        assert state["container_name"] == "kanibako-helper-1-abc"

    def test_spawn_socket_failure(self, helpers_env, capsys):
        """Spawn records 'failed' when socket returns error."""
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "image not found"}
            args = _make_args(depth=None, breadth=None, model=None)
            rc = run_spawn(args)

        assert rc == 0  # spawn itself succeeds (dirs created)
        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "failed"

    def test_stop_with_socket(self, helpers_env, capsys):
        """Stop calls socket client for container stop."""
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        # Spawn first (without socket active for dir creation)
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        # Manually set container_name in state
        state = _read_state(helpers_env / "helpers", 1)
        state["container_name"] = "kanibako-helper-1-abc"
        state["status"] = "running"
        _write_state(helpers_env / "helpers", 1, state)
        capsys.readouterr()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_stop(_make_args(number=1))

        assert rc == 0
        m.assert_called_once()
        call_req = m.call_args[0][1]
        assert call_req["action"] == "stop"
        assert call_req["container_name"] == "kanibako-helper-1-abc"

    def test_respawn_with_socket(self, helpers_env, capsys):
        """Respawn calls socket client for container relaunch."""
        sock = helpers_env / ".kanibako" / "helper.sock"
        sock.parent.mkdir(parents=True)
        sock.touch()

        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        with patch("kanibako.helper_client.send_request") as m:
            m.return_value = {"status": "ok", "container_name": "kanibako-helper-1-new"}
            rc = run_respawn(_make_args(number=1))

        assert rc == 0
        state = _read_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"


def _make_args(**kwargs):
    """Create a minimal argparse.Namespace for testing."""
    return type("Args", (), kwargs)()
