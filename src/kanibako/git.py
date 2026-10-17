"""Git checks: uncommitted/unpushed detection, metadata extraction."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from kanibako.errors import GitError


@dataclass
class GitMetadata:
  """Information about a project's git state."""

  branch: str
  commit: str
  remotes: list[tuple[str, str]]  # (name, url)


def is_git_repo(path: Path) -> bool:
  """Return True if *path* contains a .git directory."""
  return (path / ".git").is_dir()


def check_uncommitted(path: Path) -> None:
  """Raise GitError if there are uncommitted changes in *path*."""
  result = subprocess.run(
    ["git", "diff-index", "--quiet", "HEAD", "--"],
    cwd=path,
    capture_output=True,
  )
  if result.returncode != 0:
    raise GitError(
      "Uncommitted changes detected.\nCommit your changes or use --allow-uncommitted to override."
    )


def check_unpushed(path: Path) -> None:
  """Raise GitError if there are unpushed commits on the current branch."""
  # Get current branch
  branch_result = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  if branch_result.returncode != 0:
    return  # Cannot determine branch; skip check

  # Check for upstream
  upstream_result = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  if upstream_result.returncode != 0:
    return  # No upstream; skip check

  upstream = upstream_result.stdout.strip()
  count_result = subprocess.run(
    ["git", "rev-list", f"{upstream}..HEAD", "--count"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  if count_result.returncode != 0:
    return

  count = int(count_result.stdout.strip())
  if count > 0:
    raise GitError(
      f"{count} unpushed commit(s) detected.\n"
      "Push your changes or use --allow-unpushed to override."
    )


def get_metadata(path: Path) -> GitMetadata | None:
  """Extract git branch, HEAD SHA, and fetch remotes.  Returns None on failure."""
  branch_result = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  commit_result = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  if branch_result.returncode != 0 or commit_result.returncode != 0:
    return None

  remote_result = subprocess.run(
    ["git", "remote", "-v"],
    cwd=path,
    capture_output=True,
    text=True,
  )
  remotes: list[tuple[str, str]] = []
  for line in remote_result.stdout.splitlines():
    if "(fetch)" in line:
      parts = line.split()
      if len(parts) >= 2:
        remotes.append((parts[0], parts[1]))

  return GitMetadata(
    branch=branch_result.stdout.strip(),
    commit=commit_result.stdout.strip(),
    remotes=remotes,
  )
