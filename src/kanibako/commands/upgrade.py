"""kanibako upgrade: update kanibako itself from git."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "upgrade",
        help="Upgrade kanibako to the latest version",
        description="Upgrade kanibako by pulling the latest changes from git.",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Check for updates without installing",
    )
    p.set_defaults(func=run)


def _get_repo_dir() -> Path | None:
    """Find the kanibako git repository directory."""
    # Start from this file's location and walk up to find .git — a directory in a
    # normal checkout, a ``gitdir:`` FILE in a git worktree.
    current = Path(__file__).resolve().parent
    for _ in range(5):  # Don't walk up forever
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _get_current_commit(repo: Path) -> str | None:
    """Get the current commit hash."""
    result = _git("rev-parse", "HEAD", cwd=repo)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_remote_commit(repo: Path) -> str | None:
    """Get the latest remote commit hash after fetching."""
    # Fetch latest from remote
    result = _git("fetch", cwd=repo)
    if result.returncode != 0:
        return None

    # Get the upstream branch name
    result = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=repo)
    if result.returncode != 0:
        # No upstream configured, try origin/main or origin/master
        for branch in ("origin/main", "origin/master"):
            result = _git("rev-parse", branch, cwd=repo)
            if result.returncode == 0:
                return result.stdout.strip()
        return None

    upstream = result.stdout.strip()
    result = _git("rev-parse", upstream, cwd=repo)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _get_commit_count_behind(repo: Path) -> int | None:
    """Get number of commits behind upstream."""
    result = _git("rev-list", "--count", "HEAD..@{u}", cwd=repo)
    if result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            pass
    return None


def run(args: argparse.Namespace) -> int:
    repo = _get_repo_dir()
    if repo is None:
        print("Error: Could not find kanibako git repository.", file=sys.stderr)
        print("kanibako upgrade only works for git-based installations.", file=sys.stderr)
        return 1

    # Check for uncommitted changes
    result = _git("status", "--porcelain", cwd=repo)
    if result.returncode != 0:
        print("Error: Failed to check git status.", file=sys.stderr)
        return 1

    has_changes = bool(result.stdout.strip())

    current = _get_current_commit(repo)
    if current is None:
        print("Error: Failed to get current commit.", file=sys.stderr)
        return 1

    print(f"Repository: {repo}")
    print(f"Current: {current[:8]}")

    # Fetch and check for updates
    print("Checking for updates...", end=" ", flush=True)
    remote = _get_remote_commit(repo)
    if remote is None:
        print("failed")
        print("Error: Failed to fetch from remote.", file=sys.stderr)
        return 1

    if current == remote:
        print("up to date")
        return 0

    behind = _get_commit_count_behind(repo)
    behind_str = f"{behind} commit(s)" if behind else "updates"
    print(f"{behind_str} available")
    print(f"Latest: {remote[:8]}")

    if args.check:
        return 0

    # Actually upgrade
    if has_changes:
        print()
        print("Warning: You have uncommitted changes in the repository.", file=sys.stderr)
        print("Stash or commit them before upgrading.", file=sys.stderr)
        return 1

    print()
    print("Pulling latest changes...")
    result = _git("pull", "--ff-only", cwd=repo)
    if result.returncode != 0:
        print("Error: git pull failed.", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1

    print("Upgraded successfully.")

    # Check if pyproject.toml changed (might need reinstall)
    result = _git("diff", "--name-only", f"{current}..HEAD", cwd=repo)
    changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

    if "pyproject.toml" in changed_files:
        print()
        print("Note: pyproject.toml changed. You may need to reinstall:")
        print(f"  pip install -e {repo}")

    return 0
