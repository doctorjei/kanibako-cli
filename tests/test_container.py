"""Tests for kanibako.container."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError


class TestContainerRuntime:
    def test_detect_raises_when_nothing_found(self, monkeypatch):
        monkeypatch.delenv("KANIBAKO_DOCKER_CMD", raising=False)
        with patch("shutil.which", return_value=None):
            with pytest.raises(ContainerError, match="No container runtime"):
                ContainerRuntime()

    def test_uses_env_override(self, monkeypatch):
        monkeypatch.setenv("KANIBAKO_DOCKER_CMD", "/usr/bin/fake-docker")
        rt = ContainerRuntime()
        assert rt.cmd == "/usr/bin/fake-docker"

    def test_explicit_command(self):
        rt = ContainerRuntime(command="/usr/bin/podman")
        assert rt.cmd == "/usr/bin/podman"

    def test_guess_containerfile(self):
        assert ContainerRuntime._guess_containerfile("ghcr.io/x/kanibako-oci:latest") == "kanibako"
        assert ContainerRuntime._guess_containerfile("ghcr.io/x/kanibako-min:v1") == "kanibako"
        assert ContainerRuntime._guess_containerfile("ghcr.io/x/kanibako-lxc:latest") == "kanibako"
        assert ContainerRuntime._guess_containerfile("totally-unrelated:latest") is None


class TestGetLocalDigest:
    def test_success_podman_format(self):
        """Podman returns a list; extract digest from RepoDigests."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{
            "RepoDigests": ["ghcr.io/x/kanibako-oci@sha256:abc123"]
        }])
        from unittest.mock import MagicMock
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            result = rt.get_local_digest("ghcr.io/x/kanibako-oci:latest")
        assert result == "sha256:abc123"

    def test_failure_returns_none(self):
        rt = ContainerRuntime(command="echo")
        from unittest.mock import MagicMock
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            result = rt.get_local_digest("nonexistent:latest")
        assert result is None

    def test_empty_repo_digests(self):
        """Locally-built images may have no RepoDigests."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{"RepoDigests": []}])
        from unittest.mock import MagicMock
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            result = rt.get_local_digest("local:latest")
        assert result is None

    def test_exception_returns_none(self):
        """Any unexpected exception returns None."""
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.container.subprocess.run", side_effect=OSError("fail")):
            result = rt.get_local_digest("img:latest")
        assert result is None


class TestRunEnvFlags:
    """Test that run() emits -e flags from the env parameter."""

    def test_env_flags_emitted(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                vault_enabled=False,
                env={"EDITOR": "vim", "NODE_ENV": "development"},
            )
            cmd = m.call_args[0][0]
            # env flags should appear as -e KEY=VALUE pairs
            assert "-e" in cmd
            idx_editor = cmd.index("EDITOR=vim")
            assert cmd[idx_editor - 1] == "-e"
            idx_node = cmd.index("NODE_ENV=development")
            assert cmd[idx_node - 1] == "-e"

    def test_env_none_no_flags(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                vault_enabled=False,
                env=None,
            )
            cmd = m.call_args[0][0]
            assert "-e" not in cmd

    def test_env_empty_dict_no_flags(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                vault_enabled=False,
                env={},
            )
            cmd = m.call_args[0][0]
            assert "-e" not in cmd


class TestDetachMode:
    """Test detach=True uses -dt (TTY for tmux) and omits --rm."""

    def test_detach_uses_dash_dt(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                vault_enabled=False,
                detach=True,
            )
            cmd = m.call_args[0][0]
            assert "-dt" in cmd
            assert "-it" not in cmd
            assert "--rm" not in cmd

    def test_interactive_uses_it_and_rm(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                vault_enabled=False,
                detach=False,
            )
            cmd = m.call_args[0][0]
            # -it when TTY available, -i when not (e.g. CI)
            assert "-it" in cmd or "-i" in cmd
            assert "--rm" in cmd
            assert "-d" not in cmd


class TestRmAndIsRunning:
    """Test rm() and is_running() methods."""

    def test_rm_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.rm("mycontainer") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "rm", "mycontainer"]

    def test_rm_failure(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.rm("nonexistent") is False

    def test_is_running_true(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="true\n")
            assert rt.is_running("mycontainer") is True

    def test_is_running_false_stopped(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="false\n")
            assert rt.is_running("mycontainer") is False

    def test_is_running_false_not_found(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.is_running("nonexistent") is False


class TestExec:
    """Test exec() method."""

    def test_exec_basic_command(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rc = rt.exec("mycontainer", ["tmux", "attach", "-t", "kanibako"])
            assert rc == 0
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "exec", "-it",
                "mycontainer", "tmux", "attach", "-t", "kanibako",
            ]

    def test_exec_returns_exit_code(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=42)
            rc = rt.exec("mycontainer", ["false"])
            assert rc == 42

    def test_exec_with_env(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.exec("mycontainer", ["bash"], env={"FOO": "bar"})
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "exec", "-it",
                "-e", "FOO=bar",
                "mycontainer", "bash",
            ]


class TestContainerExists:
    """Test container_exists() method."""

    def test_exists_running(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.container_exists("mycontainer") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "inspect", "mycontainer"]

    def test_not_exists(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.container_exists("nonexistent") is False


class TestRunInteractive:
    """Test run_interactive() command construction."""

    def test_basic_command(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rc = rt.run_interactive("img:latest")
            assert rc == 0
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "run", "-it", "img:latest"]

    def test_with_container_name(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run_interactive("img:latest", container_name="test-build")
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "run", "-it",
                "--name", "test-build", "img:latest",
            ]

    def test_returns_exit_code(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=42)
            assert rt.run_interactive("img:latest") == 42


class TestCommit:
    """Test commit() command construction."""

    def test_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stderr="")
            rt.commit("mycontainer", "myimage:latest")
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "commit", "mycontainer", "myimage:latest"]

    def test_failure_raises(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stderr="no such container")
            with pytest.raises(ContainerError, match="Failed to commit"):
                rt.commit("bad", "img")


class TestGetBaseImage:
    """Test get_base_image() variant-to-droste mapping."""

    def test_known_variants(self):
        assert ContainerRuntime.get_base_image("kanibako-min") == "ghcr.io/doctorjei/droste-seed:latest"
        assert ContainerRuntime.get_base_image("kanibako-oci") == "ghcr.io/doctorjei/droste-fiber:latest"
        assert ContainerRuntime.get_base_image("kanibako-lxc") == "ghcr.io/doctorjei/droste-thread:latest"
        assert ContainerRuntime.get_base_image("kanibako-vm") == "ghcr.io/doctorjei/droste-hair:latest"

    def test_qualified_image_name(self):
        assert ContainerRuntime.get_base_image("ghcr.io/x/kanibako-oci:latest") == "ghcr.io/doctorjei/droste-fiber:latest"

    def test_unknown_returns_none(self):
        assert ContainerRuntime.get_base_image("totally-unrelated:latest") is None


class TestRebuildBuildArgs:
    """Test rebuild() passes --build-arg flags."""

    def test_with_build_args(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.rebuild(
                "kanibako-oci:latest",
                Path("/tmp/Containerfile"),
                Path("/tmp/context"),
                build_args={"BASE_IMAGE": "droste-fiber:latest"},
            )
            cmd = m.call_args[0][0]
            assert "--build-arg" in cmd
            idx = cmd.index("--build-arg")
            assert cmd[idx + 1] == "BASE_IMAGE=droste-fiber:latest"

    def test_without_build_args(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.rebuild(
                "custom:latest",
                Path("/tmp/Containerfile"),
                Path("/tmp/context"),
            )
            cmd = m.call_args[0][0]
            assert "--build-arg" not in cmd


class TestPrecreateMountStubs:
    """Test _precreate_mount_stubs creates directory/file stubs for mounts."""

    def test_workspace_dir_always_created(self, tmp_path):
        from kanibako.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            vault_enabled=False,
            vault_ro_path=tmp_path / "no-ro",
            vault_rw_path=tmp_path / "no-rw",
            vault_tmpfs=False,
        )
        assert (shell / "workspace").is_dir()

    def test_vault_dirs_created_when_enabled(self, tmp_path):
        from kanibako.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        vault_ro = tmp_path / "vault-ro"
        vault_ro.mkdir()
        vault_rw = tmp_path / "vault-rw"
        vault_rw.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            vault_enabled=True,
            vault_ro_path=vault_ro,
            vault_rw_path=vault_rw,
            vault_tmpfs=True,
        )
        assert (shell / "share-ro").is_dir()
        assert (shell / "share-rw").is_dir()
        assert (project / "vault").is_dir()

    def test_vault_dirs_skipped_when_source_missing(self, tmp_path):
        from kanibako.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            vault_enabled=True,
            vault_ro_path=tmp_path / "missing-ro",
            vault_rw_path=tmp_path / "missing-rw",
            vault_tmpfs=False,
        )
        assert not (shell / "share-ro").exists()
        assert not (shell / "share-rw").exists()

    def test_extra_dir_mount_under_home(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "comms-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/home/agent/comms")]
        _precreate_mount_stubs(
            shell, project, mounts,
            vault_enabled=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            vault_tmpfs=False,
        )
        assert (shell / "comms").is_dir()

    def test_extra_file_mount_under_home(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_file = tmp_path / "claude-binary"
        src_file.touch()
        mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/claude")]
        _precreate_mount_stubs(
            shell, project, mounts,
            vault_enabled=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            vault_tmpfs=False,
        )
        assert (shell / ".local" / "bin").is_dir()
        assert (shell / ".local" / "bin" / "claude").is_file()

    def test_extra_mount_under_workspace(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "vault-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/home/agent/workspace/vault")]
        _precreate_mount_stubs(
            shell, project, mounts,
            vault_enabled=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            vault_tmpfs=False,
        )
        assert (project / "vault").is_dir()

    def test_mount_outside_home_skipped(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "opt-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/opt/kanibako/kanibako")]
        _precreate_mount_stubs(
            shell, project, mounts,
            vault_enabled=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            vault_tmpfs=False,
        )
        # No dirs created under shell or project for /opt/ mounts
        assert list(shell.iterdir()) == [shell / "workspace"]

    def test_existing_file_not_overwritten(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Pre-existing file with content
        (shell / ".local" / "bin").mkdir(parents=True)
        existing = shell / ".local" / "bin" / "kanibako"
        existing.write_text("existing content")
        src_file = tmp_path / "entry"
        src_file.touch()
        mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/kanibako")]
        _precreate_mount_stubs(
            shell, project, mounts,
            vault_enabled=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            vault_tmpfs=False,
        )
        # File stub should NOT overwrite existing content
        assert existing.read_text() == "existing content"

    def test_oserror_is_swallowed(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_file = tmp_path / "f"
        src_file.touch()
        # Make shell read-only so mkdir fails
        shell.chmod(0o444)
        try:
            mounts = [FakeMount(source=src_file, destination="/home/agent/deep/nested/file")]
            # Should not raise
            _precreate_mount_stubs(
                shell, project, mounts,
                vault_enabled=False,
                vault_ro_path=tmp_path / "x",
                vault_rw_path=tmp_path / "y",
                vault_tmpfs=False,
            )
        finally:
            shell.chmod(0o755)
