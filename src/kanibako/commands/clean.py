"""kanibako purge: remove project session data."""

from __future__ import annotations

import argparse
import shlex
import shutil
import sys

from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.runtime.container import remove_box_tree, remove_path
from kanibako.errors import UserCanceled
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    _primary_box_paths,
    box_logs_location,
    load_std_paths,
    report_retained_store,
    resolve_any_project,
    standalone_box_store,
    standalone_store_teardown_plan,
)
from kanibako.utils import confirm_prompt
from kanibako.project.workset import purge_box_logs
from kanibako.channels.channels import workset_name_token, workset_root
from kanibako.settings.workset_dirkeys import EarlyScope, refuse_inherited_per_owner
from kanibako.settings.messages import MSG_DONE, STATUS_NO_DATA
from kanibako.snapshots import box_snapshot_store


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
    from kanibako.settings.config import user_config_file
    config_file = user_config_file()
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

    PRIMARY boxes live in the PRIMARY-workset ``boxes:`` membership (name →
    workspace path — the sole store since the global ``projects:`` section
    retired); STANDALONE boxes live in ``registry.standalone`` (box name → root).
    Resolve the registered name by reverse path lookup first (the workspace path
    is what the membership stores), falling back to the resolved/ metadata-dir
    name, and remove it. Best-effort: a missing or already-clean entry is a no-op.
    """
    from kanibako.project import registry_store
    from kanibako.settings.paths import (
        primary_box_name_for_workspace,
        unregister_primary_box_name,
    )

    try:
        if proj.mode is BoxMode.standalone:
            name = registry_store.standalone_name_for_root(
                std.registry, proj.metadata_path,
            ) or proj.name
            if name:
                registry_store.unregister_standalone(std.registry, name)
            return

        # PRIMARY (named-workset boxes are unregistered via remove_project, not
        # purge): the membership maps name → workspace path, so reverse-resolve.
        early = _early_scope(std, BoxMode.primary)
        name = primary_box_name_for_workspace(
            std.primary_workset, str(proj.project_path), early=early,
        ) or (proj.name or proj.metadata_path.name)
        if name:
            unregister_primary_box_name(std.primary_workset, name, early=early)
    except Exception:  # noqa: BLE001 - cleanup must never break a purge
        pass


def _unregister_purged_primary(std, metadata_path, project_path) -> None:
    """M2 mirror for ``_purge_all``: unregister a purged PRIMARY box by path.

    ``iter_projects`` yields ``(metadata_path, project_path)`` for primary-mode
    boxes; reverse-resolve the registered name from the workspace path (falling
    back to the metadata dir name) and drop it from the PRIMARY-workset ``boxes:``
    membership (the sole store since the global ``projects:`` section retired).
    """
    from kanibako.settings.paths import (
        primary_box_name_for_workspace,
        unregister_primary_box_name,
    )

    try:
        early = _early_scope(std, BoxMode.primary)
        name: str | None = None
        if project_path is not None:
            name = primary_box_name_for_workspace(
                std.primary_workset, str(project_path), early=early,
            )
        if name is None:
            name = metadata_path.name
        if name:
            unregister_primary_box_name(std.primary_workset, name, early=early)
    except Exception:  # noqa: BLE001 - cleanup must never break a purge
        pass


def _warn_undeleted(path) -> None:
    """Say so when a box tree survives BOTH rmtree and ``podman unshare rm``.

    These three sites used a bare ``shutil.rmtree`` that RAISED on failure; routing
    them through ``remove_box_tree`` (which returns a bool) would otherwise turn a
    loud failure into a silent one — the box would report "done" over a tree that is
    still on disk.
    """
    print(
        f"\nWarning: could not fully remove {path}.\n"
        f"Try: podman unshare rm -rf {shlex.quote(str(path))}",
        file=sys.stderr,
    )


def _resolve_snapshot_store(vault_rw, box_name: str | None):
    """*box_name*'s own store under the base *vault_rw* shares; ``None`` with no vault."""
    if vault_rw is None or not box_name:
        return None
    return box_snapshot_store(vault_rw, box_name)


def _remove_snapshot_store(store) -> None:
    """Delete the box's OWN store — never the shared base, nor ``.unsorted``."""
    if store is not None and not remove_path(store):
        _warn_undeleted(store)


def _purge_one(std, config, path: str, *, force: bool) -> int:
    """Purge session data for a single project."""
    proj = resolve_any_project(std, config, project_dir=path, initialize=False)

    # For standalone, metadata_path IS the project root (drift I); the actual
    # box metadata lives in box_data/ + the root workset.yaml + vault/.  "No
    # session data" means no box_data/ dir (the root always exists).
    if proj.mode is BoxMode.standalone:
        if not standalone_box_store(proj.metadata_path,
                                 early=_early_scope(std, BoxMode.standalone)).is_dir():
            print(f"No session data found for project {proj.project_path or '<None>'}")
            return 0
    elif not proj.metadata_path.is_dir():
        print(f"No session data found for project {proj.project_path}")
        return 0

    refuse_inherited_per_owner(
        workset_root(proj, std), EarlyScope(std.early_system, workset_name_token(proj)))

    # Resolve before the question: a purge that leaves the store hands it on.
    snapshot_store = _resolve_snapshot_store(proj.vault_rw_path, proj.name)

    if not force:
        print(f"Project: {proj.project_path or '<None>'}")
        if proj.name:
            print(f"Name: {proj.name}")
        print()
        try:
            confirm_prompt(
                "Delete all session data for this project? This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCanceled:
            print("Aborted.")
            return 2

    print("Removing session data... ", end="", flush=True)
    # Remove the per-box logs first (their paths are derived from the box's
    # tree, which the rmtree below may take with it for standalone).
    purge_box_logs(std, *box_logs_location(std, proj), workset_root=workset_root(proj, std))

    if proj.mode is BoxMode.standalone:
        # metadata_path is the project ROOT — remove ONLY the in-tree kanibako artifacts.
        # ⚑ THE ROOT FILE GOES ONLY WITH THE STORE: a retained store leaves the box whole.
        from kanibako.project.workset import (
            _strictly_in_tree,
            report_retained_canon,
            report_retained_vaults,
            standalone_canon_teardown,
            standalone_vault_teardown,
        )

        root = proj.metadata_path
        # ⚑⚑ RESOLVE THE VAULT FIRST: the root workset.yaml carries the only copy of a
        # ``workset.vault_*`` repoint.
        removable_vault, retained_vault = standalone_vault_teardown(
            root, early=_early_scope(std, BoxMode.standalone))
        removable_canon, retained_canon = standalone_canon_teardown(
            root, early=_early_scope(std, BoxMode.standalone))
        # ⚑ The store holds the box home + its canon skeleton (J-7), so its removal needs
        # the podman-unshare escalation; the removable/retained split is the plan's own.
        removable_store, retained_store = standalone_store_teardown_plan(
            root, early=_early_scope(std, BoxMode.standalone))
        if removable_store is not None:
            if not remove_box_tree(removable_store):
                _warn_undeleted(removable_store)
            (root / WORKSET_META_FILE).unlink(missing_ok=True)
            if removable_canon is not None and not remove_path(removable_canon):
                _warn_undeleted(removable_canon)
            if retained_canon is not None:
                report_retained_canon(retained_canon, root)
        if retained_store is not None:
            report_retained_store(retained_store, root)
        for vault_dir in removable_vault:
            if not remove_path(vault_dir):
                _warn_undeleted(vault_dir)
        # The store sits beside the vault arms, so the removals above miss it.
        if snapshot_store is not None and _strictly_in_tree(snapshot_store, root):
            _remove_snapshot_store(snapshot_store)
        report_retained_vaults(root, retained_vault)
    else:
        if not remove_box_tree(proj.metadata_path):
            _warn_undeleted(proj.metadata_path)
        # Phase 5: PRIMARY vault lives under @config.primary_workset (not under
        # metadata_path), so remove the per-box ro/rw dirs explicitly.
        if proj.mode is BoxMode.primary:
            for box_vault in (proj.vault_ro_path, proj.vault_rw_path):
                if box_vault is not None and box_vault.is_dir():
                    shutil.rmtree(box_vault, ignore_errors=True)
        _remove_snapshot_store(snapshot_store)

    # M2 (registry hygiene): the box metadata is gone, so drop its registry
    # entry too — otherwise registry.{projects,standalone} keeps a dangling
    # name → path that would shadow a future box at the same path.
    _unregister_purged(std, proj)

    print(MSG_DONE)
    print(f"Session data removed for {proj.project_path or '<None>'}")
    return 0


def _purge_all(std, config, *, force: bool) -> int:
    """Purge session data for all known projects."""
    from kanibako.settings.paths import iter_projects, iter_workset_projects

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if not projects and not ws_data:
        print("No project session data found.")
        return 0

    if projects:
        refuse_inherited_per_owner(std.primary_workset, _early_scope(std, BoxMode.primary))
    for ws_name, ws, project_list in ws_data:
        if any(status != STATUS_NO_DATA for _, status in project_list):
            refuse_inherited_per_owner(ws.root, _early_scope(std, BoxMode.named, ws_name))

    total = len(projects)
    for _, _, project_list in ws_data:
        total += sum(1 for _, status in project_list if status != STATUS_NO_DATA)

    print(f"Found {total} project(s):")
    for metadata_path, project_path in projects:
        label = str(project_path) if project_path else f"(unknown) {metadata_path.name}"
        print(f"  {label}")
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status != STATUS_NO_DATA:
                print(f"  {ws_name}/{proj_name}")
    print()

    if not force:
        try:
            confirm_prompt(
                "Delete ALL session data for every project listed above? "
                "This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCanceled:
            print("Aborted.")
            return 2

    removed = 0

    # PRIMARY-mode projects.
    for metadata_path, project_path in projects:
        label = str(project_path) if project_path else metadata_path.name
        print(f"Removing {label}... ", end="", flush=True)
        if not remove_box_tree(metadata_path):
            _warn_undeleted(metadata_path)
        # Phase 5: PRIMARY vault lives under @config.primary_workset/vault/
        # {ro,rw}/<name> (name == metadata dir name), not under metadata_path.
        _shell, vault_ro, vault_rw = _primary_box_paths(
            std, metadata_path, metadata_path.name,
        )
        for vault_dir in (vault_ro, vault_rw):
            if vault_dir is not None and vault_dir.is_dir():
                shutil.rmtree(vault_dir, ignore_errors=True)
        _remove_snapshot_store(_resolve_snapshot_store(vault_rw, metadata_path.name))

        # The per-box logs, under the PRIMARY workset's resolved ``workset.logs``
        # (box == metadata dir name).
        purge_box_logs(std, std.primary_logs, metadata_path.name,
                       workset_root=std.primary_workset)

        # M2: drop the now-dangling registry entry for this PRIMARY box.
        _unregister_purged_primary(std, metadata_path, project_path)

        print(MSG_DONE)
        removed += 1

    # Workset projects.
    for ws_name, ws, project_list in ws_data:
        # ⚑ Hoisted, and RESOLVED: both properties read the root workset.yaml, so one
        # read per workset keeps every member of it judged against the same document.
        boxes_dir, logs_dir = ws.projects_dir, ws.logs_dir
        vault_rw_base = ws.vault_rw_dir
        for proj_name, status in project_list:
            if status == STATUS_NO_DATA:
                continue
            project_dir = boxes_dir / proj_name
            if project_dir.is_dir():
                label = f"{ws_name}/{proj_name}"
                print(f"Removing {label}... ", end="", flush=True)
                if not remove_box_tree(project_dir):
                    _warn_undeleted(project_dir)
                # ⚑ NAMED logs — the RESOLVED ``workset.logs``, which is what the
                # box's helpers.jsonl mount is bound from; the default leaf is
                # ``<root>/logs``, not the box's own directory.
                purge_box_logs(std, logs_dir, proj_name, workset_root=ws.root)
                _remove_snapshot_store(_resolve_snapshot_store(
                    None if vault_rw_base is None else vault_rw_base / proj_name,
                    proj_name))
                print(MSG_DONE)
                removed += 1

    print(f"\nPurged session data for {removed} project(s).")
    return 0
