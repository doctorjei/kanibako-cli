"""Tests for `kanibako system` config verbs — the file-only system.* rule (W1).

ALL ``system.*``-prefixed keys (the structural layout keys AND the host-global
``system.default_agent``) are FILE-ONLY: the ``system`` config verbs READ/SHOW
them but REFUSE to set/reset them, pointing the user at the config file (or
``kanibako setup``).  Non-``system.``-prefixed keys still set at the global tier.
These tests exercise the integrated ``system_cmd`` set/reset/get/show paths
end-to-end through the discrete verbs (the B2 config.*-forbid guard still fires).
"""

from __future__ import annotations

import argparse

from kanibako.commands.system_cmd import run_get, run_reset, run_set, run_show
from kanibako.config import load_config
from kanibako.config_io import load_doc
from kanibako.config_interface import _write_nested_toml_key
from kanibako.paths import load_std_paths


def _set(key_value, *, force=True):
    return run_set(argparse.Namespace(key_value=key_value, force=force))


def _reset(key=None, *, all_keys=False, force=True):
    return run_reset(argparse.Namespace(key=key, all_keys=all_keys, force=force))


def _get(key):
    return run_get(argparse.Namespace(key=key))


def _show(*, effective=False):
    return run_show(argparse.Namespace(effective=effective))


def _seed_default_agent(config_file, name):
    """Programmatically seed system.default_agent into the settings file.

    Mirrors what ``kanibako setup`` does (the CLI refuses to set it).
    """
    std = load_std_paths(load_config(config_file))
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    _write_nested_toml_key(std.settings, ("agent", "default"), "default_agent", name)
    return std


class TestSystemConfigFileOnly:
    def test_set_default_agent_refused(self, config_file, tmp_home, capsys):
        rc = _set("system.default_agent=goose")
        assert rc == 1
        err = capsys.readouterr().err
        assert "structural config key" in err
        # Nothing landed in either file.
        std = load_std_paths(load_config(config_file))
        assert not std.settings.exists()
        assert "agent" not in load_doc(config_file)

    def test_set_system_path_key_refused(self, config_file, tmp_home, capsys):
        rc = _set("system.data=/custom/data")
        assert rc == 1
        assert "structural config key" in capsys.readouterr().err

    def test_reset_default_agent_refused(self, config_file, tmp_home, capsys):
        from kanibako.config import read_default_agent

        std = _seed_default_agent(config_file, "claude")
        rc = _reset("system.default_agent")
        assert rc == 1
        assert "structural config key" in capsys.readouterr().err
        # The seeded value survives the refused reset.
        assert read_default_agent(std.settings) == "claude"

    def test_get_default_agent_reads_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        _seed_default_agent(config_file, "codex")
        capsys.readouterr()
        rc = _get("system.default_agent")
        assert rc == 0
        assert "system.default_agent=codex" in capsys.readouterr().out

    def test_get_config_path_key_reads_kanibako_config_yaml(
        self, config_file, tmp_home, capsys,
    ):
        """config.data (Layer-1 CONFIG) is read from kanibako_config.yaml — get
        still works (the key moved system.data -> config.data in block #3a)."""
        custom = str(tmp_home / "custom-data")
        _write_nested_toml_key(config_file, ("config",), "data", custom)
        capsys.readouterr()
        rc = _get("config.data")
        assert rc == 0
        assert custom in capsys.readouterr().out

    def test_non_system_key_still_settable(self, config_file, tmp_home):
        """Narrow scope (a): a regular (non-system.*) key still sets fine."""
        rc = _set("model=gpt-5")
        assert rc == 0
        std = load_std_paths(load_config(config_file))
        assert load_doc(std.settings)["agent"]["default"]["model"] == "gpt-5"

    def test_show_renders_default_agent_from_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        _seed_default_agent(config_file, "goose")
        capsys.readouterr()
        rc = _show()
        assert rc == 0
        assert "goose" in capsys.readouterr().out
