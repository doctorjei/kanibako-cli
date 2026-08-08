"""Tests for helper spawning: numbering, spawn budget, and directory structure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.channels.helpers import (
    DEFAULT_BREADTH,
    DEFAULT_DEPTH,
    UNLIMITED_BREADTH,
    SpawnBudget,
    bundled_init_script,
    check_spawn_allowed,
    child_budget,
    create_broadcast_dirs,
    create_helper_dirs,
    create_peer_channels,
    effective_breadth,
    link_broadcast,
    parent_of,
    read_spawn_config,
    remove_helper_dirs,
    resolve_init_script,
    resolve_spawn_budget,
    write_spawn_config,
)


# --- effective_breadth ---


class TestEffectiveBreadth:
    def test_positive_passthrough(self):
        assert effective_breadth(3) == 3
        assert effective_breadth(1) == 1
        assert effective_breadth(100) == 100

    def test_unlimited(self):
        assert effective_breadth(-1) == UNLIMITED_BREADTH
        assert UNLIMITED_BREADTH == 2**16

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="breadth must be positive"):
            effective_breadth(0)

    def test_negative_non_minus_one_raises(self):
        with pytest.raises(ValueError, match="breadth must be positive"):
            effective_breadth(-2)


# --- parent_of ---


class TestParentOf:
    def test_director_has_no_parent(self):
        assert parent_of(0, 3) is None

    def test_director_children_b3(self):
        """Agents 1, 2, 3 are children of director (B=3)."""
        assert parent_of(1, 3) == 0
        assert parent_of(2, 3) == 0
        assert parent_of(3, 3) == 0

    def test_agent1_children_b3(self):
        """Agents 4, 5, 6 are children of agent 1 (B=3)."""
        assert parent_of(4, 3) == 1
        assert parent_of(5, 3) == 1
        assert parent_of(6, 3) == 1

    def test_agent2_children_b3(self):
        """Agents 7, 8, 9 are children of agent 2 (B=3)."""
        assert parent_of(7, 3) == 2
        assert parent_of(8, 3) == 2
        assert parent_of(9, 3) == 2

    def test_grandchildren_b3(self):
        """Agent 4's parent is 1, agent 1's parent is 0."""
        assert parent_of(4, 3) == 1
        assert parent_of(parent_of(4, 3), 3) == 0  # type: ignore[arg-type]


# --- SpawnBudget ---


class TestSpawnBudget:
    def test_defaults(self):
        b = SpawnBudget()
        assert b.depth == DEFAULT_DEPTH
        assert b.breadth == DEFAULT_BREADTH

    def test_frozen(self):
        b = SpawnBudget()
        with pytest.raises(AttributeError):
            b.depth = 10  # type: ignore[misc]


# --- check_spawn_allowed ---


class TestCheckSpawnAllowed:
    def test_allowed(self):
        assert check_spawn_allowed(SpawnBudget(depth=2, breadth=4), 0) is None

    def test_depth_zero_refused(self):
        result = check_spawn_allowed(SpawnBudget(depth=0, breadth=4), 0)
        assert result is not None
        assert "depth" in result

    def test_breadth_exhausted(self):
        result = check_spawn_allowed(SpawnBudget(depth=2, breadth=3), 3)
        assert result is not None
        assert "breadth" in result

    def test_breadth_not_yet_exhausted(self):
        assert check_spawn_allowed(SpawnBudget(depth=2, breadth=3), 2) is None

    def test_unlimited_depth(self):
        assert check_spawn_allowed(SpawnBudget(depth=-1, breadth=4), 0) is None

    def test_unlimited_breadth(self):
        assert check_spawn_allowed(SpawnBudget(depth=2, breadth=-1), 999) is None


# --- child_budget ---


class TestChildBudget:
    def test_decrements_depth(self):
        parent = SpawnBudget(depth=3, breadth=4)
        child = child_budget(parent)
        assert child.depth == 2
        assert child.breadth == 4

    def test_depth_one_to_zero(self):
        child = child_budget(SpawnBudget(depth=1, breadth=2))
        assert child.depth == 0

    def test_unlimited_depth_stays_unlimited(self):
        child = child_budget(SpawnBudget(depth=-1, breadth=3))
        assert child.depth == -1

    def test_breadth_inherited(self):
        child = child_budget(SpawnBudget(depth=4, breadth=7))
        assert child.breadth == 7


# --- resolve_spawn_budget ---


class TestResolveSpawnBudget:
    def test_ro_config_wins(self):
        ro = SpawnBudget(depth=1, breadth=1)
        host = SpawnBudget(depth=4, breadth=4)
        result = resolve_spawn_budget(ro, host, cli_depth=10, cli_breadth=10)
        assert result == ro

    def test_host_config_without_ro(self):
        host = SpawnBudget(depth=3, breadth=5)
        result = resolve_spawn_budget(None, host, cli_depth=10, cli_breadth=10)
        assert result == host

    def test_cli_flags_without_config(self):
        result = resolve_spawn_budget(None, None, cli_depth=2, cli_breadth=6)
        assert result == SpawnBudget(depth=2, breadth=6)

    def test_partial_cli_flags(self):
        result = resolve_spawn_budget(None, None, cli_depth=2, cli_breadth=None)
        assert result.depth == 2
        assert result.breadth == DEFAULT_BREADTH

    def test_defaults_when_nothing_set(self):
        result = resolve_spawn_budget(None, None, None, None)
        assert result == SpawnBudget()


# --- Spawn config I/O ---


class TestSpawnConfigIO:
    def test_write_and_read(self, tmp_path):
        path = tmp_path / "spawn.yaml"
        budget = SpawnBudget(depth=3, breadth=5)
        write_spawn_config(path, budget)
        result = read_spawn_config(path)
        assert result == budget

    def test_read_missing_file(self, tmp_path):
        assert read_spawn_config(tmp_path / "nope.yaml") is None

    def test_read_no_spawn_section(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("other:\n  foo: 1\n")
        assert read_spawn_config(path) is None

    def test_preserves_other_sections(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("other:\n  foo: 1\n")
        write_spawn_config(path, SpawnBudget(depth=2, breadth=3))
        result = read_spawn_config(path)
        assert result == SpawnBudget(depth=2, breadth=3)
        # Other section preserved
        from kanibako.settings.config_io import load_doc
        data = load_doc(path)
        assert data["other"]["foo"] == 1

    def test_unlimited_values(self, tmp_path):
        path = tmp_path / "unlimited.yaml"
        budget = SpawnBudget(depth=-1, breadth=-1)
        write_spawn_config(path, budget)
        result = read_spawn_config(path)
        assert result == budget

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "spawn.yaml"
        write_spawn_config(path, SpawnBudget())
        assert path.exists()


# --- Directory structure ---


class TestCreateHelperDirs:
    def test_creates_standard_layout(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        root = create_helper_dirs(helpers, 1)
        assert root == helpers / "1"
        assert (root / "vault" / "ro").is_dir()
        assert (root / "vault" / "rw").is_dir()
        assert (root / "workspace").is_dir()
        assert (root / "playbook" / "scripts").is_dir()
        assert (root / "peers").is_dir()

    def test_idempotent(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        # Place a file to check it isn't removed
        (helpers / "1" / "workspace" / "test.txt").write_text("hello")
        create_helper_dirs(helpers, 1)
        assert (helpers / "1" / "workspace" / "test.txt").read_text() == "hello"

    def test_creates_helpers_dir_parents(self, tmp_path):
        helpers = tmp_path / "deep" / "helpers"
        root = create_helper_dirs(helpers, 0)
        assert root.is_dir()


class TestCreateBroadcastDirs:
    def test_creates_all_rw_ro(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        all_dir = create_broadcast_dirs(helpers)
        assert all_dir == helpers / "all"
        assert (all_dir / "rw").is_dir()
        assert (all_dir / "ro").is_dir()

    def test_idempotent(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_broadcast_dirs(helpers)
        (helpers / "all" / "rw" / "test.txt").write_text("data")
        create_broadcast_dirs(helpers)
        assert (helpers / "all" / "rw" / "test.txt").read_text() == "data"


class TestCreatePeerChannels:
    def test_single_pair(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_helper_dirs(helpers, 2)
        create_peer_channels(helpers, 2, [1])

        # Channel directories exist
        channels = helpers / "channels"
        assert (channels / "1:2-ro").is_dir()
        assert (channels / "2:1-ro").is_dir()
        assert (channels / "1:2-rw").is_dir()

        # Symlinks in helper 1's peers/
        peers1 = helpers / "1" / "peers"
        assert (peers1 / "1:2-ro").is_symlink()
        assert (peers1 / "2:1-ro").is_symlink()
        assert (peers1 / "1:2-rw").is_symlink()

        # Symlinks in helper 2's peers/
        peers2 = helpers / "2" / "peers"
        assert (peers2 / "1:2-ro").is_symlink()
        assert (peers2 / "2:1-ro").is_symlink()
        assert (peers2 / "1:2-rw").is_symlink()

    def test_three_helpers_peer_count(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_helper_dirs(helpers, 2)
        create_peer_channels(helpers, 2, [1])
        create_helper_dirs(helpers, 3)
        create_peer_channels(helpers, 3, [1, 2])

        # Helper 1 should have channels to 2 and 3 (6 symlinks)
        peers1 = helpers / "1" / "peers"
        symlinks = [p for p in peers1.iterdir() if p.is_symlink()]
        assert len(symlinks) == 6

        # Helper 3 should also have 6 symlinks (to 1 and 2)
        peers3 = helpers / "3" / "peers"
        symlinks3 = [p for p in peers3.iterdir() if p.is_symlink()]
        assert len(symlinks3) == 6

    def test_channel_dirs_are_writable(self, tmp_path):
        """Channels resolve to real directories that can hold files."""
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_helper_dirs(helpers, 2)
        create_peer_channels(helpers, 2, [1])

        # Write via helper 1's symlink, read via helper 2's
        (helpers / "1" / "peers" / "1:2-ro" / "msg.txt").write_text("hello")
        content = (helpers / "2" / "peers" / "1:2-ro" / "msg.txt").read_text()
        assert content == "hello"

    def test_no_existing_helpers(self, tmp_path):
        """No crash when spawning first helper with no siblings."""
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_peer_channels(helpers, 1, [])
        # No peer symlinks created
        peers = list((helpers / "1" / "peers").iterdir())
        assert peers == []


class TestLinkBroadcast:
    def test_creates_symlink(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_broadcast_dirs(helpers)
        link_broadcast(helpers, 1)
        link_path = helpers / "1" / "all"
        assert link_path.is_symlink()
        assert (link_path / "rw").is_dir()
        assert (link_path / "ro").is_dir()

    def test_idempotent(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_broadcast_dirs(helpers)
        link_broadcast(helpers, 1)
        link_broadcast(helpers, 1)  # no error
        assert (helpers / "1" / "all").is_symlink()


class TestRemoveHelperDirs:
    def test_removes_helper_root(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        remove_helper_dirs(helpers, 1, [])
        assert not (helpers / "1").exists()

    def test_removes_peer_channels(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_helper_dirs(helpers, 2)
        create_peer_channels(helpers, 2, [1])

        remove_helper_dirs(helpers, 2, [1])

        # Helper 2's root is gone
        assert not (helpers / "2").exists()
        # Channel dirs are removed
        assert not (helpers / "channels" / "1:2-ro").exists()
        assert not (helpers / "channels" / "2:1-ro").exists()
        assert not (helpers / "channels" / "1:2-rw").exists()
        # Symlinks removed from helper 1
        assert not (helpers / "1" / "peers" / "1:2-ro").exists()
        assert not (helpers / "1" / "peers" / "2:1-ro").exists()
        assert not (helpers / "1" / "peers" / "1:2-rw").exists()

    def test_preserves_other_siblings(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        create_helper_dirs(helpers, 1)
        create_helper_dirs(helpers, 2)
        create_helper_dirs(helpers, 3)
        create_peer_channels(helpers, 2, [1])
        create_peer_channels(helpers, 3, [1, 2])

        remove_helper_dirs(helpers, 2, [1, 3])

        # Helper 1 still has channels to 3
        assert (helpers / "1" / "peers" / "1:3-ro").is_symlink()
        assert (helpers / "1" / "peers" / "3:1-ro").is_symlink()
        assert (helpers / "1" / "peers" / "1:3-rw").is_symlink()
        # But not to 2
        assert not (helpers / "1" / "peers" / "1:2-ro").exists()

    def test_nonexistent_helper(self, tmp_path):
        """No crash when removing a helper that doesn't exist."""
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        remove_helper_dirs(helpers, 99, [])


# --- helper-init.sh template ---


class TestBundledInitScript:
    def test_bundled_exists(self):
        path = bundled_init_script()
        assert path.is_file()

    def test_bundled_is_bash(self):
        path = bundled_init_script()
        content = path.read_text()
        assert content.startswith("#!/usr/bin/env bash")

    def test_bundled_contains_shebang(self):
        path = bundled_init_script()
        content = path.read_text()
        assert "set -euo pipefail" in content

    def test_bundled_socket_path_matches_mount_dest(self):
        # The hub socket is mounted under the FIXED pinned root, so the script must
        # check exactly that path for `kanibako helper register` to run and the
        # helper to join the hub. An ${XDG_STATE_HOME:-...} form here would re-open
        # the host-vs-box guess the pin deleted, so its ABSENCE is asserted too --
        # the script runs at ENTRYPOINT, before the post-boot XDG projection is
        # guaranteed to have happened, so it must not depend on it.
        path = bundled_init_script()
        content = path.read_text()
        assert 'SOCKET_PATH="$HOME/.kanibako/state/helper.sock"' in content
        # No EXECUTABLE line may RESOLVE anything from XDG (the comment explaining
        # why is exempt: it is the prose, not the resolution).
        #
        # ⚑ This was a flat "XDG_STATE_HOME appears on no executable line at all",
        # which was a sound PROXY only while the script had no reason to mention the
        # variable.  The script now also RUNS the projection, whose whole job is to
        # read $XDG_STATE_HOME and point it AT the pinned dir -- the opposite of
        # deriving the socket from it.  So the proxy is replaced by the thing it was
        # standing in for: the projection's own lines are the ONLY ones allowed to
        # name the variable, and they are verbatim-pinned to the generator by
        # TestXdgProjectionSh.test_helper_init_carries_the_snippet_verbatim.  A
        # ${XDG_STATE_HOME:-...} creeping into SOCKET_PATH still fails here.
        from kanibako.box_supervisor import xdg_projection_sh

        code = [ln for ln in content.splitlines() if not ln.lstrip().startswith("#")]
        xdg_lines = [ln for ln in code if "XDG_STATE_HOME" in ln]
        projection = xdg_projection_sh().splitlines()
        assert xdg_lines, "the projection block went missing from helper-init.sh"
        assert [ln for ln in xdg_lines if ln not in projection] == []

    def test_pinned_socket_dest_agrees_across_every_spelling(self):
        """The declared MOUNT dest, the box shell, the in-box CLI and PID-1 all
        spell one pinned path.

        This is what the retired ``box_state_home`` derivation held together by
        hand across four resolvers -- and it had already drifted (the dest honored
        ``$XDG_STATE_HOME`` while ``start.py``'s matching mkdir was hardcoded at
        ``.local/state``). With the dest pinned there is nothing left to derive,
        but the spellings still have to AGREE, so the agreement is asserted
        directly instead of being inferred from a shared function. Two of them
        CANNOT import the constant -- bash, and stdlib-only PID-1 -- which is
        exactly why this test exists rather than a shared import.
        """
        from kanibako.box_supervisor import PINNED_ROOT_RELPATH
        from kanibako.commands.helper_cmd import _log_path, _socket_path
        from kanibako.settings import core_defaults
        from kanibako.settings.settings_resolve import (
            BOX_PINNED_ROOT_RELPATH,
            BOX_PINNED_STATE_RELPATH,
            GUEST_HOME,
        )

        # 1. PID-1's quarantined duplicate == the single source of truth.
        assert PINNED_ROOT_RELPATH == BOX_PINNED_ROOT_RELPATH == ".kanibako"
        assert BOX_PINNED_STATE_RELPATH == ".kanibako/state"

        # 2. The DECLARED mount dests (what a launch actually mounts).
        declared = {
            e["key"]: e["box_dest"] for e in core_defaults._load_doc()["helpers"]
        }
        assert declared == {
            "helper_sock": f"~/{BOX_PINNED_STATE_RELPATH}/helper.sock",
            "helper_log": f"~/{BOX_PINNED_STATE_RELPATH}/helpers.jsonl",
        }

        # 3. The box-side SHELL literal, resolved with the box's HOME.
        shell_line = 'SOCKET_PATH="$HOME/.kanibako/state/helper.sock"'
        assert shell_line in bundled_init_script().read_text()
        shell_dest = shell_line.split('"')[1].replace("$HOME", GUEST_HOME)

        # 4. The in-box CLI readers, with HOME being the box's.
        with patch.object(Path, "home", staticmethod(lambda: Path(GUEST_HOME))):
            cli_sock, cli_log = str(_socket_path()), str(_log_path())

        mount_sock = declared["helper_sock"].replace("~", GUEST_HOME, 1)
        mount_log = declared["helper_log"].replace("~", GUEST_HOME, 1)
        assert shell_dest == cli_sock == mount_sock
        assert cli_log == mount_log


class TestResolveInitScript:
    def test_custom_takes_priority(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        custom = scripts / "helper-init.sh"
        custom.write_text("#!/bin/bash\necho custom\n")
        result = resolve_init_script(scripts)
        assert result == custom

    def test_falls_back_to_bundled(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        # No helper-init.sh in scripts dir
        result = resolve_init_script(scripts)
        assert result == bundled_init_script()

    def test_none_scripts_dir(self):
        result = resolve_init_script(None)
        assert result == bundled_init_script()
