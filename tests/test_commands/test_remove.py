"""Tests for kanibako.commands.system_cmd (system subcommand).

Replaces the old test_remove.py which tested the now-removed ``remove``
command.  Config removal is now handled via ``system reset --all``.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch


class TestSystemInfo:
    def test_shows_version(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        from kanibako import __version__
        assert __version__ in out

    def test_shows_python_version(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        run_info(args)
        out = capsys.readouterr().out
        assert "Python:" in out

    def test_shows_config_path(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        run_info(args)
        out = capsys.readouterr().out
        assert "Config:" in out

    def test_shows_data_path_when_configured(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        run_info(args)
        out = capsys.readouterr().out
        assert "Data:" in out
        assert "(not configured)" not in out

    def test_shows_not_initialized_without_config(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        run_info(args)
        out = capsys.readouterr().out
        assert "not initialized" in out

    def test_shows_runtime_not_found(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        with patch(
            "kanibako.container.ContainerRuntime",
            side_effect=Exception("no runtime"),
        ):
            args = argparse.Namespace()
            run_info(args)
        out = capsys.readouterr().out
        assert "not found" in out


class TestSystemConfig:
    def test_show_no_overrides(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_show

        args = argparse.Namespace(effective=False)
        rc = run_show(args)
        assert rc == 0

    def test_show_effective(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_show

        args = argparse.Namespace(effective=True)
        rc = run_show(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "box_image" in out

    def test_get_known_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_get

        args = argparse.Namespace(key="box.image")
        rc = run_get(args)
        assert rc == 0

    def test_get_unknown_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_get

        args = argparse.Namespace(key="nonexistent_key_xyz")
        rc = run_get(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "unknown config key" in err

    def test_set_value(self, tmp_home, config_file, capsys):
        # B4 R2 cross-scope write guard: a SYSTEM-scope ``set`` writes only
        # keys in its own scope's namespace (spec §0).  ``box.image`` is a box-scope
        # key, so setting it from the system scope is now correctly REFUSED (rc 1)
        # with the scope-error message — it must be set at the box scope instead.
        from kanibako.commands.system_cmd import run_set

        args = argparse.Namespace(key_value="box.image=custom:v2", force=False)
        rc = run_set(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot be set from the system scope" in err

    def test_reset_requires_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_reset

        args = argparse.Namespace(key=None, all_keys=False, force=False)
        rc = run_reset(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires a key" in err
