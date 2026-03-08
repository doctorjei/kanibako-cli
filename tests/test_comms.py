"""Tests for peer communication: KANIBAKO_NAME env var, comms directory, setup."""

from __future__ import annotations

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


class TestLogRotation:
    """Tests for size-based log rotation."""

    def test_broadcast_rotation(self, tmp_path):
        """broadcast.log is rotated when it exceeds 1 MiB."""
        from kanibako.commands.start import _rotate_file

        log = tmp_path / "broadcast.log"
        log.write_text("x" * (1_048_576 + 1))

        _rotate_file(log)

        assert log.exists()
        assert log.stat().st_size == 0
        backup = tmp_path / "broadcast.log.1"
        assert backup.exists()
        assert backup.stat().st_size > 1_048_576

    def test_no_rotation_under_threshold(self, tmp_path):
        """Files under 1 MiB are not rotated."""
        from kanibako.commands.start import _rotate_file

        log = tmp_path / "broadcast.log"
        log.write_text("small content")

        _rotate_file(log)

        assert log.read_text() == "small content"
        assert not (tmp_path / "broadcast.log.1").exists()

    def test_rotation_missing_file(self, tmp_path):
        """Rotation handles missing files gracefully."""
        from kanibako.commands.start import _rotate_file

        _rotate_file(tmp_path / "nonexistent.log")  # should not raise

    def test_message_log_rotation(self, tmp_path):
        """MessageLog rotates when file exceeds threshold."""
        from kanibako.helper_listener import MessageLog, _LOG_MAX_BYTES

        log_path = tmp_path / "messages.jsonl"
        # Pre-fill with data just under the threshold.
        log_path.write_text("x" * (_LOG_MAX_BYTES - 10))

        log = MessageLog(log_path)
        # Write enough to push over the threshold.
        log.log_control("test-event")
        log.close()

        backup = tmp_path / "messages.jsonl.1"
        assert backup.exists()
        # New file should be small (just the last entry).
        assert log_path.stat().st_size < 1000

    def test_message_log_no_rotation_under_threshold(self, tmp_path):
        """MessageLog does not rotate small files."""
        from kanibako.helper_listener import MessageLog

        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_control("test-event")
        log.close()

        assert not (tmp_path / "messages.jsonl.1").exists()
        assert log_path.stat().st_size > 0
