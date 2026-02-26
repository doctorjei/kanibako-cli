"""Tests for kanibako.utils: cp_if_newer, confirm_prompt, project_hash, short_hash, path encoding, container naming."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kanibako.errors import UserCancelled
from kanibako.utils import (
    confirm_prompt,
    container_name_for,
    cp_if_newer,
    escape_path,
    project_hash,
    short_hash,
    unescape_path,
)


# ---------------------------------------------------------------------------
# cp_if_newer
# ---------------------------------------------------------------------------

class TestCpIfNewer:
    def test_copies_when_dst_missing(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "hello"

    def test_copies_when_src_newer(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        dst.write_text("old")
        # Ensure distinct mtime
        src.write_text("new")
        os.utime(dst, (0, 0))
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "new"

    def test_skips_when_dst_newer(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("old")
        os.utime(src, (0, 0))
        dst.write_text("new")
        assert cp_if_newer(src, dst) is False
        assert dst.read_text() == "new"

    def test_skips_when_src_missing(self, tmp_path):
        dst = tmp_path / "dst.txt"
        assert cp_if_newer(tmp_path / "nope.txt", dst) is False
        assert not dst.exists()

    def test_creates_parent_dirs(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "a" / "b" / "dst.txt"
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "data"


# ---------------------------------------------------------------------------
# confirm_prompt
# ---------------------------------------------------------------------------

class TestConfirmPrompt:
    def test_yes_passes(self):
        with patch("builtins.input", return_value="yes"):
            confirm_prompt("ok? ")  # Should not raise

    def test_no_raises(self):
        with patch("builtins.input", return_value="no"):
            with pytest.raises(UserCancelled):
                confirm_prompt("ok? ")

    def test_empty_raises(self):
        with patch("builtins.input", return_value=""):
            with pytest.raises(UserCancelled):
                confirm_prompt("ok? ")

    def test_eof_raises(self):
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(UserCancelled):
                confirm_prompt("ok? ")

    def test_keyboard_interrupt_raises(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with pytest.raises(UserCancelled):
                confirm_prompt("ok? ")

    def test_whitespace_yes_passes(self):
        with patch("builtins.input", return_value="  yes  "):
            confirm_prompt("ok? ")  # Should not raise


# ---------------------------------------------------------------------------
# project_hash
# ---------------------------------------------------------------------------

class TestProjectHash:
    def test_deterministic(self):
        h1 = project_hash("/some/path")
        h2 = project_hash("/some/path")
        assert h1 == h2

    def test_different_paths(self):
        h1 = project_hash("/path/a")
        h2 = project_hash("/path/b")
        assert h1 != h2

    def test_matches_sha256(self):
        path = "/my/project"
        expected = hashlib.sha256(path.encode()).hexdigest()
        assert project_hash(path) == expected


# ---------------------------------------------------------------------------
# short_hash
# ---------------------------------------------------------------------------

class TestShortHash:
    def test_default_length(self):
        h = "abcdef1234567890"
        assert short_hash(h) == "abcdef12"

    def test_custom_length(self):
        h = "abcdef1234567890"
        assert short_hash(h, 4) == "abcd"


# ---------------------------------------------------------------------------
# escape_path / unescape_path
# ---------------------------------------------------------------------------

class TestEscapePath:
    def test_simple_path(self):
        assert escape_path("/home/user/project") == "home-user-project"

    def test_path_with_dashes(self):
        assert escape_path("/home/user/my-project/app") == "home-user-my-.project-app"

    def test_round_trip(self):
        original = "/home/user/my-project/app"
        assert unescape_path(escape_path(original)) == original

    def test_round_trip_no_dashes(self):
        original = "/home/user/project"
        assert unescape_path(escape_path(original)) == original

    def test_round_trip_consecutive_dashes(self):
        original = "/home/user/a--b/c"
        assert unescape_path(escape_path(original)) == original

    def test_single_component(self):
        assert escape_path("/app") == "app"
        assert unescape_path("app") == "/app"

    def test_trailing_slash_stripped(self):
        # Leading / is stripped; trailing / becomes trailing -
        encoded = escape_path("/home/user/")
        assert unescape_path(encoded) == "/home/user/"

    def test_multiple_dashes(self):
        """Multiple consecutive dashes in the path are each escaped."""
        original = "/a-b-c"
        encoded = escape_path(original)
        assert encoded == "a-.b-.c"
        assert unescape_path(encoded) == original


# ---------------------------------------------------------------------------
# container_name_for
# ---------------------------------------------------------------------------

def _mock_proj(*, mode="account_centric", name="", project_path="/home/user/proj",
               project_hash="abcdef1234567890abcdef1234567890"):
    """Create a duck-typed ProjectPaths-like object for testing."""
    mode_ns = SimpleNamespace(value=mode)
    return SimpleNamespace(
        mode=mode_ns,
        name=name,
        project_path=Path(project_path),
        project_hash=project_hash,
    )


class TestContainerNameFor:
    def test_ac_with_name(self):
        proj = _mock_proj(name="myapp")
        assert container_name_for(proj) == "kanibako-myapp"

    def test_ac_without_name_fallback(self):
        proj = _mock_proj(name="")
        assert container_name_for(proj) == f"kanibako-{short_hash(proj.project_hash)}"

    def test_workset_uses_hash_fallback(self):
        proj = _mock_proj(mode="workset", name="")
        assert container_name_for(proj) == f"kanibako-{short_hash(proj.project_hash)}"

    def test_decentralized_uses_escaped_path(self):
        proj = _mock_proj(mode="decentralized", project_path="/home/user/my-project")
        result = container_name_for(proj)
        assert result == f"kanibako-ronin-{escape_path('/home/user/my-project')}"
        assert result == "kanibako-ronin-home-user-my-.project"

    def test_decentralized_ignores_name(self):
        """Even if a decentralized project has a name, use escaped path."""
        proj = _mock_proj(mode="decentralized", name="myapp",
                          project_path="/home/user/proj")
        result = container_name_for(proj)
        assert result.startswith("kanibako-ronin-")
        assert "myapp" not in result

    def test_ac_name_with_number_suffix(self):
        """Collision-numbered names work correctly."""
        proj = _mock_proj(name="myapp2")
        assert container_name_for(proj) == "kanibako-myapp2"
