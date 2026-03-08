"""kanibako extract: restore session data from archive with validation."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.errors import UserCancelled
from kanibako.git import is_git_repo
from kanibako.paths import xdg, load_std_paths, resolve_any_project
from kanibako.utils import confirm_prompt


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "extract",
        help="Extract session data from archive",
        description="Extract session data from a .txz archive created by 'kanibako box archive'.",
    )
    p.add_argument("file", nargs="?", default=None, help="Archive file to extract from")
    p.add_argument("path", nargs="?", default=None, help="Path to the project directory")
    p.add_argument(
        "--name", default=None,
        help="Override project name for the extracted data",
    )
    p.add_argument(
        "--all", action="store_true", dest="all_archives",
        help="Extract all kanibako-*.txz archives in the current directory",
    )
    p.add_argument("--force", action="store_true", help="Skip all confirmation prompts")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    if args.all_archives:
        return _restore_all(std, config, args)

    if args.file is None:
        print("Error: specify an archive file, or use --all", file=sys.stderr)
        return 1

    return _restore_one(std, config, project_dir=args.path,
                        archive_file=Path(args.file), force=args.force)


def _restore_one(std, config, *, project_dir, archive_file, force) -> int:
    """Extract session data from a single archive."""
    if not archive_file.is_file():
        print(f"Error: Archive file not found: {archive_file}", file=sys.stderr)
        return 1

    proj = resolve_any_project(
        std, config,
        project_dir=str(project_dir) if project_dir else None,
        initialize=False,
    )

    temp_dir = tempfile.mkdtemp()
    try:
        try:
            with tarfile.open(str(archive_file), "r:xz") as tar:
                tar.extractall(temp_dir, filter="data")
        except (tarfile.TarError, OSError) as e:
            print(f"Error: Failed to extract archive: {e}", file=sys.stderr)
            return 1

        # Find the archive hash directory
        entries = list(Path(temp_dir).iterdir())
        if not entries:
            print("Error: Empty archive.", file=sys.stderr)
            return 1
        archive_hash_dir = entries[0]
        archive_hash = archive_hash_dir.name
        info_file = archive_hash_dir / "kanibako-archive-info.txt"

        if not info_file.is_file():
            print(
                "Error: Invalid archive format (missing kanibako-archive-info.txt)",
                file=sys.stderr,
            )
            return 1

        # Parse metadata
        info = _parse_info(info_file)
        archive_path = info.get("Project path", "")
        archive_basename = Path(archive_path).name if archive_path else ""
        current_basename = proj.project_path.name

        # Validate hash match
        hash_match = (
            archive_hash == proj.project_hash
            or archive_basename == current_basename
        )

        if not hash_match and not force:
            print("Warning: Project path mismatch")
            print()
            print(f"Archive from: {archive_path}")
            print(f"Restoring to: {proj.project_path}")
            print()
            try:
                confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
            except UserCancelled:
                print("Aborted.")
                return 2

        # Validate git state
        git_in_archive = info.get("Git repository", "") == "yes"
        if git_in_archive:
            rc = _validate_git_state(proj, info, force)
            if rc != 0:
                return rc

        # Restore session data
        print("Restoring session data... ", end="", flush=True)
        projects_base = std.data_path / "boxes"
        projects_base.mkdir(parents=True, exist_ok=True)

        if proj.metadata_path.exists():
            shutil.rmtree(proj.metadata_path)

        shutil.copytree(str(archive_hash_dir), str(proj.metadata_path))

        # Remove info file from restored data
        restored_info = proj.metadata_path / "kanibako-archive-info.txt"
        restored_info.unlink(missing_ok=True)

        print("done.")
        print(f"Session data restored to {proj.project_path}")
        return 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _peek_archive_info(archive_file: Path) -> dict[str, str] | None:
    """Extract archive to a temp dir and parse the info file."""
    temp_dir = tempfile.mkdtemp()
    try:
        try:
            with tarfile.open(str(archive_file), "r:xz") as tar:
                tar.extractall(temp_dir, filter="data")
        except (tarfile.TarError, OSError):
            return None
        entries = list(Path(temp_dir).iterdir())
        if not entries:
            return None
        info_file = entries[0] / "kanibako-archive-info.txt"
        if not info_file.is_file():
            return None
        info = _parse_info(info_file)
        info["_archive_hash"] = entries[0].name
        return info
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _restore_all(std, config, args) -> int:
    """Restore all kanibako-*.txz archives in the current directory."""
    import os

    scan_dir = Path(os.getcwd())
    archives = sorted(scan_dir.glob("kanibako-*.txz"))
    if not archives:
        print(f"No kanibako-*.txz archives found in {scan_dir}")
        return 0

    # Peek into each archive to get project path
    plan: list[tuple[Path, str]] = []
    for archive in archives:
        info = _peek_archive_info(archive)
        if info is None:
            print(f"  Skipping {archive.name} (invalid archive)", file=sys.stderr)
            continue
        project_path = info.get("Project path", "")
        if not project_path:
            print(f"  Skipping {archive.name} (no project path in metadata)", file=sys.stderr)
            continue
        plan.append((archive, project_path))

    if not plan:
        print("No valid archives found to restore.")
        return 0

    print(f"Found {len(plan)} archive(s) to restore:")
    for archive, project_path in plan:
        print(f"  {archive.name} → {project_path}")
    print()

    if not args.force:
        try:
            confirm_prompt(
                "Restore all listed archives? Existing session data will be overwritten.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    restored = 0
    failed = 0
    for archive, project_path in plan:
        print(f"\n--- {archive.name} → {project_path}")
        rc = _restore_one(
            std, config, project_dir=project_path,
            archive_file=archive, force=True,
        )
        if rc == 0:
            restored += 1
        else:
            failed += 1

    print(f"\nRestored {restored} archive(s).", end="")
    if failed:
        print(f" {failed} failed.", end="")
    print()
    return 1 if failed else 0


def _parse_info(info_file: Path) -> dict[str, str]:
    """Parse kanibako-archive-info.txt into a dict."""
    result: dict[str, str] = {}
    for line in info_file.read_text().splitlines():
        if ": " in line and not line.startswith("  "):
            key, _, value = line.partition(": ")
            result[key.strip()] = value.strip()
    return result


def _validate_git_state(proj, info: dict[str, str], force: bool) -> int:
    """Validate git state between archive and workspace. Returns 0 to continue."""
    if not is_git_repo(proj.project_path):
        if not force:
            print(
                "Warning: Archive came from a git repository, "
                "but current workspace is not a git repo."
            )
            print()
            for key in ("Branch", "Commit"):
                if key in info:
                    print(f"  {key}: {info[key]}")
            print()
            try:
                confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
            except UserCancelled:
                print("Aborted.")
                return 2
        return 0

    archive_commit = info.get("Commit", "")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=proj.project_path,
        capture_output=True,
        text=True,
    )
    current_commit = result.stdout.strip() if result.returncode == 0 else ""

    if archive_commit != current_commit and not force:
        print("Warning: Git state mismatch")
        print()
        print("Archive from:")
        for key in ("Branch", "Commit"):
            if key in info:
                print(f"  {key}: {info[key]}")
        print()
        print("Current workspace:")
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=proj.project_path,
            capture_output=True,
            text=True,
        )
        current_branch = (
            branch_result.stdout.strip()
            if branch_result.returncode == 0
            else "unknown"
        )
        print(f"  Branch: {current_branch}")
        print(f"  Commit: {current_commit}")
        print()
        try:
            confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
        except UserCancelled:
            print("Aborted.")
            return 2

    return 0
