"""Tests for kanibako.image_sharing module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from kanibako.image_sharing import (
    SHARED_STORE_CONTAINER_PATH,
    VIRTIOFS_GRAPHROOT_MESSAGE,
    _STORAGE_CONF_CONTAINER_PATH,
    build_image_sharing_mounts,
    detect_graph_root,
    generate_storage_conf,
    is_rootless_podman,
    prepare_image_sharing_sources,
    virtiofs_graphroot_message,
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
# prepare_image_sharing_sources (Phase B: the source-resolver the launch seam
# calls so the binds flow through the category resolver, not hardwired Mounts)
# ---------------------------------------------------------------------------

class TestPrepareImageSharingSources:
    """Tests for prepare_image_sharing_sources()."""

    def test_returns_graph_root_and_conf_path(self, tmp_path):
        """Returns (graph_root, storage_conf_path) when graph root is detected."""
        graph_dir = tmp_path / "overlay"
        graph_dir.mkdir()
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = graph_dir
            result = prepare_image_sharing_sources("podman", staging)

        assert result is not None
        graph_root, conf_path = result
        assert graph_root == graph_dir
        assert conf_path == staging / "storage.conf"
        # The generated conf is WRITTEN (it is the bound source, spec D-M8).
        assert conf_path.exists()
        assert SHARED_STORE_CONTAINER_PATH in conf_path.read_text()

    def test_returns_none_when_detection_fails(self, tmp_path):
        """Returns None when the host graph root cannot be detected."""
        staging = tmp_path / "staging"

        with patch("kanibako.image_sharing.detect_graph_root") as mock_detect:
            mock_detect.return_value = None
            assert prepare_image_sharing_sources("podman", staging) is None


# ---------------------------------------------------------------------------
# Integration with start.py (_run_container)
# ---------------------------------------------------------------------------

class TestImageSharingInRunContainer:
    """Tests for image sharing integration in _run_container."""

    def test_share_images_flag_triggers_mounts(self, start_mocks, tmp_path):
        """--share-images adds image sharing mounts to the container.

        Phase B: the mounts now flow through the category resolver (the seam
        injects the probed graph root + generated storage.conf into keyed
        ``box.bindings.ro.images_*`` binds).  ``prepare_image_sharing_sources`` is
        the source-resolver the seam calls; patch it to return real paths so the
        reconciled ro mount keeps its existing source (not dropped by L7).
        """
        from kanibako.commands.start import _run_container

        graph_root = tmp_path / "graph"
        graph_root.mkdir()
        conf = tmp_path / "storage.conf"
        conf.write_text("[storage]\n")

        with start_mocks() as m:
            with patch(
                "kanibako.image_sharing.prepare_image_sharing_sources",
            ) as mock_prep:
                mock_prep.return_value = (graph_root, conf)

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
                mock_prep.assert_called_once()
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
                "kanibako.image_sharing.prepare_image_sharing_sources",
            ) as mock_prep:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
                mock_prep.assert_not_called()

    def test_share_images_detection_failure_warns(self, start_mocks, capsys):
        """When detection fails, a warning is printed but launch continues."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.image_sharing.prepare_image_sharing_sources",
            ) as mock_prep:
                mock_prep.return_value = None  # detection failed
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
        """share_images=true in config triggers image sharing source resolution."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.merged.box_share_images = True
            with patch(
                "kanibako.image_sharing.prepare_image_sharing_sources",
            ) as mock_prep:
                mock_prep.return_value = None
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
                mock_prep.assert_called_once()

    def test_share_images_in_shell_mode(self, start_mocks):
        """--share-images works in shell mode too."""
        from kanibako.commands.start import _run_container

        with start_mocks():
            with patch(
                "kanibako.image_sharing.prepare_image_sharing_sources",
            ) as mock_prep:
                mock_prep.return_value = None
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
                mock_prep.assert_called_once()


# ---------------------------------------------------------------------------
# is_rootless_podman
# ---------------------------------------------------------------------------

class TestIsRootlessPodman:
    """Tests for is_rootless_podman()."""

    def test_true_for_rootless_podman(self):
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            assert is_rootless_podman("podman") is True
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "podman", "info", "--format", "{{.Host.Security.Rootless}}",
            ]

    def test_false_for_rootful_podman(self):
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
            assert is_rootless_podman("/usr/bin/podman") is False

    def test_false_for_docker_without_query(self):
        """docker is never the rootless-overlay case; no info call is made."""
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            assert is_rootless_podman("/usr/bin/docker") is False
            mock_run.assert_not_called()

    def test_none_on_command_failure(self):
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="boom")
            assert is_rootless_podman("podman") is None

    def test_none_on_unparseable_output(self):
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="maybe\n")
            assert is_rootless_podman("podman") is None

    def test_none_on_oserror(self):
        with patch("kanibako.image_sharing.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("nope")
            assert is_rootless_podman("podman") is None


# ---------------------------------------------------------------------------
# path_fstype
# ---------------------------------------------------------------------------

class TestPathFstype:
    """Tests for path_fstype() (procfs-based fstype lookup)."""

    _MOUNTINFO = (
        "21 0 0:20 / / rw - ext4 /dev/root rw\n"
        "22 21 0:21 / /mnt/share rw - virtiofs myfs rw\n"
    )

    def test_returns_fstype_of_longest_match(self, tmp_path):
        from kanibako.image_sharing import path_fstype
        from unittest.mock import mock_open

        with patch("kanibako.image_sharing.os.path.realpath",
                   return_value="/mnt/share/overlay"):
            with patch("builtins.open", mock_open(read_data=self._MOUNTINFO)):
                assert path_fstype(Path("/mnt/share/overlay")) == "virtiofs"

    def test_returns_root_fstype_for_unmatched_subpath(self):
        from kanibako.image_sharing import path_fstype
        from unittest.mock import mock_open

        with patch("kanibako.image_sharing.os.path.realpath",
                   return_value="/home/agent/.local/share"):
            with patch("builtins.open", mock_open(read_data=self._MOUNTINFO)):
                assert path_fstype(Path("/home/agent/.local/share")) == "ext4"

    def test_none_when_mountinfo_unreadable(self):
        from kanibako.image_sharing import path_fstype

        with patch("kanibako.image_sharing.os.path.realpath",
                   return_value="/x"):
            with patch("builtins.open", side_effect=OSError("no procfs")):
                assert path_fstype(Path("/x")) is None


# ---------------------------------------------------------------------------
# virtiofs_graphroot_message
# ---------------------------------------------------------------------------

class TestVirtiofsGraphrootMessage:
    """Tests for the rootless-podman-on-virtiofs preflight diagnostic."""

    def _patches(self, *, rootless, graph_root, fstype, docker_cmd=None):
        env = {} if docker_cmd is None else {"KANIBAKO_DOCKER_CMD": docker_cmd}
        return (
            patch.dict("os.environ", env, clear=False),
            patch(
                "kanibako.image_sharing.is_rootless_podman",
                return_value=rootless,
            ),
            patch(
                "kanibako.image_sharing.detect_graph_root",
                return_value=graph_root,
            ),
            patch(
                "kanibako.image_sharing.path_fstype",
                return_value=fstype,
            ),
        )

    def test_fires_for_rootless_virtiofs(self, tmp_path):
        """Rootless podman + virtiofs graph root -> the diagnostic message."""
        gr = tmp_path / "overlay"
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=gr, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            msg = virtiofs_graphroot_message("podman")
        assert msg is not None
        assert "virtiofs" in msg
        assert "kento" in msg
        assert "pivot_root" in msg
        assert "KANIBAKO_DOCKER_CMD" in msg
        assert "unsupported" in msg
        assert str(gr) in msg

    def test_silent_for_non_virtiofs(self, tmp_path):
        """A normal (ext4) graph root -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype="ext4",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_for_rootful(self, tmp_path):
        """Rootful podman handles virtiofs fine -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=False, graph_root=tmp_path, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_rootless_unknown(self, tmp_path):
        """Rootless state undeterminable -> no message (no false positive)."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=None, graph_root=tmp_path, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_shim_active(self, tmp_path):
        """A KANIBAKO_DOCKER_CMD rootful shim suppresses the check entirely."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype="virtiofs",
            docker_cmd="/usr/bin/sudo-podman",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_graph_root_undetectable(self):
        """No detectable graph root -> no message."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=None, fstype="virtiofs",
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_silent_when_fstype_undeterminable(self, tmp_path):
        """fstype undeterminable -> no message, no crash."""
        p_env, p_root, p_gr, p_fs = self._patches(
            rootless=True, graph_root=tmp_path, fstype=None,
        )
        with p_env, p_root, p_gr, p_fs:
            assert virtiofs_graphroot_message("podman") is None

    def test_message_constant_mentions_kento_and_virtiofs(self):
        """The message template names the load-bearing concepts."""
        assert "virtiofs" in VIRTIOFS_GRAPHROOT_MESSAGE
        assert "kento" in VIRTIOFS_GRAPHROOT_MESSAGE
        assert "{graph_root}" in VIRTIOFS_GRAPHROOT_MESSAGE


# ---------------------------------------------------------------------------
# Preflight integration with start.py (_run_container)
# ---------------------------------------------------------------------------

class TestVirtiofsPreflightInRunContainer:
    """Tests for the virtiofs preflight wired into _run_container."""

    def test_preflight_blocks_launch(self, start_mocks, capsys):
        """When the diagnostic applies, the launch is blocked (rc 1)."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.virtiofs_check.return_value = (
                "Error: podman's image storage is on a virtiofs filesystem.\n"
                "  This can happen if you use kento to compose VM images."
            )
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()

        captured = capsys.readouterr()
        assert "virtiofs" in captured.err
        assert "kento" in captured.err

    def test_no_preflight_allows_launch(self, start_mocks):
        """When the diagnostic does not apply, the launch proceeds normally."""
        from kanibako.commands.start import _run_container

        with start_mocks() as m:
            m.virtiofs_check.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

class TestImageSharingConfig:
    """Tests for share_images config option."""

    def test_default_is_false(self):
        """box_share_images defaults to False in KanibakoConfig."""
        from kanibako.config import KanibakoConfig
        cfg = KanibakoConfig()
        assert cfg.box_share_images is False

    def test_loaded_from_toml(self, tmp_path):
        """share_images can be set in kanibako.yaml ([box] section)."""
        from kanibako.config import load_config
        toml_path = tmp_path / "kanibako.yaml"
        toml_path.write_text("box:\n  share_images: true\n")
        cfg = load_config(toml_path)
        assert cfg.box_share_images is True

    def test_false_in_toml(self, tmp_path):
        """share_images = false is loaded correctly."""
        from kanibako.config import load_config
        toml_path = tmp_path / "kanibako.yaml"
        toml_path.write_text("box:\n  share_images: false\n")
        cfg = load_config(toml_path)
        assert cfg.box_share_images is False

    def test_merged_config_project_override(self, tmp_path):
        """Project-level box.share_images overrides global config."""
        from kanibako.config import load_merged_config
        global_toml = tmp_path / "global.yaml"
        global_toml.write_text("box:\n  share_images: false\n")
        project_toml = tmp_path / "settings.yaml"
        project_toml.write_text("box:\n  share_images: true\n")
        cfg = load_merged_config(global_toml, project_toml)
        assert cfg.box_share_images is True
