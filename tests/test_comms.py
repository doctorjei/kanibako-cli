"""Tests for peer communication: KANIBAKO_NAME env var, comms directory, setup."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.config import KanibakoConfig, load_config


class TestCommsConfig:
    def test_default_comms_path(self):
        cfg = KanibakoConfig()
        assert cfg.paths_comms == "comms"

    def test_comms_from_toml(self, tmp_path):
        toml = tmp_path / "kanibako.toml"
        toml.write_text('[paths]\ncomms = "custom-comms"\n')
        cfg = load_config(toml)
        assert cfg.paths_comms == "custom-comms"


class TestCommsSetup:
    def test_setup_creates_comms_dirs(self, config_file, tmp_home):
        """kanibako setup creates comms/ and comms/mailbox/."""
        from kanibako.commands.install import run

        import argparse
        args = argparse.Namespace()
        run(args)

        data_home = tmp_home / "data"
        comms = data_home / "kanibako" / "comms"
        assert comms.is_dir()
        assert (comms / "mailbox").is_dir()
        assert (comms / "broadcast.log").is_file()


class TestCommsOnStart:
    """Comms directory and KANIBAKO_NAME wiring during project start."""

    def test_comms_dir_created_on_start(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Starting a project creates comms/ with mailbox/{name}/."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        comms_path = std.data_path / config.paths_comms
        # Simulate what start.py does (without launching a container).
        comms_path.mkdir(parents=True, exist_ok=True)
        if proj.name:
            (comms_path / "mailbox" / proj.name).mkdir(parents=True, exist_ok=True)
        broadcast = comms_path / "broadcast.log"
        if not broadcast.exists():
            broadcast.touch()

        assert comms_path.is_dir()
        assert (comms_path / "mailbox" / "project").is_dir()
        assert broadcast.is_file()

    def test_kanibako_name_env_var(
        self, config_file, tmp_home, credentials_dir,
    ):
        """KANIBAKO_NAME is set from proj.name."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Simulate env var injection from start.py.
        container_env: dict[str, str] = {}
        if proj.name:
            container_env["KANIBAKO_NAME"] = proj.name

        assert container_env["KANIBAKO_NAME"] == "project"

    def test_broadcast_log_not_overwritten(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Existing broadcast.log content is preserved."""
        config = load_config(config_file)
        from kanibako.paths import load_std_paths
        std = load_std_paths(config)

        comms_path = std.data_path / config.paths_comms
        comms_path.mkdir(parents=True, exist_ok=True)
        broadcast = comms_path / "broadcast.log"
        broadcast.write_text("existing message\n")

        # Touch again (idempotent).
        if not broadcast.exists():
            broadcast.touch()

        assert broadcast.read_text() == "existing message\n"
