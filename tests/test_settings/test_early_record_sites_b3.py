"""The production sites in ``start``, ``workset``, ``restore`` and ``helper_listener`` hand
their early readers ``std``'s record, under the partition name the read is for.

``early_reads`` is :mod:`test_early_record_sites`'s spy: a read of the system tier with no
scope raises.  ``scoped_calls`` spies one named reader instead, for a site reached only
through a command whose other reads are another step's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.commands import start
from kanibako.project import workset
from kanibako.settings import paths
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import BoxMode, STANDALONE_META_DIR


@pytest.fixture
def early_reads(monkeypatch) -> list[str]:
    """The scope names every early read is made under; a read with no scope raises."""
    from kanibako.settings import workset_dirkeys

    real = workset_dirkeys.early_repoint
    seen: list[str] = []

    def spy(workset_root, workset_settings, key, *, early=None):
        if early is None:
            raise AssertionError(f"workset.{key} under {workset_root} was read with no record")
        seen.append(early.workset_name)
        return real(workset_root, workset_settings, key, early=early)

    for module in list(sys.modules.values()):
        if getattr(module, "early_repoint", None) is real:
            monkeypatch.setattr(module, "early_repoint", spy)
    return seen


@pytest.fixture
def scoped_calls(monkeypatch):
    """``spy(module, name)`` wraps *module.name*; returns the scope names its calls carried."""

    def spy(module, name: str) -> list[str]:
        real = getattr(module, name)
        seen: list[str] = []

        def wrapper(*args, early=None, **kwargs):
            if early is None:
                raise AssertionError(f"{name} was called with no record")
            seen.append(early.workset_name)
            return real(*args, early=early, **kwargs)

        monkeypatch.setattr(module, name, wrapper)
        return seen

    return spy


def _team(std, tmp_home: Path, *, force: bool = True):
    """A NAMED workset ``team``; *force* skips the primary-box check, which is a site of its own."""
    return workset.create_workset("team", tmp_home / "wsroot", std, force=force)


class TestWorksetSites:
    def test_create_reads_the_primary_boxes_under_the_primary_name(self, std, tmp_home,
                                                                   early_reads):
        _team(std, tmp_home, force=False)
        assert set(early_reads) == {"team", WS_TOKEN_PRIMARY}

    def test_the_default_workset(self, std, early_reads):
        assert workset.default_workset(std).is_default
        assert set(early_reads) == {WS_TOKEN_PRIMARY}

    def test_connect_and_release_a_member(self, std, tmp_home, early_reads):
        ws = _team(std, tmp_home)
        source = tmp_home / "ext-src"
        source.mkdir()
        early_reads.clear()
        workset.add_project(ws, "ext", source, std)
        workset.release_project(ws, "ext")
        assert set(early_reads) == {"team"}


class TestStartSites:
    def test_the_null_workspace_check_of_a_named_member(self, std, tmp_home, early_reads):
        ws = _team(std, tmp_home)
        source = tmp_home / "app-src"
        source.mkdir()
        workset.add_project(ws, "app", source, std)
        proj = MagicMock(mode=BoxMode.named, group=ws, project_path=source)
        proj.name = "app"
        early_reads.clear()
        start._refuse_null_workspace_bind(std, proj)
        assert early_reads == ["team"]

    def test_the_null_workspace_check_of_a_standalone_box(self, std, tmp_home, early_reads):
        root = tmp_home / "lone"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        (root / WORKSET_META_FILE).write_text("box: {}\n")
        proj = MagicMock(mode=BoxMode.standalone, group=None, metadata_path=root,
                         project_path=root)
        proj.name = "lone"
        start._refuse_null_workspace_bind(std, proj)
        assert early_reads == [WS_TOKEN_STANDALONE]

    def test_naming_and_registering_a_new_primary_box(self, std, tmp_home, early_reads):
        workspace = tmp_home / "project"
        workspace.mkdir(exist_ok=True)
        proj = MagicMock(mode=BoxMode.primary, project_path=workspace)
        proj.name = ""
        start._name_new_box_probe(std, proj)
        assert proj.name == "project"
        start._register_new_box(std, proj)
        assert paths.load_primary_boxes(std.primary_workset,
                                        early=paths._early_scope(std, BoxMode.primary)) == \
            {"project": str(workspace)}
        assert set(early_reads) == {WS_TOKEN_PRIMARY}


class TestHelperFork:
    def test_the_fork_names_its_box_under_the_primary_name(self, std, tmp_home, early_reads):
        from kanibako.channels.helper_listener import HelperContext, HelperHub

        workspace = tmp_home / "myapp"
        workspace.mkdir()
        shell = std.boxes / "myapp" / "home"
        (shell / "helpers").mkdir(parents=True)
        hub = HelperHub()
        hub._ctx = HelperContext(
            runtime=MagicMock(), image="test:latest", container_name_prefix="kanibako-myapp",
            shell_path=shell, helpers_dir=shell / "helpers", socket_path=tmp_home / "h.sock",
            project_path=workspace, data_path=std.data_path, boxes=std.boxes,
            registry=std.registry, primary_workset=std.primary_workset,
            early=paths._early_scope(std, BoxMode.primary),
        )
        assert hub._handle_fork({"name": "two"})["status"] == "ok"
        assert set(early_reads) == {WS_TOKEN_PRIMARY}


class TestRestoreSites:
    def test_the_extract_name_preflight(self, std, config_file, tmp_home, credentials_dir,
                                        scoped_calls, capsys):
        from kanibako.commands import archive, restore

        config = load_config(config_file)
        taken = tmp_home / "takenws"
        taken.mkdir()
        paths.resolve_project(std, config, project_dir=str(taken), initialize=True,
                              name_override="taken")
        src = tmp_home / "src"
        src.mkdir()
        paths.resolve_project(std, config, project_dir=str(src), initialize=True)
        archive_path = str(tmp_home / "src.txz")
        assert archive.run(argparse.Namespace(
            path=str(src), file=archive_path, all_projects=False,
            allow_uncommitted=True, allow_unpushed=True, force=True,
        )) == 0
        dest = tmp_home / "dest"
        dest.mkdir()

        owners = scoped_calls(restore, "primary_box_name_for_workspace")
        frees = scoped_calls(restore, "check_primary_box_name_free")
        assert restore.run(argparse.Namespace(
            file=archive_path, path=str(dest), name="taken", all_archives=False, force=True,
        )) == 1
        assert "conflicting box" in capsys.readouterr().err
        assert owners == [WS_TOKEN_PRIMARY]
        assert frees == [WS_TOKEN_PRIMARY]


class TestImportSites:
    def test_the_import_pass_reads_the_primary_boxes_under_the_primary_name(
        self, std, tmp_home, early_reads,
    ):
        """The cross-kind check of an imported workset reads the PRIMARY membership."""
        from kanibako.project import registry_store

        root = tmp_home / "found"
        workset.create_workset("found", root, std, force=True)
        registry_store.save_section(std.registry, "worksets", {})
        early_reads.clear()
        assert paths.detect_project_mode(root, std, load_config(std.config_file)).mode is \
            BoxMode.named
        assert set(early_reads) == {"found", WS_TOKEN_PRIMARY}
