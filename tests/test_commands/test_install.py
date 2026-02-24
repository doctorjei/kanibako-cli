"""Tests for kanibako.commands.install (setup subcommand)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

from kanibako.agents import load_agent_config
from kanibako.config import KanibakoConfig, load_config, write_global_config


class TestInstall:
    def test_writes_config(self, tmp_home):
        from kanibako.commands.install import run

        config_file = tmp_home / "config" / "kanibako.toml"
        assert not config_file.exists()

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no runtime")):
            args = argparse.Namespace()
            rc = run(args)

        assert rc == 0
        assert config_file.exists()
        cfg = load_config(config_file)
        assert cfg.container_image == "ghcr.io/doctorjei/kanibako-base:latest"


class TestInstallExtended:
    def _base_setup(self, tmp_home):
        """Set up home with host credentials."""
        home = tmp_home / "home"
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"token": "t"}})
        )
        (home / ".claude.json").write_text(
            json.dumps({"oauthAccount": "a", "installMethod": "cli"})
        )
        return home

    def test_existing_toml_not_overwritten(self, tmp_home):
        from kanibako.commands.install import run

        self._base_setup(tmp_home)
        config_file = tmp_home / "config" / "kanibako.toml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        custom_cfg = KanibakoConfig(container_image="custom:v1")
        write_global_config(config_file, custom_cfg)

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            rc = run(argparse.Namespace())
        assert rc == 0
        # Custom image should be preserved
        loaded = load_config(config_file)
        assert loaded.container_image == "custom:v1"

    def test_fresh_install_writes_defaults(self, tmp_home):
        from kanibako.commands.install import run

        self._base_setup(tmp_home)
        config_file = tmp_home / "config" / "kanibako.toml"
        assert not config_file.exists()

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            rc = run(argparse.Namespace())
        assert rc == 0
        assert config_file.exists()
        loaded = load_config(config_file)
        assert loaded.container_image == KanibakoConfig().container_image


class TestInstallAgentTomls:
    def _data_path(self, tmp_home):
        return tmp_home / "data" / "kanibako"

    def test_creates_agents_directory(self, tmp_home):
        from kanibako.commands.install import run

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            run(argparse.Namespace())

        agents_dir = self._data_path(tmp_home) / "agents"
        assert agents_dir.is_dir()

    def test_creates_general_toml(self, tmp_home):
        from kanibako.commands.install import run

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            run(argparse.Namespace())

        general_toml = self._data_path(tmp_home) / "agents" / "general.toml"
        assert general_toml.is_file()
        cfg = load_agent_config(general_toml)
        assert cfg.name == "Shell"

    def test_creates_target_toml(self, tmp_home):
        from kanibako.commands.install import run

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            run(argparse.Namespace())

        # The claude target is registered via entry points, so claude.toml should exist
        claude_toml = self._data_path(tmp_home) / "agents" / "claude.toml"
        assert claude_toml.is_file()
        cfg = load_agent_config(claude_toml)
        assert cfg.name == "Claude Code"
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.shared_caches == {"plugins": ".claude/plugins"}

    def test_does_not_overwrite_existing_agent_toml(self, tmp_home):
        from kanibako.commands.install import run

        data_path = self._data_path(tmp_home)
        agents_dir = data_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        # Write a custom general.toml before setup
        general_toml = agents_dir / "general.toml"
        general_toml.write_text('[agent]\nname = "Custom Shell"\n')

        with patch("kanibako.commands.install.ContainerRuntime", side_effect=Exception("no")):
            run(argparse.Namespace())

        # Custom content should be preserved
        cfg = load_agent_config(general_toml)
        assert cfg.name == "Custom Shell"
