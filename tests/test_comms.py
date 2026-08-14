"""Tests for peer communication: KANIBAKO_NAME env var, comms directory, setup."""

from __future__ import annotations

from kanibako.settings.config import load_config


class TestCommsConfig:
    def test_default_comms_path(self, tmp_path):
        from kanibako.settings.config import KanibakoConfig
        from kanibako.settings.paths import resolve_system_paths

        cfg = KanibakoConfig()
        resolved = resolve_system_paths(
            cfg.config_paths, data_home=tmp_path, home=tmp_path,
        )
        # channelroot is a Layer-2 system.* SETTING (@config.data/channels).
        assert resolved["system.channelroot"] == tmp_path / "kanibako" / "channels"

    def test_comms_from_toml(self, tmp_path):
        from pathlib import Path

        from kanibako.settings.paths import resolve_system_paths

        toml = tmp_path / "kanibako_config.yaml"
        toml.write_text('system:\n  channelroot: "/custom-channels"\n')
        cfg = load_config(toml)
        resolved = resolve_system_paths(
            cfg.config_paths, data_home=tmp_path, home=tmp_path,
        )
        assert resolved["system.channelroot"] == Path("/custom-channels")


class TestCommsOnStart:
    """KANIBAKO_NAME wiring during project start (channels covered elsewhere)."""

    def test_kanibako_name_env_var(
        self, config_file, tmp_home, credentials_dir,
    ):
        """KANIBAKO_NAME is derived from proj.name — asked of the CODE that derives it.

        🛑 REBUILT (MBR-1 P4b). This case used to re-implement the injection in its
        own body — ``container_env["KANIBAKO_NAME"] = proj.name`` under a comment
        saying "simulate env var injection from start.py" — and then assert its own
        two lines. It pinned nothing: it would have stayed green through the whole of
        P4b, while the statement it appears to make ("the box gets its name") became
        one nothing in the product had to honour.

        It calls ``_core_env_default_categories`` now, the one function that spells
        the variable, and reads the KEY the launch floor carries it under. The full
        arrival chain — floor → collapse → the leaf a box is launched from — is
        ``tests/test_targets/test_agent_envs.py::TestTheCoreStampsRideTheSameWire``;
        what belongs HERE is the channel system's own claim, that the name a peer
        addresses this box by is the project's name.
        """
        from kanibako.commands.start import _core_env_default_categories
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        table = _core_env_default_categories(
            proj=proj, target=None, agent_id="claude",
        )
        assert table["system.env.KANIBAKO_NAME"] == "project"
        assert proj.name == "project"


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
        from kanibako.channels.helper_listener import MessageLog, _LOG_MAX_BYTES

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
        from kanibako.channels.helper_listener import MessageLog

        log_path = tmp_path / "messages.jsonl"
        log = MessageLog(log_path)
        log.log_control("test-event")
        log.close()

        assert not (tmp_path / "messages.jsonl.1").exists()
        assert log_path.stat().st_size > 0
