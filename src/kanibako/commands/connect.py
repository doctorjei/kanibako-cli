"""kanibako connect: persistent session entry point for remote access."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.names import read_names, resolve_name, resolve_qualified_name
from kanibako.paths import xdg, load_std_paths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "connect",
        help="Connect to a persistent project session",
        description=(
            "Connect to a persistent project session. Creates a detached\n"
            "container with tmux if not running, or reattaches if already\n"
            "running. Designed for remote access — no cd required.\n"
            "\n"
            "examples:\n"
            "  kanibako connect myproject          # by project name\n"
            "  kanibako connect client/webapp      # workset/project\n"
            "  kanibako connect /home/user/myapp   # by path\n"
            "  kanibako connect myproject -N       # new conversation\n"
            "  kanibako connect --list             # list available projects"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project name, workset/project, or filesystem path",
    )
    p.add_argument(
        "-i", "--image", default=None,
        help="Use IMAGE as the container image for this run",
    )
    p.add_argument(
        "-N", "--new", action="store_true",
        help="Start a new conversation (skip default --continue)",
    )
    p.add_argument(
        "-S", "--safe", action="store_true",
        help="Run without --dangerously-skip-permissions",
    )
    p.add_argument(
        "--list", action="store_true", dest="list_projects",
        help="List available projects and their status",
    )
    p.set_defaults(func=run_connect)


def run_connect(args: argparse.Namespace) -> int:
    if args.list_projects:
        return _list_projects()

    if args.project is None:
        print(
            "Error: project name or path is required.\n"
            "Run 'kanibako connect --list' to see available projects.",
            file=sys.stderr,
        )
        return 1

    project_dir = _resolve_project_arg(args.project)
    if project_dir is None:
        return 1

    from kanibako.commands.start import _run_container
    return _run_container(
        project_dir=project_dir,
        entrypoint=None,
        image_override=getattr(args, "image", None),
        new_session=getattr(args, "new", False),
        safe_mode=getattr(args, "safe", False),
        resume_mode=False,
        extra_args=[],
        persistent=True,
    )


def _resolve_project_arg(arg: str) -> str | None:
    """Resolve a project argument to a filesystem path.

    Resolution order:
    1. If contains ``/`` and doesn't start with ``/`` or ``.``:
       parse as ``workset/project`` qualified name
    2. Try ``resolve_name()`` (bare name, context-aware)
    3. Fall back to treating as a filesystem path

    Returns the resolved path string, or None on failure (error printed).
    """
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # 1. Qualified name (workset/project) — contains / but isn't a path
    if "/" in arg and not arg.startswith("/") and not arg.startswith("."):
        try:
            path, _ws_name = resolve_qualified_name(std.data_path, arg)
            return path
        except ProjectError:
            pass  # Fall through to bare name / path

    # 2. Bare name lookup
    try:
        path, _kind = resolve_name(std.data_path, arg, cwd=Path.cwd())
        return path
    except ProjectError:
        pass

    # 3. Filesystem path
    candidate = Path(arg).resolve()
    if candidate.is_dir():
        return str(candidate)

    print(
        f"Error: '{arg}' is not a known project name or valid path.\n"
        f"Run 'kanibako connect --list' to see available projects.",
        file=sys.stderr,
    )
    return None


def _list_projects() -> int:
    """List available projects with running status."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    names = read_names(std.data_path)
    projects = names.get("projects", {})
    worksets = names.get("worksets", {})

    if not projects and not worksets:
        print("No projects registered.")
        return 0

    try:
        runtime = ContainerRuntime()
        running = {name for name, _img, _st in runtime.list_running()}
    except ContainerError:
        running = set()

    rows: list[tuple[str, str, str]] = []

    for name, path in sorted(projects.items()):
        cname = f"kanibako-{name}"
        status = "running" if cname in running else ""
        rows.append((name, path, status))

    for ws_name, ws_path in sorted(worksets.items()):
        cname = f"kanibako-{ws_name}"
        status = "running" if cname in running else ""
        rows.append((f"{ws_name} (workset)", ws_path, status))

    if not rows:
        print("No projects registered.")
        return 0

    # Compute column widths
    name_w = max(len(r[0]) for r in rows)
    path_w = max(len(r[1]) for r in rows)

    header = f"  {'NAME':<{name_w}}  {'PATH':<{path_w}}  STATUS"
    print(header)
    print(f"  {'-' * name_w}  {'-' * path_w}  ------")
    for name, path, status in rows:
        print(f"  {name:<{name_w}}  {path:<{path_w}}  {status}")

    return 0
