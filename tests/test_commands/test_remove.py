"""Tests for kanibako.commands.system_cmd (system subcommand).

Replaces the old test_remove.py which tested the now-removed ``remove``
command.  Config removal is now handled via ``system config --reset --all``.
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

    def test_shows_not_configured_without_config(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        args = argparse.Namespace()
        run_info(args)
        out = capsys.readouterr().out
        assert "(not configured)" in out

    def test_shows_runtime_not_found(self, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_info

        with patch(
            "kanibako.container.ContainerRuntime",
            side_effect=Exception("no runtime"),
        ):
            args = argparse.Namespace()
            run_info(args)
        out = capsys.readouterr().out
        assert "(not found)" in out


class TestSystemConfig:
    def test_show_no_overrides(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value=None, effective=False, reset=False,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0

    def test_show_effective(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value=None, effective=True, reset=False,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "container_image" in out

    def test_get_known_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value="image", effective=False, reset=False,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0

    def test_get_unknown_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value="nonexistent_key_xyz", effective=False, reset=False,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "unknown config key" in err

    def test_set_value(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value="image=custom:v2", effective=False, reset=False,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Set" in out

    def test_reset_requires_key(self, tmp_home, config_file, capsys):
        from kanibako.commands.system_cmd import run_config

        args = argparse.Namespace(
            key_value=None, effective=False, reset=True,
            all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "--reset requires a key" in err
