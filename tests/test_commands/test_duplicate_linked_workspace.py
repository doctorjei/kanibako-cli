"""A duplicate's workspace landing, and Q102 (a) on a linked workspace root.

Two defects meet here.  ``_duplicate_from_workset`` merged the member's workspace
into the NEW BOX ROOT, so the workspace files sat beside the box's own
``workset.yaml`` / ``box_data/`` and the new box's live workspace came out empty.
And because that root was the user's directory, the D1 fix had to forbid
``_merge_workspace`` from ever laying a linked root -- which also forbade it
everywhere else, contradicting the class ruling **Q102 -> (a): a duplicate
re-creates a linked tree root AS A LINK, the copy sharing the target**.

Landing the workspace in the destination's resolved ``workspace/`` subdir unties
the knot: a link laid THERE shares the target without writing a byte into the
user's directory.  ``--to primary`` lays the link at the destination itself: box
identity is the path, not the resolved target (Q4Jei 155-DUPLINK), so the twin
is still its own box.
"""

from __future__ import annotations

import argparse
import os
import shutil


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_config(config_file), load_std_paths(load_config(config_file))


def _member_with_workspace(tmp_home, config_file, name, workspace, linked=True):
    """A named workset whose member ``name``'s workspace is *workspace* as stored.

    ``linked=False`` makes the member's workspace an ordinary real directory.
    ``add_project`` records the RESOLVED path, which hides a linked workspace from
    the duplicate entirely.  The registry row is rewritten to name *workspace*
    literally -- the shape a relocation that laid a link leaves behind, and the one
    the reviewer hit.
    """
    from kanibako.project.workset import add_project, create_workset

    config, std = _std(config_file)
    root = (tmp_home / "worksets" / "wsa").resolve()
    ws = create_workset("wsa", root, std)
    leaf = ws.workspaces_dir / name
    leaf.parent.mkdir(parents=True, exist_ok=True)
    if not linked:
        leaf.mkdir(parents=True)
        if workspace.exists():
            shutil.copytree(str(workspace), str(leaf), dirs_exist_ok=True)
    else:
        workspace.mkdir(parents=True, exist_ok=True)
        leaf.symlink_to(workspace)
    add_project(ws, name, leaf, std)
    (root / "registry.yaml").write_text(f"boxes:\n  {name}: {leaf}\n")
    return config, std, leaf


def _dup(config, source, dest, to_mode, force=True):
    from kanibako.commands.box._duplicate import run_duplicate

    return run_duplicate(argparse.Namespace(
        source_path=str(source), new_path=str(dest),
        to_mode=to_mode, bare=False, force=force,
    ))


class TestAWorksetDuplicateLandsItsWorkspaceInItsOwnSubdir:
    """The root belongs to the box artifacts; the workspace lives under it."""

    def test_the_workspace_lands_at_dst_workspace_not_at_the_root(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config, _std_, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "plain_app", linked=False)
        (leaf / "keepme.txt").write_text("payload\n")
        dest = (tmp_home / "dup-target").resolve()

        rc = _dup(config, leaf, dest, "standalone")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        landed = dest / "workspace" / "keepme.txt"
        assert landed.is_file(), (
            f"the workspace did not land at {dest / 'workspace'}; "
            f"found {sorted(str(p.relative_to(dest)) for p in dest.rglob('keepme.txt'))}. "
            f"out={cap.out}\nerr={cap.err}"
        )
        assert landed.read_text() == "payload\n"
        # The root carries the box's own artifacts, not the workspace's files.
        assert (dest / "workset.yaml").is_file()
        assert (dest / "box_data").is_dir()
        assert not (dest / "keepme.txt").exists()


class TestALinkedWorkspaceRootIsCarriedAsALink:
    """Q102 (a): the copy SHARES the target rather than becoming a second copy."""

    def test_the_landed_workspace_is_a_link_sharing_the_source_target(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config, _std_, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app")
        (leaf / "keepme.txt").write_text("payload\n")
        target = tmp_home / "outside_app"
        dest = (tmp_home / "dup-target").resolve()

        rc = _dup(config, leaf, dest, "standalone")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        landed = dest / "workspace"
        assert landed.is_symlink(), (
            f"{landed} is {describe(landed)}, not a link sharing {target}. "
            f"out={cap.out}\nerr={cap.err}"
        )
        assert os.path.realpath(landed) == os.path.realpath(target)
        assert (landed / "keepme.txt").read_text() == "payload\n"

    def test_nothing_is_written_into_the_users_target_directory(
        self, config_file, tmp_home, credentials_dir,
    ):
        config, _std_, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app")
        (leaf / "keepme.txt").write_text("payload\n")
        target = tmp_home / "outside_app"
        before = sorted(p.name for p in target.iterdir())
        dest = (tmp_home / "dup-target").resolve()

        _dup(config, leaf, dest, "standalone")

        # The box's own workset.yaml / box_data / .gitignore must NOT appear in
        # the user's directory -- that leak was the D1 defect.
        assert sorted(p.name for p in target.iterdir()) == before

    def test_a_workspace_the_user_already_made_is_not_traded_for_a_pointer(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """An empty ``<dst>/workspace`` is the user's directory, not a placeholder
        this operation made.  The ordinary merge runs; no link is laid over it."""
        config, _std_, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app")
        (leaf / "keepme.txt").write_text("payload\n")
        dest = (tmp_home / "dup-target").resolve()
        (dest / "workspace").mkdir(parents=True)

        rc = _dup(config, leaf, dest, "standalone")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        landed = dest / "workspace"
        assert not landed.is_symlink()
        assert (landed / "keepme.txt").read_text() == "payload\n"


class TestAPrimaryTargetSharesTheLinkAndKeepsItsOwnIdentity:
    """Q102 (a) without a carve-out: the twin shares the target, yet each box
    answers for itself and keeps its own store."""

    def test_to_primary_lays_the_link_and_each_twin_is_its_own_box(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.box_resolve import resolve_box_identity

        config, std, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app")
        (leaf / "keepme.txt").write_text("payload\n")
        target = tmp_home / "outside_app"
        dest = (tmp_home / "dup-primary").resolve()

        rc = _dup(config, leaf, dest, "primary")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        assert dest.is_symlink(), f"{dest} is {describe(dest)}"
        assert os.path.realpath(dest) == os.path.realpath(target)
        assert sorted(p.name for p in target.iterdir()) == ["keepme.txt"]
        dst_id = resolve_box_identity(dest, std, config)
        src_id = resolve_box_identity(leaf, std, config)
        assert (dst_id["name"], dst_id["mode"].value) == ("dup-primary", "primary")
        assert (src_id["name"], src_id["mode"].value) == ("app", "named")
        store = std.boxes / "dup-primary"
        assert store.is_dir() and not store.is_symlink()
        assert not store.resolve().is_relative_to(target.resolve())


def describe(p):
    if p.is_symlink():
        return f"LINK->{os.readlink(p)}" + ("" if p.exists() else " (DANGLING)")
    if p.is_dir():
        return "REAL-DIR"
    return "ABSENT"


class TestMergeWorkspaceStillRefusesToLinkIntoARoot:
    """The D1 protection is the DEFAULT, and it survives the new opt-in."""

    def test_the_default_never_lays_the_source_root_link(self, tmp_path):
        from kanibako.commands.box._duplicate import _merge_workspace

        real = tmp_path / "real"
        real.mkdir()
        (real / "f.txt").write_text("f")
        src = tmp_path / "ws"
        src.symlink_to(real)
        dst = tmp_path / "boxroot"

        _merge_workspace(src, dst, False)

        assert not dst.is_symlink()
        assert (dst / "f.txt").read_text() == "f"

    def test_the_opt_in_lays_the_link_only_while_the_destination_is_absent(
        self, tmp_path,
    ):
        from kanibako.commands.box._duplicate import _merge_workspace

        real = tmp_path / "real"
        real.mkdir()
        (real / "f.txt").write_text("f")
        src = tmp_path / "ws"
        src.symlink_to(real)
        dst = tmp_path / "boxroot" / "workspace"

        _merge_workspace(src, dst, False, share_root_link=True)

        assert dst.is_symlink()
        assert os.path.realpath(dst) == os.path.realpath(real)


class TestADuplicateThroughALinkedParentIsFoundThere:
    def test_the_destination_is_registered_at_the_path_given(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.box_resolve import resolve_box_identity

        config, std, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app", linked=False)
        real = tmp_home / "real"
        real.mkdir()
        alias = tmp_home / "alias"
        alias.symlink_to(real)
        dest = alias / "dup"

        rc = _dup(config, leaf, dest, "primary")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        identity = resolve_box_identity(dest, std, config)
        assert identity is not None
        assert (identity["name"], identity["workspace"]) == ("dup", dest)

    def test_a_destination_linking_to_the_source_is_the_same_path(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        config, std, leaf = _member_with_workspace(
            tmp_home, config_file, "app", tmp_home / "outside_app", linked=False)
        dest = tmp_home / "same"
        dest.symlink_to(leaf)

        rc = _dup(config, leaf, dest, "primary")

        assert rc == 1
        assert "source and destination paths are the same" in capsys.readouterr().err
