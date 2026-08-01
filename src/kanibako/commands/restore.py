"""kanibako extract: restore session data from archive with validation."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from kanibako.settings.config import config_file_path, load_config
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.core_defaults import materialize_canon_skeleton
from kanibako.errors import ProjectError, UserCancelled, WorksetError
from kanibako.git import is_git_repo
from kanibako.settings.paths import (
    BoxMode,
    check_primary_box_name_free,
    load_std_paths,
    primary_box_name_for_workspace,
    resolve_any_project,
    xdg,
)
from kanibako.utils import confirm_prompt


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "extract",
        help="Extract session data from archive",
        description="Extract session data from a .txz archive created by 'kanibako box archive'.",
    )
    p.add_argument("file", nargs="?", default=None, help="Archive file to extract from")
    p.add_argument("path", nargs="?", default=None, help="Path to the project directory")
    p.add_argument(
        "--name", default=None,
        help="Override project name for the extracted data",
    )
    p.add_argument(
        "--all", action="store_true", dest="all_archives",
        help="Extract all kanibako-*.txz archives in the current directory",
    )
    p.add_argument("--force", action="store_true", help="Skip all confirmation prompts")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    if args.all_archives:
        if getattr(args, "name", None):
            # One name cannot address N boxes.  Refusing beats the old behaviour
            # (silently dropping it) — see the --name note in _restore_one.
            print(
                "Error: --name cannot be combined with --all (it names ONE box, "
                "and --all restores many).",
                file=sys.stderr,
            )
            return 1
        return _restore_all(std, config, args)

    if args.file is None:
        print("Error: specify an archive file, or use --all", file=sys.stderr)
        return 1

    return _restore_one(std, config, project_dir=args.path,
                        archive_file=Path(args.file), force=args.force,
                        name=getattr(args, "name", None))


def _restore_one(std, config, *, project_dir, archive_file, force, name=None) -> int:
    """Extract session data from a single archive.

    ⚑ EXTRACT IS A RE-MATERIALIZATION, NOT A FILE COPY.  It must land in the box's
    REAL metadata dir and leave that box REGISTERED, exactly as ``create`` does.
    Before this was fixed it did neither: it resolved with ``initialize=False``, and
    for a workspace with no registered box ``paths._resolve_local_dir`` returns the
    SENTINEL ``("", std.boxes / "__unregistered__")`` — a name-assignment placeholder
    that is only valid on the ``initialize=True`` path, where ``resolve_project``
    overwrites it.  Used as a real destination it is a shared junk drawer: every
    unregistered extract wrote into the SAME ``boxes/__unregistered__`` directory,
    silently clobbering the previous one, and no box was ever registered.
    (Proven on a real box, 2026-07-31; ``--name`` was parsed and then dropped.)

    The resolve is therefore SPLIT in two:

    * a VALIDATION resolve (``initialize=False``) — used ONLY for ``project_hash``
      and ``project_path``, which the hash/git gates need and which do not depend on
      a box existing.  Its ``metadata_path``/``name`` are the sentinel and must not
      be touched;
    * a DESTINATION resolve (``initialize=True, register=False``) taken only AFTER
      those gates pass, so a cancelled or mismatched extract does not materialize a
      box dir.  ``register=False`` defers registration to after the copy succeeds —
      create's ordering, for create's reason.

    (The same re-resolve-for-the-real-box pattern the deferred-persona launch uses,
    ``start.py``'s ``__unregistered__`` note.)
    """
    if not archive_file.is_file():
        print(f"Error: Archive file not found: {archive_file}", file=sys.stderr)
        return 1

    # VALIDATION resolve — read project_hash / project_path ONLY.
    proj = resolve_any_project(
        std, config,
        project_dir=str(project_dir) if project_dir else None,
        initialize=False,
    )

    # ⚑ NAME COLLISION IS A TRUE PRE-FLIGHT, exactly as in ``box create``.  Extract
    # DELETES the destination tree before copying, so a collision discovered at
    # registration time (which is where it used to surface) would abort AFTER the old
    # box was already gone — destroy-then-fail. Check before touching anything.
    #
    # ⚑ BUT ONLY WHEN THE NAME IS SOMEONE ELSE'S.  ``check_primary_box_name_free``
    # refuses on MEMBERSHIP alone — it takes a workspace but uses it only for the
    # $HOME guard — so asking it about a box's OWN name always refuses.  Naming your
    # own box on a restore (``extract --name mybox`` into mybox's workspace) is the
    # single most natural way to spell this command, and unguarded it failed with a
    # cure that told the user to delete the very box they were restoring.
    #
    # The checker is NOT widened: ``box create`` depends on its refuse-on-membership
    # semantics (a create must never land on an existing name).  The re-materialize
    # exception belongs to extract, so extract states it, here.
    if name and proj.mode is BoxMode.primary:
        owner = primary_box_name_for_workspace(
            std.primary_workset, str(proj.project_path),
        )
        if owner != name:
            try:
                check_primary_box_name_free(
                    std.primary_workset, std.registry, name,
                    str(proj.project_path), force=False,
                )
            except (ProjectError, WorksetError) as e:
                print(f"Error: {e}", file=sys.stderr)
                print(
                    "  Extract into that box's own workspace, pick another --name, "
                    "or remove the conflicting box first "
                    "('kanibako box list' to see them).",
                    file=sys.stderr,
                )
                return 1

    temp_dir = tempfile.mkdtemp()
    try:
        try:
            with tarfile.open(str(archive_file), "r:xz") as tar:
                tar.extractall(temp_dir, filter="data")
        except (tarfile.TarError, OSError) as e:
            print(f"Error: Failed to extract archive: {e}", file=sys.stderr)
            return 1

        # Find the archive hash directory
        entries = list(Path(temp_dir).iterdir())
        if not entries:
            print("Error: Empty archive.", file=sys.stderr)
            return 1
        archive_hash_dir = entries[0]
        archive_hash = archive_hash_dir.name
        info_file = archive_hash_dir / "kanibako-archive-info.txt"

        if not info_file.is_file():
            print(
                "Error: Invalid archive format (missing kanibako-archive-info.txt)",
                file=sys.stderr,
            )
            return 1

        # Parse metadata
        info = _parse_info(info_file)
        archive_path = info.get("Project path", "")
        archive_basename = Path(archive_path).name if archive_path else ""
        current_basename = proj.project_path.name

        # Validate hash match
        hash_match = (
            archive_hash == proj.project_hash
            or archive_basename == current_basename
        )

        if not hash_match and not force:
            print("Warning: Project path mismatch")
            print()
            print(f"Archive from: {archive_path}")
            print(f"Restoring to: {proj.project_path}")
            print()
            try:
                confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
            except UserCancelled:
                print("Aborted.")
                return 2

        # Validate git state
        git_in_archive = info.get("Git repository", "") == "yes"
        if git_in_archive:
            rc = _validate_git_state(proj, info, force)
            if rc != 0:
                return rc

        # ⚑ DESTINATION resolve — only now that every gate has passed, so a
        # cancelled/mismatched extract leaves no box dir behind.  ``initialize=True``
        # is what replaces the ``__unregistered__`` sentinel with the box's REAL
        # metadata dir (assigning a name if the workspace has none, or REUSING the
        # existing one via the registry reverse-lookup, which is what makes
        # re-extracting over your own box restore in place rather than fork).
        try:
            proj = resolve_any_project(
                std, config,
                project_dir=str(project_dir) if project_dir else None,
                initialize=True,
                register=False,
                name_override=name,
            )
        except (ProjectError, WorksetError) as e:
            # Guard-1 path-uniqueness / name-collision: --name already belongs to a
            # different workspace.  Name the cure rather than the rule.
            print(f"Error: {e}", file=sys.stderr)
            print(
                "  Extract into the existing box's own workspace, pick another "
                "--name, or remove the conflicting box first "
                "('kanibako box list' to see them).",
                file=sys.stderr,
            )
            return 1

        if name and proj.mode is not BoxMode.primary:
            # --name only means anything for a default/primary box: a standalone box
            # carries its identity in its own settings.yaml and a workset box takes
            # its name from its workspace dir.  Say so instead of dropping it.
            print(
                f"Warning: --name is ignored for {proj.mode.value}-mode boxes "
                f"(this box is named {proj.name!r}).",
                file=sys.stderr,
            )

        from kanibako.commands.start import _register_new_box

        # Restore session data
        print("Restoring session data... ", end="", flush=True)
        projects_base = std.boxes
        projects_base.mkdir(parents=True, exist_ok=True)

        if proj.metadata_path.exists() and not remove_box_tree(proj.metadata_path):
            # A box home carries the root-owned canon skeleton (J-7) and may carry
            # files a rootless container wrote as root; a bare rmtree fails on both.
            print("failed.")
            print(
                f"Error: could not remove the existing box data at "
                f"{proj.metadata_path}.\nTry: podman unshare rm -rf "
                f"{proj.metadata_path}",
                file=sys.stderr,
            )
            return 1

        shutil.copytree(str(archive_hash_dir), str(proj.metadata_path))

        # Remove info file from restored data
        restored_info = proj.metadata_path / "kanibako-archive-info.txt"
        restored_info.unlink(missing_ok=True)

        # ⚑ Re-assert the canon skeleton on the RESTORED home.  ``tar.extractall``
        # runs with filter="data", which STRIPS ownership AND NORMALISES directory
        # modes (to 0755) — so the extracted skeleton is host-user-owned (= the
        # agent, in-box) and plainly writable.  This call RESTORES both the ownership
        # and the declared 555/444 modes; it is not merely an ownership top-up.
        if proj.shell_path.is_dir():
            materialize_canon_skeleton(proj.shell_path)

        # ⚑ REGISTER, exactly as create does — an extracted box that is not in the
        # registry is invisible to `box list`, unreachable by name, and (being
        # unregistered) would resolve back to the `__unregistered__` sentinel on the
        # next command.  Deferred to here for create's reason: register only once
        # the tree is actually in place.
        try:
            _register_new_box(std, proj)
        except (ProjectError, WorksetError) as e:
            # The tree is restored; only the registry write failed.  Report that
            # recoverable state instead of letting a traceback escape.
            print("done.")
            print(f"Error: restored data to {proj.metadata_path}, but could not "
                  f"register the box: {e}", file=sys.stderr)
            return 1

        print("done.")
        print(f"Session data restored to {proj.project_path}")
        print(f"  box: {proj.name} ({proj.mode.value})")
        return 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _peek_archive_info(archive_file: Path) -> dict[str, str] | None:
    """Extract archive to a temp dir and parse the info file."""
    temp_dir = tempfile.mkdtemp()
    try:
        try:
            with tarfile.open(str(archive_file), "r:xz") as tar:
                tar.extractall(temp_dir, filter="data")
        except (tarfile.TarError, OSError):
            return None
        entries = list(Path(temp_dir).iterdir())
        if not entries:
            return None
        info_file = entries[0] / "kanibako-archive-info.txt"
        if not info_file.is_file():
            return None
        info = _parse_info(info_file)
        info["_archive_hash"] = entries[0].name
        return info
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _restore_all(std, config, args) -> int:
    """Restore all kanibako-*.txz archives in the current directory."""
    import os

    scan_dir = Path(os.getcwd())
    archives = sorted(scan_dir.glob("kanibako-*.txz"))
    if not archives:
        print(f"No kanibako-*.txz archives found in {scan_dir}")
        return 0

    # Peek into each archive to get project path
    plan: list[tuple[Path, str]] = []
    for archive in archives:
        info = _peek_archive_info(archive)
        if info is None:
            print(f"  Skipping {archive.name} (invalid archive)", file=sys.stderr)
            continue
        project_path = info.get("Project path", "")
        if not project_path:
            print(f"  Skipping {archive.name} (no project path in metadata)", file=sys.stderr)
            continue
        plan.append((archive, project_path))

    if not plan:
        print("No valid archives found to restore.")
        return 0

    print(f"Found {len(plan)} archive(s) to restore:")
    for archive, project_path in plan:
        print(f"  {archive.name} → {project_path}")
    print()

    if not args.force:
        try:
            confirm_prompt(
                "Restore all listed archives? Existing session data will be overwritten.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted.")
            return 2

    restored = 0
    failed = 0
    for archive, project_path in plan:
        print(f"\n--- {archive.name} → {project_path}")
        rc = _restore_one(
            std, config, project_dir=project_path,
            archive_file=archive, force=True,
        )
        if rc == 0:
            restored += 1
        else:
            failed += 1

    print(f"\nRestored {restored} archive(s).", end="")
    if failed:
        print(f" {failed} failed.", end="")
    print()
    return 1 if failed else 0


def _parse_info(info_file: Path) -> dict[str, str]:
    """Parse kanibako-archive-info.txt into a dict."""
    result: dict[str, str] = {}
    for line in info_file.read_text().splitlines():
        if ": " in line and not line.startswith("  "):
            key, _, value = line.partition(": ")
            result[key.strip()] = value.strip()
    return result


def _validate_git_state(proj, info: dict[str, str], force: bool) -> int:
    """Validate git state between archive and workspace. Returns 0 to continue."""
    if not is_git_repo(proj.project_path):
        if not force:
            print(
                "Warning: Archive came from a git repository, "
                "but current workspace is not a git repo."
            )
            print()
            for key in ("Branch", "Commit"):
                if key in info:
                    print(f"  {key}: {info[key]}")
            print()
            try:
                confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
            except UserCancelled:
                print("Aborted.")
                return 2
        return 0

    archive_commit = info.get("Commit", "")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=proj.project_path,
        capture_output=True,
        text=True,
    )
    current_commit = result.stdout.strip() if result.returncode == 0 else ""

    if archive_commit != current_commit and not force:
        print("Warning: Git state mismatch")
        print()
        print("Archive from:")
        for key in ("Branch", "Commit"):
            if key in info:
                print(f"  {key}: {info[key]}")
        print()
        print("Current workspace:")
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=proj.project_path,
            capture_output=True,
            text=True,
        )
        current_branch = (
            branch_result.stdout.strip()
            if branch_result.returncode == 0
            else "unknown"
        )
        print(f"  Branch: {current_branch}")
        print(f"  Commit: {current_commit}")
        print()
        try:
            confirm_prompt("Continue anyway? Type 'yes' to confirm: ")
        except UserCancelled:
            print("Aborted.")
            return 2

    return 0
