"""kanibako image: list built-in/local/remote container images."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.containerfiles import get_containerfile, list_containerfile_suffixes
from kanibako.errors import ContainerError
from kanibako.paths import xdg, load_std_paths, resolve_project


# Descriptions for known Containerfile variants.
_VARIANT_DESCRIPTIONS = {
    "base": "Python, nano, git, jq, ssh, gh, archives",
    "systems": "C/C++, Rust, assemblers, QEMU, debuggers",
    "jvm": "Java, Kotlin, Maven",
    "android": "JVM + Gradle, Android SDK",
    "ndk": "Android + systems toolchain",
    "dotnet": ".NET SDK 8.0",
    "behemoth": "All toolchains combined",
    "host": "Kanibako host environment with rootless podman",
    "host-claude": "Host environment + Claude Code plugin",
}


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "image",
        help="List or rebuild container images",
        description="List available container images or rebuild them.",
    )
    image_sub = p.add_subparsers(dest="image_command", metavar="COMMAND")

    # kanibako image list (default behavior)
    list_p = image_sub.add_parser(
        "list",
        help="List available container images (default)",
        description="List available container images (built-in variants, local, and remote).",
    )
    list_p.add_argument(
        "-p", "--project", default=None, help="Show current image for a specific project"
    )
    list_p.set_defaults(func=run_list)

    # kanibako image rebuild
    rebuild_p = image_sub.add_parser(
        "rebuild",
        help="Update container image(s) from registry (or rebuild locally)",
        description=(
            "Pull the latest image from the registry (default), or rebuild\n"
            "locally from Containerfiles with --local."
        ),
    )
    rebuild_p.add_argument(
        "image", nargs="?", default=None,
        help="Image to update (default: current configured image)",
    )
    rebuild_p.add_argument(
        "--all", action="store_true", dest="all_images",
        help="Update all local kanibako images",
    )
    rebuild_p.add_argument(
        "--local", action="store_true", dest="local_build",
        help="Build from local Containerfiles instead of pulling from registry",
    )
    rebuild_p.set_defaults(func=run_rebuild)

    # Default to list if no subcommand given
    p.set_defaults(func=run_list, project=None)


def run_list(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    proj = resolve_project(std, config, project_dir=args.project, initialize=False)

    # Merged config for current image display
    project_toml = proj.metadata_path / "project.toml"
    merged = load_merged_config(config_file, project_toml)

    # ---- Built-in Variants ----
    containers_dir = std.data_path / "containers"
    variants = list_containerfile_suffixes(containers_dir)
    if variants:
        print("Built-in image variants:")
        for variant in variants:
            desc = _VARIANT_DESCRIPTIONS.get(variant, "(no description)")
            print(f"  {variant:<12} {desc}")
    else:
        print("Built-in image variants: (none installed)")

    print()

    # ---- Local Images ----
    try:
        runtime = ContainerRuntime()
        print("Local images:")
        images = runtime.list_local_images()
        if images:
            for repo, size in images:
                print(f"  {repo:<50} {size}")
        else:
            print("  (none)")
    except ContainerError:
        print("Local images: (no container runtime found)")

    print()

    # ---- Remote Registry Images ----
    image = merged.container_image
    owner = _extract_ghcr_owner(image)

    print("Remote registry images:")
    if owner:
        _list_remote_packages(owner)
    elif image:
        print(f"  (registry owner not detected from image: {image})")
    else:
        print("  (image not configured)")

    print()

    # ---- Current Image ----
    print(f"Current image: {merged.container_image}")
    return 0


def _extract_ghcr_owner(image: str) -> str | None:
    """Extract GitHub owner from ghcr.io/<owner>/... image path."""
    if not image.startswith("ghcr.io/"):
        return None
    remainder = image[len("ghcr.io/"):]
    return remainder.split("/")[0] if "/" in remainder else None


def _list_remote_packages(owner: str) -> None:
    """Query GitHub API for the owner's kanibako container packages."""
    url = f"https://api.github.com/users/{owner}/packages?package_type=container"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kanibako"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        print("  (could not reach GitHub API)")
        return

    packages = [pkg["name"] for pkg in data if "kanibako" in pkg.get("name", "").lower()]
    if packages:
        for pkg in packages:
            print(f"  ghcr.io/{owner}/{pkg}")
    else:
        print(f"  (no kanibako packages found for {owner})")


def _extract_registry_prefix(image: str) -> str | None:
    """Extract ``registry/owner`` prefix from a fully qualified image name.

    >>> _extract_registry_prefix("ghcr.io/doctorjei/kanibako-base:latest")
    'ghcr.io/doctorjei'
    """
    # Expect at least registry/owner/name
    parts = image.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:-1])
    return None


# Known shorthand suffixes that map to kanibako-<suffix> images.
_KNOWN_SUFFIXES = {"base", "systems", "jvm", "android", "ndk", "dotnet", "behemoth"}


def resolve_image_name(name: str, configured_image: str) -> str:
    """Expand a shorthand image name to a fully qualified image reference.

    - If *name* contains ``/``, it is already qualified — returned as-is.
    - If *name* is a known suffix (``base``, ``systems``, …), expand to
      ``{prefix}/kanibako-{name}:latest``.
    - If *name* starts with ``kanibako-``, expand to ``{prefix}/{name}:latest``.
    - Otherwise return *name* unchanged.
    """
    if "/" in name:
        return name

    prefix = _extract_registry_prefix(configured_image)
    if prefix is None:
        return name

    if name in _KNOWN_SUFFIXES:
        return f"{prefix}/kanibako-{name}:latest"

    if name.startswith("kanibako-"):
        return f"{prefix}/{name}:latest"

    return name


def run_rebuild(args: argparse.Namespace) -> int:
    """Update container image(s): pull from registry (default) or build locally."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    containers_dir = std.data_path / "containers"

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    local_build = getattr(args, "local_build", False)

    if local_build and not containers_dir.is_dir():
        print(f"Error: containers directory not found: {containers_dir}", file=sys.stderr)
        print("Run 'kanibako setup' first.", file=sys.stderr)
        return 1

    if args.all_images:
        return _update_all(runtime, containers_dir, local_build=local_build)

    # Determine which image to update
    merged = load_merged_config(config_file, None)
    image = args.image
    if image is None:
        image = merged.container_image
    else:
        image = resolve_image_name(image, merged.container_image)

    return _update_one(runtime, image, containers_dir, local_build=local_build)


def _pull_one(runtime: ContainerRuntime, image: str) -> int:
    """Pull a single image from the registry."""
    print(f"Pulling {image}...")
    print()
    if runtime.pull(image, quiet=False):
        print()
        print(f"Successfully pulled {image}")
        return 0
    else:
        print()
        print(f"Failed to pull {image}", file=sys.stderr)
        return 1


def _build_one(runtime: ContainerRuntime, image: str, containers_dir: Path) -> int:
    """Build a single image locally from its Containerfile."""
    suffix = runtime.guess_containerfile(image)
    if suffix is None:
        print(f"Error: cannot determine Containerfile for image: {image}", file=sys.stderr)
        print("Known patterns: " + ", ".join(
            f"{p} -> Containerfile.{s}"
            for p, s in sorted(set(
                (p, runtime.guess_containerfile(p))
                for p in ["kanibako-base", "kanibako-systems", "kanibako-jvm",
                          "kanibako-android", "kanibako-ndk", "kanibako-dotnet",
                          "kanibako-behemoth"]
            ))
        ), file=sys.stderr)
        return 1

    containerfile = get_containerfile(suffix, containers_dir)
    if containerfile is None:
        print(f"Error: Containerfile not found for variant: {suffix}", file=sys.stderr)
        return 1

    print(f"Building {image} from Containerfile.{suffix}...")
    print()
    rc = runtime.rebuild(image, containerfile, containerfile.parent)
    if rc == 0:
        print()
        print(f"Successfully built {image}")
    else:
        print()
        print(f"Build failed with exit code {rc}", file=sys.stderr)
    return rc


def _update_one(
    runtime: ContainerRuntime, image: str, containers_dir: Path, *, local_build: bool
) -> int:
    """Update a single image: pull from registry or build locally."""
    if local_build:
        return _build_one(runtime, image, containers_dir)
    return _pull_one(runtime, image)


def _update_all(
    runtime: ContainerRuntime, containers_dir: Path, *, local_build: bool
) -> int:
    """Update all local kanibako images."""
    images = runtime.list_local_images()
    if not images:
        print("No local kanibako images to update.")
        return 0

    failed = 0
    for repo, _size in images:
        print(f"\n{'=' * 60}")
        print(f"Updating {repo}")
        print('=' * 60)
        rc = _update_one(runtime, repo, containers_dir, local_build=local_build)
        if rc != 0:
            failed += 1

    print()
    if failed:
        print(f"Updated {len(images) - failed}/{len(images)} images ({failed} failed)")
        return 1
    else:
        print(f"Updated {len(images)} image(s) successfully.")
        return 0
