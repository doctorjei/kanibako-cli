"""Tests for environment variable management via box config env.* interface.

These tests verify that env vars can be set, get, and unset through the
unified ``box config`` command (via config_interface.py env.* key routing).
"""

from __future__ import annotations

from kanibako.config_interface import (
    get_config_value,
    reset_config_value,
    set_config_value,
)
from kanibako.shellenv import read_env_file


class TestEnvViaConfigInterface:
    """Functional tests for env.* keys through the config interface."""

    def test_set_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.EDITOR", "vim",
            config_path=tmp_path / "project.toml",
            env_path=env_path,
        )
        assert "Set EDITOR=vim" in msg
        assert read_env_file(env_path)["EDITOR"] == "vim"

    def test_set_env_var_invalid_key(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.123BAD", "val",
            config_path=tmp_path / "project.toml",
            env_path=env_path,
        )
        assert "Error" in msg or "Invalid" in msg

    def test_get_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("EDITOR=vim\n")
        val = get_config_value(
            "env.EDITOR",
            global_config_path=tmp_path / "kanibako.toml",
            env_project=env_path,
        )
        assert val == "vim"

    def test_get_env_var_missing(self, tmp_path):
        val = get_config_value(
            "env.MISSING",
            global_config_path=tmp_path / "kanibako.toml",
        )
        assert val is None

    def test_get_env_var_from_global(self, tmp_path):
        global_env = tmp_path / "global_env"
        global_env.write_text("GLOBAL_KEY=hello\n")
        val = get_config_value(
            "env.GLOBAL_KEY",
            global_config_path=tmp_path / "kanibako.toml",
            env_global=global_env,
        )
        assert val == "hello"

    def test_get_env_var_project_overrides_global(self, tmp_path):
        global_env = tmp_path / "global_env"
        global_env.write_text("EDITOR=nano\n")
        project_env = tmp_path / "project_env"
        project_env.write_text("EDITOR=vim\n")
        val = get_config_value(
            "env.EDITOR",
            global_config_path=tmp_path / "kanibako.toml",
            env_global=global_env,
            env_project=project_env,
        )
        assert val == "vim"

    def test_reset_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("EDITOR=vim\n")
        msg = reset_config_value(
            "env.EDITOR",
            config_path=tmp_path / "project.toml",
            env_path=env_path,
        )
        assert "Unset" in msg
        assert "EDITOR" not in read_env_file(env_path)

    def test_reset_env_var_missing(self, tmp_path):
        msg = reset_config_value(
            "env.MISSING",
            config_path=tmp_path / "project.toml",
            env_path=tmp_path / "env",
        )
        assert "No override" in msg
