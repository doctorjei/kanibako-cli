"""Duplicate logic for kanibako box."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.names import assign_name, unregister_name
from kanibako.paths import (
    _STANDALONE_META_DIR,
    BoxMode,
    WorksetSpec,
    _resolve_local_dir,
    _resolve_workset_or_connected,
    xdg,
    detect_project_mode,
    load_std_paths,
    resolve_standalone_project,
    resolve_project,
    resolve_workset_project,
)
from kanibako.utils import confirm_prompt


# -- External-source detection --

def _source_is_external(args: argparse.Namespace, std) -> bool:
    """True when the duplicate's source resolves to an external-connected project.

    An external-connected project is one whose live workspace lives outside its
    owning workset (recorded in ``connected.yaml``).  Used to refuse a bare
    duplicate of such a source (the connection is 1:1).
    """
    from kanibako.workset import _find_connected_project

    raw = getattr(args, "source_path", None)
    if not raw:
        return False
    try:
        source_path = Path(raw).resolve()
    except (OSError, ValueError):
        return False
    return _find_connected_project(source_path, std) is not None


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
    if source_mode == BoxMode.named:
        return _duplicate_from_workset(args, source_path, new_path, std, config)

    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if source_mode == BoxMode.primary:
        src_proj = resolve_project(std, config, project_dir=str(source_path), initialize=False)
    else:
        src_proj = resolve_standalone_project(std, config, project_dir=str(source_path), initialize=False)

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
    target_mode = BoxMode.standalone if to_mode_str == "standalone" else BoxMode.primary
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

    # Copy workspace (unless --bare).  The copy SOURCE is the source box's live
    # workspace (``src_proj.project_path``) — for a standalone source that is the
    # ``<root>/workspace`` subdir, NOT the root (which holds kanibako artifacts);
    # for a primary source it is the project root.  For a standalone TARGET the
    # files land in the destination's ``workspace/`` subdir (drift H), since the
    # destination root holds the standalone artifacts (settings.yaml, box_data/,
    # vault/).
    workspace_src = src_proj.project_path
    if not args.bare and workspace_src.is_dir():
        if target_mode == BoxMode.standalone:
            shutil.copytree(
                workspace_src, new_path / "workspace", dirs_exist_ok=args.force,
            )
        else:
            shutil.copytree(workspace_src, new_path, dirs_exist_ok=args.force)

    # Copy metadata into target mode layout.
    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if target_mode == BoxMode.standalone:
        _duplicate_to_standalone(src_proj, new_path, std, args.force)
    else:
        _duplicate_to_local(src_proj, new_path, std, config, args.force)

    print(f"Duplicated project to {target_mode.value} mode:")
    print(f"  from: {source_path}")
    print(f"    to: {new_path}")
    return 0


def _duplicate_to_standalone(src_proj, new_path, std, force):
    """Establish a fresh standalone box at *new_path*.

    A duplicate is a NEW box, so this mirrors ``create --standalone`` /
    ``convert --standalone`` rather than copying the source verbatim: the
    source's box metadata (agent/session state, minus the lock + home + the
    source ``settings.yaml``) is copied into ``box_data/``, its home into
    ``box_data/home``, and its ``settings.yaml`` to the destination ROOT (drift
    I — settings live at ``<root>/settings.yaml``, NOT in ``box_data/``), then
    that root ``settings.yaml`` is REWRITTEN with ``mode=standalone``, a freshly
    generated ``<random24>_<leaf>`` identity (never the source's name), and the
    standalone path table — and the box is registered in ``registry.standalone``.
    Without this the dest would keep the source's ``mode`` (e.g. ``primary``) and
    name, so standalone detection (``_is_standalone_meta_dir`` requires
    ``mode == "standalone"``) would never find it → an orphaned box (BUG#3).
    """
    from kanibako.config import BOX_META_FILE
    from kanibako.paths import establish_standalone
    from kanibako.utils import write_project_gitignore

    dst_metadata = new_path / _STANDALONE_META_DIR
    dst_shell = dst_metadata / "home"
    dst_settings = new_path / BOX_META_FILE

    # Ensure new_path exists for bare duplicates.
    new_path.mkdir(parents=True, exist_ok=True)

    # Copy the source box metadata into box_data/ — preserving misc session
    # files — but NOT the lock, the home (copied separately below), or the
    # source settings.yaml (which is relocated to the ROOT, drift I).
    if force and dst_metadata.is_dir():
        shutil.rmtree(dst_metadata)
    shutil.copytree(
        src_proj.metadata_path, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home", BOX_META_FILE),
        dirs_exist_ok=True,
    )

    if src_proj.shell_path.is_dir():
        if force and dst_shell.is_dir():
            shutil.rmtree(dst_shell)
        shutil.copytree(src_proj.shell_path, dst_shell)

    # Copy the source settings.yaml to the destination ROOT so establish can
    # preserve any non-identity sections (e.g. agent config) when it rewrites
    # the meta in place.
    src_settings = src_proj.metadata_path / BOX_META_FILE
    if src_settings.is_file():
        if force and dst_settings.exists():
            dst_settings.unlink()
        if not dst_settings.exists():
            shutil.copy2(src_settings, dst_settings)

    # Establish the canonical standalone shape (mode=standalone, a FRESH
    # <random24>_<leaf> identity even from a standalone source, the standalone
    # path table) + register it, via the shared core.  The root settings.yaml
    # was just copied above; establish overwrites its meta in place, preserving
    # any other sections copied from the source.
    establish_standalone(
        std, new_path,
        enable_vault=src_proj.enable_vault,
    )

    write_project_gitignore(new_path)

    # Write vault .gitignore if vault exists.
    vault_dir = new_path / "vault"
    if vault_dir.is_dir():
        vault_gitignore = vault_dir / ".gitignore"
        if not vault_gitignore.exists():
            vault_gitignore.write_text("rw/\n")


def _unwind_local_name(std, project_name: str, dst_project: Path) -> None:
    """Best-effort rollback of a default-mode name registration + partial dir.

    Used when a copy fails after :func:`assign_name` has already registered the
    duplicate's name (and possibly created a partial metadata dir), to avoid
    leaving a "registered but no metadata" orphan.  Each step is independently
    guarded so one failure does not mask the rest.
    """
    try:
        unregister_name(std.registry, project_name)
    except Exception:  # noqa: BLE001 - best-effort restore
        pass
    try:
        if dst_project.exists():
            shutil.rmtree(dst_project)
    except Exception:  # noqa: BLE001 - best-effort restore
        pass


def _duplicate_to_local(src_proj, new_path, std, config, force):
    """Copy metadata into default-mode layout for new_path."""
    from kanibako.config import BOX_META_FILE
    from kanibako.paths import BoxMode

    # Assign a new name for the duplicate.  The name MUST be registered first
    # because the destination metadata dir is derived from it (std.boxes/<name>).
    project_name = assign_name(std.registry, str(new_path))
    projects_base = std.boxes
    dst_project = projects_base / project_name

    # The source box's metadata dir (home + agent/session state + settings.yaml):
    # for primary/named it is ``metadata_path`` (boxes/<name>/), but for a
    # standalone source ``metadata_path`` is the project ROOT — its box metadata
    # lives in ``box_data/`` with settings.yaml at the root.  Copy from the right
    # place per mode so the workspace tree is not dragged into the box dir.
    if src_proj.mode is BoxMode.standalone:
        src_meta_dir = src_proj.shell_path.parent   # <root>/box_data
        src_settings = src_proj.metadata_path / BOX_META_FILE  # <root>/settings.yaml
    else:
        src_meta_dir = src_proj.metadata_path
        src_settings = src_proj.metadata_path / BOX_META_FILE

    # Failure-consistency: a crash AFTER assign_name (which registers the name)
    # but DURING the metadata/shell copy below would otherwise strand a
    # "registered but no metadata" orphan.  Unwind the registration + any partial
    # dest dir on failure, then re-raise — duplicate either fully succeeds or
    # leaves no trace.
    try:
        if force and dst_project.is_dir():
            shutil.rmtree(dst_project)
        shutil.copytree(
            src_meta_dir, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock"),
        )
        # Place the source settings.yaml at the box dir root (for a standalone
        # source it lived at the project root, not inside box_data/).
        if src_settings.is_file():
            shutil.copy2(src_settings, dst_project / BOX_META_FILE)

        # Ensure home is inside the project dir.
        if src_proj.shell_path.is_dir():
            dst_home = dst_project / "home"
            if not dst_home.is_dir():
                shutil.copytree(src_proj.shell_path, dst_home)
    except BaseException:
        _unwind_local_name(std, project_name, dst_project)
        raise


def _duplicate_to_workset(args, std, config) -> int:
    """Duplicate a project into a workset (source untouched)."""
    from kanibako.commands.box._lifecycle import copy_into_workset
    from kanibako.workset import list_worksets, load_workset

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
    if source_mode == BoxMode.named:
        print("Error: source is already a workset project.", file=sys.stderr)
        return 1

    # R2: every box name is lowercase — fold a user-supplied --name, and also
    # lowercase the basename-derived default for a consistent invariant.
    proj_name = (getattr(args, "project_name", None) or source_path.name).lower()

    # Validate name not taken.
    for p in ws.projects:
        if p.name == proj_name:
            print(f"Error: project '{proj_name}' already exists in workset '{ws_name}'.", file=sys.stderr)
            return 1

    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if source_mode == BoxMode.primary:
        src_proj = resolve_project(std, config, project_dir=str(source_path), initialize=False)
    else:
        src_proj = resolve_standalone_project(std, config, project_dir=str(source_path), initialize=False)

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

    # Re-root the project into the workset group (copy workspace unless --bare).
    # std-aware: the duplicate always lands a fresh INTERNAL workspace (a copy,
    # never a connection); a bare duplicate of an external-connected source is
    # refused upstream in run_duplicate per the 1:1 connected.yaml policy.
    copy_into_workset(
        ws, proj_name, src_proj.metadata_path, src_proj.shell_path,
        source_path, source_mode, copy_workspace=not args.bare, std=std,
    )

    print("Duplicated project to workset:")
    print(f"  from:    {source_path}")
    print(f"  workset: {ws_name}/{proj_name}")
    return 0


def _duplicate_from_workset(args, source_path, new_path, std, config) -> int:
    """Duplicate a workset project to default-mode or standalone layout (source untouched)."""
    to_mode_str = args.to_mode

    # Resolve via workset-or-connected fallback: an external-connected source
    # lives outside any workset tree, so the in-tree lookup alone would miss it
    # and raise an uncaught WorksetError.
    ws, proj_name = _resolve_workset_or_connected(source_path, std)
    if proj_name is None:
        print("Error: not inside a specific project workspace.", file=sys.stderr)
        return 1
    src_proj = resolve_workset_project(
        WorksetSpec.from_workset(ws), proj_name, std, config, initialize=False,
    )

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for source path: {source_path}", file=sys.stderr)
        return 1

    target_mode = BoxMode.standalone if to_mode_str == "standalone" else BoxMode.primary

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

    # Copy workspace (unless --bare).  Copy from the RESOLVED workspace, not a
    # hardcoded ws.workspaces_dir/proj_name -- for an external-connected source
    # the latter is only the discoverability symlink, while project_path (set
    # via resolve_workset_project's meta["workspace"] override) is the live
    # workspace.  No-op difference for ordinary internal workset sources.
    if not args.bare:
        ws_workspace = src_proj.project_path
        if ws_workspace.is_dir():
            shutil.copytree(ws_workspace, new_path, dirs_exist_ok=args.force)

    # Copy metadata into target layout.
    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if target_mode == BoxMode.standalone:
        _duplicate_to_standalone(src_proj, new_path, std, args.force)
    else:
        _duplicate_to_local(src_proj, new_path, std, config, args.force)

    print(f"Duplicated project to {target_mode.value} mode:")
    print(f"  from: {ws.name}/{proj_name}")
    print(f"    to: {new_path}")
    return 0


def run_duplicate(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # --box names the SUBJECT (the box being duplicated); reconcile with the
    # source_path positional (same → warn / differ → error).
    from kanibako.commands.flags import resolve_subject_value
    args.source_path = resolve_subject_value(
        getattr(args, "source_path", None), getattr(args, "box", None),
    )

    # Refuse --bare on an external-connected source ONLY when the bare copy
    # would alias the same external dir.  connected.yaml is a 1:1 mapping
    # (external path -> one {workset, project}); a bare duplicate has no
    # workspace of its own, so it could only point at the SAME external dir as
    # the original -> would violate the 1:1 mapping.  This does NOT apply when
    # duplicating --to default/standalone: there the bare result makes new_path
    # itself the workspace (no aliasing), so it is allowed.
    _to_mode = getattr(args, "to_mode", None)
    if (
        _to_mode not in ("default", "standalone")
        and getattr(args, "bare", False)
        and _source_is_external(args, std)
    ):
        print(
            "Error: cannot --bare duplicate an external-connected project "
            "(its connection is 1:1).",
            file=sys.stderr,
        )
        print(
            "  Use a non-bare copy (lands a fresh workspace), or pass an "
            "explicit fresh path.",
            file=sys.stderr,
        )
        return 1

    # Cross-mode duplication.
    if getattr(args, "to_mode", None) is not None:
        return _run_duplicate_cross_mode(args, std, config)

    # No --to: the default-mode path below resolves the source via
    # _resolve_local_dir, which only knows PRIMARY (central-store) boxes.  A
    # STANDALONE or NAMED source would miss → a misleading "no project data
    # found" (BUG-B).  Detect the source mode (ancestor-walk) and, for a
    # non-primary source, default the target mode sensibly so a bare
    # `box duplicate <src> <dst>` works: standalone → a fresh standalone box at
    # the destination (matching `--to standalone`); named → a default-mode box
    # (matching `--to default`).
    src_for_detect = Path(args.source_path)
    if src_for_detect.is_dir():
        src_mode = detect_project_mode(src_for_detect.resolve(), std, config).mode
        if src_mode is BoxMode.standalone:
            args.to_mode = "standalone"
            return _run_duplicate_cross_mode(args, std, config)
        if src_mode is BoxMode.named:
            args.to_mode = "default"
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
    source_name, source_project_dir = _resolve_local_dir(
        std.registry, str(source_path), std.boxes,
    )

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
    new_name, new_project_dir = _resolve_local_dir(
        std.registry, str(new_path), std.boxes,
    )

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

    # Assign a new name for the duplicate.  The name MUST be registered first
    # because the destination metadata dir is derived from it (std.boxes/<name>).
    dup_name = assign_name(std.registry, str(new_path))
    new_project_dir = std.boxes / dup_name

    # Failure-consistency: a crash AFTER assign_name but DURING the metadata copy
    # would otherwise strand a "registered but no metadata" orphan.  Unwind the
    # registration + any partial dest dir on failure, then re-raise.  (The
    # workspace copytree above runs BEFORE registration, so it is intentionally
    # outside this unwind.)
    try:
        # Copy metadata (entire project dir including home/).
        if args.force and new_project_dir.is_dir():
            shutil.rmtree(new_project_dir)
        shutil.copytree(
            source_project_dir, new_project_dir,
            ignore=shutil.ignore_patterns(".kanibako.lock"),
        )
    except BaseException:
        _unwind_local_name(std, dup_name, new_project_dir)
        raise

    print("Duplicated project:")
    print(f"  from: {source_path} ({source_name})")
    print(f"    to: {new_path} ({dup_name})")
    return 0
