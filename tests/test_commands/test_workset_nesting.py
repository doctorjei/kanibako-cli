"""``workset connect`` onto another box's tree (keyspace § 0 "Per-owner resources").

"Except for identical workspaces and a standalone root link, refusal may be overridden via
--force": a link to a standalone root is refused whatever the flag.  Measured before the
fix: ``connect --force`` onto the link returned 0 and dropped the standalone's registry row.
The absorb at the root's own path stays allowed under --force.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._parser import run_create
from kanibako.commands.workset_cmd import run_connect
from kanibako.project import registry_store
from kanibako.project.workset import add_project, create_workset, list_worksets, load_workset


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    return load_std_paths(load_config(config_file))


def _connect(ws: str, source: Path, name: str, *, force: bool) -> int:
    return run_connect(argparse.Namespace(
        workset=ws, source=str(source), project_name=name, force=force))


def _members(std, ws: str) -> dict:
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc
    loaded = load_workset(list_worksets(std)[ws], ws, early_system=std.early_system)
    return workset_registry.load_workset_boxes(workset_registry.resolve_workset_registry_path(
        loaded.root, load_doc(loaded.root / "workset.yaml"), early=loaded.early_scope))


@pytest.fixture
def env(config_file, credentials_dir, tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    monkeypatch.setenv("PWD", str(tmp_home))
    std = _std(config_file)
    area = (tmp_home / "nest").resolve()
    area.mkdir()
    create_workset("wn", area / "wn", std)
    return std, area


def _standalone(std, area: Path, capsys) -> tuple[Path, str]:
    root = area / "XS"
    assert run_create(build_parser().parse_args(
        ["box", "create", "--standalone", "--register", str(root)])) == 0
    capsys.readouterr()
    name = registry_store.standalone_name_for_root(std.registry, root)
    assert name is not None
    return root, name


class TestConnectStandaloneRootLink:
    @pytest.mark.parametrize("force", [False, True])
    def test_link_to_a_standalone_root_is_refused_whatever_the_flag(
            self, env, capsys, force):
        std, area = env
        root, name = _standalone(std, area, capsys)
        rows = registry_store.load_standalone(std.registry)
        assert rows.get(name) == str(root)
        link = area / "rlnk"
        link.symlink_to(root)

        assert _connect("wn", link, "m", force=force) == 1
        err = capsys.readouterr().err
        assert (f"Cannot connect '{link}': it is a link to the root of standalone box "
                f"'{name}' ({root}).") in err, err
        assert "no flag overrides this" in err
        assert registry_store.load_standalone(std.registry) == rows  # the row survives
        assert "m" not in _members(std, "wn")

    def test_absorb_at_the_roots_own_path_still_works(self, env, capsys):
        std, area = env
        root, name = _standalone(std, area, capsys)
        assert _connect("wn", root, "m", force=True) == 0, capsys.readouterr().err
        assert _members(std, "wn").get("m") == str(root)
        assert name not in registry_store.load_standalone(std.registry)


class TestConnectInsideAMember:
    def _member(self, std, area: Path) -> Path:
        other = create_workset("other", area / "other", std)
        ext = area / "XN"
        (ext / "sub").mkdir(parents=True)
        add_project(other, "XN", ext, std)
        return ext

    @pytest.mark.parametrize("force", [False, True])
    def test_inside_a_member_names_the_members_path(self, env, capsys, force):
        std, area = env
        ext = self._member(std, area)
        assert _connect("wn", ext / "sub", "m", force=force) == 1
        err = capsys.readouterr().err
        assert (f"Cannot connect '{ext / 'sub'}': it is inside the workspace of member "
                f"'XN' of workset 'other' ({ext}).") in err, err
        assert "already connected as project" not in err
        assert "m" not in _members(std, "wn")

    def test_the_members_own_path_keeps_its_message(self, env, capsys):
        std, area = env
        ext = self._member(std, area)
        assert _connect("wn", ext, "m", force=True) == 1
        assert ("already connected as project 'XN' in workset 'other'"
                in capsys.readouterr().err)
