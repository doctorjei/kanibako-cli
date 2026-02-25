"""Tests for helper spawning: numbering and spawn budget."""

from __future__ import annotations

import pytest

from kanibako.helpers import (
    DEFAULT_BREADTH,
    DEFAULT_DEPTH,
    UNLIMITED_BREADTH,
    SpawnBudget,
    agent_depth,
    check_spawn_allowed,
    child_budget,
    children_of,
    create_broadcast_dirs,
    create_helper_dirs,
    create_peer_channels,
    effective_breadth,
    link_broadcast,
    nth_child,
    parent_of,
    read_spawn_config,
    remove_helper_dirs,
    resolve_spawn_budget,
    sibling_index,
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


# --- children_of ---


class TestChildrenOf:
    def test_director_b3(self):
        """Director (0) with B=3: children are 1, 2, 3."""
        assert children_of(0, 3) == (1, 3)

    def test_agent1_b3(self):
        """Agent 1 with B=3: children are 4, 5, 6."""
        assert children_of(1, 3) == (4, 6)

    def test_agent2_b3(self):
        """Agent 2 with B=3: children are 7, 8, 9."""
        assert children_of(2, 3) == (7, 9)

    def test_agent3_b3(self):
        """Agent 3 with B=3: children are 10, 11, 12."""
        assert children_of(3, 3) == (10, 12)

    def test_director_b4(self):
        """Director (0) with B=4: children are 1, 2, 3, 4."""
        assert children_of(0, 4) == (1, 4)

    def test_agent1_b4(self):
        """Agent 1 with B=4: children are 5, 6, 7, 8."""
        assert children_of(1, 4) == (5, 8)

    def test_slot_count_equals_breadth(self):
        """Each agent has exactly B child slots."""
        for b in (1, 2, 3, 4, 10):
            first, last = children_of(0, b)
            assert last - first + 1 == b

    def test_no_overlap_between_siblings(self):
        """Children of different agents don't overlap (B=3)."""
        ranges = [children_of(i, 3) for i in range(4)]
        for i, (a_first, a_last) in enumerate(ranges):
            for j, (b_first, b_last) in enumerate(ranges):
                if i != j:
                    assert a_last < b_first or b_last < a_first

    def test_unlimited_breadth(self):
        first, last = children_of(0, -1)
        assert first == 1
        assert last == UNLIMITED_BREADTH


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

    def test_round_trip_b4(self):
        """children_of and parent_of are inverses (B=4)."""
        for agent in range(5):
            first, last = children_of(agent, 4)
            for child in range(first, last + 1):
                assert parent_of(child, 4) == agent


# --- agent_depth ---


class TestAgentDepth:
    def test_director_depth_zero(self):
        assert agent_depth(0, 3) == 0

    def test_director_children_depth_one(self):
        assert agent_depth(1, 3) == 1
        assert agent_depth(2, 3) == 1
        assert agent_depth(3, 3) == 1

    def test_grandchildren_depth_two(self):
        assert agent_depth(4, 3) == 2
        assert agent_depth(9, 3) == 2

    def test_great_grandchildren_depth_three(self):
        # Agent 4's first child with B=3: 4*3+1 = 13
        assert agent_depth(13, 3) == 3

    def test_depth_b1(self):
        """With breadth=1, tree is a chain: depth equals agent number."""
        for i in range(10):
            assert agent_depth(i, 1) == i


# --- nth_child ---


class TestNthChild:
    def test_first_child_of_director(self):
        assert nth_child(0, 0, 3) == 1

    def test_last_child_of_director(self):
        assert nth_child(0, 2, 3) == 3

    def test_first_child_of_agent1(self):
        assert nth_child(1, 0, 3) == 4

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            nth_child(0, 3, 3)  # B=3, index 3 is out of bounds

    def test_negative_index_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            nth_child(0, -1, 3)

    def test_matches_children_of(self):
        """nth_child produces values within children_of range."""
        for b in (2, 3, 4):
            for agent in range(4):
                first, last = children_of(agent, b)
                for n in range(b):
                    child = nth_child(agent, n, b)
                    assert first <= child <= last


# --- sibling_index ---


class TestSiblingIndex:
    def test_director_returns_zero(self):
        assert sibling_index(0, 3) == 0

    def test_first_child_is_zero(self):
        assert sibling_index(1, 3) == 0

    def test_second_child_is_one(self):
        assert sibling_index(2, 3) == 1

    def test_third_child_is_two(self):
        assert sibling_index(3, 3) == 2

    def test_grandchild_indices(self):
        """Agent 1's children (4,5,6) have indices 0,1,2."""
        assert sibling_index(4, 3) == 0
        assert sibling_index(5, 3) == 1
        assert sibling_index(6, 3) == 2

    def test_round_trip_with_nth_child(self):
        """sibling_index is the inverse of nth_child."""
        for b in (2, 3, 4):
            for agent in range(4):
                for n in range(b):
                    child = nth_child(agent, n, b)
                    assert sibling_index(child, b) == n


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
        path = tmp_path / "spawn.toml"
        budget = SpawnBudget(depth=3, breadth=5)
        write_spawn_config(path, budget)
        result = read_spawn_config(path)
        assert result == budget

    def test_read_missing_file(self, tmp_path):
        assert read_spawn_config(tmp_path / "nope.toml") is None

    def test_read_no_spawn_section(self, tmp_path):
        path = tmp_path / "empty.toml"
        path.write_text("[other]\nfoo = 1\n")
        assert read_spawn_config(path) is None

    def test_preserves_other_sections(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("[other]\nfoo = 1\n")
        write_spawn_config(path, SpawnBudget(depth=2, breadth=3))
        result = read_spawn_config(path)
        assert result == SpawnBudget(depth=2, breadth=3)
        # Other section preserved
        import tomllib as tl
        with open(path, "rb") as f:
            data = tl.load(f)
        assert data["other"]["foo"] == 1

    def test_unlimited_values(self, tmp_path):
        path = tmp_path / "unlimited.toml"
        budget = SpawnBudget(depth=-1, breadth=-1)
        write_spawn_config(path, budget)
        result = read_spawn_config(path)
        assert result == budget

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "spawn.toml"
        write_spawn_config(path, SpawnBudget())
        assert path.exists()


# --- Directory structure ---


class TestCreateHelperDirs:
    def test_creates_standard_layout(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        root = create_helper_dirs(helpers, 1)
        assert root == helpers / "1"
        assert (root / "vault" / "share-ro").is_dir()
        assert (root / "vault" / "share-rw").is_dir()
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
