"""kanibako code: open host VS Code attached to a running box.

Purely a launcher: it resolves a box, verifies the box is running, builds a
VS Code "attach to running container" URI pointing at the box's in-box
workspace, and launches the host ``code`` CLI.  It changes NO launch/box
behavior.
"""

from __future__ import annotations

import argparse
import binascii
import json
import shutil
import subprocess
import sys

from kanibako.config import config_file_path, load_config
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.paths import (
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings_resolve import GUEST_HOME
from kanibako.utils import container_name_for


def add_code_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "code",
        help="Open host VS Code attached to a running box",
        description=(
            "Open your host VS Code attached to a running kanibako box "
            "(Dev Containers: Attach to Running Container), opened at the "
            "box's workspace."
        ),
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project name or path (default: cwd)",
    )
    p.set_defaults(func=run_code)


def _attach_uri(container_name: str) -> str:
    """Build the VS Code ``vscode-remote://`` attach URI for *container_name*.

    The container is named by a hex-encoded JSON object
    (``{"containerName":"/<name>"}`` — note the leading slash), followed
    immediately by the in-box workspace path.
    """
    payload = json.dumps(
        {"containerName": f"/{container_name}"}, separators=(",", ":")
    )
    hex_name = binascii.hexlify(payload.encode()).decode()
    workspace_path = GUEST_HOME + "/workspace"
    return f"vscode-remote://attached-container+{hex_name}{workspace_path}"


def run_code(args: argparse.Namespace) -> int:
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )

    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker.",
            file=sys.stderr,
        )
        return 1

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    proj = resolve_box_target(std, config, project_dir, initialize=False)
    cname = container_name_for(proj)

    if not runtime.is_running(cname):
        name = proj.name or cname
        print(
            f"Error: box '{name}' is not running. "
            f"Start it first: kanibako start {name} --persistent",
            file=sys.stderr,
        )
        return 1

    code_bin = shutil.which("code")
    if code_bin is None:
        print(
            "Error: the VS Code 'code' CLI was not found on your PATH.\n"
            "  Install VS Code and add its 'code' command to PATH "
            "(Command Palette: 'Shell Command: Install code command in PATH').\n"
            "  You also need the Dev Containers extension, with "
            "'dev.containers.dockerPath' set to 'podman'.",
            file=sys.stderr,
        )
        return 1

    uri = _attach_uri(cname)
    name = proj.name or cname
    print(f"Opening VS Code attached to box '{name}'...")
    subprocess.run([code_bin, "--folder-uri", uri])
    return 0
