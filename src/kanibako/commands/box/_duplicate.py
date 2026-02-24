"""Duplicate logic for kanibako box."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.paths import (
    ProjectMode,
    _find_workset_for_path,
    xdg,
    detect_project_mode,
    load_std_paths,
    resolve_decentralized_project,
    resolve_project,
    resolve_workset_project,
)
from kanibako.utils import confirm_prompt, project_hash, short_hash


# -- Cross-mode duplicate helpers --

def _run_duplicate_cross_mode(args: argparse.Namespace, std, config) -> int:
    """Duplicate a project into a different mode layout."""
    to_mode_str = args.to_mode

    # Duplicate TO workset: separate code path.
    if to_mode_str == "workset":
        return _duplicate_to_workset(args, std, config)

    source_path = Path(args.source_path).resolve()
    new_path = Path(args.new_path).resolve()

    if source_path == new_path:
        print("Error: source and destination paths are the same.", file=sys.stderr)
        return 1

    if not source_path.is_dir():
        print(f"Error: source path does not exist as a directory: {source_path}", file=sys.stderr)
        return 1

    # Detect source mode and resolve.
    source_mode = detect_project_mode(source_path, std, config).mode

    # Duplicate FROM workset: separate code path.
    if source_mode == ProjectMode.workset:
        return _duplicate_from_workset(args, source_path, new_path, std, config)

    if source_mode == ProjectMode.account_centric:
        src_proj = resolve_project(std, config, project_dir=str(source_path), initialize=False)
    else:
        src_proj = resolve_decentralized_project(std, config, project_dir=str(source_path), initialize=False)

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for source path: {source_path}", file=sys.stderr)
        return 1

    # Lock file warning.
    lock_file = src_proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            print("Aborted.")
            return 2

    # Confirm with user.
    target_mode = ProjectMode.decentralized if to_mode_str == "decentralized" else ProjectMode.account_centric
    if not args.force:
        mode = "metadata only (bare)" if args.bare else "workspace + metadata"
        print(f"Duplicate project ({mode}) to {target_mode.value} mode:")
        print(f"  from: {source_path}")
        print(f"    to: {new_path}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Copy workspace (unless --bare).
    if not args.bare:
        shutil.copytree(source_path, new_path, dirs_exist_ok=args.force)

    # Copy metadata into target mode layout.
    if target_mode == ProjectMode.decentralized:
        _duplicate_to_decentral(src_proj, new_path, args.force)
    else:
        _duplicate_to_ac(src_proj, new_path, std, config, args.force)

    print(f"Duplicated project to {target_mode.value} mode:")
    print(f"  from: {source_path}")
    print(f"    to: {new_path}")
    return 0


def _duplicate_to_decentral(src_proj, new_path, force):
    """Copy metadata into decentralized layout at new_path."""
    from kanibako.commands.init import _write_project_gitignore

    dst_metadata = new_path / ".kanibako"
    dst_shell = dst_metadata / "shell"

    # Ensure new_path exists for bare duplicates.
    new_path.mkdir(parents=True, exist_ok=True)

    if force and dst_metadata.is_dir():
        shutil.rmtree(dst_metadata)
    shutil.copytree(
        src_proj.metadata_path, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
    )

    # Remove breadcrumb if present (decentralized doesn't use it).
    breadcrumb = dst_metadata / "project-path.txt"
    if breadcrumb.exists():
        breadcrumb.unlink()

    if src_proj.shell_path.is_dir():
        if force and dst_shell.is_dir():
            shutil.rmtree(dst_shell)
        shutil.copytree(src_proj.shell_path, dst_shell)

    _write_project_gitignore(new_path)

    # Write vault .gitignore if vault exists.
    vault_dir = new_path / "vault"
    if vault_dir.is_dir():
        vault_gitignore = vault_dir / ".gitignore"
        if not vault_gitignore.exists():
            vault_gitignore.write_text("share-rw/\n")


def _duplicate_to_ac(src_proj, new_path, std, config, force):
    """Copy metadata into account-centric layout for new_path."""
    phash = project_hash(str(new_path))
    projects_base = std.data_path / "boxes"
    dst_project = projects_base / phash

    if force and dst_project.is_dir():
        shutil.rmtree(dst_project)
    shutil.copytree(
        src_proj.metadata_path, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock"),
    )

    # Write breadcrumb.
    (dst_project / "project-path.txt").write_text(str(new_path) + "\n")

    # Ensure home is inside the project dir.
    if src_proj.shell_path.is_dir():
        dst_home = dst_project / "shell"
        if not dst_home.is_dir():
            shutil.copytree(src_proj.shell_path, dst_home)


def _duplicate_to_workset(args, std, config) -> int:
    """Duplicate a project into a workset (source untouched)."""
    from kanibako.workset import add_project, list_worksets, load_workset

    ws_name = getattr(args, "workset", None)
    if not ws_name:
        print("Error: --workset is required when duplicating to workset mode.", file=sys.stderr)
        return 1

    registry = list_worksets(std)
    if ws_name not in registry:
        print(f"Error: workset '{ws_name}' not found.", file=sys.stderr)
        return 1
    ws = load_workset(registry[ws_name])

    source_path = Path(args.source_path).resolve()
    if not source_path.is_dir():
        print(f"Error: source path does not exist as a directory: {source_path}", file=sys.stderr)
        return 1

    source_mode = detect_project_mode(source_path, std, config).mode
    if source_mode == ProjectMode.workset:
        print("Error: source is already a workset project.", file=sys.stderr)
        return 1

    proj_name = getattr(args, "project_name", None) or source_path.name

    # Validate name not taken.
    for p in ws.projects:
        if p.name == proj_name:
            print(f"Error: project '{proj_name}' already exists in workset '{ws_name}'.", file=sys.stderr)
            return 1

    if source_mode == ProjectMode.account_centric:
        src_proj = resolve_project(std, config, project_dir=str(source_path), initialize=False)
    else:
        src_proj = resolve_decentralized_project(std, config, project_dir=str(source_path), initialize=False)

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for source path: {source_path}", file=sys.stderr)
        return 1

    # Lock file warning.
    lock_file = src_proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            print("Aborted.")
            return 2

    if not args.force:
        mode = "metadata only (bare)" if args.bare else "workspace + metadata"
        print(f"Duplicate project ({mode}) to workset:")
        print(f"  from:    {source_path}")
        print(f"  workset: {ws_name}/{proj_name}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Register in workset (creates skeleton dirs).
    add_project(ws, proj_name, source_path)

    # Copy metadata (excluding lock, breadcrumb, and home/).
    dst_project = ws.projects_dir / proj_name
    shutil.copytree(
        src_proj.metadata_path, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "project-path.txt", "shell"),
        dirs_exist_ok=True,
    )

    # Copy home.
    if src_proj.shell_path.is_dir():
        dst_home = dst_project / "shell"
        shutil.copytree(src_proj.shell_path, dst_home, dirs_exist_ok=True)

    # Copy workspace (unless --bare).
    if not args.bare:
        dst_workspace = ws.workspaces_dir / proj_name
        ignore = None
        if source_mode == ProjectMode.decentralized:
            ignore = shutil.ignore_patterns(".kanibako")
        shutil.copytree(source_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)

    print("Duplicated project to workset:")
    print(f"  from:    {source_path}")
    print(f"  workset: {ws_name}/{proj_name}")
    return 0


def _duplicate_from_workset(args, source_path, new_path, std, config) -> int:
    """Duplicate a workset project to AC or decentralized layout (source untouched)."""
    to_mode_str = args.to_mode

    ws, proj_name = _find_workset_for_path(source_path, std)
    src_proj = resolve_workset_project(ws, proj_name, std, config, initialize=False)

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for source path: {source_path}", file=sys.stderr)
        return 1

    target_mode = ProjectMode.decentralized if to_mode_str == "decentralized" else ProjectMode.account_centric

    # Lock file warning.
    lock_file = src_proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            print("Aborted.")
            return 2

    if not args.force:
        mode = "metadata only (bare)" if args.bare else "workspace + metadata"
        print(f"Duplicate workset project ({mode}) to {target_mode.value} mode:")
        print(f"  from: {ws.name}/{proj_name}")
        print(f"    to: {new_path}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Copy workspace (unless --bare).
    if not args.bare:
        ws_workspace = ws.workspaces_dir / proj_name
        if ws_workspace.is_dir():
            shutil.copytree(ws_workspace, new_path, dirs_exist_ok=args.force)

    # Copy metadata into target layout.
    if target_mode == ProjectMode.decentralized:
        _duplicate_to_decentral(src_proj, new_path, args.force)
    else:
        _duplicate_to_ac(src_proj, new_path, std, config, args.force)

    print(f"Duplicated project to {target_mode.value} mode:")
    print(f"  from: {ws.name}/{proj_name}")
    print(f"    to: {new_path}")
    return 0


def run_duplicate(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # Cross-mode duplication.
    if getattr(args, "to_mode", None) is not None:
        return _run_duplicate_cross_mode(args, std, config)

    source_path = Path(args.source_path).resolve()
    new_path = Path(args.new_path).resolve()

    # 1. Paths must differ.
    if source_path == new_path:
        print("Error: source and destination paths are the same.", file=sys.stderr)
        return 1

    # 2. Source must be an existing directory.
    if not source_path.is_dir():
        print(f"Error: source path does not exist as a directory: {source_path}", file=sys.stderr)
        return 1

    # 3. Source must have kanibako metadata.
    source_hash = project_hash(str(source_path))
    projects_base = std.data_path / "boxes"
    source_project_dir = projects_base / source_hash

    if not source_project_dir.is_dir():
        print(
            f"Error: no project data found for source path: {source_path}",
            file=sys.stderr,
        )
        return 1

    # 4. Non-bare: destination workspace must not already exist (unless --force).
    if not args.bare and new_path.exists() and not args.force:
        print(
            f"Error: destination already exists: {new_path}",
            file=sys.stderr,
        )
        print("  Use --force to overwrite.", file=sys.stderr)
        return 1

    # 5. Destination metadata must not already exist (unless --force).
    new_hash = project_hash(str(new_path))
    new_project_dir = projects_base / new_hash

    if new_project_dir.is_dir() and not args.force:
        print(
            f"Error: project data already exists for destination: {new_path}",
            file=sys.stderr,
        )
        print("  Use --force to overwrite.", file=sys.stderr)
        return 1

    # 6. Lock file warning.
    lock_file = source_project_dir / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            print("Aborted.")
            return 2

    # 7. User confirmation.
    if not args.force:
        mode = "metadata only (bare)" if args.bare else "workspace + metadata"
        print(f"Duplicate project ({mode}):")
        print(f"  from: {source_path}")
        print(f"    to: {new_path}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Copy workspace (unless --bare).
    if not args.bare:
        shutil.copytree(source_path, new_path, dirs_exist_ok=args.force)

    # Copy metadata (entire project dir including home/).
    if args.force and new_project_dir.is_dir():
        shutil.rmtree(new_project_dir)
    shutil.copytree(
        source_project_dir, new_project_dir,
        ignore=shutil.ignore_patterns(".kanibako.lock"),
    )

    # Update breadcrumb.
    breadcrumb = new_project_dir / "project-path.txt"
    breadcrumb.write_text(str(new_path) + "\n")

    print("Duplicated project:")
    print(f"  from: {source_path} ({short_hash(source_hash)})")
    print(f"    to: {new_path} ({short_hash(new_hash)})")
    return 0
