"""Duplicate logic for kanibako box."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kanibako.settings.config import (
    carried_box_settings,
    config_file_path,
    load_config,
)
from kanibako.settings.config_io import dump_doc
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.core_defaults import materialize_canon_skeleton
from kanibako.settings.paths import (
    _STANDALONE_META_DIR,
    BoxMode,
    WorksetSpec,
    _resolve_local_dir,
    _resolve_workset_or_connected,
    assign_primary_box_name,
    box_metadata_dir,
    box_workset_settings_paths,
    xdg,
    detect_project_mode,
    load_std_paths,
    primary_box_name_for_workspace,
    resolve_standalone_project,
    resolve_project,
    resolve_workset_project,
    unregister_primary_box_name,
)
from kanibako.utils import confirm_prompt


# -- External-source detection --

def _source_is_external(args: argparse.Namespace, std) -> bool:
    """True when the duplicate's source resolves to an external-connected project.

    An external-connected project is one whose live workspace lives outside its
    owning workset (a per-workset ``boxes:`` entry whose path is EXTERNAL, D10).
    Used to refuse a bare duplicate of such a source (the connection is 1:1).
    """
    from kanibako.launch import box_resolve

    raw = getattr(args, "source_path", None)
    if not raw:
        return False
    try:
        source_path = Path(raw).resolve()
    except (OSError, ValueError):
        return False
    return box_resolve.find_connected_external_box(source_path, std) is not None


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

    # F-3 (guard-before-copy): for a PRIMARY (local) target, front-run the
    # one-box-per-workspace-path (Guard-1) refusal BEFORE prompting or copying, so
    # a duplicate onto an ALREADY-registered primary workspace costs no prompt and
    # no copy.  assign_primary_box_name raises this same ProjectError, but only
    # AFTER the workspace copy — and a no-force copy onto an existing dir would
    # first raise FileExistsError (an OSError), stranding the copy uncaught.
    # Standalone targets mint a fresh <kuid> identity outside the primary
    # membership, so the guard does not apply to them.
    if target_mode == BoxMode.primary:
        existing_box = primary_box_name_for_workspace(
            std.primary_workset, str(new_path),
        )
        if existing_box is not None:
            print(
                f"Error: Workspace {str(new_path)!r} is already registered as "
                f"box {existing_box!r}; refusing to duplicate onto it "
                f"(one box per workspace path).",
                file=sys.stderr,
            )
            return 1

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
    # F2/F-3: capture whether the destination dir pre-existed BEFORE the workspace
    # copy, so a refusal/OSError can roll back a copy THIS call created without
    # deleting a pre-existing dir.
    new_path_existed = new_path.is_dir()

    workspace_src = src_proj.project_path

    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if target_mode == BoxMode.standalone:
        if not args.bare and workspace_src.is_dir():
            shutil.copytree(
                workspace_src, new_path / "workspace", dirs_exist_ok=args.force,
            )
        _duplicate_to_standalone(src_proj, new_path, std, args.force)
    else:
        # PRIMARY (local) target.  F-3: copy the workspace and lay down the
        # metadata inside ONE try that catches BOTH a Guard-1 ProjectError (a late
        # defense-in-depth re-check inside _duplicate_to_local) AND any OSError
        # mid copy/metadata, so a partial dir THIS call created never survives.
        # Roll back only when new_path did NOT pre-exist (F2: never delete a dir
        # the user already had); _duplicate_to_local's own unwind already cleans
        # the boxes/<name> metadata dir + its registration.
        from kanibako.errors import ProjectError
        try:
            if not args.bare and workspace_src.is_dir():
                shutil.copytree(workspace_src, new_path, dirs_exist_ok=args.force)
            _duplicate_to_local(src_proj, new_path, std, config, args.force)
        except FileExistsError:
            # F-3 (NIT): a no-force copy onto a pre-existing (unregistered) dir
            # raises FileExistsError from copytree — surface the friendly
            # destination-exists guidance (matching run_duplicate's non-cross-mode
            # message) instead of the raw ``[Errno 17] File exists`` traceback.
            # The dir pre-existed, so new_path_existed is True → no deletion.
            print(f"Error: destination already exists: {new_path}", file=sys.stderr)
            print("  Use --force to overwrite.", file=sys.stderr)
            if not new_path_existed and new_path.is_dir():
                # ⚑ The failure points below are all AFTER the skeleton is created,
                # so a plain rmtree leaves a half-built box behind (silently, under
                # ignore_errors) instead of rolling the duplicate back cleanly.
                remove_box_tree(new_path)
            return 1
        except (ProjectError, OSError) as e:
            print(f"Error: {e}", file=sys.stderr)
            if not new_path_existed and new_path.is_dir():
                # ⚑ The failure points below are all AFTER the skeleton is created,
                # so a plain rmtree leaves a half-built box behind (silently, under
                # ignore_errors) instead of rolling the duplicate back cleanly.
                remove_box_tree(new_path)
            return 1

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
    generated ``<kuid>_<leaf>`` identity (never the source's name), and the
    standalone path table — and the box is registered in ``registry.standalone``.
    Without this the dest would keep the source's ``mode`` (e.g. ``primary``) and
    name, so standalone detection (``_is_standalone_meta_dir`` requires
    ``mode == "standalone"``) would never find it → an orphaned box (BUG#3).
    """
    from kanibako.settings.config import BOX_META_FILE
    from kanibako.errors import ProjectError
    from kanibako.settings.paths import establish_standalone
    from kanibako.utils import write_project_gitignore

    dst_metadata = new_path / _STANDALONE_META_DIR
    dst_shell = dst_metadata / "home"
    # (The destination ROOT settings.yaml is written by ``establish_standalone`` below
    # — it is the WORKSET tier and carries the FRESH workset.kuid, never a copy of the
    # source's.  The box tier is ``dst_metadata / BOX_META_FILE``, handled further down.)

    # Ensure new_path exists for bare duplicates.
    new_path.mkdir(parents=True, exist_ok=True)

    # Copy the source box metadata into box_data/ — preserving misc session
    # files — but NOT the lock, the home (copied separately below), or the
    # source settings.yaml (which is relocated to the ROOT, drift I).
    if force and dst_metadata.is_dir() and not remove_box_tree(dst_metadata):
        # The copytree below uses dirs_exist_ok=True, so a silently-failed removal
        # would MERGE the new box into the old one rather than replace it.
        raise ProjectError(
            f"could not remove the existing box data at {dst_metadata}.\n"
            f"Try: podman unshare rm -rf {dst_metadata}"
        )
    shutil.copytree(
        src_proj.metadata_path, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home", BOX_META_FILE),
        dirs_exist_ok=True,
    )

    if src_proj.shell_path.is_dir():
        if force and dst_shell.is_dir():
            # The home carries the root-owned canon skeleton (J-7); a bare rmtree
            # fails with EACCES and strands a half-removed destination.
            remove_box_tree(dst_shell)
        shutil.copytree(src_proj.shell_path, dst_shell)
        # copytree carries the skeleton's modes but not its ownership — re-assert.
        materialize_canon_skeleton(dst_shell)

    # Carry the source's box-scope settings into the destination's BOX TIER (M-8) —
    # box tier first, with a pre-P2 standalone source's root-stored ``box.*`` keys
    # underlaid (:func:`kanibako.settings.config.carried_box_settings`).  Without that underlay
    # EVERY box created before the box tier existed loses box.image & friends on the
    # first duplicate.  ``establish_standalone`` below then read-modify-writes
    # ``box.enable_vault`` into this SAME file, preserving what was carried, and
    # writes the FRESH ``workset.kuid`` to the destination ROOT.
    src_box, src_ws = box_workset_settings_paths(src_proj)
    carried = carried_box_settings(src_box, src_ws)
    dst_box_settings = dst_metadata / BOX_META_FILE
    if carried:
        if force and dst_box_settings.exists():
            dst_box_settings.unlink()
        if not dst_box_settings.exists():
            dump_doc(dst_box_settings, carried)

    # Establish the canonical standalone shape (mode=standalone, a FRESH
    # <kuid>_<leaf> identity even from a standalone source, the standalone
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

    Used when a copy fails after :func:`~kanibako.settings.paths.assign_primary_box_name` has already registered the
    duplicate's name (and possibly created a partial metadata dir), to avoid
    leaving a "registered but no metadata" orphan.  Each step is independently
    guarded so one failure does not mask the rest.
    """
    try:
        unregister_primary_box_name(std.primary_workset, project_name)
    except Exception:  # noqa: BLE001 - best-effort restore
        pass
    try:
        if dst_project.exists():
            remove_box_tree(dst_project)
    except Exception:  # noqa: BLE001 - best-effort restore
        pass


def _assert_dup_home_free(std, name: str) -> None:
    """Refuse a duplicate whose minted primary home is a deregistered/orphaned box.

    ⚑ Box-lifecycle cleanup (I4 follow-up).  ``assign_primary_box_name``'s picker
    does NOT consult ``std.boxes`` (``boxes_dir=None``), so a freshly-minted
    duplicate name can land on a ``std.boxes/<name>`` dir still occupied by a
    DEREGISTERED box (retained by ``rm``) or a hand-left ORPHAN.  With ``--force``
    the copy path ``rmtree``s that home before copying — the very data-loss window
    the create-side guard closes.  REUSE that guard here, BEFORE any home
    materialises.

    On a conflict, unwind ONLY the just-registered name (``assign_primary_box_name``
    registered it a moment ago) and re-raise :class:`ProjectError` — NEVER touch
    the occupied home dir, which is the retained data we are protecting (so the
    reused guard is deliberately NOT wrapped in ``_unwind_local_name``, whose
    ``rmtree`` would delete it).  The caller surfaces the register/purge guidance.
    A genuinely-fresh duplicate name has no such dir, so this is a no-op for it.
    """
    from kanibako.commands.box._parser import _assert_primary_home_free_for_create
    from kanibako.errors import ProjectError

    try:
        _assert_primary_home_free_for_create(std, name)
    except ProjectError:
        unregister_primary_box_name(std.primary_workset, name)
        raise


def _duplicate_to_local(src_proj, new_path, std, config, force):
    """Copy metadata into default-mode layout for new_path."""
    from kanibako.settings.config import BOX_META_FILE

    # Assign a new name for the duplicate.  The name MUST be registered first
    # because the destination metadata dir is derived from it (std.boxes/<name>).
    # Registers the PRIMARY membership (the sole store; a duplicate now joins the
    # membership like any other primary box — closing the old global-only gap).
    project_name = assign_primary_box_name(
        std.primary_workset, std.registry, str(new_path),
    )
    projects_base = std.boxes
    dst_project = projects_base / project_name

    # ⚑ Refuse (register/purge guidance) if this home is a deregistered/orphaned
    # box before any copy — reuse the create-side guard.  Raises ProjectError
    # (name already unwound); callers surface it.  No-op for a fresh dup name.
    _assert_dup_home_free(std, project_name)

    # The source box's metadata dir (home + agent/session state): for primary/named it
    # is ``metadata_path`` (boxes/<name>/), but for a standalone source
    # ``metadata_path`` is the project ROOT — its box metadata lives in ``box_data/``.
    # Copy from the right place per mode so the workspace tree is not dragged into the
    # box dir.  The carried box settings come from the ONE pair (M-8), mode-aware:
    # box tier <root>/box_data/settings.yaml for a standalone source,
    # <metadata_path>/settings.yaml otherwise — with a pre-P2 standalone source's
    # root-stored ``box.*`` keys underlaid so a legacy box does not lose them.
    src_box, src_ws = box_workset_settings_paths(src_proj)
    carried = carried_box_settings(src_box, src_ws)
    src_meta_dir = box_metadata_dir(src_proj.mode, src_proj.metadata_path)

    # Failure-consistency: a crash AFTER assign_primary_box_name (which registers it)
    # but DURING the metadata/shell copy below would otherwise strand a
    # "registered but no metadata" orphan.  Unwind the registration + any partial
    # dest dir on failure, then re-raise — duplicate either fully succeeds or
    # leaves no trace.
    try:
        if force and dst_project.is_dir():
            remove_box_tree(dst_project)
        shutil.copytree(
            src_meta_dir, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock"),
        )
        # Deliver the carried box settings to the DESTINATION's box tier (which for a
        # primary/named destination is <dst_project>/settings.yaml).  The copytree
        # above already places a standalone source's box tier there; this overwrites
        # it with the carried doc so the legacy underlay is applied and the source's
        # ``workset:`` identity is not inherited.
        if carried:
            dump_doc(dst_project / BOX_META_FILE, carried)

        # Ensure home is inside the project dir.
        if src_proj.shell_path.is_dir():
            dst_home = dst_project / "home"
            if not dst_home.is_dir():
                shutil.copytree(src_proj.shell_path, dst_home)
            materialize_canon_skeleton(dst_home)
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
        from kanibako.errors import ProjectError
        try:
            _duplicate_to_local(src_proj, new_path, std, config, args.force)
        except ProjectError as e:
            # A local target onto a deregistered/orphaned (or name-colliding) home
            # is refused with register/purge guidance rather than clobbered.
            print(f"Error: {e}", file=sys.stderr)
            return 1

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
    source_name, source_project_dir = _resolve_local_dir(std, str(source_path))

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
    new_name, new_project_dir = _resolve_local_dir(std, str(new_path))

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
    # The PRIMARY membership enforces one box per workspace path (Bug-A guard), so
    # a bare duplicate whose destination workspace is ALREADY a registered box
    # refuses cleanly rather than mint a second box for the same workspace.
    from kanibako.errors import ProjectError
    try:
        dup_name = assign_primary_box_name(
            std.primary_workset, std.registry, str(new_path),
        )
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    new_project_dir = std.boxes / dup_name

    # ⚑ Refuse if the minted home is a deregistered/orphaned box before the copy
    # (--force would rmtree it below → I4 data-loss).  Reuse the create-side guard;
    # the name is unwound inside on conflict, and the protected dir is untouched.
    try:
        _assert_dup_home_free(std, dup_name)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Failure-consistency: a crash AFTER assign_primary_box_name but DURING the metadata copy
    # would otherwise strand a "registered but no metadata" orphan.  Unwind the
    # registration + any partial dest dir on failure, then re-raise.  (The
    # workspace copytree above runs BEFORE registration, so it is intentionally
    # outside this unwind.)
    try:
        # Copy metadata (entire project dir including home/).
        if args.force and new_project_dir.is_dir():
            remove_box_tree(new_project_dir)
        shutil.copytree(
            source_project_dir, new_project_dir,
            ignore=shutil.ignore_patterns(".kanibako.lock"),
        )
        # The copy included home/ — re-assert its canon skeleton's ownership (J-7).
        _dup_home = new_project_dir / "home"
        if _dup_home.is_dir():
            materialize_canon_skeleton(_dup_home)
    except BaseException:
        _unwind_local_name(std, dup_name, new_project_dir)
        raise

    print("Duplicated project:")
    print(f"  from: {source_path} ({source_name})")
    print(f"    to: {new_path} ({dup_name})")
    return 0
