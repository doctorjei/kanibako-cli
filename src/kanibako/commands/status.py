"""kanibako status: show per-project status information."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.paths import (
    ProjectMode,
    xdg,
    load_std_paths,
    resolve_any_project,
)
from kanibako.targets import resolve_target
from kanibako.utils import container_name_for, short_hash


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "status",
        help="Show project status and configuration",
        description="Show per-project status: mode, paths, container state, image, and credentials.",
    )
    p.add_argument(
        "-p", "--project", default=None,
        help="Use DIR as the project directory (default: cwd)",
    )
    p.set_defaults(func=run_status)


def _format_credential_age(creds_path: Path) -> str:
    """Return a human-readable age string for a credentials file, or 'n/a'."""
    if not creds_path.is_file():
        return "n/a (no credentials file)"
    try:
        mtime = creds_path.stat().st_mtime
    except OSError:
        return "n/a (unreadable)"
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        age = f"{total_seconds}s ago"
    elif total_seconds < 3600:
        age = f"{total_seconds // 60}m ago"
    elif total_seconds < 86400:
        age = f"{total_seconds // 3600}h ago"
    else:
        age = f"{total_seconds // 86400}d ago"
    return f"{age} ({dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"


def _check_container_running(proj) -> tuple[bool, str]:
    """Check if a kanibako container is running for this project.

    Accepts a ``ProjectPaths`` (or duck-typed equivalent).
    Returns ``(is_running, detail_string)``.
    """
    container_name = container_name_for(proj)
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        return False, "unknown (no container runtime)"
    containers = runtime.list_running()
    for name, image, status in containers:
        if name == container_name:
            return True, f"running ({container_name}: {image})"
    # Check for stopped persistent container
    if runtime.container_exists(container_name):
        return False, f"stopped persistent ({container_name})"
    return False, f"not running ({container_name})"


def run_status(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    try:
        std = load_std_paths(config)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_dir = getattr(args, "project", None)
    raw = project_dir or os.getcwd()
    raw_dir = Path(raw).resolve()

    if not raw_dir.is_dir():
        print(f"Error: directory does not exist: {raw_dir}", file=sys.stderr)
        return 1

    # Detect mode and resolve project paths (without initializing).
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check if the project has been initialized (has metadata on disk).
    has_data = proj.metadata_path.is_dir()

    if not has_data:
        print(f"No project data found for: {proj.project_path}")
        print()
        if proj.mode == ProjectMode.account_centric:
            print("This directory has not been used with kanibako yet.")
            print("Start a session with 'kanibako start', or initialize with:")
            print("  kanibako init --local   (decentralized mode)")
        else:
            print("This directory has not been initialized.")
        return 1

    # Load merged config for image info.
    project_toml = proj.metadata_path / "project.toml"
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
    )

    # Gather status info.
    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_held = lock_file.exists()

    container_running, container_detail = _check_container_running(proj)

    # Resolve target for credential check path
    try:
        target = resolve_target(merged.target_name or None)
        creds_file = target.credential_check_path(proj.shell_path)
    except (KeyError, Exception):
        creds_file = None
    cred_age = _format_credential_age(creds_file) if creds_file else "n/a (no target)"

    # Display mode name with dashes for readability.
    mode_display = proj.mode.value.replace("_", "-")

    # Format output.
    rows = [
        ("Name", proj.name or "(unnamed)"),
        ("Mode", mode_display),
        ("Project", str(proj.project_path)),
        ("Hash", short_hash(proj.project_hash)),
        ("Metadata", str(proj.metadata_path)),
        ("Shell", str(proj.shell_path)),
        ("Vault RO", str(proj.vault_ro_path)),
        ("Vault RW", str(proj.vault_rw_path)),
        ("Image", merged.container_image),
        ("Lock", "ACTIVE" if lock_held else "none"),
        ("Container", container_detail),
        ("Credentials", cred_age),
    ]

    # Compute alignment width from longest label.
    label_width = max(len(label) for label, _ in rows) + 1  # +1 for colon
    for label, value in rows:
        print(f"  {label + ':':<{label_width}}  {value}")

    return 0
