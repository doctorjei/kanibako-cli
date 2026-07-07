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
from kanibako.log import get_logger
from kanibako.paths import (
    xdg,
    load_std_paths,
    resolve_box_target,
)
from kanibako.settings_resolve import GUEST_HOME
from kanibako.utils import container_name_for
from kanibako.vscode_config import (
    attached_container_config_path,
    seed_attached_container_config,
)


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

    # Best-effort: seed the attached-container config so VS Code opens the box's
    # workspace and auto-installs the box agent's editor extension on attach.
    # NEVER blocks the launch — a failure here (unresolved agent, unwritable path)
    # is logged and swallowed so `code` still opens (Phase-1 zero-launch-delta).
    _seed_attached_config(runtime, std, proj, cname)

    uri = _attach_uri(cname)
    name = proj.name or cname
    print(f"Opening VS Code attached to box '{name}'...")
    subprocess.run([code_bin, "--folder-uri", uri])
    return 0


def _extension_for_agent(agent_name: str, proj) -> str | None:
    """Resolve *agent_name*'s ``descriptor.vscode_extension`` (or ``None``).

    ``agent_name`` is a NODE-name; the plugin/target is keyed by its HARNESS
    (``harness_of``), exactly as ``stop.py`` / ``start.py`` resolve a stamped box.
    A descriptor-less target (the no-agent shell) or an unset extension → ``None``.
    """
    from kanibako.agent_ref import harness_of
    from kanibako.targets import resolve_target

    target = resolve_target(harness_of(agent_name), proj.project_path)
    desc = target.descriptor
    return desc.vscode_extension if desc is not None else None


def _resolve_box_vscode_extension(runtime, std, proj, container_name: str) -> str | None:
    """Best-effort: the RUNNING box agent's ``descriptor.vscode_extension``.

    STAMP-FIRST, mirroring ``stop.py._writeback_on_stop`` and ``start.py``'s
    reattach fast-source: a running box's authoritative agent is its
    ``KANIBAKO_AGENT`` launch stamp (``runtime.inspect_env``), NOT the create-time
    cascade.  Using the stamp avoids two cascade mis-resolutions on a running box:
    (1) 2+ installed agents + no system default → the cascade RAISES (seed nothing
    for a live claude box); (2) a system default that has since diverged from the
    box's actually-running agent → seed the WRONG agent's extension.

    Falls back to the ``resolve_agent`` create-cascade ONLY for pre-stamp (older)
    boxes with no ``KANIBAKO_AGENT`` env.  Swallows every failure (unresolved
    agent, descriptor-less/no-agent shell, unset extension) → ``None``.  NEVER
    raises.
    """
    try:
        stamp = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
        if stamp:
            return _extension_for_agent(stamp, proj)

        # Pre-stamp (older) box: fall back to the create-time resolve_agent cascade.
        from kanibako.config import (
            BOX_META_FILE,
            load_merged_config,
            resolve_agent,
        )
        from kanibako.paths import workset_settings_path

        merged = load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            proj.metadata_path / BOX_META_FILE,
            workset_path=workset_settings_path(proj.group),
        )
        agent_name = resolve_agent(
            explicit_agent=None,
            box_agent_name=merged.box_agent_name,
            workset_agent=None,
            system_default_path=std.settings,
            project_path=proj.project_path,
        )
        return _extension_for_agent(agent_name, proj)
    except Exception:
        get_logger("code").debug(
            "could not resolve box agent VS Code extension; seeding none",
            exc_info=True,
        )
        return None


def _resolve_box_image(runtime, proj, container_name: str) -> str | None:
    """Best-effort: the image reference keying the box's attached-container config.

    The attached config is IMAGE-shared, so we must key it by the box's image.
    STAMP-FIRST-style, mirroring ``_resolve_box_vscode_extension``: prefer the
    RUNNING container's ACTUAL image (``runtime.container_image``) — the
    authoritative source for a live box.  Falls back to the box's configured
    ``box_image`` (the create-time merged config, which itself defaults to the
    packaged ``ghcr.io/doctorjei/kanibako-oci:latest``).  Returns ``None`` only
    if every source fails — callers then SKIP seeding rather than crash.
    """
    image = runtime.container_image(container_name)
    if image:
        return image
    try:
        from kanibako.config import BOX_META_FILE, load_merged_config
        from kanibako.paths import workset_settings_path

        merged = load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            proj.metadata_path / BOX_META_FILE,
            workset_path=workset_settings_path(proj.group),
        )
        return merged.box_image or None
    except Exception:
        get_logger("code").debug(
            "could not resolve box image; skipping attached-config seed",
            exc_info=True,
        )
        return None


def _seed_attached_config(runtime, std, proj, container_name: str) -> None:
    """Best-effort seed of the box's attached-container config. NEVER raises.

    UNION-MERGES the box workspace + the box agent's editor extension into the
    IMAGE-keyed devcontainer.json-subset VS Code reads on attach (preserving
    everything VS Code/the user already wrote).  Any failure — image resolution,
    agent resolution, filesystem — is logged at debug and swallowed so the
    `code` launch is unaffected (Phase-1 zero-launch-delta).
    """
    try:
        image_ref = _resolve_box_image(runtime, proj, container_name)
        if image_ref is None:
            return  # can't key the image-shared config → skip, never crash
        extension = _resolve_box_vscode_extension(runtime, std, proj, container_name)
        path = attached_container_config_path(
            image_ref, xdg("XDG_CONFIG_HOME", ".config"),
        )
        seed_attached_container_config(
            path,
            workspace_folder=GUEST_HOME + "/workspace",
            extension=extension,
        )
    except Exception:
        get_logger("code").debug(
            "failed to seed VS Code attached-container config", exc_info=True,
        )
