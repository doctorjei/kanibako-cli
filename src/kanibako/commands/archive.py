"""kanibako archive: archive session data + git metadata to .txz."""

from __future__ import annotations

import argparse
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from kanibako.settings.config import config_file_path, load_config
from kanibako.errors import GitError
from kanibako.git import check_uncommitted, check_unpushed, get_metadata, is_git_repo
from kanibako.settings.paths import xdg, load_std_paths, resolve_any_project


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "archive",
        help="Archive project session data to .txz file",
        description="Archive project session data and git metadata to a .txz file.",
    )
    p.add_argument("path", nargs="?", default=None, help="Path to the project directory")
    p.add_argument("file", nargs="?", default=None, help="Output filename (default: auto-generated)")
    p.add_argument(
        "--all", action="store_true", dest="all_projects",
        help="Archive session data for every known project",
    )
    p.add_argument("--allow-uncommitted", action="store_true",
                    help="Allow archiving with uncommitted changes")
    p.add_argument("--allow-unpushed", action="store_true",
                    help="Allow archiving with unpushed commits")
    p.add_argument("--force", action="store_true", help="Skip all confirmation prompts")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    if args.all_projects:
        return _archive_all(std, config, args)

    if args.path is None:
        print("Error: specify a project path, or use --all", file=sys.stderr)
        return 1

    proj = resolve_any_project(std, config, project_dir=args.path, initialize=False)
    return _archive_one(std, config, proj, output_file=args.file, args=args)


def _archive_one(std, config, proj, *, output_file, args) -> int:
    """Archive session data for a single project."""
    if not proj.metadata_path.is_dir():
        print(f"Error: No session data found for project {proj.project_path}", file=sys.stderr)
        return 1

    # Generate default archive filename
    archive_file = output_file
    if not archive_file:
        label = proj.name or proj.project_path.name
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_file = f"kanibako-{label}-{timestamp}.txz"

    # Prepare metadata
    info_file = proj.metadata_path / "kanibako-archive-info.txt"
    lines = [
        f"Project path: {proj.project_path}",
        f"Project hash: {proj.project_hash}",
        f"Archive date: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    # Git checks (only if project path exists on disk)
    if proj.project_path.is_dir() and is_git_repo(proj.project_path):
        if not args.allow_uncommitted:
            try:
                check_uncommitted(proj.project_path)
            except GitError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        if not args.allow_unpushed:
            try:
                check_unpushed(proj.project_path)
            except GitError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        meta = get_metadata(proj.project_path)
        if meta:
            lines.append("Git repository: yes")
            lines.append(f"Branch: {meta.branch}")
            lines.append(f"Commit: {meta.commit}")
            lines.append("Remotes:")
            for name, url in meta.remotes:
                lines.append(f"  {name}: {url}")
    else:
        if proj.project_path.is_dir():
            print(
                f"Warning: No git repository detected in {proj.project_path}",
                file=sys.stderr,
            )
            print("Only kanibako session data will be archived.", file=sys.stderr)
        lines.append("")
        lines.append("Git repository: no")

    info_file.write_text("\n".join(lines) + "\n")

    # Create archive using Python tarfile
    print(f"Creating archive {archive_file}... ", end="", flush=True)
    try:
        with tarfile.open(archive_file, "w:xz") as tar:
            tar.add(
                str(proj.metadata_path),
                arcname=proj.project_hash,
            )
    except Exception as e:
        info_file.unlink(missing_ok=True)
        print(f"\nError: Failed to create archive: {e}", file=sys.stderr)
        return 1
    finally:
        info_file.unlink(missing_ok=True)

    print("done.")
    print(f"Archive created: {archive_file}")
    return 0


def _archive_all(std, config, args) -> int:
    """Archive session data for all known projects."""
    from kanibako.settings.paths import (
        WorksetSpec,
        iter_projects,
        iter_workset_projects,
        resolve_project,
        resolve_workset_project,
    )

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if not projects and not ws_data:
        print("No project session data found.")
        return 0

    total = len(projects)
    for _, _, project_list in ws_data:
        total += sum(1 for _, status in project_list if status != "no-data")

    print(f"Found {total} project(s) to archive:")
    for metadata_path, project_path in projects:
        label = str(project_path) if project_path else f"(unknown) {metadata_path.name[:8]}"
        print(f"  {label}")
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status != "no-data":
                print(f"  {ws_name}/{proj_name}")
    print()

    archived = 0
    failed = 0

    # Default-mode projects.
    for metadata_path, project_path in projects:
        if project_path:
            try:
                proj = resolve_project(
                    std, config, project_dir=str(project_path), initialize=False
                )
            except Exception:
                proj = _stub_project(metadata_path, project_path, std, config)
        else:
            proj = _stub_project(metadata_path, None, std, config)

        rc = _archive_one(std, config, proj, output_file=None, args=args)
        if rc == 0:
            archived += 1
        else:
            failed += 1

    # Workset projects.
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status == "no-data":
                continue
            try:
                proj = resolve_workset_project(
                    WorksetSpec.from_workset(ws), proj_name, std, config, initialize=False,
                )
            except Exception:
                failed += 1
                continue
            rc = _archive_one(std, config, proj, output_file=None, args=args)
            if rc == 0:
                archived += 1
            else:
                failed += 1

    print(f"\nArchived {archived} project(s).", end="")
    if failed:
        print(f" {failed} failed.", end="")
    print()
    return 1 if failed else 0


def _stub_project(metadata_path, project_path, std, config):
    """Create a minimal ProjectPaths stand-in for projects whose path is gone."""
    from kanibako.launch import box_resolve
    from kanibako.settings.paths import ProjectPaths, project_hash

    # P8a: name from box_resolve's identity (registry KEY / composed standalone
    # name).  A gone-path box that is STILL registered resolves by its (now
    # missing) workspace path — ``resolve_box_identity`` matches the persisted
    # registry entry.  Fall back to the box-dir leaf (the primary box name IS its
    # dir) when nothing resolves.  ``project_hash`` is a DEAD ``resolved:`` field
    # — recompute it from the (known) workspace, else the leaf fallback (as
    # before).  Replaces the transitional ``read_project_meta`` read.
    lookup = project_path if project_path is not None else metadata_path
    identity = box_resolve.resolve_box_identity(lookup, std, config)
    name = identity["name"] if identity is not None else metadata_path.name

    if project_path is not None:
        phash = project_hash(str(Path(project_path).resolve()))
    else:
        phash = metadata_path.name

    effective_path = project_path or Path(f"(unknown-{name or metadata_path.name})")
    return ProjectPaths(
        project_path=effective_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=metadata_path / "home",
        vault_ro_path=effective_path / "vault" / "ro",
        vault_rw_path=effective_path / "vault" / "rw",
        is_new=False,
        name=name,
    )
