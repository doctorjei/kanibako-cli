"""Tests for kanibako.git."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from kanibako.errors import GitError
from kanibako.git import check_uncommitted, check_unpushed, get_metadata, is_git_repo


class TestIsGitRepo:
  def test_true_when_git_dir(self, tmp_path):
    (tmp_path / ".git").mkdir()
    assert is_git_repo(tmp_path)

  def test_false_when_no_git(self, tmp_path):
    assert not is_git_repo(tmp_path)


class TestCheckUncommitted:
  def test_raises_on_dirty(self, tmp_path):
    result = MagicMock()
    result.returncode = 1
    with patch("kanibako.git.subprocess.run", return_value=result):
      with pytest.raises(GitError, match="Uncommitted"):
        check_uncommitted(tmp_path)

  def test_passes_on_clean(self, tmp_path):
    result = MagicMock()
    result.returncode = 0
    with patch("kanibako.git.subprocess.run", return_value=result):
      check_uncommitted(tmp_path)  # Should not raise


class TestCheckUnpushed:
  def test_raises_on_unpushed(self, tmp_path):
    results = [
      MagicMock(returncode=0, stdout="main\n"),  # branch
      MagicMock(returncode=0, stdout="origin/main\n"),  # upstream
      MagicMock(returncode=0, stdout="3\n"),  # count
    ]
    with patch("kanibako.git.subprocess.run", side_effect=results):
      with pytest.raises(GitError, match="3 unpushed"):
        check_unpushed(tmp_path)

  def test_passes_with_no_upstream(self, tmp_path):
    results = [
      MagicMock(returncode=0, stdout="main\n"),
      MagicMock(returncode=1, stdout="", stderr=""),  # no upstream
    ]
    with patch("kanibako.git.subprocess.run", side_effect=results):
      check_unpushed(tmp_path)  # Should not raise


class TestGetMetadata:
  def test_returns_metadata(self, tmp_path):
    results = [
      MagicMock(returncode=0, stdout="main\n"),
      MagicMock(returncode=0, stdout="abc123\n"),
      MagicMock(returncode=0, stdout="origin\tgit@github.com:x/y.git (fetch)\n"),
    ]
    with patch("kanibako.git.subprocess.run", side_effect=results):
      meta = get_metadata(tmp_path)
    assert meta is not None
    assert meta.branch == "main"
    assert meta.commit == "abc123"
    assert meta.remotes == [("origin", "git@github.com:x/y.git")]

  def test_returns_none_on_failure(self, tmp_path):
    result = MagicMock(returncode=1, stdout="")
    with patch("kanibako.git.subprocess.run", return_value=result):
      assert get_metadata(tmp_path) is None
