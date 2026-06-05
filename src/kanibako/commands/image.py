"""kanibako image: manage container images (list, create, info, rm, rebuild)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from kanibako.config import config_file_path, load_config, load_merged_config
from kanibako.container import ContainerRuntime
from kanibako.containerfiles import get_containerfile
from kanibako.errors import ContainerError
from kanibako.paths import xdg, load_std_paths
from kanibako.rig_meta import RigMeta, write_rig_meta
from kanibako.rig_registry import (
    RigRecord,
    get as registry_get,
    load_registry,
    registry_path,
    remove as registry_remove,
    upsert,
)
from kanibako.rig_resolve import resolve_rig
from kanibako.rig_source import derive_name, detect_source_kind, fetch_to_temp
from kanibako.templates_image import (
    list_bundled_templates,
    read_template_checks,
    rig_image_name,
    template_image_name,
)


_TEMPLATE_PREFIX = "kanibako-template-"


def _confirm(prompt: str) -> bool:
    """Prompt the user for yes/no confirmation. Returns True on 'y'."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def _deprecated(old: str, new: str) -> None:
    """Print a one-line deprecation notice to stderr."""
    print(f"note: '{old}' is deprecated; use '{new}'.", file=sys.stderr)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "rig",
        help="Manage box rigs (images)",
        description="Create, list, inspect, remove, or rebuild box rigs (container images).",
    )
    image_sub = p.add_subparsers(dest="rig_command", metavar="COMMAND")

    # kanibako image create
    create_p = image_sub.add_parser(
        "create",
        help="Create a new template image from a base image",
    )
    create_p.add_argument("name", help="Template name (e.g. jvm, systems)")
    create_p.add_argument(
        "--base", default=None,
        help="Base image to start from. With --template, defaults to the "
             "template's declared base; without --template, defaults to "
             "kanibako-oci.",
    )
    create_p.add_argument(
        "--template",
        help="Build a bundled template Containerfile (see 'kanibako rig list') "
             "instead of an interactive session",
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

    # kanibako rig prep
    prep_p = image_sub.add_parser(
        "prep", aliases=["prepare"],
        help="Materialize a rig: build a template or pull a prefab",
        description="Resolve a rig name and make it ready to use (build templates, pull prefabs).",
    )
    prep_p.add_argument("name", nargs="?", default=None, help="Rig name to prep (omit with --all)")
    prep_p.add_argument("--force", action="store_true", help="Re-prep even if already prepped")
    prep_p.add_argument("--all", action="store_true", dest="all_images", help="Prep all local kanibako rigs")
    prep_p.set_defaults(func=run_prep)

    # kanibako rig add
    add_p = image_sub.add_parser(
        "add",
        help="Register a foreign rig (prefab image ref or template Containerfile)",
        description="Add a rig by source: an image reference/tar (prefab) or a Containerfile (template). Does not pull or build; run 'rig prep <name>' afterward.",
    )
    add_p.add_argument("source", help="Image ref, image tar, Containerfile path, or URL")
    add_p.add_argument("--name", default=None, help="Rig name (derived from source if omitted)")
    add_p.add_argument("--as", dest="as_", choices=["image", "template"], default=None, help="Force the source kind (escape hatch)")
    add_p.add_argument("--force", action="store_true", help="Overwrite an existing rig of the same name")
    add_p.set_defaults(func=run_add)

    # kanibako rig extend
    extend_p = image_sub.add_parser(
        "extend",
        help="Build a custom rig interactively from a foundation rig",
        description="Auto-prep a foundation rig, open an interactive shell to customize it, and commit the result as an extended rig (kanibako-rig-<name>).",
    )
    extend_p.add_argument("name", help="Name for the new extended rig")
    extend_p.add_argument("--from", dest="from_", required=True, metavar="RIG", help="Foundation rig to build from (prefab/template/extended)")
    extend_commit = extend_p.add_mutually_exclusive_group()
    extend_commit.add_argument("--always-commit", action="store_true", help="Commit even if the container exits with an error")
    extend_commit.add_argument("--no-commit-on-error", action="store_true", help="Skip commit if the container exits with an error")
    extend_p.set_defaults(func=run_extend)

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
    list_p.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable JSON",
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

    # rig diagnose
    from kanibako.commands.diagnose import run_rig_diagnose

    diagnose_p = image_sub.add_parser(
        "diagnose",
        help="Check rig (image) status",
    )
    diagnose_p.set_defaults(func=run_rig_diagnose)

    # Default to list if no subcommand given
    p.set_defaults(func=run_list, quiet=False)


def run_create(args: argparse.Namespace) -> int:
    """Create a template image.

    With ``--template`` builds a bundled ``Containerfile.template-<name>``;
    otherwise runs an interactive container and commits it on exit.
    """
    if getattr(args, "template", None):
        _deprecated("rig create --template", "rig prep")
        return _create_from_template(args)

    # Interactive create is now an alias for 'rig extend'. The base becomes the
    # foundation rig, and the result is committed as kanibako-rig-<name> with a
    # registry row -- that IS the migration.
    _deprecated("rig create (interactive)", "rig extend")
    args.from_ = args.base or "kanibako-oci"
    return run_extend(args)


def run_extend(args: argparse.Namespace) -> int:
    """Build a custom *extended* rig interactively from a foundation rig.

    Auto-preps the ``--from`` foundation (build a template / pull-or-build a
    prefab; an extended foundation must already exist), opens an interactive
    container, writes in-image ``/etc/kanibako/rig.yaml`` metadata, commits the
    result as ``kanibako-rig-<name>``, and records a registry row.
    """
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    merged = load_merged_config(config_file, None)
    containers_dir = std.data_path / "containers"

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Validate the new name early.
    try:
        image = rig_image_name(args.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Resolve + auto-prep the foundation.
    registry = load_registry(registry_path(std))
    res = resolve_rig(args.from_, runtime, std, merged, registry=registry)
    if res.prep_action != "none":
        if res.kind == "extended":
            # A missing extended foundation has no recipe to rebuild.
            print(
                f"Error: foundation rig '{args.from_}' is extended but its image "
                "is missing; no recipe to re-prep it (try 'rig import').",
                file=sys.stderr,
            )
            return 1
        if res.kind == "template":
            cf = res.containerfile or get_containerfile(
                f"template-{args.from_}", containers_dir,
            )
            if cf is None:
                print(
                    f"Error: Containerfile not found for template '{args.from_}'.",
                    file=sys.stderr,
                )
                return 1
            print(f"Preparing foundation '{args.from_}' (build {cf.name})...")
            rc = runtime.rebuild(res.image, cf, cf.parent, build_args=None)
            if rc != 0:
                print(
                    f"Error: failed to build foundation '{args.from_}'.",
                    file=sys.stderr,
                )
                return rc
        else:
            # prefab: build-or-pull.
            print(f"Preparing foundation '{args.from_}'...")
            rc = _update_one(runtime, res.image, containers_dir)
            if rc != 0:
                print(
                    f"Error: failed to prep foundation '{args.from_}'.",
                    file=sys.stderr,
                )
                return 1
    foundation_image = res.image

    # Interactive session (mirror run_create's commit-on-error logic).
    container_name = f"kanibako-extend-{args.name}"
    print(f"Starting interactive container from {foundation_image}...")
    print(f"Customize it, then exit to save as extended rig '{args.name}'.")
    rc = runtime.run_interactive(foundation_image, container_name=container_name)
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

    # Write in-image metadata, then commit. Always clean up the container.
    created = datetime.now(timezone.utc).isoformat()
    foundation_source = res.source_ref or args.from_
    try:
        meta = RigMeta(
            name=args.name,
            kind="extended",
            parent=foundation_image,
            foundation_source=foundation_source,
            reproducible=False,
            created=created,
        )
        with tempfile.TemporaryDirectory() as td:
            meta_dir = Path(td) / "kanibako"
            write_rig_meta(meta, meta_dir / "rig.yaml")
            # Copy the DIRECTORY into /etc/ -> lands at /etc/kanibako/rig.yaml.
            # Do NOT cp a bare file to :/etc/kanibako/rig.yaml (fails if the dir
            # is absent in the image).
            if not runtime.cp(meta_dir, f"{container_name}:/etc/"):
                print(
                    "Error: failed to write rig metadata into the container.",
                    file=sys.stderr,
                )
                return 1
        try:
            runtime.commit(container_name, image)
        except ContainerError as e:
            print(f"Failed to commit: {e}", file=sys.stderr)
            return 1
        upsert(
            registry_path(std),
            RigRecord(
                name=args.name,
                kind="extended",
                image=image,
                parent=foundation_image,
                foundation_source=foundation_source,
                reproducible=False,
                created=created,
                source_type="extend",
            ),
        )
        print(f"\nExtended rig saved as {image}")
    finally:
        runtime.rm(container_name)

    return 0


def _create_from_template(args: argparse.Namespace) -> int:
    """Build a bundled template Containerfile into a local template image."""
    template = args.template

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    containers_dir = std.data_path / "containers"

    available = sorted(
        t.name for t in list_bundled_templates(override_dir=containers_dir)
    )
    if template not in available:
        print(
            f"error: unknown template '{template}'. "
            f"Available: {', '.join(available)}",
            file=sys.stderr,
        )
        return 1

    try:
        image_name = template_image_name(args.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    containerfile = get_containerfile(f"template-{template}", containers_dir)
    if containerfile is None:
        print(
            f"Error: Containerfile not found for template: {template}",
            file=sys.stderr,
        )
        return 1

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    build_args: dict[str, str] | None
    if args.base is None:
        # Let the Containerfile's declared ARG BASE_IMAGE default stand.
        build_args = None
        print(f"Building template '{template}' from its default base...")
    else:
        merged = load_merged_config(config_file, None)
        base_image = resolve_image_name(args.base, merged.container_image)
        build_args = {"BASE_IMAGE": base_image}
        print(
            f"Note: overriding template '{template}' default base "
            f"with {base_image}."
        )
        print(f"Building template '{template}' from {base_image}...")
    print()
    rc = runtime.rebuild(image_name, containerfile, containerfile.parent, build_args=build_args)
    if rc == 0:
        print()
        print(f"Template saved as {image_name}")
    else:
        print()
        print(f"Build failed with exit code {rc}", file=sys.stderr)
    return rc


def _bare_repo(repo: str) -> str:
    """Strip a trailing ``:tag`` from a ``repo:tag`` reference."""
    return repo.split(":")[0] if ":" in repo else repo


def run_list(args: argparse.Namespace) -> int:
    """List rigs grouped by kind (prefab / template / extended) with live status.

    Status is derived from the local image store at call time -- never read from
    a stored field. ``-q/--quiet`` keeps the legacy one-name-per-line behavior;
    ``--json`` emits a machine-readable document.
    """
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    merged = load_merged_config(config_file, None)

    quiet = getattr(args, "quiet", False)

    runtime: ContainerRuntime | None

    if quiet:
        # Quiet mode: just print image names (unchanged).
        try:
            runtime = ContainerRuntime()
            for repo, _size in runtime.list_local_images():
                print(repo)
        except ContainerError:
            pass
        return 0

    # A runtime is required to derive live status; without one, every status is
    # reported as "unknown" but the (registry/template) catalogue still lists.
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        runtime = None

    containers_dir = std.data_path / "containers"
    registry = load_registry(registry_path(std))

    def _status(image: str, *, absent: str = "unprepped") -> str:
        if runtime is None:
            return "unknown"
        return "prepped" if runtime.image_exists(image) else absent

    # ---- Prefabs ----
    prefabs: list[dict[str, str]] = []
    for suffix in sorted(_KNOWN_SUFFIXES):
        prefabs.append({
            "name": suffix,
            "image": f"kanibako-{suffix}",
            "status": _status(f"kanibako-{suffix}:latest"),
        })
    for rec in registry.values():
        if rec.kind != "prefab":
            continue
        if rec.image:
            img = rec.image
        elif runtime is not None:
            img = resolve_image_reference(
                rec.source or rec.name, runtime, merged.container_image,
            )
        else:
            img = rec.source or rec.name
        prefabs.append({"name": rec.name, "image": img, "status": _status(img)})

    # ---- Templates ----
    templates: list[dict[str, str]] = []
    for t in list_bundled_templates(override_dir=containers_dir):
        image = template_image_name(t.name)
        templates.append({
            "name": t.name,
            "source": t.source,
            "image": image,
            "status": _status(image),
        })

    # ---- Extended ----
    extended: list[dict[str, str]] = []
    seen: set[str] = set()
    if runtime is not None:
        for repo, _size in runtime.list_local_images():
            bare = _bare_repo(repo)
            basename = bare.rsplit("/", 1)[-1]
            if basename.startswith("kanibako-rig-"):
                name = basename[len("kanibako-rig-"):]
                seen.add(name)
                extended.append({"name": name, "image": bare, "status": "prepped"})
    for rec in registry.values():
        if rec.kind != "extended" or rec.name in seen:
            continue
        if rec.image:
            img = rec.image
        else:
            try:
                img = rig_image_name(rec.name)
            except ValueError:
                img = rec.image or rec.name
        extended.append({
            "name": rec.name,
            "image": img,
            "status": _status(img, absent="missing"),
        })

    if getattr(args, "as_json", False):
        data = {
            "prefabs": prefabs,
            "templates": templates,
            "extended": extended,
            "current": merged.container_image,
        }
        print(json.dumps(data, indent=2))
        return 0

    print("Prefabs (pull to prep):")
    if prefabs:
        for p in prefabs:
            print(f"  {p['name']:<32} {p['status']}")
    else:
        print("  (none)")

    print()
    print("Templates (build to prep):")
    if templates:
        for t_row in templates:
            tag = f"[{t_row['source']}]"
            print(f"  {t_row['name']:<16} {tag:<11} {t_row['status']}")
    else:
        print("  (none)")

    print()
    print("Extended (interactive; export/import to move):")
    if extended:
        for e in extended:
            print(f"  {e['name']:<32} {e['status']}")
    else:
        print("  (none)")

    print()
    print(f"Current rig: {merged.container_image}")
    return 0


_PREP_STATUS = {"none": "prepped", "pull": "unprepped", "build": "unprepped", "missing": "missing"}


def run_info(args: argparse.Namespace) -> int:
    """Show details about a rig: kind, live status, image, and provenance."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    merged = load_merged_config(config_file, None)

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    registry = load_registry(registry_path(std))
    res = resolve_rig(args.image, runtime, std, merged, registry=registry)

    # A speculative prefab guess for an unknown name is "not found": only a known
    # base, a registered rig, or a discovered template is shown when unprepped.
    if (
        res.kind == "prefab"
        and res.prep_action != "none"
        and args.image not in _KNOWN_SUFFIXES
        and args.image not in registry
    ):
        print(f"Error: rig not found: {args.image}", file=sys.stderr)
        return 1

    status = _PREP_STATUS.get(res.prep_action, res.prep_action)

    print(f"Name:    {args.image}")
    print(f"Kind:    {res.kind}")
    print(f"Status:  {status}")
    print(f"Image:   {res.image}")
    if res.source_ref:
        print(f"Source:  {res.source_ref}")

    # Live image details (only available once the rig is prepped).
    data = runtime.image_inspect(res.image)
    if data is not None:
        image_id = data.get("Id", "")
        if image_id:
            short_id = image_id[:19] if len(image_id) > 19 else image_id
            print(f"ID:      {short_id}")
        created = data.get("Created", "")
        if created:
            print(f"Created: {created}")
        size = data.get("Size")
        if size is not None:
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

    # Provenance.
    record = registry.get(args.image)
    if record is not None:
        if record.parent:
            print(f"Parent:     {record.parent}")
        if record.foundation_source:
            print(f"Foundation: {record.foundation_source}")

    if res.kind == "template":
        cf = get_containerfile(f"template-{args.image}", std.data_path / "containers")
        if cf is not None:
            print(f"Containerfile: {cf}")
            checks = read_template_checks(cf)
            if checks:
                print("Checks:")
                for check in checks:
                    print(f"  {check}")

    return 0


def run_rm(args: argparse.Namespace) -> int:
    """Remove a local container image, or un-add a registered/template rig."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # --- Un-add: a registered rig (rigs.yaml) wins over image removal. ---
    record = registry_get(registry_path(std), args.image)
    if record is not None:
        registry_remove(registry_path(std), args.image)
        print(f"Removed rig '{args.image}' from the registry.")
        # A loaded (file-sourced) prefab owns its local image; clean it up.
        if record.source_type == "file" and record.image:
            try:
                runtime.remove_image(record.image)
            except ContainerError:
                pass
        return 0

    # --- Un-add: an installed user template removes its Containerfile. ---
    override = std.data_path / "containers" / f"Containerfile.template-{args.image}"
    if override.is_file():
        override.unlink()
        print(f"Removed user template '{args.image}'.")
        return 0

    merged = load_merged_config(config_file, None)
    image = resolve_image_name(args.image, merged.container_image)

    if not args.force:
        # Check if local-only (template images are local-only)
        # Extract just the image name (strip registry prefix and tag)
        bare = image.split(":")[0] if ":" in image else image
        image_basename = bare.rsplit("/", 1)[-1]
        if image_basename.startswith(_TEMPLATE_PREFIX):
            print(f"Rig '{image}' is a local template (not recoverable from registry).")
        else:
            print(f"Rig '{image}' may be recoverable via 'kanibako rig rebuild'.")

        if not _confirm(f"Remove image '{image}'?"):
            print("Cancelled.")
            return 0

    try:
        runtime.remove_image(image)
        print(f"Removed rig '{image}'.")
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

# Last-resort registry/owner when none can be derived from configuration.
_FALLBACK_REGISTRY = "ghcr.io/doctorjei"


def resolve_image_reference(
    name: str, runtime: ContainerRuntime, configured_image: str,
) -> str:
    """Resolve a kanibako image name to a runtime-usable reference.

    Resolution order:

    1. Already qualified (*name* contains ``/``) -> returned unchanged.
    2. Non-kanibako bare names (e.g. ``busybox``, ``ubuntu``) -> returned
       unchanged, so the runtime's own ``unqualified-search-registries``
       resolve them as before. Only kanibako-branded names — a known suffix
       (``min``/``oci``/``lxc``/``vm``) or a ``kanibako-`` prefix — are
       expanded and prefixed.
    3. Local-first: if the local image store already has the (suffix-expanded)
       bare reference, use it as-is — lets a locally built or bare-tagged
       kanibako image win without contacting the registry.
    4. Otherwise prefix it with the ``registry/owner`` derived from
       *configured_image* (falling back to :data:`_FALLBACK_REGISTRY`) so the
       runtime can pull it without relying on ``unqualified-search-registries``.

    Unlike :func:`resolve_image_name`, this consults the local store first.
    The branding restriction is deliberate: we cannot safely assume an
    arbitrary bare name (a public Docker Hub image) belongs to the kanibako
    registry, so only names we actually publish are rewritten.
    """
    if "/" in name:
        return name

    if name in _KNOWN_SUFFIXES:
        candidate = f"kanibako-{name}"
    elif name.startswith("kanibako-"):
        candidate = name
    else:
        return name

    bare = candidate if ":" in candidate else f"{candidate}:latest"

    if runtime.image_exists(bare):
        return bare

    prefix = _extract_registry_prefix(configured_image) or _FALLBACK_REGISTRY
    return f"{prefix}/{bare}"


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


def run_prep(args: argparse.Namespace) -> int:
    """Materialize a rig: build a template or pull/build a prefab.

    Resolves *name* via :func:`resolve_rig` (pure) and then performs the
    side effect it implies. ``--force`` re-preps even if already prepped;
    ``--all`` build-or-pulls every local kanibako rig.
    """
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

    if args.name is None:
        print("error: rig name required (or use --all)", file=sys.stderr)
        return 1

    merged = load_merged_config(config_file, None)
    registry = load_registry(registry_path(std))
    res = resolve_rig(args.name, runtime, std, merged, registry=registry)
    force = getattr(args, "force", False)

    if res.kind == "extended":
        if force:
            print(
                f"error: extended rig '{args.name}' has no recipe to re-prep "
                f"(use 'rig export'/'rig import' or 'rig extend').",
                file=sys.stderr,
            )
            return 1
        if res.prep_action == "none":
            print(f"Rig '{args.name}' is already prepped.")
            return 0
        print(
            f"error: extended rig '{args.name}' image is missing.",
            file=sys.stderr,
        )
        return 1

    if res.prep_action == "none" and not force:
        print(f"Rig '{args.name}' is already prepped.")
        return 0

    if res.kind == "template":
        cf = res.containerfile or get_containerfile(
            f"template-{args.name}", containers_dir,
        )
        if cf is None:
            print(
                f"error: Containerfile not found for template '{args.name}'.",
                file=sys.stderr,
            )
            return 1
        print(f"Building rig '{args.name}' from {cf.name}...")
        rc = runtime.rebuild(res.image, cf, cf.parent, build_args=None)
        if rc == 0:
            print(f"Rig '{args.name}' prepped as {res.image}")
        else:
            print(f"Build failed with exit code {rc}", file=sys.stderr)
        return rc

    # prefab: build-if-Containerfile-else-pull (same as rebuild).
    return _update_one(runtime, res.image, containers_dir)


def run_add(args: argparse.Namespace) -> int:
    """Register a foreign rig from a source. Never pulls or builds.

    The *source* is classified (an image ref/tar -> prefab; a Containerfile ->
    template) and recorded. Templates install their Containerfile under the
    user-override dir (the file IS the source of truth -- no registry row);
    prefabs get a ``rigs.yaml`` row (a tar is loaded via ``runtime.load`` first,
    a ref is recorded as-is). Run ``rig prep <name>`` afterward to materialize.
    """
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    source = args.source

    # A raw URL is undecidable on its own; fetch it first, then classify the
    # downloaded file. The temp file doubles as the source for tar/template.
    if source.lower().startswith(("http://", "https://")):
        local_path: Path | None = fetch_to_temp(source)
        detect_target = str(local_path)
    else:
        local_path = None
        detect_target = source

    try:
        kind = detect_source_kind(detect_target, force=args.as_)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    name = args.name or derive_name(source, kind)
    if name is None:
        print(
            "Error: could not derive a rig name from source; pass --name NAME.",
            file=sys.stderr,
        )
        return 1

    # Collision: a registry row OR an installed user template of the same name.
    containers_dir = std.data_path / "containers"
    exists = registry_get(registry_path(std), name) is not None or (
        get_containerfile(f"template-{name}", containers_dir) is not None
    )
    if exists and not args.force:
        print(
            f"Error: rig '{name}' already exists; use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    if kind == "template":
        # Install the Containerfile under the user-override dir; that file is the
        # source of truth for templates, so no registry row is written.
        dest_dir = std.data_path / "containers"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"Containerfile.template-{name}"
        src_file = local_path if local_path is not None else Path(source)
        shutil.copyfile(src_file, dest)
        print(
            f"Added template '{name}' ({dest}). "
            f"Run 'kanibako rig prep {name}' to build it."
        )
        return 0

    # kind == "image": a local tar (or fetched tar) is loaded now; a bare
    # reference is recorded without pulling.
    is_tar = local_path is not None or Path(source).is_file()
    if is_tar:
        archive = local_path if local_path is not None else Path(source)
        loaded_ref = runtime.load(archive)
        if loaded_ref is None:
            print(
                f"Error: failed to load image archive '{archive}'.",
                file=sys.stderr,
            )
            return 1
        if not loaded_ref:
            # Loaded, but the archive carries no RepoTag, so there is no stable
            # reference to run the rig by. Don't record a guessed/wrong image.
            print(
                f"Error: loaded '{archive}' but it has no image tag; re-save "
                "the image with a tag, or add it by reference instead.",
                file=sys.stderr,
            )
            return 1
        upsert(
            registry_path(std),
            RigRecord(
                name=name,
                kind="prefab",
                source=str(Path(source).resolve()) if local_path is None else source,
                source_type="file",
                image=loaded_ref,
            ),
        )
        print(f"Added prefab '{name}' from archive.")
        return 0

    upsert(
        registry_path(std),
        RigRecord(name=name, kind="prefab", source=source, source_type="ref"),
    )
    print(
        f"Added prefab '{name}' -> {source}. "
        f"Run 'kanibako rig prep {name}' to pull it."
    )
    return 0


def run_rebuild(args: argparse.Namespace) -> int:
    """Update container image(s): auto-detect local build vs registry pull."""
    _deprecated("rig rebuild", "rig prep --force")
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
        print(f"Error: cannot determine Containerfile for rig: {image}", file=sys.stderr)
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
    variant = runtime.get_variant(image)
    if variant:
        build_args["VARIANT"] = variant

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
        print("No local kanibako rigs to update.")
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
        print(f"Updated {len(images) - failed}/{len(images)} rigs ({failed} failed)")
        return 1
    else:
        print(f"Updated {len(images)} rig(s) successfully.")
        return 0
