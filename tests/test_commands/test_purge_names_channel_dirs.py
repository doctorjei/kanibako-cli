"""A purge keeps the box's inbox and share dirs, and names them (171-RM Q2 = (b) [Jei:2026-10-11]).

system-design § "Box lifecycle": a destructive verb keeps any path outside the box's
root and reports it.  The channel dirs are outside every mode's root.  Each door lists
them as kept before the question (where it has one) and names them after the run; a
message in the inbox survives.  The expected paths come from ``box_channel_addresses``,
the launch's own mount resolver, not from the code under test.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.project import registry_store
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode, WorksetSpec, load_std_paths, resolve_project, resolve_standalone_project,
    resolve_workset_project)


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    monkeypatch.setattr("kanibako.commands.start.seed_new_box",
                        lambda std, config, proj, **kw: None)


def _reload(config_file):
    config = load_config(config_file)
    return load_std_paths(config), config


def _fill_channels(std, proj) -> list[Path]:
    """Make the box's inbox (with a message) and share dirs; return them."""
    from kanibako.channels.channels import box_channel_addresses

    addr = box_channel_addresses(proj, std)
    dirs = [d for d in (addr.inbox, addr.share_global, addr.share_workset) if d is not None]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    assert addr.inbox is not None
    (addr.inbox / "msg.md").write_text("mail from another box")
    return dirs


def _assert_kept_and_named(dirs: list[Path], out: str, err: str, *, listed: bool = True):
    for d in dirs:
        assert d.is_dir(), f"{d} was deleted"
        assert f"at {d} — it is outside the box's root" in err, (d, err)
        if listed:
            assert f": {d}\n" in out and "kept channel" in out, (d, out)
    assert (dirs[0] / "msg.md").read_text() == "mail from another box"


def _primary(config_file, tmp_home):
    std, config = _reload(config_file)
    pdir = tmp_home / "code" / "pk"
    pdir.mkdir(parents=True)
    proj = resolve_project(std, config, project_dir=str(pdir), initialize=True)
    assert proj.mode is BoxMode.primary and proj.name == "pk", "setup"
    return std, proj


def _box_rm(target: str, *, purge: bool = True) -> int:
    from kanibako.commands.box._parser import run_rm

    return run_rm(argparse.Namespace(target=target, purge=purge, force=True))


def _box_purge(path: str | None, *, all_projects: bool = False) -> int:
    from kanibako.commands.clean import run

    return run(argparse.Namespace(all_projects=all_projects, force=True, path=path))


class TestPrimary:
    def test_box_rm_purge(self, config_file, tmp_home, credentials_dir, capsys):
        std, proj = _primary(config_file, tmp_home)
        dirs = _fill_channels(std, proj)
        assert len(dirs) == 3, dirs  # inbox, global share, workset share
        capsys.readouterr()
        assert _box_rm("pk") == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err)
        assert out.index("--purge KEEPS") < out.index("--purge deletes")

    def test_a_parked_primary_purge(self, config_file, tmp_home, credentials_dir, capsys):
        std, proj = _primary(config_file, tmp_home)
        dirs = _fill_channels(std, proj)
        assert _box_rm("pk", purge=False) == 0
        assert "pk" in registry_store.load_deregistered(std.registry)
        capsys.readouterr()
        assert _box_rm("pk") == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err)

    def test_box_purge(self, config_file, tmp_home, credentials_dir, capsys):
        std, proj = _primary(config_file, tmp_home)
        dirs = _fill_channels(std, proj)
        capsys.readouterr()
        assert _box_purge(str(proj.project_path)) == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err)

    def test_box_purge_all(self, config_file, tmp_home, credentials_dir, capsys):
        std, proj = _primary(config_file, tmp_home)
        dirs = _fill_channels(std, proj)
        capsys.readouterr()
        assert _box_purge(None, all_projects=True) == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err, listed=False)

    def test_a_declined_question_names_them_but_prints_no_note(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.commands.box._parser import run_rm

        std, proj = _primary(config_file, tmp_home)
        dirs = _fill_channels(std, proj)

        def _eof(*_a):
            raise EOFError
        monkeypatch.setattr("builtins.input", _eof)
        capsys.readouterr()
        assert run_rm(argparse.Namespace(target="pk", purge=True, force=False)) == 2
        out, err = capsys.readouterr()
        assert all(f": {d}\n" in out for d in dirs), out
        assert "outside the box's root" not in err, err

    def test_absent_channel_dirs_are_not_named(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        _primary(config_file, tmp_home)
        capsys.readouterr()
        assert _box_rm("pk") == 0
        out, err = capsys.readouterr()
        assert "kept channel" not in out and "outside the box's root" not in err


class TestStandalone:
    def test_box_rm_purge(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_create

        std, config = _reload(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        assert run_create(argparse.Namespace(
            path=str(root), standalone=True, no_vault=False, name=None, image=None,
            agent=None, allow_home=False, register=True, recover=False)) == 0
        (name,) = registry_store.load_standalone(std.registry)
        proj = resolve_standalone_project(std, config, str(root))
        dirs = _fill_channels(std, proj)
        assert len(dirs) == 2, dirs  # no workset-local share for a standalone
        assert "__STANDALONE__" in str(dirs[0]), dirs
        capsys.readouterr()
        assert _box_rm(name) == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err)


def _member(config_file, tmp_home):
    std, config = _reload(config_file)
    ws = create_workset("nws", tmp_home / "worksets" / "nws", std)
    src = tmp_home / "nsrc" / "bar"
    src.mkdir(parents=True)
    add_project(ws, "bar", src)
    member = resolve_workset_project(WorksetSpec.from_workset(ws), "bar", std, config,
                                     initialize=True)
    assert member.mode is BoxMode.named, "setup"
    return std, ws, member


class TestNamedMember:
    def test_box_purge(self, config_file, tmp_home, credentials_dir, capsys):
        std, _ws, member = _member(config_file, tmp_home)
        dirs = _fill_channels(std, member)
        assert len(dirs) == 3 and "nws" in str(dirs[0]), dirs
        capsys.readouterr()
        assert _box_purge(str(member.project_path)) == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err)

    def test_disconnect_remove_files(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        std, _ws, member = _member(config_file, tmp_home)
        dirs = _fill_channels(std, member)
        capsys.readouterr()
        assert run_disconnect(argparse.Namespace(
            workset="nws", project="bar", box=None, remove_files=True, force=True)) == 0
        out, err = capsys.readouterr()
        _assert_kept_and_named(dirs, out, err, listed=False)


class TestUnstatableChannelDir:
    """An inbox whose parent cannot be searched is named, never crashed on (3.11 raises,
    3.13 hides it).  The stat fails, so it is named as unchecked, before and after."""

    @staticmethod
    def _lock(inbox: Path):
        inbox.parent.chmod(0)
        with pytest.raises(PermissionError):  # setup: root would see through it
            inbox.lstat()
        return inbox.parent

    def test_box_rm_purge(self, config_file, tmp_home, credentials_dir, capsys):
        std, proj = _primary(config_file, tmp_home)
        inbox = _fill_channels(std, proj)[0]
        locked = self._lock(inbox)
        try:
            capsys.readouterr()
            assert _box_rm("pk") == 0
            out, err = capsys.readouterr()
        finally:
            locked.chmod(0o755)
        assert f"kept channel inbox: {inbox} (could not be checked: " in out, out
        assert f"could not check the box's channel inbox at {inbox} (" in err, err
        assert (inbox / "msg.md").read_text() == "mail from another box"

    def test_disconnect_remove_files(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        std, _ws, member = _member(config_file, tmp_home)
        inbox = _fill_channels(std, member)[0]
        locked = self._lock(inbox)
        try:
            capsys.readouterr()
            assert run_disconnect(argparse.Namespace(
                workset="nws", project="bar", box=None, remove_files=True, force=True)) == 0
            _out, err = capsys.readouterr()
        finally:
            locked.chmod(0o755)
        assert f"could not check the box's channel inbox at {inbox} (" in err, err
        assert (inbox / "msg.md").read_text() == "mail from another box"
