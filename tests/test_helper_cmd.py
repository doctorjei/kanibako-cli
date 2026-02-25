"""Tests for kanibako helper CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.helper_cmd import (
    _get_existing_helpers,
    _helpers_dir,
    _next_helper_number,
    _read_state,
    run_list,
    run_spawn,
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


def _make_args(**kwargs):
    """Create a minimal argparse.Namespace for testing."""
    return type("Args", (), kwargs)()
