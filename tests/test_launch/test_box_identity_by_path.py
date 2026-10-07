"""Box identity is the PATH, not the resolved target (Q4Jei 155-DUPLINK).

Two boxes whose workspaces link one target are twins: they share content, never
identity.  Every lookup on the identity chain compares literal paths, so a box
registered at its own path is never shadowed by another box whose path merely
resolves to the same place.  A connected dir still answers from a SUBDIR (the
ancestor rule), but only along its literal path.
"""

from __future__ import annotations

import os
from pathlib import Path

from kanibako.launch import box_resolve, journal
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode,
    detect_project_mode,
    load_std_paths,
    register_primary_box_name,
    resolve_designation,
    _early_scope,
)


def _connected(config_file, tmp_home):
    """A named workset ``ext-set`` connected to an external dir ``extproj``."""
    from kanibako.project.workset import add_project, create_workset

    config = load_config(config_file)
    std = load_std_paths(config)
    ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
    external = (tmp_home / "external_repo").resolve()
    external.mkdir()
    add_project(ws, "extproj", external, std)
    return config, std, external


def _primary_twin(std, link: Path, name: str) -> None:
    (std.boxes / name).mkdir(parents=True)
    register_primary_box_name(std.primary_workset, name, link,
                              early=_early_scope(std, BoxMode.primary))


class TestATwinOfAConnectedDirIsItsOwnBox:
    def test_the_twin_is_not_shadowed_by_the_connected_box(
        self, config_file, tmp_home,
    ):
        config, std, external = _connected(config_file, tmp_home)
        twin = tmp_home / "twin"
        twin.symlink_to(external)
        _primary_twin(std, twin, "twin")

        assert box_resolve.find_connected_external_box(twin, std) is None
        assert detect_project_mode(twin, std, config).mode is BoxMode.primary
        identity = box_resolve.resolve_box_identity(twin, std, config)
        assert identity is not None
        assert (identity["name"], identity["mode"]) == ("twin", BoxMode.primary)
        assert identity["workspace"] == twin

    def test_the_connected_box_still_answers_from_a_subdir(
        self, config_file, tmp_home,
    ):
        """bifrost A0: a launch from a SUBDIR of a connected dir still resolves."""
        config, std, external = _connected(config_file, tmp_home)
        subdir = external / "src" / "nested"
        subdir.mkdir(parents=True)

        owned = box_resolve.find_connected_external_box(subdir, std)
        assert owned is not None and owned.box_name == "extproj"
        assert detect_project_mode(subdir, std, config).mode is BoxMode.named

    def test_another_link_to_the_connected_dir_is_not_that_box(
        self, config_file, tmp_home,
    ):
        config, std, external = _connected(config_file, tmp_home)
        alias = tmp_home / "alias"
        alias.symlink_to(external)

        assert box_resolve.find_connected_external_box(alias, std) is None
        assert box_resolve.find_connected_external_box(alias / ".", std) is None


class TestTheCurrentDirectoryIsThePathTheUserTook:
    def test_pwd_names_the_link_the_shell_went_through(
        self, config_file, tmp_home, monkeypatch,
    ):
        std = load_std_paths(load_config(config_file))
        real = tmp_home / "real"
        real.mkdir()
        link = tmp_home / "link"
        link.symlink_to(real)
        monkeypatch.chdir(link)
        monkeypatch.setenv("PWD", str(link))

        assert os.getcwd() == str(real.resolve())
        assert resolve_designation(std, None, unknown_name_is_path=True) == str(link)

    def test_a_stale_pwd_falls_back_to_the_real_directory(
        self, config_file, tmp_home, monkeypatch,
    ):
        std = load_std_paths(load_config(config_file))
        here = tmp_home / "here"
        here.mkdir()
        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(here)
        monkeypatch.setenv("PWD", str(elsewhere))

        assert resolve_designation(std, None, unknown_name_is_path=True) == os.getcwd()


class TestAPendingCreateIsFoundByItsOwnPath:
    def test_a_twin_does_not_pick_up_the_other_twins_pending_create(
        self, tmp_path,
    ):
        jp = tmp_path / "journal.yaml"
        real = tmp_path / "real"
        real.mkdir()
        first = tmp_path / "first"
        first.symlink_to(real)
        second = tmp_path / "second"
        second.symlink_to(real)
        journal.write_entry(jp, "/box/first", op="create", name="first",
                            mode="primary", workspace=str(first))

        assert journal.pending_create_for_workspace(jp, first)["name"] == "first"
        assert journal.pending_create_for_workspace(jp, second) is None
