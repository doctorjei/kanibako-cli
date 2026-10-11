"""kanibako purge: remove project session data."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from kanibako.settings.config import load_config
from kanibako.runtime.container import remove_box_tree, remove_path
from kanibako.errors import UserCanceled
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    _primary_box_paths,
    box_cure_ref,
    box_logs_location,
    load_std_paths,
    project_cure_ref,
    resolve_any_project,
    standalone_box_store,
)
from kanibako.utils import (
    confirm_prompt,
    container_name_for_box_name,
)
from kanibako.project.workset import (
    _symlink_loop,
    purge_box_logs,
    report_retained_loop,
    resolve_workset_vault_pair,
)
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
        help="Purge every primary and named-workset box; standalone boxes are skipped "
             "(purge each one by its path)",
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


def _unregister_purged(std, proj, *, park: bool = False) -> None:
    """Drop a purged box's registry entry (M2 orphan-cleanup).

    PRIMARY boxes live in the PRIMARY-workset ``boxes:`` membership (name →
    workspace path — the sole store since the global ``projects:`` section
    retired); STANDALONE boxes live in ``registry.standalone`` (box name → root).
    Resolve the registered name by reverse path lookup first (the workspace path
    is what the membership stores), falling back to the resolved/ metadata-dir
    name, and remove it. Best-effort: a missing or already-clean entry is a no-op.

    ⚑ A NAMED member touches neither registry: its membership is its workset's, and it
    stays registered, so its path is the retry's address.  Running the primary branch
    for it unregistered a same-named PRIMARY box and parked the member as a primary.
    """
    from datetime import datetime, timezone

    from kanibako.project import registry_store
    from kanibako.settings.paths import (
        primary_box_name_for_workspace,
        unregister_primary_box_name,
    )

    def _park(name: str, kind: str, workspace: str, metadata: str) -> None:
        registry_store.register_deregistered(
            std.registry, name, kind=kind, workspace=workspace, metadata=metadata,
            deregistered_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def _drop_parked(name: str) -> None:
        # ⚑ A completed purge drops the row an earlier failed pass parked.
        registry_store.unregister_deregistered(std.registry, name)

    if proj.mode is BoxMode.named:
        return
    try:
        if proj.mode is BoxMode.standalone:
            name = registry_store.standalone_name_for_root(
                std.registry, proj.metadata_path,
            ) or proj.name
            if name:
                registry_store.unregister_standalone(std.registry, name)
                if park:
                    _park(name, "standalone", str(proj.metadata_path),
                          str(proj.metadata_path))
                else:
                    _drop_parked(name)
            return

        # PRIMARY: the membership maps name → workspace path, so reverse-resolve.
        early = _early_scope(std, BoxMode.primary)
        name = primary_box_name_for_workspace(
            std.primary_workset, str(proj.project_path), early=early,
        ) or (proj.name or proj.metadata_path.name)
        if name:
            unregister_primary_box_name(std.primary_workset, name, early=early)
            if park:
                _park(name, "primary", str(proj.project_path),
                      str(proj.metadata_path))
            else:
                _drop_parked(name)
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


def _remove_snapshot_store(store) -> bool:
    """Delete the box's OWN store — never the shared base, nor ``.unsorted``.

    ⚑ A LOOP ON OR ABOVE the store makes it unreachable, not absent: ``unlink`` on a path
    under a loop raises ``ELOOP``, and a purge that crashes stops partway with the rest of
    the box still standing.  Kept and NAMED instead — the same answer every door gives a
    loop, and not a failure: kanibako never had a path to it.
    """
    if store is None:
        return True
    if _symlink_loop(store):
        report_retained_loop(store, "box store")
        return True
    try:
        if remove_path(store):
            return True
    except OSError:
        pass  # a read-only parent raises: counted below, as any refusal is
    _warn_undeleted(store)
    return False


def _remove_member_vault_leaves(vault_ro, vault_rw) -> bool:
    """Take a box's OWN ro/rw vault leaves (a named member's, or a primary's) — never a shared arm.

    ``is_symlink`` is tested: a dangling link is neither dir nor file.
    ⚑ A LOOPED LEAF IS KEPT AND NAMED.  Before this it went away silently: a self-loop is
    a symlink, so ``is_symlink()`` let it through to ``remove_path``, and nothing was said.
    """
    all_gone = True
    for leaf in (vault_ro, vault_rw):
        if leaf is None or not (leaf.is_dir() or leaf.is_symlink()):
            continue
        if _symlink_loop(leaf):
            report_retained_loop(leaf, "vault")
            continue
        try:
            removed = remove_path(leaf)
        except OSError:
            # ⚑ A read-only parent RAISES on a link's unlink: a counted failure, never a
            # traceback over a half-run purge.
            removed = False
        if not removed:
            _warn_undeleted(leaf)
            all_gone = False
    return all_gone


def _parked_reentries(std, path: str) -> "list[tuple[str, dict]]":
    """EVERY parked entry *path* addresses, not just one."""
    from kanibako.project import registry_store

    return registry_store.deregistered_matches_for_workspace(std.registry, Path(path))


def _purge_parked(std, config, parked: "tuple[str, dict]", *, force: bool) -> int:
    """Finish a parked box via ``_purge_deregistered`` — the ONE parked-entry purge."""
    from kanibako.commands.box._parser import _purge_deregistered

    name, entry = parked
    return _purge_deregistered(std, name, dict(entry),
                              argparse.Namespace(purge=True, force=force),
                              config=config)


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
        # ⚑ "No session data" is about the RECORD: the park is the retry's address.
        parked = _parked_reentries(std, path)
        if len(parked) > 1:
            from kanibako.commands.box._parser import refuse_ambiguous_parked_purge
            return refuse_ambiguous_parked_purge(parked, path)
        if parked:
            return _purge_parked(std, config, parked[0], force=force)
        print(f"No session data found for project {proj.project_path}")
        return 0

    refuse_inherited_per_owner(
        workset_root(proj, std), EarlyScope(std.early_system, workset_name_token(proj)))

    # ⚑ S6r: the SAME running-box pre-flight the other verbs use — no second probe.  A
    # live-container purge takes its vaults and canon mid-write; the lock sits with the
    # store, as ``box rm --purge`` reads it.
    from kanibako.commands.box._lifecycle import abort_if_box_running

    _lock_home = (standalone_box_store(proj.metadata_path,
                                      early=_early_scope(std, BoxMode.standalone))
                 if proj.mode is BoxMode.standalone else proj.metadata_path)
    if abort_if_box_running(proj, _lock_home / ".kanibako.lock", force,
                            "Purging this box would delete its vaults and canon while "
                            "writes are in flight."):
        return 1

    if proj.mode is not BoxMode.named:
        failed = _purge_by_plan(std, proj, force=force)
        if failed is None:
            return 2
    else:
        named = _purge_named_member(std, proj, force=force)
        if named is None:
            return 2
        failed = named

    # M2 (registry hygiene): the box metadata is gone, so drop its registry
    # entry too — otherwise registry.{projects,standalone} keeps a dangling
    # name → path that would shadow a future box at the same path.  ⚑ When
    # something was KEPT the entry is parked instead: dropping it leaves the retry
    # this function just promised with no address to re-enter by.
    _unregister_purged(std, proj, park=failed)

    if failed:
        # ⚑ CLOSE THE LINE: every later line here is stderr, so stdout ended mid-sentence.
        print(flush=True)
        if proj.mode is BoxMode.named:
            # ⚑ A named member is never parked: it stays a member, the retry's address.
            ref = project_cure_ref(proj)
            print(f"Purge incomplete: something was kept, so {ref} stays a member of "
                  f"its workset — re-run the purge to finish: kanibako box purge {ref}",
                  file=sys.stderr)
            return 1
        print("Purge incomplete: something was kept, so the box is parked — re-run "
              "the purge to finish.", file=sys.stderr)
        return 1
    print(MSG_DONE)
    print(f"Session data removed for {proj.project_path or '<None>'}")
    return 0


def _purge_by_plan(std, proj, *, force: bool) -> bool | None:
    """A primary or standalone box, through ``box rm --purge``'s plan: the ONE carrier.

    The same builder lists the paths, asks, and deletes them.  Returns whether something
    was left behind, or ``None`` when the question was declined.
    """
    from kanibako.commands.box._parser import (
        _confirm_purge, _primary_purge_plan, _report_retained_roles, _run_purge_plan,
        _standalone_purge_plan)
    from kanibako.project import registry_store

    retained: "list[tuple[str, Path, str | None]]" = []
    if proj.mode is BoxMode.standalone:
        root = proj.metadata_path
        registered = registry_store.standalone_name_for_same_dir(std.registry, root)
        plan, retained = _standalone_purge_plan(
            root, registered or root.name, std=std, early=_early_scope(std, BoxMode.standalone))
    else:
        plan = _primary_purge_plan(std, proj.name, proj.metadata_path, retained)
    if not force:
        print(f"Project: {proj.project_path or '<None>'}")
        if proj.name:
            print(f"Name: {proj.name}")
    if not _confirm_purge(plan, force=force, retained=retained):
        print("Aborted.")
        return None
    print("Removing session data... ", flush=True)
    removed = _run_purge_plan(plan, std)
    sys.stdout.flush()
    if proj.mode is BoxMode.standalone:
        _report_retained_roles(proj.metadata_path, retained,
                              early=_early_scope(std, BoxMode.standalone))
    else:
        for what, kept, _why in retained:
            report_retained_loop(kept, what)
    return not removed


def _purge_named_member(std, proj, *, force: bool) -> bool | None:
    """A NAMED member: its vault leaves, own store, logs, then its box tree LAST.

    Returns whether something was left behind, or ``None`` when declined.
    """
    from kanibako.commands.box._parser import (
        _PurgeStep, _VAULT_LABELS, _confirm_purge, _without_recorded_workspaces)
    from kanibako.project.workset import box_logs_to_purge

    snapshot_store = _resolve_snapshot_store(proj.vault_rw_path, proj.name)
    logs_dir, log_box = box_logs_location(std, proj)
    log_files = box_logs_to_purge(std, logs_dir, log_box, workset_root=workset_root(proj, std))
    plan = [_PurgeStep("log", "log", log_file) for log_file in log_files]
    for what, leaf in zip(_VAULT_LABELS, (proj.vault_ro_path, proj.vault_rw_path)):
        if (leaf is not None and (leaf.is_dir() or leaf.is_symlink())
                and not _symlink_loop(leaf)):
            plan.append(_PurgeStep("vault", what, leaf))
    if (snapshot_store is not None and not _symlink_loop(snapshot_store)
            and (snapshot_store.is_dir() or snapshot_store.is_symlink())):
        plan.append(_PurgeStep("vault", "vault snapshots", snapshot_store))
    plan.append(_PurgeStep("metadata", "box metadata", proj.metadata_path, anchor=True))
    # ⚑ A folder holding a recorded workspace is KEPT whole and named (keyspec § 0).
    listed = _without_recorded_workspaces(plan, std)
    kept = {step.path for step in plan} - {step.path for step in listed}
    if not force:
        print(f"Project: {proj.project_path or '<None>'}")
        if proj.name:
            print(f"Name: {proj.name}")
    if not _confirm_purge(listed, force=force):
        print("Aborted.")
        return None

    # The per-box logs go first: a log's Warning must not split the progress line.
    failed = not purge_box_logs(std, logs_dir, log_box, workset_root=workset_root(proj, std),
                                files=log_files)
    print("Removing session data... ", end="", flush=True)
    if not _remove_member_vault_leaves(
            *(None if leaf in kept else leaf
              for leaf in (proj.vault_ro_path, proj.vault_rw_path))):
        failed = True
    if not _remove_snapshot_store(None if snapshot_store in kept else snapshot_store):
        failed = True
    # ⚑ ANCHOR LAST, ONLY IF NOTHING ABOVE FAILED.  Take it early and the parked entry
    # points at a dir already gone.
    if failed:
        print(flush=True)  # end the progress line before the stderr Kept line
        print(f"Kept {proj.metadata_path} — something above could not be removed, "
              "so the box's metadata is kept and re-running the purge can finish.",
              file=sys.stderr)
    elif proj.metadata_path not in kept and not remove_box_tree(proj.metadata_path):
        _warn_undeleted(proj.metadata_path)
        failed = True
    return failed


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

    from kanibako.commands.box._parser import (
        _primary_probe_container, kept_for_recorded_workspace)

    # ⚑ EVERY BOX IS PLANNED BEFORE THE QUESTION AND BEFORE ANY DELETE: a box purged
    # first is unregistered, and its workspace inside a later box must still be kept.
    # (label, logs dir, box, workset root, metadata, vault leaves, store, primary entry)
    jobs: list[tuple[str, Path | None, str, Path, Path, tuple[Path | None, Path | None],
                     Path | None, tuple[Path, Path | None] | None]] = []
    # (label, lock file, container, quoted stop ref): what the running-box probe asks.
    probes: list[tuple[str, Path, str | None, str]] = []
    for metadata_path, project_path in projects:
        # Phase 5: PRIMARY vault lives under @config.primary_workset/vault/
        # {ro,rw}/<name> (name == metadata dir name), not under metadata_path.
        _shell, vault_ro, vault_rw = _primary_box_paths(
            std, metadata_path, metadata_path.name,
        )
        jobs.append((str(project_path) if project_path else metadata_path.name,
                     std.primary_logs, metadata_path.name, std.primary_workset,
                     metadata_path, (vault_ro, vault_rw),
                     _resolve_snapshot_store(vault_rw, metadata_path.name),
                     (metadata_path, project_path)))
        probes.append((jobs[-1][0], metadata_path / ".kanibako.lock",
                       _primary_probe_container(metadata_path.name),
                       box_cure_ref(BoxMode.primary, metadata_path.name, None, metadata_path)))
    for ws_name, ws, project_list in ws_data:
        # ⚑ Hoisted, and RESOLVED: both properties read the root workset.yaml, so one
        # read per workset keeps every member of it judged against the same document.
        # ⚑ NAMED logs — the RESOLVED ``workset.logs``, which is what the box's
        # helpers.jsonl mount is bound from; the default leaf is ``<root>/logs``.
        boxes_dir, logs_dir = ws.projects_dir, ws.logs_dir
        # ⚑ One read, not two.
        vault_ro_base, vault_rw_base = resolve_workset_vault_pair(
            ws.root, early=_early_scope(std, BoxMode.named, ws_name))
        for proj_name, status in project_list:
            if status == STATUS_NO_DATA or not (boxes_dir / proj_name).is_dir():
                continue
            # The store is the rw LEAF's sibling, so derive it once.
            vault_rw_leaf = None if vault_rw_base is None else vault_rw_base / proj_name
            vault_ro_leaf = None if vault_ro_base is None else vault_ro_base / proj_name
            jobs.append((f"{ws_name}/{proj_name}", logs_dir, proj_name, ws.root,
                         boxes_dir / proj_name, (vault_ro_leaf, vault_rw_leaf),
                         _resolve_snapshot_store(vault_rw_leaf, proj_name), None))
            probes.append((jobs[-1][0], boxes_dir / proj_name / ".kanibako.lock",
                           container_name_for_box_name(proj_name, ws_name),
                           box_cure_ref(BoxMode.named, proj_name, ws_name, ws.root)))
    # A folder holding a recorded workspace is KEPT whole and named (keyspec § 0).
    kept = {path for job in jobs for path in (*job[5], job[6], job[4])
            if path is not None and kept_for_recorded_workspace(std, path)}

    # ⚑ EVERY BOX IS PROBED BEFORE THE QUESTION, by the one pre-flight ``_purge_one``
    # uses.  One live box refuses the whole run: nothing is deleted, each live box is named
    # with its stop cure, and --force overrides (each warning still prints).
    from kanibako.commands.box._lifecycle import abort_if_box_running

    live = [label for label, lock, container, ref in probes
            if abort_if_box_running(None, lock, force,
                                    "Purging it would delete its vaults and canon while "
                                    "writes are in flight.",
                                    container_name=container, cure_ref=ref,
                                    announce_abort=False)]
    if live:
        print(f"Error: {len(live)} box(es) may be running: {', '.join(live)}. Nothing was "
              "purged. Stop each one, or pass --force.", file=sys.stderr)
        print("Aborted.")
        return 1

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
    failures = 0
    for label, box_logs_dir, box, ws_root, metadata, leaves, store, primary in jobs:
        # The per-box logs first, before the progress line their Warning would split.
        failed = not purge_box_logs(std, box_logs_dir, box, workset_root=ws_root)
        print(f"Removing {label}... ", end="", flush=True)
        if not _remove_member_vault_leaves(*(None if leaf in kept else leaf
                                             for leaf in leaves)):
            failed = True
        if not _remove_snapshot_store(None if store in kept else store):
            failed = True
        # ⚑ ANCHOR LAST, ONLY IF NOTHING ABOVE FAILED, as in ``_purge_one``; the entry
        # stays too, as the re-run's address.
        if failed:
            print(flush=True)
            print(f"Kept {metadata} — something above could not be removed, so the box's "
                  "metadata is kept and re-running the purge can finish.", file=sys.stderr)
            failures += 1
            continue
        if metadata not in kept and not remove_box_tree(metadata):
            _warn_undeleted(metadata)
            failures += 1
            continue
        if primary is not None:
            # M2: drop the now-dangling registry entry for this PRIMARY box.
            _unregister_purged_primary(std, *primary)
        print(MSG_DONE)
        removed += 1

    print(f"\nPurged session data for {removed} project(s).")
    if failures:
        sys.stdout.flush()  # piped, stdout is buffered: the summary line comes first
        print(f"Purge incomplete: {failures} could not be fully purged; each kept its "
              "metadata, so re-running the purge can finish.", file=sys.stderr)
        return 1
    return 0
