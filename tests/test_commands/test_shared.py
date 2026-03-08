"""Tests for shared.* config keys via the config_interface engine.

The old ``kanibako shared`` command has been replaced by ``box config shared.*``
keys.  These tests verify the config_interface's shared.* key support.
"""

from __future__ import annotations

from kanibako.config_interface import (
    get_config_value,
    set_config_value,
)


class TestSharedViaConfigInterface:
    """Tests for shared.* keys through the unified config interface."""

    def test_set_shared_cache(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        msg = set_config_value(
            "shared.pip", ".cache/pip",
            config_path=project_toml,
        )
        assert "Set shared.pip" in msg

    def test_get_shared_cache(self, tmp_path):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[shared]\npip = ".cache/pip"\n')

        val = get_config_value(
            "shared.pip",
            global_config_path=global_cfg,
        )
        assert val == ".cache/pip"

    def test_get_shared_cache_not_set(self, tmp_path):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text("")

        val = get_config_value(
            "shared.nonexistent",
            global_config_path=global_cfg,
        )
        assert val is None
