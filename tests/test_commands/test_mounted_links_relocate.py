"""Q74's exception: a relative link that is a BIND SOURCE keeps reaching its host target.

Podman resolves a directly mounted link on the host, so after a relocation its relative
text is repointed to the same host target.  Every other link keeps its text (box view).
"""

from __future__ import annotations

import argparse
import os

from kanibako.commands.box._duplicate import run_duplicate
from kanibako.commands.box._lifecycle import run_convert, run_move
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project


def _move_args(old, new, *, to_workset=None):
    return argparse.Namespace(
        old=str(old), new=str(new), force=True, to_default=False,
        to_standalone=False, to_workset=to_workset, name=None,
    )


def _bind(box_dir, *sources):
    """Declare one read-only bind per source in the box tier, at ``/opt/m<N>``."""
    rows = "".join(f'      /opt/m{n}: ["{src}"]\n' for n, src in enumerate(sources))
    (box_dir / "box.yaml").write_text(f"box:\n  bindings:\n    ro:\n{rows}")


def _seed(tree, outside):
    """A mounted relative link, a plain relative link, a mounted absolute and a dangling one."""
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "f.txt").write_text("host data")
    rel = os.path.relpath(outside, os.path.realpath(tree))
    texts = {
        "mounted": rel,
        "plain": rel,
        "mounted_abs": str(outside),
        "mounted_gone": os.path.join(rel, "no-such"),
    }
    for name, text in texts.items():
        (tree / name).symlink_to(text)
    return texts


def _primary(config_file, tmp_home, name="linked"):
    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = tmp_home / name
    project_dir.mkdir()
    texts = _seed(project_dir, tmp_home / "outside")
    proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
    _bind(proj.metadata_path, "{meta.box.workspace}/mounted",
          "{meta.box.workspace}/mounted_abs", "{meta.box.workspace}/mounted_gone")
    return std, project_dir, texts


def _assert_repointed(tree, outside, texts):
    """The mounted links name the host target from *tree*; every other link kept its text."""
    assert os.readlink(tree / "mounted") == os.path.relpath(outside, os.path.realpath(tree))
    assert (tree / "mounted" / "f.txt").read_text() == "host data"
    assert os.readlink(tree / "plain") == texts["plain"]
    assert os.readlink(tree / "mounted_abs") == texts["mounted_abs"]
    # A dangling source still names the same host path; it is not dropped or followed.
    assert os.path.realpath(tree / "mounted_gone") == str(outside / "no-such")


class TestMoveRepointsMountedLinks:
    def test_primary_move_across_depth(self, config_file, tmp_home, credentials_dir):
        _std, project_dir, texts = _primary(config_file, tmp_home)
        dest = tmp_home / "a" / "b" / "moved"
        dest.parent.mkdir(parents=True)

        assert run_move(_move_args(project_dir, dest)) == 0

        _assert_repointed(dest, tmp_home / "outside", texts)
        assert (tmp_home / "outside" / "f.txt").read_text() == "host data"

    def test_workset_to_workset_move_through_the_stash(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The home rides the stash (two copies); its mounted link lands repointed."""
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "x" / "y" / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        home = ws_a.projects_dir / "b1" / "home"
        home.mkdir(parents=True, exist_ok=True)
        ws_texts = _seed(internal, tmp_home / "outside")
        home_texts = _seed(home, tmp_home / "outside")
        _bind(ws_a.projects_dir / "b1",
              "{meta.box.workspace}/mounted", "{meta.box.home}/mounted")

        dest = ws_b.workspaces_dir / "b1"
        assert run_move(_move_args(internal, dest, to_workset="wsb")) == 0

        new_home = ws_b.projects_dir / "b1" / "home"
        for tree, texts in ((dest, ws_texts), (new_home, home_texts)):
            assert os.readlink(tree / "mounted") == os.path.relpath(
                tmp_home / "outside", os.path.realpath(tree))
            assert (tree / "mounted" / "f.txt").read_text() == "host data"
            assert os.readlink(tree / "plain") == texts["plain"]


class TestConvertAndDuplicateRepointMountedLinks:
    def test_convert_to_standalone_with_move(self, config_file, tmp_home, credentials_dir):
        _std, project_dir, texts = _primary(config_file, tmp_home)
        dest = tmp_home / "p" / "q" / "solo"

        rc = run_convert(argparse.Namespace(
            old=str(project_dir), box=None, to_default=False, to_standalone=True,
            to_workset=None, move=str(dest), force=True, name=None))

        assert rc == 0
        _assert_repointed(dest / "workspace", tmp_home / "outside", texts)

    def test_duplicate_to_another_depth_leaves_the_source_alone(
        self, config_file, tmp_home, credentials_dir,
    ):
        _std, project_dir, texts = _primary(config_file, tmp_home)
        dest = tmp_home / "d" / "e" / "twin"
        dest.parent.mkdir(parents=True)

        rc = run_duplicate(argparse.Namespace(
            source_path=str(project_dir), new_path=str(dest), to_mode=None,
            bare=False, force=True, box=None, project_name=None))

        assert rc == 0
        _assert_repointed(dest, tmp_home / "outside", texts)
        for name, text in texts.items():
            assert os.readlink(project_dir / name) == text

    def test_a_bare_duplicate_leaves_the_users_own_workspace_alone(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--bare`` copies no workspace, so a same-named link already there is the user's."""
        _std, project_dir, texts = _primary(config_file, tmp_home)
        dest = tmp_home / "d" / "e" / "own"
        dest.mkdir(parents=True)
        (dest / "mounted").symlink_to(texts["mounted"])

        rc = run_duplicate(argparse.Namespace(
            source_path=str(project_dir), new_path=str(dest), to_mode=None,
            bare=True, force=True, box=None, project_name=None))

        assert rc == 0
        assert os.readlink(dest / "mounted") == texts["mounted"]


class TestUnresolvedBindingsCarryLinksVerbatim:
    def test_a_move_of_a_box_whose_settings_refuse_says_so(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Nothing is repointed, the move still succeeds, and a Note names the cure."""
        std, project_dir, texts = _primary(config_file, tmp_home)
        box_dir = resolve_project(
            std, load_config(config_file), project_dir=str(project_dir)).metadata_path
        (box_dir / "box.yaml").write_text(
            'box:\n  bindings:\n    ro:\n      /opt/m0: ["{no.such.key}/mounted"]\n')
        dest = tmp_home / "a" / "b" / "moved"
        dest.parent.mkdir(parents=True)

        assert run_move(_move_args(project_dir, dest)) == 0

        for name, text in texts.items():
            assert os.readlink(dest / name) == text
        assert "could not resolve the bindings of" in capsys.readouterr().err
