"""kanibako image: manage container images (list, create, info, rm, rebuild)."""

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
from kanibako.paths import xdg, load_std_paths
from kanibako.templates_image import template_image_name


# Descriptions for known Containerfile variants.
_VARIANT_DESCRIPTIONS = {
    "kanibako": "Base agent container (droste tier selected at build time)",
    "jvm": "JVM template (Java, Kotlin, Maven)",
    "systems": "Systems template (C/C++, Rust, cross-compilation)",
}

_TEMPLATE_PREFIX = "kanibako-template-"


def _confirm(prompt: str) -> bool:
    """Prompt the user for yes/no confirmation. Returns True on 'y'."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "image",
        help="Manage container images",
        description="Create, list, inspect, remove, or rebuild container images.",
    )
    image_sub = p.add_subparsers(dest="image_command", metavar="COMMAND")

    # kanibako image create
    create_p = image_sub.add_parser(
        "create",
        help="Create a new template image from a base image",
    )
    create_p.add_argument("name", help="Template name (e.g. jvm, systems)")
    create_p.add_argument(
        "--base", default="kanibako-oci",
        help="Base image to start from (default: kanibako-oci)",
    )
    commit_group = create_p.add_mutually_exclusive_group()
    commit_group.add_argument(
        "--always-commit", action="store_true",
        help="Commit template even if the container exits with an error",
    )
    commit_group.add_argument(
        "--no-commit-on-error", action="store_true",
        help="Skip commit if the container exits with an error",
    )
    create_p.set_defaults(func=run_create)

    # kanibako image list (default behavior)
    list_p = image_sub.add_parser(
        "list",
        help="List available container images (default)",
        description="List available container images (built-in variants, local, and remote).",
    )
    list_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Print only image names, one per line",
    )
    list_p.set_defaults(func=run_list)

    # kanibako image info / inspect
    info_p = image_sub.add_parser(
        "info", aliases=["inspect"],
        help="Show details about a container image",
    )
    info_p.add_argument("image", help="Image name or shorthand")
    info_p.set_defaults(func=run_info)

    # kanibako image rm / delete
    rm_p = image_sub.add_parser(
        "rm", aliases=["delete"],
        help="Remove a local container image",
    )
    rm_p.add_argument("image", help="Image name or shorthand")
    rm_p.add_argument(
        "--force", "-f", action="store_true",
        help="Remove without confirmation",
    )
    rm_p.set_defaults(func=run_rm)

    # kanibako image rebuild
    rebuild_p = image_sub.add_parser(
        "rebuild",
        help="Update container image(s) (pull or rebuild locally)",
        description=(
            "Pull the latest image from the registry, or rebuild locally\n"
            "if a matching Containerfile is found."
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
    rebuild_p.set_defaults(func=run_rebuild)

    # Default to list if no subcommand given
    p.set_defaults(func=run_list, quiet=False)


def run_create(args: argparse.Namespace) -> int:
    """Create a template: run interactive container, commit on exit."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    base = args.base
    name = args.name
    try:
        image_name = template_image_name(name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    container_name = f"kanibako-template-build-{name}"

    print(f"Starting interactive container from {base}...")
    print(f"Install your tools, then exit to save as template '{name}'.")
    print()

    rc = runtime.run_interactive(base, container_name=container_name)

    should_commit = True
    if rc != 0:
        print(f"\nContainer exited with code {rc}.", file=sys.stderr)
        if args.no_commit_on_error:
            should_commit = False
        elif not args.always_commit:
            should_commit = _confirm("Commit container state anyway?")

    if not should_commit:
        print("Skipping commit.", file=sys.stderr)
        runtime.rm(container_name)
        return 1

    try:
        runtime.commit(container_name, image_name)
        print(f"\nTemplate saved as {image_name}")
    except ContainerError as e:
        print(f"Failed to commit: {e}", file=sys.stderr)
        return 1
    finally:
        # Clean up the build container
        runtime.rm(container_name)

    return 0


def run_list(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    quiet = getattr(args, "quiet", False)

    if quiet:
        # Quiet mode: just print image names
        try:
            runtime = ContainerRuntime()
            images = runtime.list_local_images()
            for repo, _size in images:
                print(repo)
        except ContainerError:
            pass
        return 0

    # Full display mode
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
    merged = load_merged_config(config_file, None)
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


def run_info(args: argparse.Namespace) -> int:
    """Show details about a container image."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    merged = load_merged_config(
        config_file_path(xdg("XDG_CONFIG_HOME", ".config")), None,
    )
    image = resolve_image_name(args.image, merged.container_image)

    data = runtime.image_inspect(image)
    if data is None:
        print(f"Error: image not found: {image}", file=sys.stderr)
        return 1

    # Display key fields
    print(f"Name:    {image}")
    image_id = data.get("Id", "")
    if image_id:
        short_id = image_id[:19] if len(image_id) > 19 else image_id
        print(f"ID:      {short_id}")
    created = data.get("Created", "")
    if created:
        print(f"Created: {created}")
    size = data.get("Size")
    if size is not None:
        # Convert bytes to human-readable
        if isinstance(size, (int, float)):
            if size >= 1_000_000_000:
                print(f"Size:    {size / 1_000_000_000:.1f} GB")
            elif size >= 1_000_000:
                print(f"Size:    {size / 1_000_000:.1f} MB")
            else:
                print(f"Size:    {size} bytes")
        else:
            print(f"Size:    {size}")
    labels = data.get("Labels") or data.get("Config", {}).get("Labels")
    if labels:
        print("Labels:")
        for k, v in sorted(labels.items()):
            print(f"  {k}={v}")

    return 0


def run_rm(args: argparse.Namespace) -> int:
    """Remove a local container image."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    merged = load_merged_config(
        config_file_path(xdg("XDG_CONFIG_HOME", ".config")), None,
    )
    image = resolve_image_name(args.image, merged.container_image)

    if not args.force:
        # Check if local-only (template images are local-only)
        # Extract just the image name (strip registry prefix and tag)
        bare = image.split(":")[0] if ":" in image else image
        image_basename = bare.rsplit("/", 1)[-1]
        if image_basename.startswith(_TEMPLATE_PREFIX):
            print(f"Image '{image}' is a local template (not recoverable from registry).")
        else:
            print(f"Image '{image}' may be recoverable via 'kanibako image rebuild'.")

        if not _confirm(f"Remove image '{image}'?"):
            print("Cancelled.")
            return 0

    try:
        runtime.remove_image(image)
        print(f"Removed image '{image}'.")
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
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

    >>> _extract_registry_prefix("ghcr.io/doctorjei/kanibako-oci:latest")
    'ghcr.io/doctorjei'
    """
    # Expect at least registry/owner/name
    parts = image.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:-1])
    return None


# Known shorthand suffixes that map to kanibako-<suffix> images.
_KNOWN_SUFFIXES = {"min", "oci", "lxc", "vm"}


def resolve_image_name(name: str, configured_image: str) -> str:
    """Expand a shorthand image name to a fully qualified image reference.

    - If *name* contains ``/``, it is already qualified -- returned as-is.
    - If *name* is a known suffix (``min``, ``oci``, ``lxc``, ``vm``), expand
      to ``{prefix}/kanibako-{name}:latest``.
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
    """Update container image(s): auto-detect local build vs registry pull."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    containers_dir = std.data_path / "containers"

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.all_images:
        return _update_all(runtime, containers_dir)

    # Determine which image to update
    merged = load_merged_config(config_file, None)
    image = args.image
    if image is None:
        image = merged.container_image
    else:
        image = resolve_image_name(image, merged.container_image)

    return _update_one(runtime, image, containers_dir)


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
                for p in ["kanibako-min", "kanibako-oci", "kanibako-lxc",
                          "kanibako-vm"]
            ))
        ), file=sys.stderr)
        return 1

    containerfile = get_containerfile(suffix, containers_dir)
    if containerfile is None:
        print(f"Error: Containerfile not found for variant: {suffix}", file=sys.stderr)
        return 1

    build_args: dict[str, str] = {}
    base = runtime.get_base_image(image)
    if base:
        build_args["BASE_IMAGE"] = base

    print(f"Building {image} from Containerfile.{suffix}...")
    print()
    rc = runtime.rebuild(image, containerfile, containerfile.parent, build_args=build_args)
    if rc == 0:
        print()
        print(f"Successfully built {image}")
    else:
        print()
        print(f"Build failed with exit code {rc}", file=sys.stderr)
    return rc


def _update_one(
    runtime: ContainerRuntime, image: str, containers_dir: Path,
) -> int:
    """Update a single image: build locally if Containerfile exists, else pull."""
    suffix = runtime.guess_containerfile(image)
    if suffix is not None and containers_dir.is_dir():
        containerfile = get_containerfile(suffix, containers_dir)
        if containerfile is not None:
            return _build_one(runtime, image, containers_dir)
    return _pull_one(runtime, image)


def _update_all(
    runtime: ContainerRuntime, containers_dir: Path,
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
        rc = _update_one(runtime, repo, containers_dir)
        if rc != 0:
            failed += 1

    print()
    if failed:
        print(f"Updated {len(images) - failed}/{len(images)} images ({failed} failed)")
        return 1
    else:
        print(f"Updated {len(images)} image(s) successfully.")
        return 0
