"""Tests for `kanibako system config` config-vs-settings file split.

The SYSTEM scope writes behavior SETTINGS (system.default_agent + agent
settings) to ``@system.settings`` = ``global/settings.yaml`` and structural
``system.*`` CONFIG keys to ``~/.config/kanibako.yaml``.  These tests exercise
the integrated ``system_cmd.run_config`` path end-to-end.
"""

from __future__ import annotations

import argparse

from kanibako.config import load_config
from kanibako.config_io import load_doc
from kanibako.paths import load_std_paths


def _ns(key_value=None, *, effective=False, reset=None, all_keys=False, force=True):
    return argparse.Namespace(
        key_value=key_value,
        effective=effective,
        reset=reset,
        all_keys=all_keys,
        force=force,
    )


class TestSystemConfigSettingsSplit:
    def test_default_agent_lands_in_settings_file(self, config_file, tmp_home):
        from kanibako.commands.system_cmd import run_config

        rc = run_config(_ns("system.default_agent=goose"))
        assert rc == 0

        std = load_std_paths(load_config(config_file))
        # The SETTING is in global/settings.yaml, NOT kanibako.yaml.
        assert std.settings.exists()
        assert load_doc(std.settings)["agent"]["default"]["default_agent"] == "goose"
        assert "agent" not in load_doc(config_file)

    def test_default_agent_resolves_via_settings_reader(self, config_file, tmp_home):
        from kanibako.commands.system_cmd import run_config
        from kanibako.config import read_default_agent

        run_config(_ns("system.default_agent=claude"))
        std = load_std_paths(load_config(config_file))
        # The launch-time reader (fed std.settings) sees the value.
        assert read_default_agent(std.settings) == "claude"
        # A read against the CONFIG file does NOT see it (separation holds).
        assert read_default_agent(config_file) is None

    def test_get_reads_setting_from_settings_file(self, config_file, tmp_home, capsys):
        from kanibako.commands.system_cmd import run_config

        run_config(_ns("system.default_agent=codex"))
        capsys.readouterr()
        rc = run_config(_ns("system.default_agent"))  # get
        assert rc == 0
        assert "system.default_agent=codex" in capsys.readouterr().out

    def test_system_config_key_reads_kanibako_yaml(self, config_file, tmp_home, capsys):
        """system.data (CONFIG) is read from kanibako.yaml, not settings.yaml."""
        from kanibako.commands.system_cmd import run_config
        from kanibako.config_interface import _write_nested_toml_key

        custom = str(tmp_home / "custom-data")
        _write_nested_toml_key(config_file, ("system",), "data", custom)
        capsys.readouterr()
        rc = run_config(_ns("system.data"))  # get
        assert rc == 0
        assert custom in capsys.readouterr().out

    def test_setting_does_not_write_to_config_file(self, config_file, tmp_home):
        """Setting a behavior setting never mutates kanibako.yaml."""
        from kanibako.commands.system_cmd import run_config

        before = config_file.read_text()
        run_config(_ns("system.default_agent=goose"))
        run_config(_ns("model=gpt-5"))
        assert config_file.read_text() == before

    def test_reset_clears_setting_from_settings_file(self, config_file, tmp_home):
        from kanibako.commands.system_cmd import run_config
        from kanibako.config import read_default_agent

        run_config(_ns("system.default_agent=claude"))
        rc = run_config(_ns("system.default_agent", reset=True))
        assert rc == 0
        std = load_std_paths(load_config(config_file))
        assert read_default_agent(std.settings) is None

    def test_show_renders_setting_from_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.system_cmd import run_config

        run_config(_ns("system.default_agent=goose"))
        capsys.readouterr()
        rc = run_config(_ns(effective=False))  # show
        assert rc == 0
        assert "goose" in capsys.readouterr().out
