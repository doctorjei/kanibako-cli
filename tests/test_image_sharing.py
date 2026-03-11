"""Tests for kanibako.image_sharing module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from kanibako.image_sharing import (
    SHARED_STORE_CONTAINER_PATH,
    _STORAGE_CONF_CONTAINER_PATH,
    build_image_sharing_mounts,
    detect_graph_root,
    generate_storage_conf,
)


# ---------------------------------------------------------------------------
# detect_graph_root
# ---------------------------------------------------------------------------

class TestDetectGraphRoot:
    """Tests for detect_graph_root()."""

    def test_returns_path_on_success(self, tmp_path):
        """Successfully detects graph root from podman info output."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()

        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(graph_dir) + "\n",
            )
            result = detect_graph_root("podman")
            assert result == graph_dir
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["podman", "info", "--format", "{{.Store.GraphRoot}}"]

    def test_returns_none_on_command_failure(self):
        """Returns None when the command fails."""
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="not found",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_empty_output(self):
        """Returns None when the output is empty."""
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="   \n",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_when_directory_missing(self, tmp_path):
        """Returns None when the graph root path does not exist."""
        missing = tmp_path / "nonexistent"

        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(missing) + "\n",
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_timeout(self):
        """Returns None on subprocess timeout."""
        import subprocess

        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="podman info", timeout=10,
            )
            result = detect_graph_root("podman")
            assert result is None

    def test_returns_none_on_file_not_found(self):
        """Returns None when the binary is not found."""
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("no such file")
            result = detect_graph_root("podman")
            assert result is None

    def test_uses_provided_runtime_cmd(self, tmp_path):
        """Uses the given runtime command (docker, podman path, etc.)."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()

        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(graph_dir) + "\n",
            )
            detect_graph_root("/usr/bin/docker")
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/docker"


# ---------------------------------------------------------------------------
# generate_storage_conf
# ---------------------------------------------------------------------------

class TestGenerateStorageConf:
    """Tests for generate_storage_conf()."""

    def test_basic_output(self):
        """Generated config includes the additionalImageStores path."""
        conf = generate_storage_conf("/var/lib/shared-images")
        assert "[storage]" in conf
        assert 'driver = "overlay"' in conf
        assert "[storage.options]" in conf
        assert '"/var/lib/shared-images"' in conf
        assert "additionalimagestores" in conf

    def test_custom_path(self):
        """Custom path is correctly embedded."""
        conf = generate_storage_conf("/mnt/custom/store")
        assert '"/mnt/custom/store"' in conf

    def test_valid_toml_structure(self):
        """Generated output has valid TOML-like structure."""
        conf = generate_storage_conf("/test")
        lines = conf.strip().split("\n")
        assert lines[0] == "[storage]"
        assert any("[storage.options]" in line for line in lines)


# ---------------------------------------------------------------------------
# build_image_sharing_mounts
# ---------------------------------------------------------------------------

class TestBuildImageSharingMounts:
    """Tests for build_image_sharing_mounts()."""

    def test_returns_mounts_on_success(self, tmp_path):
        """Returns two mounts when graph root is detected."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = graph_dir
            mounts = build_image_sharing_mounts("podman", staging)

        assert len(mounts) == 2

        # First mount: graph root -> shared store path (read-only)
        assert mounts[0].source == graph_dir
        assert mounts[0].destination == SHARED_STORE_CONTAINER_PATH
        assert mounts[0].options == "ro"

        # Second mount: storage.conf -> container config path (read-only)
        assert mounts[1].source == staging / "storage.conf"
        assert mounts[1].destination == _STORAGE_CONF_CONTAINER_PATH
        assert mounts[1].options == "ro"

    def test_returns_empty_when_detection_fails(self, tmp_path):
        """Returns empty list when graph root cannot be detected."""
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = None
            mounts = build_image_sharing_mounts("podman", staging)

        assert mounts == []

    def test_staging_dir_created(self, tmp_path):
        """Staging directory is created if it doesn't exist."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()
        staging = tmp_path / "staging" / "nested"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = graph_dir
            build_image_sharing_mounts("podman", staging)

        assert staging.is_dir()

    def test_storage_conf_written(self, tmp_path):
        """storage.conf is written to the staging directory."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = graph_dir
            build_image_sharing_mounts("podman", staging)

        conf_file = staging / "storage.conf"
        assert conf_file.exists()
        content = conf_file.read_text()
        assert SHARED_STORE_CONTAINER_PATH in content

    def test_mount_volume_args(self, tmp_path):
        """Mounts produce correct -v argument strings."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = graph_dir
            mounts = build_image_sharing_mounts("podman", staging)

        for mount in mounts:
            vol_arg = mount.to_volume_arg()
            assert ":ro" in vol_arg

    def test_runtime_cmd_passed_to_detect(self, tmp_path):
        """The runtime command is forwarded to detect_graph_root."""
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = None
            build_image_sharing_mounts("/usr/bin/docker", staging)

        mock_detect.assert_called_once_with("/usr/bin/docker")


# ---------------------------------------------------------------------------
# Integration with start.py (_run_container)
# ---------------------------------------------------------------------------

class TestImageSharingInRunContainer:
    """Tests for image sharing integration in _run_container."""

    def test_share_images_flag_triggers_mounts(self, start_mocks):
        """--share-images adds image sharing mounts to the container."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            with patch(
                "kanibako.image_sharing.build_image_sharing_mounts",
            ) as mock_build:
                from kanibako.targets.base import Mount
                fake_mount = Mount(
                    source=Path("/fake/graph"),
                    destination=SHARED_STORE_CONTAINER_PATH,
                    options="ro",
                )
                mock_build.return_value = [fake_mount]

                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                assert rc == 0
                mock_build.assert_called_once()
                # Verify the mount was included in the runtime.run call
                call_kwargs = m.runtime.run.call_args.kwargs
                extra = call_kwargs.get("extra_mounts") or []
                destinations = [mt.destination for mt in extra]
                assert SHARED_STORE_CONTAINER_PATH in destinations

    def test_share_images_disabled_by_default(self, start_mocks):
        """Without --share-images, no image sharing mounts are added."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.image_sharing.build_image_sharing_mounts",
            ) as mock_build:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
                mock_build.assert_not_called()

    def test_share_images_detection_failure_warns(self, start_mocks, capsys):
        """When detection fails, a warning is printed but launch continues."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.image_sharing.build_image_sharing_mounts",
            ) as mock_build:
                mock_build.return_value = []  # detection failed
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                assert rc == 0  # continues without image sharing

        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "image storage" in captured.err

    def test_share_images_via_config(self, start_mocks):
        """share_images=true in config triggers image sharing mounts."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.merged.share_images = True
            with patch(
                "kanibako.image_sharing.build_image_sharing_mounts",
            ) as mock_build:
                mock_build.return_value = []
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=False,
                )
                mock_build.assert_called_once()

    def test_share_images_in_shell_mode(self, start_mocks):
        """--share-images works in shell mode too."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.image_sharing.build_image_sharing_mounts",
            ) as mock_build:
                mock_build.return_value = []
                _run_container(
                    project_dir=None,
                    entrypoint="/bin/bash",
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    share_images=True,
                )
                mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

class TestImageSharingConfig:
    """Tests for share_images config option."""

    def test_default_is_false(self):
        """share_images defaults to False in KanibakoConfig."""
        from kanibako.config import KanibakoConfig
        cfg = KanibakoConfig()
        assert cfg.share_images is False

    def test_loaded_from_toml(self, tmp_path):
        """share_images can be set in kanibako.toml (top-level key)."""
        from kanibako.config import load_config
        toml_path = tmp_path / "kanibako.toml"
        toml_path.write_text("share_images = true\n")
        cfg = load_config(toml_path)
        assert cfg.share_images is True

    def test_false_in_toml(self, tmp_path):
        """share_images = false is loaded correctly."""
        from kanibako.config import load_config
        toml_path = tmp_path / "kanibako.toml"
        toml_path.write_text("share_images = false\n")
        cfg = load_config(toml_path)
        assert cfg.share_images is False

    def test_merged_config_project_override(self, tmp_path):
        """Project-level share_images overrides global config."""
        from kanibako.config import load_merged_config
        global_toml = tmp_path / "global.toml"
        global_toml.write_text("share_images = false\n")
        project_toml = tmp_path / "project.toml"
        project_toml.write_text("share_images = true\n")
        cfg = load_merged_config(global_toml, project_toml)
        assert cfg.share_images is True
