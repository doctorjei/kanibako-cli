"""The box, workset, and purge verbs hand their early readers ``std``'s record, under the
partition name the read is for.

The spy is ``early_reads`` from ``test_early_record_sites``: a read with no scope raises,
and every scope name is recorded.  Each test installs it after its setup, and hands the
verb the ``std`` it already loaded, so only the verb's own reads count: a fresh
``load_std_paths`` would add the four primary-root reads to every verb.
"""

from __future__ import annotations

import argparse
import sys

from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.project.workset import create_workset
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import (
    BoxMode, _early_scope, load_primary_boxes, load_std_paths, resolve_project,
    resolve_standalone_project,
)
from tests.test_settings.test_early_record_sites import early_reads  # noqa: F401 - the spy


def _env(config_file):
    config = load_config(config_file)
    return config, load_std_paths(config)


def _spy(request, std) -> list[str]:
    """Install the spy, and make every ``load_std_paths`` the verb calls return *std*."""
    monkeypatch = request.getfixturevalue("monkeypatch")
    real = load_std_paths  # this module holds the name too, so it is patched in the loop
    for module in list(sys.modules.values()):
        if getattr(module, "load_std_paths", None) is real:
            monkeypatch.setattr(module, "load_std_paths", lambda *_a, **_k: std)
    return request.getfixturevalue("early_reads")


def _primary(config, std, workspace):
    workspace.mkdir()
    resolve_project(std, config, project_dir=str(workspace), initialize=True)
    return workspace


def _standalone(config, std, root):
    root.mkdir()
    return resolve_standalone_project(std, config, str(root), initialize=True)


class TestPurge:
    def test_a_primary_purge(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.clean import run

        config, std = _env(config_file)
        workspace = _primary(config, std, tmp_home / "p")
        seen = _spy(request, std)
        assert run(argparse.Namespace(path=str(workspace), all_projects=False, force=True)) == 0
        assert set(seen) == {WS_TOKEN_PRIMARY}
        # The unregister swallows any error, the spy's included, so the entry must be gone.
        assert workspace.name not in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary))

    def test_a_standalone_purge(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.clean import run

        config, std = _env(config_file)
        _standalone(config, std, tmp_home / "lone")
        seen = _spy(request, std)
        assert run(argparse.Namespace(path=str(tmp_home / "lone"), all_projects=False,
                                      force=True)) == 0
        # The log purge's share walk reads the default workset under the primary token.
        assert set(seen) == {WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE}


class TestBoxRm:
    def test_a_primary_rm(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.box import run_rm

        config, std = _env(config_file)
        workspace = _primary(config, std, tmp_home / "p")
        seen = _spy(request, std)
        assert run_rm(argparse.Namespace(target=str(workspace), purge=False, force=True)) == 0
        assert set(seen) == {WS_TOKEN_PRIMARY}

    def test_a_standalone_rm_purge(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.box import run_rm

        config, std = _env(config_file)
        _standalone(config, std, tmp_home / "lone")
        seen = _spy(request, std)
        assert run_rm(argparse.Namespace(target=str(tmp_home / "lone"), purge=True,
                                         force=True)) == 0
        # The target is looked up in the primary membership before it resolves as a root.
        assert set(seen) == {WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE}


class TestDuplicate:
    def test_primary_to_primary(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.box import run_duplicate

        config, std = _env(config_file)
        src = _primary(config, std, tmp_home / "src")
        seen = _spy(request, std)
        assert run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(tmp_home / "dst"),
            bare=False, force=True, to_mode=None,
        )) == 0
        assert set(seen) == {WS_TOKEN_PRIMARY}

    def test_primary_to_standalone(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.box import run_duplicate

        config, std = _env(config_file)
        src = _primary(config, std, tmp_home / "src")
        seen = _spy(request, std)
        assert run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(tmp_home / "dst"), to_mode="standalone",
            bare=False, force=True, workset=None, project_name=None,
        )) == 0
        assert set(seen) == {WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE}


class TestLifecycle:
    def test_a_standalone_move(self, config_file, tmp_home, credentials_dir, request):
        from kanibako.commands.box._lifecycle import run_move

        config, std = _env(config_file)
        _standalone(config, std, tmp_home / "lone")
        seen = _spy(request, std)
        assert run_move(argparse.Namespace(
            old=str(tmp_home / "lone"), new=str(tmp_home / "moved"), force=True,
            to_default=False, to_standalone=False, to_workset=None, name=None,
        )) == 0
        # ``vault_enabled`` loads the system tier again, which resolves the primary roots.
        assert set(seen) == {WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE}

    def test_a_primary_convert_to_standalone(self, config_file, tmp_home, credentials_dir,
                                             request):
        from kanibako.commands.box._lifecycle import run_convert

        config, std = _env(config_file)
        workspace = _primary(config, std, tmp_home / "p")
        seen = _spy(request, std)
        assert run_convert(argparse.Namespace(
            old=str(workspace), force=True, to_default=False, to_standalone=True,
            to_workset=None, move=None, name=None,
        )) == 0
        assert set(seen) == {WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE}


class TestWorksetVerbs:
    """``project/workset.py``'s own reads are S2b3's; these paths stop before them."""

    def test_rm_of_a_workset_whose_settings_do_not_load(self, config_file, tmp_home,
                                                        credentials_dir, request):
        from kanibako.commands.workset_cmd import run_rm

        _config, std = _env(config_file)
        ws = create_workset("team", tmp_home / "team", std)
        (ws.root / WORKSET_META_FILE).write_text("workset: [unclosed\n")
        seen = _spy(request, std)
        assert run_rm(argparse.Namespace(name="team", force=False, purge=False)) == 1
        assert set(seen) == {"team"}

    def test_connect_refuses_a_primary_workspace(self, config_file, tmp_home,
                                                 credentials_dir, request):
        from kanibako.commands.workset_cmd import run_connect

        config, std = _env(config_file)
        create_workset("team", tmp_home / "team", std)
        workspace = _primary(config, std, tmp_home / "p")
        seen = _spy(request, std)
        assert run_connect(argparse.Namespace(
            workset="team", source=str(workspace), project_name=None, force=True,
        )) == 1
        assert set(seen) == {WS_TOKEN_PRIMARY, "team"}


class TestWorksetStamp:
    def test_the_named_stamp_reads_under_the_workset_name(self, std, tmp_path, request):
        from kanibako.launch.templates import check_workset_template, install_workset_template

        root = tmp_path / "ws"
        root.mkdir()
        seen = _spy(request, std)
        check_workset_template(std, root, workset_name="team")
        install_workset_template(std, root, workset_name="team")
        assert set(seen) == {"team"}

    def test_the_standalone_stamp_reads_under_the_partition(self, std, tmp_path, request):
        from kanibako.launch.templates import check_workset_template, install_workset_template

        root = tmp_path / "lone"
        root.mkdir()
        seen = _spy(request, std)
        check_workset_template(std, root, workset_name=WS_TOKEN_STANDALONE, canon_only=True)
        install_workset_template(std, root, workset_name=WS_TOKEN_STANDALONE, canon_only=True)
        assert set(seen) == {WS_TOKEN_STANDALONE}
