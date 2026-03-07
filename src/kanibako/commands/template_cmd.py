"""kanibako template: create, list, delete user template images."""

from __future__ import annotations

import argparse
import sys

from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.templates_image import (
    delete_template,
    list_templates,
    template_image_name,
)


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
        "template",
        help="Manage user template images",
        description="Create, list, or delete user template images.",
    )
    sub = p.add_subparsers(dest="template_command", metavar="COMMAND")

    # template create
    create_p = sub.add_parser(
        "create",
        help="Create a new template from a base image",
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

    # template list
    list_p = sub.add_parser("list", help="List local templates")
    list_p.set_defaults(func=run_list)

    # template delete
    del_p = sub.add_parser("delete", help="Delete a template image")
    del_p.add_argument("name", help="Template name to delete")
    del_p.add_argument(
        "--force", "-f", action="store_true",
        help="Delete without confirmation",
    )
    del_p.set_defaults(func=run_delete)

    p.set_defaults(func=run_list)


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
    """List local template images."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    templates = list_templates(runtime)
    if not templates:
        print("No templates found. Create one with 'kanibako template create <name>'.")
        return 0

    print("Templates:")
    for short_name, _full_image, size in templates:
        print(f"  {short_name:<20} {size}")
    return 0


def run_delete(args: argparse.Namespace) -> int:
    """Delete a template image."""
    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not args.force:
        if not _confirm(f"Delete template '{args.name}'?"):
            print("Cancelled.")
            return 0

    try:
        delete_template(runtime, args.name)
        print(f"Deleted template '{args.name}'.")
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0
