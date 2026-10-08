"""Duplicate logic for kanibako box."""

from __future__ import annotations

import argparse
import shlex
import shutil
import sys
from pathlib import Path

from kanibako.settings.bootstrap import STANDALONE_META_DIR
from kanibako.settings.config import (
    BOX_META_FILE,
    WORKSET_META_FILE,
    carried_box_settings,
    user_config_file,
    load_config,
    read_box_enable_vault,
)
from kanibako.identifiers import find_identifier
from kanibako.settings.config_io import dump_doc, refuse_scalar_sections
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.core_defaults import materialize_canon_skeleton
from kanibako.tree_copy import copy_tree_keeping_links, failed_entries, lay_root_link
from kanibako.settings.paths import (
    BoxMode,
    WorksetSpec,
    _early_scope,
    _resolve_local_dir,
    _resolve_workset_or_connected,
    assign_primary_box_name,
    box_metadata_dir,
    box_workset_settings_paths,
    detect_project_mode,
    load_std_paths,
    primary_box_name_for_workspace,
    resolve_standalone_project,
    resolve_project,
    resolve_workset_project,
    unregister_primary_box_name,
)
from kanibako.utils import confirm_prompt, literal_path
from kanibako.channels.channels import workset_name_token, workset_root
from kanibako.settings.workset_dirkeys import EarlyScope, refuse_inherited_per_owner


def _refuse_inherited(std, source, target: tuple[Path, EarlyScope]) -> None:
    """Before ``--force`` overwrites: refuse a collided value the source's or target's workset inherits."""
    refuse_inherited_per_owner(
        workset_root(source, std), EarlyScope(std.early_system, workset_name_token(source)))
    refuse_inherited_per_owner(*target)


def _refuse_existing_destination(path: Path) -> int:
    print(f"Error: destination already exists: {path}", file=sys.stderr)
    print("  Use --force to overwrite.", file=sys.stderr)
    return 1


def _local_target(std, mode: BoxMode, new_path: Path) -> tuple[Path, EarlyScope]:
    if mode is BoxMode.primary:
        return std.primary_workset, _early_scope(std, BoxMode.primary)
    return new_path, _early_scope(std, BoxMode.standalone)


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
        source_path = Path(literal_path(raw))
    except (OSError, ValueError):
        return False
    return box_resolve.find_connected_external_box(source_path, std) is not None


# -- Cross-mode duplicate helpers --

def _run_duplicate_cross_mode(args: argparse.Namespace, std, config) -> int:
    """Duplicate a project into a different mode layout."""
    to_mode = BoxMode(args.to_mode)

    # Duplicate TO a named (workset) box: separate code path.
    if to_mode is BoxMode.named:
        return _duplicate_to_workset(args, std, config)

    source_path = Path(literal_path(args.source_path))
    new_path = Path(literal_path(args.new_path))

    if new_path == source_path or new_path.resolve() == source_path.resolve():
        print("Error: source and destination paths are the same.", file=sys.stderr)
        return 1

    if not source_path.is_dir():
        print(f"Error: source path does not exist as a directory: {source_path}", file=sys.stderr)
        return 1

    if not args.bare and new_path.exists() and not args.force:
        return _refuse_existing_destination(new_path)

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
    target_mode = to_mode
    if args.force:
        _refuse_inherited(std, src_proj, _local_target(std, target_mode, new_path))

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
            std.primary_workset, str(new_path), early=_early_scope(std, BoxMode.primary),
        )
        if existing_box is not None:
            print(
                f"Error: Workspace {str(new_path)!r} is already registered as "
                f"box {existing_box!r}; refusing to duplicate onto it "
                f"(one box per workspace path).",
                file=sys.stderr,
            )
            return 1

    # A standalone target copies the workspace into the destination root's
    # ``workset.workspaces``; a pre-existing root that nulls it has none (Q96).
    if target_mode == BoxMode.standalone and not args.bare:
        from kanibako.project.workset import refuse_null_workspaces

        refuse_null_workspaces(new_path, f"a workspace for '{new_path.name}'", standalone=True,
                               early=_early_scope(std, BoxMode.standalone))

    src_enable_vault = (
        _source_authored_vault(src_proj) if target_mode == BoxMode.standalone else None
    )
    carried = carried_box_settings(box_workset_settings_paths(src_proj)[0])

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
    # destination root holds the standalone artifacts (workset.yaml, box_data/,
    # vault/).
    # F2/F-3: capture whether the destination dir pre-existed BEFORE the workspace
    # copy, so a refusal/OSError can roll back a copy THIS call created without
    # deleting a pre-existing dir.
    new_path_existed = new_path.is_dir()

    workspace_src = src_proj.project_path

    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if target_mode == BoxMode.standalone:
        # ⚑ Route 3: refuse a scalar ``workset:`` root BEFORE the merge, not after.
        refuse_scalar_sections(new_path / WORKSET_META_FILE, ("workset",))
        # Scalar ``box:`` refused pre-copy; --force rebuilds box_data.
        if not args.force:
            refuse_scalar_sections(new_path / STANDALONE_META_DIR / BOX_META_FILE, ("box",))
        if not args.bare and workspace_src is not None and workspace_src.is_dir():
            # The copy DESTINATION is the destination root's resolved
            # ``workset.workspaces`` (ruled 10, 2026-08-02) — the STANDALONE
            # default ``@meta.workset.path/workspace`` for a fresh root (no
            # workset.yaml yet); a pre-existing root repoint is honored.
            from kanibako.project.workset import (
                load_workset_settings_doc,
                resolve_workset_workspaces,
            )
            dest_workspace = resolve_workset_workspaces(
                new_path, load_workset_settings_doc(new_path), standalone=True,
                early=_early_scope(std, BoxMode.standalone),
            )
            assert dest_workspace is not None  # a nulling root refused before the prompt
            _merge_workspace(workspace_src, dest_workspace, args.force)
        _duplicate_to_standalone(
            src_proj, new_path, std, args.force, src_enable_vault, carried,
        )
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
            # ⚑ Route 2: the merge now happens inside ``_duplicate_to_local``.
            _duplicate_to_local(
                src_proj, new_path, std, config, args.force, carried,
                workspace_src=(workspace_src
                             if not args.bare and workspace_src is not None
                             and workspace_src.is_dir() else None),
            )
        except FileExistsError:
            # F-3 (NIT): a no-force copy onto a pre-existing (unregistered) dir
            # raises FileExistsError from copytree — surface the friendly
            # destination-exists guidance (matching run_duplicate's non-cross-mode
            # message) instead of the raw ``[Errno 17] File exists`` traceback.
            # The dir pre-existed, so new_path_existed is True → no deletion.
            _refuse_existing_destination(new_path)
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


def _merge_workspace(src: Path, dst: Path, force: bool, *,
                     share_root_link: bool = False) -> None:
    """Copy the workspace *src* to *dst*, merging into an existing *dst* under *force*.

    By DEFAULT a duplicate never lays the source's root LINK: when *dst* IS the new box
    ROOT, a link there writes the box's own ``workset.yaml`` and ``box_data/`` into the
    user's directory and ``box info <dst>`` answers the SOURCE.  *share_root_link* is
    for a *dst* that is the resolved workspace SUBDIR instead, where a linked root is
    carried as a link (Q102 (a)) -- but only while *dst* is absent, never over a
    directory the user made.

    Raises ``ProjectError`` naming each entry the merge could not copy.
    """
    from kanibako.errors import ProjectError

    try:
        if share_root_link and not dst.exists() and not dst.is_symlink() \
                and lay_root_link(src, dst):
            return
        copy_tree_keeping_links(src, dst, dirs_exist_ok=force, replace_existing=force)
    except shutil.Error as e:
        listing = failed_entries(e)
        detail = f"; {listing}" if listing is not None else f": {e}"
        raise ProjectError(f"Could not copy the workspace {src} to {dst}{detail}") from e


def _source_authored_vault(src_proj) -> bool:
    """What the SOURCE box authored for ``box.enable_vault`` — its BOX TIER, no cascade.

    ⚑ Each duplicate door calls this BEFORE its copy and destination mkdir, so a
    shape-rule refusal leaves nothing behind — the cure it prints needs a destination the
    retry can land on.
    """
    src_box, _ = box_workset_settings_paths(src_proj)
    return read_box_enable_vault(src_box)


def _duplicate_to_standalone(src_proj, new_path, std, force, src_enable_vault, carried):
    """Establish a fresh standalone box at *new_path*.

    A duplicate is a NEW box, so this mirrors ``create --standalone`` /
    ``convert --standalone`` rather than copying the source verbatim: the
    source's box METADATA DIR (agent/session state, minus the lock + home + the
    source ``box.yaml``) is copied into ``box_data/``, its home into
    ``box_data/home``, and its carried box-scope settings into the destination's
    BOX tier ``box_data/box.yaml`` (M-8).  ``establish_standalone`` then WRITES
    the destination ROOT ``workset.yaml`` — drift I: the workset tier lives at
    ``<root>/workset.yaml``, NOT in ``box_data/`` — with a freshly generated
    ``<kuid>_<leaf>`` identity, never the source's, and registers the box in
    ``registry.standalone``.
    ⚑ The source's own ``workset.yaml`` does NOT travel: it is the source's
    WORKSET tier and carries the source's ``workset.kuid``.  The destination's
    root file is MINTED, not copied — without that fresh mint the destination
    would answer to the source's identity, and detection
    (``_is_standalone_meta_dir``: ``box_data/`` plus a root ``workset.yaml``,
    presence-only since D4) would resolve the two boxes to one (BUG#3).
    """
    from kanibako.errors import ProjectError
    from kanibako.settings.paths import establish_standalone, standalone_box_store, write_vault_gitignore
    from kanibako.utils import write_project_gitignore

    dst_metadata = standalone_box_store(
        new_path, early=_early_scope(std, BoxMode.standalone))
    dst_shell = dst_metadata / "home"
    # (The destination ROOT workset.yaml is written by ``establish_standalone`` below
    # — it is the WORKSET tier and carries the FRESH workset.kuid, never a copy of the
    # source's.  The box tier is ``dst_metadata / BOX_META_FILE``, handled further down.)

    # Ensure new_path exists for bare duplicates.
    new_path.mkdir(parents=True, exist_ok=True)

    # Copy the source box metadata into box_data/, but NOT the lock, the home (copied
    # below), or the source box.yaml (the box tier is written from *carried* below).
    if force and dst_metadata.is_dir() and not remove_box_tree(dst_metadata):
        # The copytree below uses dirs_exist_ok=True, so a silently-failed removal
        # would MERGE the new box into the old one rather than replace it.
        raise ProjectError(
            f"could not remove the existing box data at {dst_metadata}.\n"
            f"Try: podman unshare rm -rf {shlex.quote(str(dst_metadata))}"
        )
    # ⚑ Copy from the box METADATA DIR, never ``metadata_path``: for a standalone
    # source those differ (root vs ``box_data/``), and the root would drag
    # workspace+vault into the box dir AND land the source's WORKSET-tier file at
    # the dest's BOX tier (M-8) — the same guard ``_lifecycle.py`` applies when it
    # re-roots a box.  The stray nested root is not inert: ``<dst>/box_data`` would
    # then carry BOTH ``box_data/`` and a ``workset.yaml``, i.e. the standalone
    # MARKER (``box_resolve.stores_standalone_registry_null``), under the SOURCE's kuid.
    src_meta_dir = box_metadata_dir(src_proj.mode, src_proj.metadata_path,
                                    early=src_proj._require_early())
    copy_tree_keeping_links(
        src_meta_dir, dst_metadata,
        ignore=shutil.ignore_patterns(".kanibako.lock", "home", BOX_META_FILE),
        dirs_exist_ok=True,
    )

    if src_proj.shell_path.is_dir():
        if force and dst_shell.is_dir():
            # The home carries the root-owned canon skeleton (J-7); a bare rmtree
            # fails with EACCES and strands a half-removed destination.
            remove_box_tree(dst_shell)
        if not lay_root_link(src_proj.shell_path, dst_shell):
            copy_tree_keeping_links(src_proj.shell_path, dst_shell)
        # copytree carries the skeleton's modes but not its ownership — re-assert.
        materialize_canon_skeleton(dst_shell)

    # Carry the source's box-scope settings into the destination's BOX TIER (M-8) — the
    # BOX TIER ONLY (:func:`kanibako.settings.config.carried_box_settings`).  A ``box.*``
    # key at the source's WORKSET tier (its ROOT file) is that workset's DOWNWARD DEFAULT,
    # not the box's, so it is not copied down into the box tier where later workset edits
    # could not reach it.  ⚑ A duplicate is a NEW workset scope — ``establish_standalone``
    # below writes the destination ROOT fresh — so such a key does not reach the duplicate
    # at all; that is the rule, not a gap.  ``establish_standalone`` also read-modify-writes
    # ``box.enable_vault`` into this SAME box-tier file, preserving what was carried.
    dst_box_settings = dst_metadata / BOX_META_FILE
    if carried:
        if force and dst_box_settings.exists():
            dst_box_settings.unlink()
        if not dst_box_settings.exists():
            dump_doc(dst_box_settings, carried)

    # Establish the canonical standalone shape (the root marker file, a FRESH
    # <kuid>_<leaf> identity even from a standalone source, the standalone
    # path table) + register it, via the shared core.  ⚑ It WRITES the destination
    # ROOT workset.yaml — nothing above copies one there, and nothing should: the
    # source's root file is the SOURCE's workset tier.
    # ⚑⚑ ``enable_vault`` is the BOX-AUTHORED value — ``src_box`` alone, NEVER
    # ``src_proj.vault_enabled()`` (which is RESOLVED, box tier over the source workset's
    # downward default).  ``establish_standalone`` read-modify-writes this straight into
    # the destination's BOX tier, so the resolved value would undo the carry above and pin
    # the SOURCE workset's default as an override of a box that never left with it — the
    # very thing the ``carried_box_settings`` comment above says does not reach a duplicate.
    # Mirrors ``ProjectState.box_authored_vault`` in ``_lifecycle.py``; the caller decides
    # because ``establish_standalone`` is also the CREATE core, where its argument is a
    # genuinely authored ``--no-vault``.
    _box_name, _dst_shell, _dst_vault_ro, dst_vault_rw = establish_standalone(
        std, new_path,
        enable_vault=src_enable_vault,
    )

    write_project_gitignore(new_path)

    # Write the vault ``.gitignore`` if the skeleton is there.  ⚑ On a FRESH destination it
    # never is, and that is CONFIRMED INTENDED (2026-08-27): a duplicate does NOT carry the
    # source's vault.  establish_standalone does not create the vault dirs, so a duplicated
    # box with enable_vault true starts without one and _flag_missing_vault advises the user.
    # _duplicate_to_local does not carry a vault either.  Do NOT "fix" this by copying the
    # source's vault across -- vaults do not travel on duplicate.
    # ⚑ A destination that ALREADY held a standalone box is the case that made this reachable:
    # ``--force`` rebuilds ``box_data/`` and leaves the old ``vault/`` and root ``workset.yaml``
    # in place, and ``establish_standalone`` read-modify-writes ``workset.kuid`` INTO that file
    # rather than replacing it, so a ``workset.vault_rw`` repoint survives the duplicate — the
    # same pre-existing repoint the workspace copy above deliberately honors.  The gate is
    # therefore the RESOLVED arm, which is why establish_standalone's return value is unpacked.
    write_vault_gitignore(new_path, dst_vault_rw)


def _unwind_local_name(std, project_name: str, dst_project: Path) -> None:
    """Best-effort rollback of a default-mode name registration + partial dir.

    Used when a copy fails after :func:`~kanibako.settings.paths.assign_primary_box_name` has already registered the
    duplicate's name (and possibly created a partial metadata dir), to avoid
    leaving a "registered but no metadata" orphan.  Each step is independently
    guarded so one failure does not mask the rest.
    """
    try:
        unregister_primary_box_name(std.primary_workset, project_name, early=_early_scope(std, BoxMode.primary))
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
    materializes.

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
        unregister_primary_box_name(std.primary_workset, name, early=_early_scope(std, BoxMode.primary))
        raise


def _duplicate_to_local(src_proj, new_path, std, config, force, carried,
                       workspace_src=None):
    """Copy metadata into default-mode layout for new_path.

    ⚑ The source's VAULT is not carried, and that is CONFIRMED INTENDED
    (2026-08-27) -- the same holds for _duplicate_to_standalone.  Vaults do not
    travel on duplicate.

    ⚑⚑ *workspace_src*, when given, is merged into *new_path* HERE — after the mint
    and the home-free check — so no refusal is preceded by a write to the
    destination (task-dupforce-fix1).
    """
    # Assign a new name for the duplicate.  The name MUST be registered first
    # because the destination metadata dir is derived from it (std.boxes/<name>).
    # Registers the PRIMARY membership (the sole store).
    project_name = assign_primary_box_name(
        std.primary_workset, str(new_path), early=_early_scope(std, BoxMode.primary),
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
    # box tier <root>/box_data/box.yaml for a standalone source,
    # <metadata_path>/box.yaml otherwise — the BOX TIER ONLY.  A standalone source's
    # root-stored ``box.*`` belongs to the workset scope it is leaving, so it does not
    # travel; the destination resolves the PRIMARY workset's tier instead.
    src_meta_dir = box_metadata_dir(src_proj.mode, src_proj.metadata_path,
                                    early=src_proj._require_early())

    # Failure-consistency: a crash AFTER assign_primary_box_name (which registers it)
    # but DURING the workspace or metadata copy below would otherwise strand a
    # "registered but no metadata" orphan.  Unwind the registration + any partial
    # dest dir on failure, then re-raise — duplicate either fully succeeds or
    # leaves no trace.
    try:
        if workspace_src is not None:
            _merge_workspace(workspace_src, new_path, force, share_root_link=True)
        if force and dst_project.is_dir():
            remove_box_tree(dst_project)
        copy_tree_keeping_links(
            src_meta_dir, dst_project,
            ignore=shutil.ignore_patterns(".kanibako.lock"),
        )
        # Deliver the carried box settings to the DESTINATION's box tier (which for a
        # primary/named destination is <dst_project>/box.yaml).  The copytree above
        # already places the source's box tier there, so what this rewrite still buys is
        # the ``workset:`` strip — the source's IDENTITY must not be inherited even if a
        # hand-edited box.yaml carries one.
        if carried:
            dump_doc(dst_project / BOX_META_FILE, carried)

        # Ensure home is inside the project dir.
        if src_proj.shell_path.is_dir():
            dst_home = dst_project / "home"
            if not dst_home.is_dir() and not lay_root_link(src_proj.shell_path, dst_home):
                copy_tree_keeping_links(src_proj.shell_path, dst_home)
            materialize_canon_skeleton(dst_home)
    except BaseException:
        _unwind_local_name(std, project_name, dst_project)
        raise


def _duplicate_to_workset(args, std, config) -> int:
    """Duplicate a project into a workset (source untouched)."""
    from kanibako.commands.box._lifecycle import copy_into_workset
    from kanibako.project.workset import list_worksets, load_workset

    ws_name = getattr(args, "workset", None)
    if not ws_name:
        print("Error: --workset is required with --to named.", file=sys.stderr)
        return 1

    registry = list_worksets(std)
    # ⚑ Case-blind (spec §0, ⚑ NAMING RULES); the workset is loaded under the spelling
    # it is REGISTERED with, not the one typed at --workset.
    stored_ws = find_identifier(ws_name, registry)
    if stored_ws is None:
        print(f"Error: workset '{ws_name}' not found.", file=sys.stderr)
        return 1
    ws_name = stored_ws
    ws = load_workset(registry[ws_name], ws_name, early_system=std.early_system)

    source_path = Path(literal_path(args.source_path))
    # ⚑ A duplicate is always an IN-TREE member (``copy_into_workset``), even ``--bare``:
    # a null ``workset.workspaces`` refuses before the prompt, not inside ``add_project``.
    from kanibako.project.workset import refuse_null_workspaces

    refuse_null_workspaces(
        ws.root, f"a workspace for '{getattr(args, 'project_name', None) or source_path.name}'",
        early=ws.early_scope,
    )
    if not source_path.is_dir():
        print(f"Error: source path does not exist as a directory: {source_path}", file=sys.stderr)
        return 1

    source_mode = detect_project_mode(source_path, std, config).mode
    if source_mode == BoxMode.named:
        print("Error: source is already a workset project.", file=sys.stderr)
        return 1

    # ⚑ Stored as typed (spec §0): the user's ``--name``, else the source directory's
    # own basename — whose case is the user's too, and is never folded on its way here.
    proj_name = getattr(args, "project_name", None) or source_path.name

    # Validate name not taken — case-blind (spec §0), reporting the member as STORED.
    # ⚑ NOT reachable by the registry-lookup guard in
    # ``tests/test_identifier_case_enforcement.py``: this is an ``==`` inside a loop over
    # already-loaded members, not a lookup into a registry, so no detector sees it.
    held = find_identifier(proj_name, (p.name for p in ws.projects))
    if held is not None:
        print(f"Error: project '{held}' already exists in workset '{ws_name}'.", file=sys.stderr)
        return 1

    # An occupied landing needs --force, as on the primary path (``run_duplicate``).
    # ⚑ ``--bare`` adopts an existing workspace leaf on purpose; either way, a failed
    # duplicate never deletes a leaf that was there before it (``copy_into_workset``).
    occupied = [ws.projects_dir / proj_name]
    if not args.bare:
        occupied.insert(0, ws.require_workspaces_dir(f"a workspace for '{proj_name}'") / proj_name)
    for leaf in occupied:
        if (leaf.exists() or leaf.is_symlink()) and not args.force:
            return _refuse_existing_destination(leaf)

    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if source_mode == BoxMode.primary:
        src_proj = resolve_project(std, config, project_dir=str(source_path), initialize=False)
    else:
        src_proj = resolve_standalone_project(std, config, project_dir=str(source_path), initialize=False)

    if not src_proj.metadata_path.is_dir():
        print(f"Error: no project data found for source path: {source_path}", file=sys.stderr)
        return 1
    if args.force:
        _refuse_inherited(std, src_proj, (ws.root, ws.early_scope))

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

    target_mode = BoxMode(args.to_mode)
    if args.force:
        _refuse_inherited(std, src_proj, _local_target(std, target_mode, new_path))

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

    src_enable_vault = (
        _source_authored_vault(src_proj) if target_mode == BoxMode.standalone else None
    )
    carried = carried_box_settings(box_workset_settings_paths(src_proj)[0])

    # Copy workspace (unless --bare).  Copy from the member's RECORDED workspace, not
    # the RESOLVED one: a null ``workset.workspaces`` leaves ``project_path`` None while
    # the registry's ``boxes:`` row still names the real directory, so guarding on the
    # resolved value turned "nulled" into "nothing there" and registered an EMPTY box
    # with no warning at all.  One accessor for lifecycle / duplicate / archive, so the
    # three cannot drift.  For an ordinary internal member the two are the same path.
    ws_workspace: Path | None = None
    if not args.bare:
        from kanibako.commands.box._lifecycle import recorded_workspace_for
        from kanibako.project.workset import refuse_null_workspaces

        ws_workspace = recorded_workspace_for(ws, proj_name, src_proj.project_path)
        if ws_workspace is None:
            refuse_null_workspaces(ws.root, f"a workspace for '{proj_name}'",
                                   early=_early_scope(std, BoxMode.named, ws.name))
        assert ws_workspace is not None  # refused on the line above
        if not ws_workspace.is_dir():
            print(
                f"Error: the recorded workspace {ws_workspace} for "
                f"{ws.name}/{proj_name} does not exist; nothing to copy.",
                file=sys.stderr,
            )
            return 1

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

    # ⚑ Route 3, this route's own sighting of it.
    if target_mode == BoxMode.standalone:
        refuse_scalar_sections(new_path / WORKSET_META_FILE, ("workset",))

    # Copy metadata into target layout.
    # default<->standalone: architectural boundary (centralized vs in-workspace metadata), not re-rooting — kept distinct (#71 B2).
    if target_mode == BoxMode.standalone:
        # The standalone merge stays HERE; the primary target's moved inside.
        if ws_workspace is not None:
            # The resolved ``workspace/`` subdir, NOT the ROOT; a linked root stays a link.
            from kanibako.project.workset import (
                load_workset_settings_doc,
                resolve_workset_workspaces,
            )
            dest_workspace = resolve_workset_workspaces(
                new_path, load_workset_settings_doc(new_path), standalone=True,
                early=_early_scope(std, BoxMode.standalone),
            )
            assert dest_workspace is not None  # a nulling root refused before the prompt
            _merge_workspace(ws_workspace, dest_workspace, args.force,
                             share_root_link=True)
        _duplicate_to_standalone(
            src_proj, new_path, std, args.force, src_enable_vault, carried,
        )
    else:
        from kanibako.errors import ProjectError
        try:
            _duplicate_to_local(
                src_proj, new_path, std, config, args.force, carried,
                workspace_src=ws_workspace,
            )
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
    config_file = user_config_file()
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
    # duplicating --to primary/standalone: there the bare result makes new_path
    # itself the workspace (no aliasing), so it is allowed.
    _to_mode = getattr(args, "to_mode", None)
    if (
        _to_mode not in (BoxMode.primary.value, BoxMode.standalone.value)
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
    # the destination (matching `--to standalone`); named → a primary box
    # (matching `--to primary`).
    src_for_detect = Path(args.source_path)
    if src_for_detect.is_dir():
        src_mode = detect_project_mode(src_for_detect, std, config).mode
        if src_mode is BoxMode.standalone:
            args.to_mode = BoxMode.standalone.value
            return _run_duplicate_cross_mode(args, std, config)
        if src_mode is BoxMode.named:
            args.to_mode = BoxMode.primary.value
            return _run_duplicate_cross_mode(args, std, config)

    source_path = Path(literal_path(args.source_path))
    new_path = Path(literal_path(args.new_path))

    # 1. Paths must differ.
    if new_path == source_path or new_path.resolve() == source_path.resolve():
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

    # verbatim copy: refuse a non-table `box` before anything is written
    refuse_scalar_sections(source_project_dir / BOX_META_FILE, ("box",))

    if args.force:
        refuse_inherited_per_owner(std.primary_workset, _early_scope(std, BoxMode.primary))

    # 4. Non-bare: destination workspace must not already exist (unless --force).
    if not args.bare and new_path.exists() and not args.force:
        return _refuse_existing_destination(new_path)

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

    # 8. Registration runs BEFORE anything is copied.  Both refusals below used to
    # arrive AFTER ``_merge_workspace``, so `--force` had already replaced the
    # destination's files by the time the command said "already registered"
    # (task-dupforce).  Minting the name first also lets the home-free check run
    # early: it needs the minted name, and guards a retained home `--force` would
    # otherwise rmtree.
    from kanibako.errors import ProjectError
    try:
        dup_name = assign_primary_box_name(
            std.primary_workset, str(new_path), early=_early_scope(std, BoxMode.primary),
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

    # Failure-consistency: the workspace copy now shares the unwind, because the
    # name is already registered above.  A failure at EITHER step unregisters it,
    # so no "registered but no metadata" orphan survives.
    try:
        # Copy workspace (unless --bare).
        if not args.bare:
            _merge_workspace(source_path, new_path, args.force)

        # Copy metadata (entire project dir including home/).
        if args.force and new_project_dir.is_dir():
            remove_box_tree(new_project_dir)
        copy_tree_keeping_links(
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
