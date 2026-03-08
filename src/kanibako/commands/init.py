"""kanibako init: initialize projects in any mode."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config, write_project_config
from kanibako.paths import (
    xdg, load_std_paths, resolve_standalone_project, resolve_project,
)


def add_init_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "init",
        help="Initialize a kanibako project",
        description="Initialize a kanibako project in the current or given directory.",
    )
    p.add_argument(
        "path", nargs="?", default=None,
        help="Project directory (default: cwd). Created if it doesn't exist.",
    )
    p.add_argument(
        "--local", action="store_true",
        help="Use standalone mode (all state inside the project directory)",
    )
    p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for this project",
    )
    p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories (shared read-only and read-write mounts)",
    )
    p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
    )
    p.set_defaults(func=run_init)


def run_init(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    vault_enabled = not getattr(args, "no_vault", False)
    auth = "distinct" if getattr(args, "distinct_auth", False) else None
    project_dir = args.path

    # Create directory if it doesn't exist
    if project_dir is not None:
        target = Path(project_dir)
        if not target.exists():
            target.mkdir(parents=True)

    if args.local:
        proj = resolve_standalone_project(
            std, config, project_dir, initialize=True,
            vault_enabled=vault_enabled, auth=auth,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            vault_enabled=vault_enabled if not vault_enabled else None,
        )

    if not proj.is_new:
        print(
            f"Error: project already initialized in {proj.project_path}",
            file=sys.stderr,
        )
        return 1

    # Persist image setting
    image = args.image or config.container_image
    project_toml = proj.metadata_path / "project.toml"
    write_project_config(project_toml, image)

    # Write .gitignore for standalone projects only
    if args.local:
        _write_project_gitignore(proj.project_path)

    mode = "standalone" if args.local else "local"
    print(f"Initialized {mode} project in {proj.project_path}")
    return 0


_GITIGNORE_ENTRIES = [".kanibako/"]


def _write_project_gitignore(project_path: Path) -> None:
    """Append .kanibako/ to the project's root .gitignore."""
    gitignore = project_path / ".gitignore"
    existing = ""
    if gitignore.is_file():
        existing = gitignore.read_text()

    lines_to_add = [
        entry for entry in _GITIGNORE_ENTRIES
        if entry not in existing.splitlines()
    ]

    if not lines_to_add:
        return

    with open(gitignore, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        for line in lines_to_add:
            f.write(line + "\n")
