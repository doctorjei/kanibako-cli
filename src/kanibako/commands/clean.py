"""kanibako purge: remove project session data."""

from __future__ import annotations

import argparse
import shutil
import sys

from kanibako.config import load_config
from kanibako.errors import UserCancelled
from kanibako.paths import (
    BoxMode,
    load_std_paths,
    resolve_any_project,
)
from kanibako.utils import confirm_prompt


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "purge",
        help="Remove all project session data",
        description="Remove all project session data (credentials, conversation history).",
    )
    p.add_argument("path", nargs="?", default=None, help="Path to the project directory")
    p.add_argument(
        "--all", action="store_true", dest="all_projects",
        help="Purge session data for every known project",
    )
    p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from kanibako.paths import xdg
    from kanibako.config import config_file_path
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    if args.all_projects:
        return _purge_all(std, config, force=args.force)

    if args.path is None:
        print("Error: specify a project path, or use --all", file=sys.stderr)
        return 1

    return _purge_one(std, config, args.path, force=args.force)


def _unregister_purged(std, proj) -> None:
    """Drop a purged box's registry entry (M2 orphan-cleanup).

    PRIMARY boxes live in ``registry.projects`` (name → workspace path);
    STANDALONE boxes live in ``registry.standalone`` (box name → root). Resolve
    the registered name by reverse path lookup first (the workspace path is what
    the registry stores), falling back to the resolved/ metadata-dir name, and
    remove it. Best-effort: a missing or already-clean entry is a no-op.
    """
    from kanibako import registry_store
    from kanibako.names import lookup_by_path, unregister_name

    try:
        if proj.mode is BoxMode.standalone:
            name = registry_store.standalone_name_for_root(
                std.data_path, proj.project_path,
            ) or proj.name
            if name:
                registry_store.unregister_standalone(std.data_path, name)
            return

        # PRIMARY (named-workset boxes are unregistered via remove_project, not
        # purge): the registry maps name → workspace path, so reverse-resolve.
        found = lookup_by_path(std.data_path, str(proj.project_path))
        name = found[0] if found else (proj.name or proj.metadata_path.name)
        if name:
            unregister_name(std.data_path, name)
    except Exception:  # noqa: BLE001 - cleanup must never break a purge
        pass


def _unregister_purged_primary(std, metadata_path, project_path) -> None:
    """M2 mirror for ``_purge_all``: unregister a purged PRIMARY box by path.

    ``iter_projects`` yields ``(metadata_path, project_path)`` for primary-mode
    boxes; reverse-resolve the registered name from the workspace path (falling
    back to the metadata dir name) and drop it from ``registry.projects``.
    """
    from kanibako.names import lookup_by_path, unregister_name

    try:
        name: str | None = None
        if project_path is not None:
            found = lookup_by_path(std.data_path, str(project_path))
            if found:
                name = found[0]
        if name is None:
            name = metadata_path.name
        if name:
            unregister_name(std.data_path, name)
    except Exception:  # noqa: BLE001 - cleanup must never break a purge
        pass


def _purge_one(std, config, path: str, *, force: bool) -> int:
    """Purge session data for a single project."""
    proj = resolve_any_project(std, config, project_dir=path, initialize=False)

    if not proj.metadata_path.is_dir():
        print(f"No session data found for project {proj.project_path}")
        return 0

    if not force:
        print(f"Project: {proj.project_path}")
        if proj.name:
            print(f"Name: {proj.name}")
        print()
        try:
            confirm_prompt(
                "Delete all session data for this project? This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    print("Removing session data... ", end="", flush=True)
    shutil.rmtree(proj.metadata_path)
    # Phase 5: PRIMARY vault lives under @system.primary_workset (not under
    # metadata_path), so remove the per-box ro/rw dirs explicitly.
    if proj.mode is BoxMode.primary:
        for vault_dir in (proj.vault_ro_path, proj.vault_rw_path):
            if vault_dir.is_dir():
                shutil.rmtree(vault_dir, ignore_errors=True)

    # Remove helper log directory if it exists.
    _log_id = proj.name if proj.name else proj.metadata_path.name
    log_dir = std.data_path / "logs" / _log_id
    if log_dir.is_dir():
        shutil.rmtree(log_dir)

    # M2 (registry hygiene): the box metadata is gone, so drop its registry
    # entry too — otherwise registry.{projects,standalone} keeps a dangling
    # name → path that would shadow a future box at the same path.
    _unregister_purged(std, proj)

    print("done.")
    print(f"Session data removed for {proj.project_path}")
    return 0


def _purge_all(std, config, *, force: bool) -> int:
    """Purge session data for all known projects."""
    from kanibako.paths import iter_projects, iter_workset_projects

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if not projects and not ws_data:
        print("No project session data found.")
        return 0

    total = len(projects)
    for _, _, project_list in ws_data:
        total += sum(1 for _, status in project_list if status != "no-data")

    print(f"Found {total} project(s):")
    for metadata_path, project_path in projects:
        label = str(project_path) if project_path else f"(unknown) {metadata_path.name}"
        print(f"  {label}")
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status != "no-data":
                print(f"  {ws_name}/{proj_name}")
    print()

    if not force:
        try:
            confirm_prompt(
                "Delete ALL session data for every project listed above? "
                "This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    removed = 0

    # PRIMARY-mode projects.
    for metadata_path, project_path in projects:
        label = str(project_path) if project_path else metadata_path.name
        print(f"Removing {label}... ", end="", flush=True)
        shutil.rmtree(metadata_path)
        # Phase 5: PRIMARY vault lives under @system.primary_workset/vault/
        # {ro,rw}/<name> (name == metadata dir name), not under metadata_path.
        for vault_dir in (
            std.primary_vault_ro / metadata_path.name,
            std.primary_vault_rw / metadata_path.name,
        ):
            if vault_dir.is_dir():
                shutil.rmtree(vault_dir, ignore_errors=True)

        # Remove helper log directory if it exists.
        log_dir = std.data_path / "logs" / metadata_path.name
        if log_dir.is_dir():
            shutil.rmtree(log_dir)

        # M2: drop the now-dangling registry entry for this PRIMARY box.
        _unregister_purged_primary(std, metadata_path, project_path)

        print("done.")
        removed += 1

    # Workset projects.
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status == "no-data":
                continue
            project_dir = ws.projects_dir / proj_name
            if project_dir.is_dir():
                label = f"{ws_name}/{proj_name}"
                print(f"Removing {label}... ", end="", flush=True)
                shutil.rmtree(project_dir)
                print("done.")
                removed += 1

    print(f"\nPurged session data for {removed} project(s).")
    return 0
