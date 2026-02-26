"""kanibako stop: stop running kanibako containers."""

from __future__ import annotations

import argparse
import sys

from kanibako.config import config_file_path, load_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.paths import xdg, load_std_paths, resolve_any_project
from kanibako.utils import container_name_for


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "stop",
        help="Stop a running kanibako container",
        description="Stop a running kanibako container for a project.",
    )
    p.add_argument(
        "path", nargs="?", default=None,
        help="Path to the project directory (default: cwd)",
    )
    p.add_argument(
        "--all", action="store_true", dest="all_containers",
        help="Stop all running kanibako containers",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.all_containers:
        return _stop_all(runtime)

    return _stop_one(runtime, project_dir=args.path)


def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int:
    """Stop the container for a single project."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    proj = resolve_any_project(std, config, project_dir, initialize=False)
    container_name = container_name_for(proj)

    lock_file = proj.metadata_path / ".kanibako.lock"

    if runtime.stop(container_name):
        print(f"Stopped {container_name}")
        # Clean up stopped container (persistent containers lack --rm)
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
    else:
        print(f"No running container found for this project ({container_name})")
        # Clean up stopped persistent container if it exists
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
            print(f"Removed stopped container: {container_name}")
        else:
            print("\nIf a stale lock file is blocking a new session, remove it manually:")
            print(f"  rm {lock_file}")

    return 0


def _stop_all(runtime: ContainerRuntime) -> int:
    """Stop all running kanibako containers."""
    containers = runtime.list_running()
    if not containers:
        print("No running kanibako containers found.")
        return 0

    stopped = 0
    for name, image, status in containers:
        if runtime.stop(name):
            print(f"Stopped {name}")
            # Clean up stopped container (persistent containers lack --rm)
            if runtime.container_exists(name):
                runtime.rm(name)
            stopped += 1
        else:
            print(f"Failed to stop {name}", file=sys.stderr)

    print(f"\nStopped {stopped} container(s).")
    return 0
