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
    """Test detach=True uses -d instead of -it and omits --rm."""

    def test_detach_uses_dash_d(self):
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
            assert "-d" in cmd
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
