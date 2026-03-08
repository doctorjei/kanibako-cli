"""Extended tests for kanibako.container: ensure_image chain, run args, list_local_images."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError
from kanibako.targets.base import Mount


# ---------------------------------------------------------------------------
# ensure_image chain
# ---------------------------------------------------------------------------

class TestEnsureImage:
    def test_exists_locally(self):
        rt = ContainerRuntime(command="echo")
        with patch.object(rt, "image_exists", return_value=True) as m:
            rt.ensure_image("test:latest", Path("/containers"))
            m.assert_called_once_with("test:latest")

    def test_pull_succeeds(self):
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=True) as m_pull,
        ):
            rt.ensure_image("test:latest", Path("/containers"))
            m_pull.assert_called_once_with("test:latest")

    def test_pull_fails_build_fallback(self, tmp_path):
        rt = ContainerRuntime(command="echo")
        containers_dir = tmp_path / "containers"
        containers_dir.mkdir()
        (containers_dir / "Containerfile.oci").write_text("FROM ubuntu\n")

        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
            patch.object(rt, "build") as m_build,
        ):
            rt.ensure_image("kanibako-oci:latest", containers_dir)
            m_build.assert_called_once()

    def test_no_containerfile_raises(self, tmp_path):
        rt = ContainerRuntime(command="echo")
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
        ):
            with pytest.raises(ContainerError, match="cannot determine Containerfile"):
                rt.ensure_image("unknown-image:latest", tmp_path)

    def test_unknown_image_no_file_raises(self, tmp_path):
        rt = ContainerRuntime(command="echo")
        containers_dir = tmp_path / "containers"
        containers_dir.mkdir()
        # No matching Containerfile for kanibako-oci (mock bundled to return None too)
        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
            patch("kanibako.container.get_containerfile", return_value=None),
        ):
            with pytest.raises(ContainerError, match="no local Containerfile"):
                rt.ensure_image("kanibako-oci:latest", containers_dir)

    def test_build_fails_raises(self, tmp_path):
        rt = ContainerRuntime(command="echo")
        containers_dir = tmp_path / "containers"
        containers_dir.mkdir()
        (containers_dir / "Containerfile.oci").write_text("FROM ubuntu\n")

        with (
            patch.object(rt, "image_exists", return_value=False),
            patch.object(rt, "pull", return_value=False),
            patch.object(rt, "build", side_effect=ContainerError("build failed")),
        ):
            with pytest.raises(ContainerError, match="build failed"):
                rt.ensure_image("kanibako-oci:latest", containers_dir)


# ---------------------------------------------------------------------------
# run() command assembly
# ---------------------------------------------------------------------------

class TestRunCommandAssembly:
    def _make_rt(self):
        return ContainerRuntime(command="/usr/bin/podman")

    def _base_kwargs(self, tmp_path, *, vault_dirs=True):
        """Return minimal kwargs for run() using tmp_path for vault dirs."""
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        if vault_dirs:
            vault_ro.mkdir(exist_ok=True)
            vault_rw.mkdir(exist_ok=True)
        return dict(
            shell_path=tmp_path / "home",
            project_path=tmp_path / "proj",
            vault_ro_path=vault_ro,
            vault_rw_path=vault_rw,
        )

    def test_volume_mounts(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            # Core mounts
            assert f"{kwargs['shell_path']}:/home/agent:Z,U" in cmd
            assert f"{kwargs['project_path']}:/home/agent/workspace:Z,U" in cmd
            # Vault mounts (dirs exist)
            assert f"{kwargs['vault_ro_path']}:/home/agent/share-ro:ro" in cmd
            assert f"{kwargs['vault_rw_path']}:/home/agent/share-rw:Z,U" in cmd

    def test_vault_mounts_skipped_when_missing(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path, vault_dirs=False)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert "share-ro" not in cmd_str
            assert "share-rw" not in cmd_str

    def test_entrypoint_override(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", entrypoint="/bin/bash", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--entrypoint")
            assert cmd[idx + 1] == "/bin/bash"

    def test_no_entrypoint(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            assert "--entrypoint" not in cmd

    def test_cli_args_appended(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", cli_args=["--continue", "--verbose"], **kwargs)
            cmd = m_run.call_args[0][0]
            assert cmd[-2:] == ["--continue", "--verbose"]

    def test_extra_mounts(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        mounts = [
            Mount(
                source=Path("/home/user/.local/share/claude"),
                destination="/home/agent/.local/share/claude",
                options="ro",
            ),
            Mount(
                source=Path("/home/user/.local/bin/claude"),
                destination="/home/agent/.local/bin/claude",
                options="ro",
            ),
        ]
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", extra_mounts=mounts, **kwargs)
            cmd = m_run.call_args[0][0]
            assert "/home/user/.local/share/claude:/home/agent/.local/share/claude:ro" in cmd
            assert "/home/user/.local/bin/claude:/home/agent/.local/bin/claude:ro" in cmd

    def test_no_extra_mounts(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", extra_mounts=None, **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert ".local/share/claude" not in cmd_str

    def test_cli_args_none(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", cli_args=None, **kwargs)
            cmd = m_run.call_args[0][0]
            # Last element should be the image name
            assert cmd[-1] == "img:latest"

    def test_container_name(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", name="kanibako-test", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--name")
            assert cmd[idx + 1] == "kanibako-test"

    def test_vault_tmpfs(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", vault_tmpfs=True, **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("--mount")
            assert cmd[idx + 1] == "type=tmpfs,dst=/home/agent/workspace/vault,ro"

    def test_vault_tmpfs_false(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", vault_tmpfs=False, **kwargs)
            cmd = m_run.call_args[0][0]
            assert "--mount" not in cmd

    def test_no_settings_dot_cfg_mounts(self, tmp_path):
        """Verify old-style settings_path/dot_path/cfg_file mounts are gone."""
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # Old mounts no longer present
            assert ".kanibako:" not in cmd_str
            assert ".claude:" not in cmd_str
            assert ".claude.json:" not in cmd_str

    def test_returns_exit_code(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=42)
            rc = rt.run("img:latest", **kwargs)
            assert rc == 42

    def test_working_directory(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            idx = cmd.index("-w")
            assert cmd[idx + 1] == "/home/agent/workspace"

    def test_base_flags(self, tmp_path):
        rt = self._make_rt()
        kwargs = self._base_kwargs(tmp_path)
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run("img:latest", **kwargs)
            cmd = m_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/podman"
            assert "run" in cmd
            # -it when TTY available, -i when not (e.g. CI)
            assert "-it" in cmd or "-i" in cmd
            assert "--rm" in cmd
            assert "--userns=keep-id" in cmd


# ---------------------------------------------------------------------------
# list_local_images
# ---------------------------------------------------------------------------

class TestListLocalImages:
    def test_filters_kanibako(self):
        rt = ContainerRuntime(command="echo")
        output = (
            "ghcr.io/owner/kanibako-oci:latest\t500MB\n"
            "docker.io/library/ubuntu:latest\t100MB\n"
            "ghcr.io/owner/kanibako-lxc:latest\t800MB\n"
        )
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout=output)
            images = rt.list_local_images()
            assert len(images) == 2
            assert images[0][0] == "ghcr.io/owner/kanibako-oci:latest"
            assert images[1][0] == "ghcr.io/owner/kanibako-lxc:latest"

    def test_empty_output(self):
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout="")
            images = rt.list_local_images()
            assert images == []

    def test_tab_parsing(self):
        rt = ContainerRuntime(command="echo")
        output = "ghcr.io/x/kanibako:latest\t1.2GB\n"
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0, stdout=output)
            images = rt.list_local_images()
            assert len(images) == 1
            assert images[0] == ("ghcr.io/x/kanibako:latest", "1.2GB")


class TestVaultDisabledRun:
    """Tests that vault_enabled=False suppresses vault mounts and tmpfs."""

    def _make_rt(self):
        return ContainerRuntime(command="/usr/bin/podman")

    def test_vault_disabled_skips_mounts_and_tmpfs(self, tmp_path):
        rt = self._make_rt()
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        vault_ro.mkdir()
        vault_rw.mkdir()
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                shell_path=tmp_path / "home",
                project_path=tmp_path / "proj",
                vault_ro_path=vault_ro,
                vault_rw_path=vault_rw,
                vault_tmpfs=True,
                vault_enabled=False,
            )
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # No vault mounts even though dirs exist
            assert "share-ro" not in cmd_str
            assert "share-rw" not in cmd_str
            # No tmpfs overlay
            assert "tmpfs" not in cmd_str

    def test_vault_enabled_includes_mounts(self, tmp_path):
        rt = self._make_rt()
        vault_ro = tmp_path / "vault-ro"
        vault_rw = tmp_path / "vault-rw"
        vault_ro.mkdir()
        vault_rw.mkdir()
        with patch("kanibako.container.subprocess.run") as m_run:
            m_run.return_value = MagicMock(returncode=0)
            rt.run(
                "img:latest",
                shell_path=tmp_path / "home",
                project_path=tmp_path / "proj",
                vault_ro_path=vault_ro,
                vault_rw_path=vault_rw,
                vault_tmpfs=True,
                vault_enabled=True,
            )
            cmd = m_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert "share-ro" in cmd_str
            assert "share-rw" in cmd_str
            assert "tmpfs" in cmd_str
