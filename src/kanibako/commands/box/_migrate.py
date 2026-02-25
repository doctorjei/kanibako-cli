"""Migrate and cross-mode conversion logic for kanibako box."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.paths import (
    ProjectMode,
    _ensure_human_vault_symlink,
    _find_workset_for_path,
    _remove_human_vault_symlink,
    _remove_project_vault_symlink,
    xdg,
    detect_project_mode,
    load_std_paths,
    resolve_decentralized_project,
    resolve_project,
    resolve_workset_project,
)
from kanibako.utils import confirm_prompt, project_hash, short_hash


def run_migrate(args: argparse.Namespace) -> int:
    import os

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # Cross-mode conversion.
    if getattr(args, "to_mode", None) is not None:
        return _run_convert(args, std, config)

    # Same-mode path remap: old_path is required.
    if args.old_path is None:
        print("Error: old_path is required for path remap (use --to for mode conversion).", file=sys.stderr)
        return 1

    # Resolve paths — old path may no longer exist, so use str directly.
    old_path = Path(args.old_path).resolve()
    new_path = Path(args.new_path).resolve() if args.new_path else Path(os.getcwd()).resolve()

    # Validate: paths must differ.
    if old_path == new_path:
        print("Error: old and new paths are the same.", file=sys.stderr)
        return 1

    # Validate: new path must exist as a directory.
    if not new_path.is_dir():
        print(f"Error: new path does not exist as a directory: {new_path}", file=sys.stderr)
        return 1

    # Compute hashes.
    old_hash = project_hash(str(old_path))
    new_hash = project_hash(str(new_path))

    projects_base = std.data_path / "boxes"
    old_project_dir = projects_base / old_hash
    new_project_dir = projects_base / new_hash

    # Validate: old project data must exist.
    if not old_project_dir.is_dir():
        print(
            f"Error: no project data found for old path: {old_path}",
            file=sys.stderr,
        )
        print(f"  (expected: {old_project_dir})", file=sys.stderr)
        return 1

    # Validate: new project data must NOT already exist.
    if new_project_dir.is_dir():
        print(
            f"Error: project data already exists for new path: {new_path}",
            file=sys.stderr,
        )
        print("  Use 'kanibako box purge' to remove it first.", file=sys.stderr)
        return 1

    # Warn if lock file exists.
    lock_file = old_project_dir / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            try:
                confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
            except Exception:
                print("Aborted.")
                return 2

    # Confirm with user.
    if not args.force:
        print("Migrate project data:")
        print(f"  from: {old_path}")
        print(f"    to: {new_path}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Remove old human-friendly symlink before rename.
    human_vault_dir = std.data_path / config.paths_vault
    _remove_human_vault_symlink(human_vault_dir, old_project_dir / "vault")

    # Rename project directory (includes home/ inside it).
    old_project_dir.rename(new_project_dir)

    # Update the breadcrumb.
    breadcrumb = new_project_dir / "project-path.txt"
    breadcrumb.write_text(str(new_path) + "\n")

    # Create new human-friendly symlink (best-effort).
    vault_parent = new_project_dir / "vault"
    if vault_parent.is_dir():
        _ensure_human_vault_symlink(human_vault_dir, new_path, vault_parent)

    print("Migrated project data:")
    print(f"  from: {old_path} ({short_hash(old_hash)})")
    print(f"    to: {new_path} ({short_hash(new_hash)})")
    return 0


# -- Cross-mode conversion helpers --

def _run_convert(args: argparse.Namespace, std, config) -> int:
    """Dispatch cross-mode conversion based on --to flag."""
    import os

    to_mode_str = args.to_mode

    # Convert TO workset: separate code path.
    if to_mode_str == "workset":
        return _convert_to_workset(args, std, config)

    # Resolve project path (positional arg or cwd).
    raw_path = args.old_path or os.getcwd()
    project_path = Path(raw_path).resolve()

    if not project_path.is_dir():
        print(f"Error: project path does not exist: {project_path}", file=sys.stderr)
        return 1

    # Detect current mode.
    current_mode = detect_project_mode(project_path, std, config).mode

    # Convert FROM workset: separate code path.
    if current_mode == ProjectMode.workset:
        return _convert_from_workset(args, project_path, std, config)

    # Parse target mode.
    target_mode = ProjectMode.decentralized if to_mode_str == "decentralized" else ProjectMode.account_centric

    if current_mode == target_mode:
        print(f"Error: project is already in {current_mode.value} mode.", file=sys.stderr)
        return 1

    # Resolve current project paths.
    if current_mode == ProjectMode.account_centric:
        proj = resolve_project(std, config, project_dir=str(project_path), initialize=False)
    else:
        proj = resolve_decentralized_project(std, config, project_dir=str(project_path), initialize=False)

    # Check that project data exists.
    if not proj.metadata_path.is_dir():
        print(f"Error: no project data found for {project_path}", file=sys.stderr)
        return 1

    # Lock file warning.
    lock_file = proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Warning: lock file found — a container may be running for this project.",
            file=sys.stderr,
        )
        if not args.force:
            print("Aborted.")
            return 2

    # Confirm with user.
    if not args.force:
        print(f"Convert project to {target_mode.value} mode:")
        print(f"  project: {project_path}")
        print(f"  from:    {current_mode.value}")
        print(f"    to:    {target_mode.value}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Dispatch.
    if target_mode == ProjectMode.decentralized:
        _convert_ac_to_decentral(project_path, std, config, proj)
    else:
        _convert_decentral_to_ac(project_path, std, config, proj)

    print(f"Converted project to {target_mode.value} mode:")
    print(f"  project: {project_path}")
    return 0


def _convert_ac_to_decentral(project_path, std, config, proj):
    """Convert an account-centric project to decentralized layout."""
    from kanibako.commands.init import _write_project_gitignore

    dst_metadata = project_path / ".kanibako"
    dst_shell = dst_metadata / "shell"

    # Copy metadata (excluding lock file and shell/ directory).
    shutil.copytree(
        proj.metadata_path, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
    )

    # Remove breadcrumb (decentralized doesn't use it).
    breadcrumb = dst_metadata / "project-path.txt"
    if breadcrumb.exists():
        breadcrumb.unlink()

    # Copy shell.
    if proj.shell_path.is_dir():
        shutil.copytree(proj.shell_path, dst_shell)

    # Write .gitignore entries for .kanibako/.
    _write_project_gitignore(project_path)

    # Write vault .gitignore if vault exists but gitignore doesn't.
    vault_dir = project_path / "vault"
    if vault_dir.is_dir():
        vault_gitignore = vault_dir / ".gitignore"
        if not vault_gitignore.exists():
            vault_gitignore.write_text("share-rw/\n")

    # Remove vault symlinks before cleaning up old AC data.
    human_vault_dir = std.data_path / config.paths_vault
    _remove_human_vault_symlink(human_vault_dir, proj.metadata_path / "vault")
    _remove_project_vault_symlink(project_path)

    # Clean up old AC data.
    shutil.rmtree(proj.metadata_path)


def _convert_decentral_to_ac(project_path, std, config, proj):
    """Convert a decentralized project to account-centric layout."""
    phash = project_hash(str(project_path))
    settings_base = std.data_path / "boxes"
    dst_project = settings_base / phash

    # Copy metadata (excluding lock file and shell/).
    shutil.copytree(
        proj.metadata_path, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
    )

    # Write breadcrumb.
    (dst_project / "project-path.txt").write_text(str(project_path) + "\n")

    # Copy shell into the settings dir.
    if proj.shell_path.is_dir():
        dst_shell = dst_project / "shell"
        shutil.copytree(proj.shell_path, dst_shell)

    # Create human-friendly vault symlink if robust layout.
    vault_parent = dst_project / "vault"
    if vault_parent.is_dir():
        human_vault_dir = std.data_path / config.paths_vault
        _ensure_human_vault_symlink(human_vault_dir, project_path, vault_parent)

    # Clean up old decentralized data.
    shutil.rmtree(proj.metadata_path)


# -- Workset conversion helpers --

def _convert_to_workset(args, std, config) -> int:
    """Convert an AC or decentralized project into a workset."""
    import os

    from kanibako.workset import add_project, list_worksets, load_workset

    ws_name = getattr(args, "workset", None)
    if not ws_name:
        print("Error: --workset is required when converting to workset mode.", file=sys.stderr)
        return 1

    # Load target workset.
    registry = list_worksets(std)
    if ws_name not in registry:
        print(f"Error: workset '{ws_name}' not found.", file=sys.stderr)
        return 1
    ws = load_workset(registry[ws_name])

    # Resolve source project.
    raw_path = args.old_path or os.getcwd()
    project_path = Path(raw_path).resolve()

    if not project_path.is_dir():
        print(f"Error: project path does not exist: {project_path}", file=sys.stderr)
        return 1

    current_mode = detect_project_mode(project_path, std, config).mode
    if current_mode == ProjectMode.workset:
        print("Error: project is already in workset mode.", file=sys.stderr)
        return 1

    # Determine project name.
    proj_name = getattr(args, "project_name", None) or project_path.name

    # Validate name not taken.
    for p in ws.projects:
        if p.name == proj_name:
            print(f"Error: project '{proj_name}' already exists in workset '{ws_name}'.", file=sys.stderr)
            return 1

    # Resolve source paths.
    if current_mode == ProjectMode.account_centric:
        src_proj = resolve_project(std, config, project_dir=str(project_path), initialize=False)
    else:
        src_proj = resolve_decentralized_project(std, config, project_dir=str(project_path), initialize=False)

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for {project_path}", file=sys.stderr)
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

    in_place = getattr(args, "in_place", False)

    # Confirm.
    if not args.force:
        action = "in-place (workspace stays)" if in_place else "move workspace into workset"
        print(f"Convert project to workset mode ({action}):")
        print(f"  project:  {project_path}")
        print(f"  workset:  {ws_name}")
        print(f"  name:     {proj_name}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # Register project in workset (creates skeleton dirs).
    add_project(ws, proj_name, project_path)

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

    # Move workspace unless --in-place.
    if not in_place:
        dst_workspace = ws.workspaces_dir / proj_name
        # Copy workspace content (exclude decentralized metadata).
        ignore = None
        if current_mode == ProjectMode.decentralized:
            ignore = shutil.ignore_patterns(".kanibako")
        shutil.copytree(project_path, dst_workspace, ignore=ignore, dirs_exist_ok=True)

    # Remove vault symlinks before cleaning up old metadata.
    if current_mode == ProjectMode.account_centric:
        human_vault_dir = std.data_path / config.paths_vault
        _remove_human_vault_symlink(human_vault_dir, src_proj.metadata_path / "vault")
    _remove_project_vault_symlink(project_path)

    # Clean up old metadata.
    shutil.rmtree(src_proj.metadata_path)
    if src_proj.shell_path.is_dir():
        shutil.rmtree(src_proj.shell_path)

    print("Converted project to workset mode:")
    print(f"  workset: {ws_name}/{proj_name}")
    return 0


def _convert_from_workset(args, project_path, std, config) -> int:
    """Convert a workset project to AC or decentralized mode."""
    to_mode_str = args.to_mode

    ws, proj_name = _find_workset_for_path(project_path, std)
    if proj_name is None:
        print("Error: not inside a specific project workspace.", file=sys.stderr)
        return 1
    src_proj = resolve_workset_project(ws, proj_name, std, config, initialize=False)

    target_mode = ProjectMode.decentralized if to_mode_str == "decentralized" else ProjectMode.account_centric

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for {project_path}", file=sys.stderr)
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

    # Determine destination path: use source_path from workset project.
    found_proj = None
    for p in ws.projects:
        if p.name == proj_name:
            found_proj = p
            break
    dest_path = found_proj.source_path if found_proj else project_path

    # Confirm.
    if not args.force:
        print(f"Convert workset project to {target_mode.value} mode:")
        print(f"  workset:  {ws.name}/{proj_name}")
        print(f"  target:   {dest_path}")
        print()
        try:
            confirm_prompt("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    if target_mode == ProjectMode.account_centric:
        _convert_ws_to_ac(src_proj, dest_path, std, config)
    else:
        _convert_ws_to_decentral(src_proj, dest_path)

    # Move workspace from workset to destination if it exists and differs.
    ws_workspace = ws.workspaces_dir / proj_name
    in_place = getattr(args, "in_place", False)
    if not in_place and ws_workspace.is_dir() and ws_workspace != dest_path:
        dest_path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ws_workspace, dest_path, dirs_exist_ok=True)

    # Remove workset registration + workset dirs.
    from kanibako.workset import remove_project

    remove_project(ws, proj_name, remove_files=True)

    print(f"Converted project to {target_mode.value} mode:")
    print(f"  project: {dest_path}")
    return 0


def _convert_ws_to_ac(src_proj, dest_path, std, config):
    """Copy workset project metadata into account-centric layout."""
    phash = project_hash(str(dest_path))
    projects_base = std.data_path / "boxes"
    dst_project = projects_base / phash

    # Copy metadata (excluding lock and home/).
    shutil.copytree(
        src_proj.metadata_path, dst_project,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
    )

    # Write breadcrumb.
    (dst_project / "project-path.txt").write_text(str(dest_path) + "\n")

    # Copy home.
    if src_proj.shell_path.is_dir():
        dst_home = dst_project / "shell"
        shutil.copytree(src_proj.shell_path, dst_home)

    # Create human-friendly vault symlink if robust layout.
    vault_parent = dst_project / "vault"
    if vault_parent.is_dir():
        human_vault_dir = std.data_path / config.paths_vault
        _ensure_human_vault_symlink(human_vault_dir, dest_path, vault_parent)


def _convert_ws_to_decentral(src_proj, dest_path):
    """Copy workset project metadata into decentralized layout."""
    from kanibako.commands.init import _write_project_gitignore

    dest_path.mkdir(parents=True, exist_ok=True)
    dst_metadata = dest_path / ".kanibako"
    dst_shell = dst_metadata / "shell"

    # Copy metadata (excluding lock and shell/).
    shutil.copytree(
        src_proj.metadata_path, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "shell"),
    )

    # Copy shell.
    if src_proj.shell_path.is_dir():
        shutil.copytree(src_proj.shell_path, dst_shell)

    _write_project_gitignore(dest_path)

    # Write vault .gitignore if vault exists.
    vault_dir = dest_path / "vault"
    if vault_dir.is_dir():
        vault_gitignore = vault_dir / ".gitignore"
        if not vault_gitignore.exists():
            vault_gitignore.write_text("share-rw/\n")
