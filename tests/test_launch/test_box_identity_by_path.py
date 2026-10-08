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


class TestAWorksetMadeThroughALinkIsFoundThroughIt:
    """Registration stores the path the user gave, so the lookups that compare
    literal paths find it again along that same path."""

    def _through_alias(self, config_file, tmp_home):
        from kanibako.project.workset import add_project, create_workset, list_worksets

        config = load_config(config_file)
        std = load_std_paths(config)
        real = tmp_home / "real"
        (real / "src1" / "sub").mkdir(parents=True)
        alias = tmp_home / "alias"
        alias.symlink_to(real)
        ws = create_workset("ws1", alias / "ws1", std)
        add_project(ws, "src1", alias / "src1", std)
        return config, std, alias, list_worksets(std)["ws1"]

    def test_the_root_and_the_connected_source_are_stored_as_given(
        self, config_file, tmp_home,
    ):
        from kanibako.project import workset_registry

        _config, _std, alias, root = self._through_alias(config_file, tmp_home)

        assert root == alias / "ws1"
        boxes = workset_registry.load_workset_boxes(root / "registry.yaml")
        assert boxes == {"src1": str(alias / "src1")}

    def test_the_member_answers_from_the_linked_path_a_subdir_and_its_leaf(
        self, config_file, tmp_home,
    ):
        config, std, alias, _root = self._through_alias(config_file, tmp_home)

        for where in (alias / "src1", alias / "ws1" / "workspaces" / "src1"):
            identity = box_resolve.resolve_box_identity(where, std, config)
            assert identity is not None, where
            assert (identity["name"], identity["mode"]) == ("src1", BoxMode.named), where
        subdir = alias / "src1" / "sub"
        owned = box_resolve.find_connected_external_box(subdir, std)
        assert owned is not None and owned.box_name == "src1"
        assert detect_project_mode(subdir, std, config).mode is BoxMode.named

    def test_the_workset_also_answers_at_its_resolved_root(
        self, config_file, tmp_home,
    ):
        """A workset has one root, so either spelling of it finds the workset."""
        config, std, _alias, _root = self._through_alias(config_file, tmp_home)

        leaf = tmp_home / "real" / "ws1" / "workspaces" / "src1"
        identity = box_resolve.resolve_box_identity(leaf, std, config)
        assert identity is not None
        assert (identity["name"], identity["mode"]) == ("src1", BoxMode.named)


class TestARelativePathJoinsThePathTheUserTook:
    def test_dot_inside_a_link_names_the_link(
        self, config_file, tmp_home, monkeypatch,
    ):
        from kanibako.utils import literal_path

        std = load_std_paths(load_config(config_file))
        real = tmp_home / "real"
        (real / "sub").mkdir(parents=True)
        link = tmp_home / "link"
        link.symlink_to(real)
        monkeypatch.chdir(link)
        monkeypatch.setenv("PWD", str(link))

        assert literal_path(".") == str(link)
        assert literal_path("sub/..") == str(link)
        from kanibako.settings.paths import resolve_project

        assert resolve_project(std, load_config(config_file), ".").project_path == link


class TestTwinsHashTheirOwnPaths:
    def test_two_links_to_one_target_have_two_hashes(
        self, config_file, tmp_home,
    ):
        from kanibako.settings.paths import resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        real = tmp_home / "real"
        real.mkdir()
        first = tmp_home / "first"
        first.symlink_to(real)
        second = tmp_home / "second"
        second.symlink_to(real)

        hashes = {resolve_project(std, config, str(p)).project_hash
                  for p in (first, second, real)}
        assert len(hashes) == 3

    def test_a_path_with_no_link_keeps_its_hash(self, config_file, tmp_home):
        """A box registered at its resolved path hashes exactly as before."""
        from kanibako.settings.paths import resolve_project
        from kanibako.utils import project_hash

        config = load_config(config_file)
        std = load_std_paths(config)
        real = (tmp_home / "real").resolve()
        real.mkdir()

        proj = resolve_project(std, config, str(real))
        assert proj.project_hash == project_hash(str(real))


class TestAWorksetRootIsLoadedAndImportedAsGiven:
    """A workset root reached through a link keeps the link's spelling: loading it,
    importing it, and connecting a member under it never swap in the real path."""

    def _alias(self, tmp_home):
        real = tmp_home / "real"
        real.mkdir()
        alias = tmp_home / "alias"
        alias.symlink_to(real)
        return alias

    def test_an_in_tree_member_records_the_root_as_given(
        self, config_file, tmp_home,
    ):
        from kanibako.project import workset_registry
        from kanibako.project.workset import add_project, create_workset, load_workset

        std = load_std_paths(load_config(config_file))
        alias = self._alias(tmp_home)
        create_workset("ws1", alias / "ws1", std)
        ws = load_workset(alias / "ws1", "ws1", early_system=std.early_system)
        assert ws.root == alias / "ws1"

        add_project(ws, "m1", alias / "ws1" / "workspaces" / "m1", std)
        boxes = workset_registry.load_workset_boxes(alias / "ws1" / "registry.yaml")
        assert boxes == {"m1": str(alias / "ws1" / "workspaces" / "m1")}

    def test_an_imported_root_is_stored_as_given(self, config_file, tmp_home):
        from kanibako.project import import_reconcile, registry_store

        std = load_std_paths(load_config(config_file))
        alias = self._alias(tmp_home)
        (alias / "wsx").mkdir()

        name = import_reconcile.import_named_workset(std.registry, alias / "wsx")
        assert name == "wsx"
        stored = registry_store.load_section(std.registry, "worksets")["wsx"]
        assert str(stored) == str(alias / "wsx")
        # The same directory by its real path is the same workset: a no-op.
        again = import_reconcile.import_named_workset(
            std.registry, tmp_home / "real" / "wsx")
        assert again == "wsx"
        stored = registry_store.load_section(std.registry, "worksets")["wsx"]
        assert str(stored) == str(alias / "wsx")

    def test_connect_names_the_box_after_the_path_as_given(
        self, config_file, tmp_home, capsys,
    ):
        import argparse

        from kanibako.commands.workset_cmd import run_connect
        from kanibako.project import workset_registry
        from kanibako.project.workset import create_workset

        std = load_std_paths(load_config(config_file))
        create_workset("cws", tmp_home / "cws", std)
        real = tmp_home / "real_proj"
        real.mkdir()
        alias = tmp_home / "alias_proj"
        alias.symlink_to(real)

        rc = run_connect(argparse.Namespace(
            workset="cws", source=str(alias), project_name=None, force=False,
        ))
        assert rc == 0, capsys.readouterr().err
        boxes = workset_registry.load_workset_boxes(tmp_home / "cws" / "registry.yaml")
        assert boxes == {"alias_proj": str(alias)}

    def test_connect_refusal_names_the_path_as_given(
        self, config_file, tmp_home, capsys,
    ):
        import argparse

        from kanibako.commands.workset_cmd import run_connect
        from kanibako.project.workset import create_workset

        std = load_std_paths(load_config(config_file))
        create_workset("rws", tmp_home / "rws", std)
        alias = self._alias(tmp_home)

        rc = run_connect(argparse.Namespace(
            workset="rws", source=str(alias / "missing"), project_name=None, force=False,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert f"Cannot connect '{alias / 'missing'}'" in err


class TestALinkToARegisteredWorksetRootIsThatWorkset:
    """The directory is a workset's identity: a link named unlike the root it reaches
    never registers a second workset on that directory."""

    def _ws2_and_link(self, config_file, tmp_home):
        from kanibako.project.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("ws2", tmp_home / "ws2", std)
        link = tmp_home / "wsB"
        link.symlink_to(tmp_home / "ws2")
        return config, std, link

    def test_an_import_through_the_link_is_a_no_op(self, config_file, tmp_home):
        from kanibako.project import import_reconcile, registry_store

        _, std, link = self._ws2_and_link(config_file, tmp_home)
        assert import_reconcile.import_named_workset(std.registry, link) == "ws2"
        section = registry_store.load_section(std.registry, "worksets")
        assert dict(section) == {"ws2": str(tmp_home / "ws2")}

    def test_a_treewalk_through_the_link_imports_nothing(self, config_file, tmp_home):
        from kanibako.project import registry_store

        config, std, link = self._ws2_and_link(config_file, tmp_home)
        member = link / "workspaces" / "w1"
        member.mkdir(parents=True)
        detect_project_mode(member, std, config)
        section = registry_store.load_section(std.registry, "worksets")
        assert dict(section) == {"ws2": str(tmp_home / "ws2")}


class TestConnectRefusalsNameThePathAsGiven:
    def test_the_in_tree_refusal_names_the_link_and_the_literal_leaf(
        self, config_file, tmp_home, capsys,
    ):
        import argparse

        from kanibako.commands.workset_cmd import run_connect
        from kanibako.project.workset import create_workset

        std = load_std_paths(load_config(config_file))
        real = tmp_home / "real"
        real.mkdir()
        (tmp_home / "alias").symlink_to(real)
        root = tmp_home / "alias" / "lws"
        create_workset("lws", root, std)
        link = tmp_home / "inlink"
        link.symlink_to(root / "workspaces")

        rc = run_connect(argparse.Namespace(
            workset="lws", source=str(link), project_name="p", force=False,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert f"Cannot connect '{link}': it is inside the working set" in err
        assert f"it takes an existing '{root / 'workspaces' / 'p'}' directory" in err

    def test_the_primary_owner_refusal_names_the_link(
        self, config_file, tmp_home, capsys,
    ):
        import argparse

        from kanibako.commands.workset_cmd import run_connect
        from kanibako.project.workset import create_workset

        std = load_std_paths(load_config(config_file))
        create_workset("ows", tmp_home / "ows", std)
        real = tmp_home / "real_ws"
        real.mkdir()
        link = tmp_home / "link_ws"
        link.symlink_to(real)
        _primary_twin(std, link, "beta")

        rc = run_connect(argparse.Namespace(
            workset="ows", source=str(link), project_name=None, force=False,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert (f"Cannot connect '{link}': it is already the workspace of "
                f"primary box 'beta'") in err
