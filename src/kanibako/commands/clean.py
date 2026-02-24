"""kanibako purge: remove project session data."""

from __future__ import annotations

import argparse
import shutil
import sys

from kanibako.config import load_config
from kanibako.errors import UserCancelled
from kanibako.paths import (
    _remove_human_vault_symlink,
    _remove_project_vault_symlink,
    load_std_paths,
    resolve_any_project,
)
from kanibako.utils import confirm_prompt, short_hash


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "purge",
        help="Remove all project session data",
        description="Remove all project session data (credentials, conversation history).",
    )
    p.add_argument("path", nargs="?", default=None, help="Path to the project directory")
    p.add_argument(
        "--all", action="store_true", dest="all_projects",
        help="Purge session data for every known project",
    )
    p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from kanibako.paths import xdg
    from kanibako.config import config_file_path
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    if args.all_projects:
        return _purge_all(std, config, force=args.force)

    if args.path is None:
        print("Error: specify a project path, or use --all", file=sys.stderr)
        return 1

    return _purge_one(std, config, args.path, force=args.force)


def _purge_one(std, config, path: str, *, force: bool) -> int:
    """Purge session data for a single project."""
    proj = resolve_any_project(std, config, project_dir=path, initialize=False)

    if not proj.metadata_path.is_dir():
        print(f"No session data found for project {proj.project_path}")
        return 0

    if not force:
        h8 = short_hash(proj.project_hash)
        print(f"Project: {proj.project_path}")
        print(f"Hash: {h8}")
        print()
        try:
            confirm_prompt(
                "Delete all session data for this project? This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    # Clean up vault symlinks before removing data.
    vault_dir = std.data_path / config.paths_vault
    _remove_human_vault_symlink(vault_dir, proj.metadata_path / "vault")
    _remove_project_vault_symlink(proj.project_path)

    print("Removing session data... ", end="", flush=True)
    shutil.rmtree(proj.metadata_path)
    print("done.")
    print(f"Session data removed for {proj.project_path}")
    return 0


def _purge_all(std, config, *, force: bool) -> int:
    """Purge session data for all known projects."""
    from kanibako.paths import iter_projects, iter_workset_projects

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if not projects and not ws_data:
        print("No project session data found.")
        return 0

    total = len(projects)
    for _, _, project_list in ws_data:
        total += sum(1 for _, status in project_list if status != "no-data")

    print(f"Found {total} project(s):")
    for metadata_path, project_path in projects:
        h8 = short_hash(metadata_path.name)
        label = str(project_path) if project_path else f"(unknown) {h8}"
        print(f"  {label}")
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status != "no-data":
                print(f"  {ws_name}/{proj_name}")
    print()

    if not force:
        try:
            confirm_prompt(
                "Delete ALL session data for every project listed above? "
                "This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    removed = 0

    # Account-centric projects.
    vault_dir = std.data_path / config.paths_vault
    for metadata_path, project_path in projects:
        # Clean up vault symlinks before removing data.
        _remove_human_vault_symlink(vault_dir, metadata_path / "vault")
        if project_path is not None:
            _remove_project_vault_symlink(project_path)
        label = str(project_path) if project_path else short_hash(metadata_path.name)
        print(f"Removing {label}... ", end="", flush=True)
        shutil.rmtree(metadata_path)
        print("done.")
        removed += 1

    # Workset projects.
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status == "no-data":
                continue
            project_dir = ws.projects_dir / proj_name
            if project_dir.is_dir():
                label = f"{ws_name}/{proj_name}"
                print(f"Removing {label}... ", end="", flush=True)
                shutil.rmtree(project_dir)
                print("done.")
                removed += 1

    print(f"\nPurged session data for {removed} project(s).")
    return 0
