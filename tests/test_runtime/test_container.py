"""Tests for kanibako.runtime.container."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.runtime.container import (
  ContainerRuntime,
  creation_metadata_from_inspect,
  image_ref_or_none,
)
from kanibako.errors import ContainerError


class TestContainerRuntime:
  def test_detect_raises_when_nothing_found(self, monkeypatch):
    monkeypatch.delenv("KANIBAKO_DOCKER_CMD", raising=False)
    with patch("shutil.which", return_value=None):
      with pytest.raises(ContainerError, match="No container runtime"):
        ContainerRuntime()

  def test_uses_env_override(self, monkeypatch, tmp_path):
    stub = tmp_path / "fake-docker"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
    rt = ContainerRuntime()
    assert rt.cmd == str(stub)

  def test_env_override_naming_a_missing_binary_is_refused_by_name(self, monkeypatch, tmp_path):
    missing = str(tmp_path / "no-such-runtime")
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", missing)
    with pytest.raises(ContainerError, match="KANIBAKO_DOCKER_CMD names") as exc_info:
      ContainerRuntime()
    assert missing in str(exc_info.value)

  def test_explicit_command(self):
    rt = ContainerRuntime(command="/usr/bin/podman")
    assert rt.cmd == "/usr/bin/podman"


class TestListRunningAnchor:
  def test_default_running_argv_has_no_dash_a(self):
    from unittest.mock import MagicMock, patch

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="")
      rt.list_running()
      argv = m.call_args.args[0]
    assert "-a" not in argv
    assert "--all" not in argv
    assert any("name=kb-" in a for a in argv)

  def test_include_stopped_argv_has_dash_a(self):
    from unittest.mock import MagicMock, patch

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="")
      rt.list_running(include_stopped=True)
      argv = m.call_args.args[0]
    assert "-a" in argv
    assert any("name=kb-" in a for a in argv)

  def test_post_filter_anchors_the_prefix(self):
    from unittest.mock import MagicMock, patch

    rt = ContainerRuntime(command="/usr/bin/podman")
    # The regex-shape ``--filter name=kb-`` would also match ``mykb-cache``;
    # the post-filter ``startswith("kb-")`` is the gate that rejects it.
    fake_ps = (
      "kb-aabbccdd\timg:latest\tUp 5 minutes\n"
      "mykb-cache\timg:latest\tExited (0) 1 hour ago\n"
      "kb-11223344\timg:latest\tExited (0) 2 hours ago\n"
      "totally-other\timg:latest\tUp 1 minute\n"
    )
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=fake_ps)
      result = rt.list_running(include_stopped=True)
    names = [r[0] for r in result]
    assert names == ["kb-aabbccdd", "kb-11223344"]
    assert "mykb-cache" not in names
    assert "totally-other" not in names

  def test_post_filter_also_runs_in_running_only(self):
    from unittest.mock import MagicMock, patch

    rt = ContainerRuntime(command="/usr/bin/podman")
    fake_ps = "mykb-cache\timg:latest\tUp 1 minute\nkb-real\timg:latest\tUp 2 minutes\n"
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=fake_ps)
      result = rt.list_running()
    names = [r[0] for r in result]
    assert names == ["kb-real"]


class TestGetLocalDigest:
  def test_success_podman_format(self):
    """Podman returns a list; extract digest from RepoDigests."""
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps([{"RepoDigests": ["ghcr.io/x/kanibako-oci@sha256:abc123"]}])
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      result = rt.get_local_digest("ghcr.io/x/kanibako-oci:latest")
    assert result == "sha256:abc123"

  def test_failure_returns_none(self):
    rt = ContainerRuntime(command="echo")
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      result = rt.get_local_digest("nonexistent:latest")
    assert result is None

  def test_empty_repo_digests(self):
    """Locally-built images may have no RepoDigests."""
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps([{"RepoDigests": []}])
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      result = rt.get_local_digest("local:latest")
    assert result is None

  def test_exception_returns_none(self):
    """Any unexpected exception returns None."""
    rt = ContainerRuntime(command="echo")
    with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
      result = rt.get_local_digest("img:latest")
    assert result is None

  def test_returns_first_of_multiple(self):
    """With multiple RepoDigests, get_local_digest returns the first."""
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps(
      [
        {
          "RepoDigests": [
            "ghcr.io/x/kanibako-oci@sha256:3de8",
            "ghcr.io/x/kanibako-oci@sha256:4f49",
          ]
        }
      ]
    )
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      assert rt.get_local_digest("ghcr.io/x/kanibako-oci:latest") == "sha256:3de8"


class TestGetLocalDigests:
  def test_returns_full_list(self):
    """All RepoDigests are returned with the repo@ prefix stripped."""
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps(
      [
        {
          "RepoDigests": [
            "ghcr.io/x/kanibako-oci@sha256:3de8",
            "ghcr.io/x/kanibako-oci@sha256:4f49",
          ]
        }
      ]
    )
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      result = rt.get_local_digests("ghcr.io/x/kanibako-oci:latest")
    assert result == ["sha256:3de8", "sha256:4f49"]

  def test_empty_repo_digests(self):
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps([{"RepoDigests": []}])
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      assert rt.get_local_digests("local:latest") == []

  def test_failure_returns_empty(self):
    rt = ContainerRuntime(command="echo")
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.get_local_digests("nonexistent:latest") == []

  def test_exception_returns_empty(self):
    rt = ContainerRuntime(command="echo")
    with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
      assert rt.get_local_digests("img:latest") == []


class TestGetLocalPlatform:
  def test_parses_os_arch(self):
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps([{"Os": "linux", "Architecture": "amd64"}])
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      assert rt.get_local_platform("img:latest") == "linux/amd64"

  def test_parses_os_arch_variant(self):
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps(
      [
        {
          "Os": "linux",
          "Architecture": "arm",
          "Variant": "v7",
        }
      ]
    )
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      assert rt.get_local_platform("img:latest") == "linux/arm/v7"

  def test_missing_fields_returns_none(self):
    import json

    rt = ContainerRuntime(command="echo")
    inspect_output = json.dumps([{"Os": "linux"}])
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=inspect_output)
      assert rt.get_local_platform("img:latest") is None

  def test_failure_returns_none(self):
    rt = ContainerRuntime(command="echo")
    from unittest.mock import MagicMock

    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.get_local_platform("img:latest") is None

  def test_exception_returns_none(self):
    rt = ContainerRuntime(command="echo")
    with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
      assert rt.get_local_platform("img:latest") is None


class TestUnshareRm:
  """Test ContainerRuntime.unshare_rm()."""

  def test_invokes_podman_unshare_rm(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      ok = rt.unshare_rm(Path("/data/boxes/proj"))
    assert ok is True
    cmd = m.call_args[0][0]
    assert cmd == ["/usr/bin/podman", "unshare", "rm", "-rf", "/data/boxes/proj"]

  def test_returns_false_on_nonzero(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.unshare_rm(Path("/data/boxes/proj")) is False

  def test_docker_has_no_unshare(self):
    rt = ContainerRuntime(command="/usr/bin/docker")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      assert rt.unshare_rm(Path("/data/boxes/proj")) is False
      m.assert_not_called()


class TestUnshareRename:
  """``unshare_rename`` is ``rename(2)`` run as namespace root: never ``mv``, never a copy."""

  def test_argv_is_an_in_namespace_os_rename(self):
    import sys
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.unshare_rename(Path("/r/a"), Path("/r/w/a")) is True
    assert m.call_args[0][0] == [
      "/usr/bin/podman",
      "unshare",
      sys.executable,
      "-I",
      "-c",
      "import os,sys; os.rename(*sys.argv[1:])",
      "/r/a",
      "/r/w/a",
    ]

  def test_the_argv_body_renames_and_never_copies(self, tmp_path):
    import subprocess
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.unshare_rename(tmp_path / "a", tmp_path / "b")
    body = m.call_args[0][0][2:]
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_text("x")
    ino = (tmp_path / "a").stat().st_ino
    assert subprocess.run(body).returncode == 0
    assert not (tmp_path / "a").exists()
    assert (tmp_path / "b").stat().st_ino == ino
    (tmp_path / "c").mkdir()
    (tmp_path / "other").mkdir()
    cross = [*body[:-2], str(tmp_path / "c"), str(tmp_path / "other" / "x" / "c")]
    assert subprocess.run(cross, capture_output=True).returncode != 0
    assert (tmp_path / "c").is_dir()

  def test_false_on_nonzero_and_on_docker(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.unshare_rename(Path("/r/a"), Path("/r/b")) is False
    rt = ContainerRuntime(command="/usr/bin/docker")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      assert rt.unshare_rename(Path("/r/a"), Path("/r/b")) is False
      m.assert_not_called()


class TestRemoveBoxTree:
  """``container.remove_box_tree`` — THE box-tree deleter every verb reuses.

  Since J-7 every box home carries the root-owned canon skeleton, so this is no
  longer a rare has-a-root-owned-file case: a bare ``rmtree`` of ANY box home fails.
  """

  def test_removes_a_normal_tree(self, tmp_path):
    from kanibako.runtime.container import remove_box_tree

    d = tmp_path / "box"
    (d / "home").mkdir(parents=True)
    (d / "box.yaml").write_text("x")
    assert remove_box_tree(d) is True
    assert not d.exists()

  def test_falls_back_to_unshare_on_permission_error(self, tmp_path):
    from unittest.mock import patch as _p
    from kanibako.runtime.container import remove_box_tree

    d = tmp_path / "box"
    d.mkdir()
    with (
      _p("shutil.rmtree", side_effect=PermissionError("denied")),
      _p("kanibako.runtime.container.ContainerRuntime") as mock_rt,
    ):
      mock_rt.return_value.unshare_rm.return_value = True
      assert remove_box_tree(d) is True
      mock_rt.return_value.unshare_rm.assert_called_once_with(d)

  def test_false_when_unshare_fails_and_tree_remains(self, tmp_path):
    from unittest.mock import patch as _p
    from kanibako.runtime.container import remove_box_tree

    d = tmp_path / "box"
    d.mkdir()
    with (
      _p("shutil.rmtree", side_effect=PermissionError("denied")),
      _p("kanibako.runtime.container.ContainerRuntime") as mock_rt,
    ):
      mock_rt.return_value.unshare_rm.return_value = False
      assert remove_box_tree(d) is False
      assert d.exists()

  def test_a_symlink_root_loses_only_the_link(self, tmp_path):
    """R1: the link is unlinked; nothing inside its target is chmodded or removed."""
    from unittest.mock import patch as _p
    from kanibako.runtime.container import remove_box_tree

    outside = tmp_path / "outside"
    (outside / "locked").mkdir(parents=True)
    (outside / "locked" / "f").write_text("keep")
    (outside / "locked").chmod(0o500)
    link = tmp_path / "box"
    link.symlink_to(outside)
    try:
      with _p("kanibako.runtime.container.ContainerRuntime") as mock_rt:
        assert remove_box_tree(link) is True
        mock_rt.return_value.unshare_rm.assert_not_called()
      assert not link.is_symlink() and not link.exists()
      assert (outside / "locked").stat().st_mode & 0o777 == 0o500
      assert (outside / "locked" / "f").read_text() == "keep"
    finally:
      (outside / "locked").chmod(0o700)


class TestRunEnvFlags:
  """Test that run() emits -e flags from the env parameter."""

  def test_env_flags_emitted(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
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
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        env=None,
      )
      cmd = m.call_args[0][0]
      assert "-e" not in cmd

  def test_env_empty_dict_no_flags(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        env={},
      )
      cmd = m.call_args[0][0]
      assert "-e" not in cmd


class TestDetachMode:
  """Test detach=True uses -dt (TTY for tmux) and omits --rm."""

  def test_detach_uses_dash_dt(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        detach=True,
      )
      cmd = m.call_args[0][0]
      assert "-dt" in cmd
      assert "-it" not in cmd
      assert "--rm" not in cmd
      # The persistent (detached) path maps the caller onto the image
      # agent user too — same contract as the ephemeral path; literal
      # oracle on purpose (see test_container_extended.test_base_flags).
      assert "--userns=keep-id:uid=1000,gid=1000" in cmd
      assert "--userns=keep-id" not in cmd  # plain form must be gone

  def test_interactive_uses_it_and_rm(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        detach=False,
      )
      cmd = m.call_args[0][0]
      # -it when TTY available, -i when not (e.g. CI)
      assert "-it" in cmd or "-i" in cmd
      assert "--rm" in cmd
      assert "-d" not in cmd

  def test_detach_captures_stdout_so_id_not_leaked(self):
    """detach=True captures stdout (container id stays off the terminal)."""
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="57c49acad8fb" * 4, stderr="")
      rc = rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        detach=True,
      )
      assert rc == 0
      assert m.call_args.kwargs.get("capture_output") is True

  def test_interactive_does_not_capture_stdout(self):
    """detach=False stays foreground (no capture) so stdio is inherited."""
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rc = rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        detach=False,
      )
      assert rc == 0
      assert m.call_args.kwargs.get("capture_output") is not True

  def test_detach_propagates_failure_returncode(self):
    """A failed detached launch still propagates its non-zero return code."""
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=125, stdout="", stderr="Error: boom\n")
      rc = rt.run(
        "test:latest",
        shell_path=Path("/tmp/shell"),
        project_path=Path("/tmp/project"),
        vault_ro_path=Path("/tmp/vault-ro"),
        vault_rw_path=Path("/tmp/vault-rw"),
        enable_vault=False,
        detach=True,
      )
      assert rc == 125


class TestStop:
  """Test stop(): the bool is the runtime's EXIT STATUS, never a liveness reading."""

  def test_stop_success(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.stop("mycontainer") is True
      m.assert_called_once()
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "stop", "mycontainer"]

  def test_stop_failure(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=125)
      assert rt.stop("nonexistent") is False

  @staticmethod
  def _runs(*results):
    """A ``subprocess.run`` stub answering each call in turn; its argvs are recorded."""
    from unittest.mock import MagicMock

    return MagicMock(side_effect=[MagicMock(returncode=rc, stdout=out) for rc, out in results])

  def test_a_paused_container_is_unpaused_then_stopped(self):
    """podman refuses to stop a PAUSED container (rc 125); unpause, then stop again."""
    rt = ContainerRuntime(command="/usr/bin/podman")
    m = self._runs((125, ""), (0, "kb-paused\tpaused\n"), (0, ""), (0, ""))
    with patch("kanibako.runtime.container.subprocess.run", m):
      assert rt.stop("kb-paused") is True
    assert [c.args[0] for c in m.call_args_list] == [
      ["/usr/bin/podman", "stop", "kb-paused"],
      self._ps_a("kb-paused"),
      ["/usr/bin/podman", "unpause", "kb-paused"],
      ["/usr/bin/podman", "stop", "kb-paused"],
    ]

  def test_a_failed_unpause_is_a_failed_stop(self):
    """No second stop is asked of a container that is still paused."""
    rt = ContainerRuntime(command="/usr/bin/podman")
    m = self._runs((125, ""), (0, "kb-paused\tpaused\n"), (125, ""))
    with patch("kanibako.runtime.container.subprocess.run", m):
      assert rt.stop("kb-paused") is False
    assert [c.args[0][1] for c in m.call_args_list] == ["stop", "ps", "unpause"]

  def test_a_failed_stop_of_an_unpaused_container_is_not_retried(self):
    rt = ContainerRuntime(command="/usr/bin/podman")
    m = self._runs((125, ""), (0, "kb-stuck\texited\n"))
    with patch("kanibako.runtime.container.subprocess.run", m):
      assert rt.stop("kb-stuck") is False
    assert [c.args[0][1] for c in m.call_args_list] == ["stop", "ps"]

  @staticmethod
  def _ps_a(name):
    """The one state reader's argv: ``ps -a`` (``inspect`` fails on a ``removing`` one)."""
    return [
      "/usr/bin/podman",
      "ps",
      "-a",
      "--filter",
      f"name={name}",
      "--format",
      "{{.Names}}\t{{.State}}",
    ]

  @pytest.mark.parametrize(
    "rc, out, state",
    [
      (0, "kb-x\tpaused\n", "paused"),
      (0, "kb-x\trunning\n", "running"),
      (0, "kb-x\tremoving\n", "removing"),
      (0, "kb-x2\texited\n", None),
      (0, "", None),
      (125, "", None),
    ],
  )
  def test_container_state_reads_ps_a_state(self, rc, out, state):
    rt = ContainerRuntime(command="/usr/bin/podman")
    m = self._runs((rc, out))
    with patch("kanibako.runtime.container.subprocess.run", m):
      assert rt.container_state("kb-x") == state
    assert m.call_args.args[0] == self._ps_a("kb-x")


class TestRmAndIsRunning:
  """Test rm() and is_running() methods."""

  def test_rm_success(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.rm("mycontainer") is True
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "rm", "mycontainer"]

  def test_rm_failure(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.rm("nonexistent") is False

  def test_is_running_true(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="true\n")
      assert rt.is_running("mycontainer") is True

  def test_is_running_false_stopped(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="false\n")
      assert rt.is_running("mycontainer") is False

  def test_is_running_false_not_found(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.is_running("nonexistent") is False


class TestInspectEnv:
  """Test inspect_env() — reads a container's recorded .Config.Env."""

  def test_returns_value_for_present_key(self):
    import json
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    env = json.dumps(["PATH=/usr/bin", "KANIBAKO_AGENT=claude", "TERM=xterm"])
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=env)
      assert rt.inspect_env("box", "KANIBAKO_AGENT") == "claude"
      cmd = m.call_args[0][0]
      assert cmd == [
        "/usr/bin/podman",
        "inspect",
        "--format",
        "{{json .Config.Env}}",
        "box",
      ]

  def test_returns_none_for_absent_key(self):
    import json
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    env = json.dumps(["PATH=/usr/bin", "TERM=xterm"])
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=env)
      assert rt.inspect_env("box", "KANIBAKO_AGENT") is None

  def test_returns_none_when_inspect_fails(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.inspect_env("nope", "KANIBAKO_AGENT") is None

  def test_returns_none_on_bad_json(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="not json")
      assert rt.inspect_env("box", "KANIBAKO_AGENT") is None

  def test_value_with_equals_sign_preserved(self):
    import json
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    env = json.dumps(["FOO=a=b=c"])
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=env)
      assert rt.inspect_env("box", "FOO") == "a=b=c"


class TestCreationMetadataFromInspect:
  @pytest.mark.parametrize(
    "labels",
    [None, {}, {"kanibako.target": ""}, {"kanibako.target": "Goose", "custom": "value=1"}],
  )
  def test_returns_exact_id_and_labels(self, labels):
    import json
    from subprocess import CompletedProcess

    result = CompletedProcess([], 0, stdout=json.dumps(["ABC123", labels]) + "\n")
    assert creation_metadata_from_inspect(result) == (
      "ABC123",
      labels if labels is not None else {},
    )

  @pytest.mark.parametrize(
    "metadata",
    [
      None,
      {},
      "id",
      [],
      ["id"],
      ["id", {}, {}],
      [None, {}],
      [1, {}],
      [True, {}],
      ["", {}],
      [" \t", {}],
      ["id", []],
      ["id", ""],
      ["id", {"kanibako.target": None}],
      ["id", {"kanibako.target": 1}],
      ["id", {"kanibako.target": False}],
      ["id", {"kanibako.target": {}}],
    ],
  )
  def test_unreadable_shape_never_becomes_a_legacy_empty_map(self, metadata):
    import json
    from subprocess import CompletedProcess

    result = CompletedProcess([], 0, stdout=json.dumps(metadata))
    assert creation_metadata_from_inspect(result) is None

  @pytest.mark.parametrize("stdout", ["", " \n", "<no value>", "not json", '["id",'])
  def test_unreadable_json_returns_none(self, stdout):
    from subprocess import CompletedProcess

    assert creation_metadata_from_inspect(CompletedProcess([], 0, stdout=stdout)) is None

  def test_failed_inspect_ignores_even_valid_payload(self):
    from subprocess import CompletedProcess

    result = CompletedProcess([], 125, stdout='["id", {}]', stderr="container missing")
    assert creation_metadata_from_inspect(result) is None


class TestImageRefOrNone:
  """The ONE rule for "is this inspect output an image reference?"."""

  @pytest.mark.parametrize(
    "raw",
    [
      "ghcr.io/doctorjei/kanibako-oci:latest\n",
      "localhost/kanibako-min:latest",
      "docker.io/library/busybox",
      "registry.example.com:5000/team/img:v1.2.3",
      "ghcr.io/x/y@sha256:" + "a" * 64,
      "ghcr.io/x/y:RC1",  # a tag may legally carry uppercase
      "0a1b2c3d4e5f",  # a bare image ID
    ],
  )
  def test_accepts_a_reference_stripped(self, raw):
    assert image_ref_or_none(raw) == raw.strip()

  @pytest.mark.parametrize(
    "raw",
    [
      "<no value>",  # Go text/template on a missing MAP key, at exit 0
      "<no value>\n",
      "",
      "   \n",
      "Error: no such object: kanibako-foo",
      "template parsing error: bad character",
      "ghcr.io/x/y:latest extra",
    ],
  )
  def test_refuses_anything_that_cannot_be_a_reference(self, raw):
    assert image_ref_or_none(raw) is None


class TestContainerImage:
  """container_image() — the image reference a live box was created from."""

  _REF = "ghcr.io/doctorjei/kanibako-oci:latest"

  def test_returns_the_inspected_reference(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout=self._REF + "\n")
      assert rt.container_image("box") == self._REF
      assert m.call_args[0][0] == [
        "/usr/bin/podman",
        "inspect",
        "--format",
        "{{.ImageName}}",
        "box",
      ]

  def test_returns_none_when_inspect_fails(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=125, stdout="")
      assert rt.container_image("box") is None

  def test_a_no_value_at_exit_zero_is_not_a_reference(self):
    """Go's missing-map-key sentinel printed at rc 0 must not become a reference.

    Modern docker does NOT do this — it retries the template with
    ``missingkey=error`` and exits 1 on an unknown top-level field.  Docker built
    with Go 1.4 (pre-1.12) DID, and ``KANIBAKO_DOCKER_CMD`` points ``self.cmd`` at
    any engine binary the user likes.  Without the guard the literal
    ``"<no value>"`` reaches the caller as though it were a reference and keys a
    VS Code attached-container config file at ``%3cno%20value%3e.json``.
    """
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/docker")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="<no value>\n")
      assert rt.container_image("box") is None

  def test_empty_output_at_exit_zero_is_none(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="\n")
      assert rt.container_image("box") is None


class TestExec:
  """Test exec() method."""

  def test_exec_basic_command(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with (
      patch("kanibako.runtime.container.subprocess.run") as m,
      patch("kanibako.runtime.container.sys.stdin.isatty", return_value=True),
    ):
      m.return_value = MagicMock(returncode=0)
      rc = rt.exec("mycontainer", ["tmux", "attach", "-t", "kanibako"])
      assert rc == 0
      cmd = m.call_args[0][0]
      assert cmd == [
        "/usr/bin/podman",
        "exec",
        "-it",
        "mycontainer",
        "tmux",
        "attach",
        "-t",
        "kanibako",
      ]

  def test_exec_no_tty_when_not_a_terminal(self):
    """Without a stdin TTY (CI, scripts), use -i instead of -it.

    `-t` would cause interactive commands like tmux attach to render
    but never return.
    """
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with (
      patch("kanibako.runtime.container.subprocess.run") as m,
      patch("kanibako.runtime.container.sys.stdin.isatty", return_value=False),
    ):
      m.return_value = MagicMock(returncode=0)
      rt.exec("mycontainer", ["echo", "hi"])
      cmd = m.call_args[0][0]
      assert cmd[2] == "-i"

  def test_exec_returns_exit_code(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=42)
      rc = rt.exec("mycontainer", ["false"])
      assert rc == 42

  def test_exec_with_env(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with (
      patch("kanibako.runtime.container.subprocess.run") as m,
      patch("kanibako.runtime.container.sys.stdin.isatty", return_value=True),
    ):
      m.return_value = MagicMock(returncode=0)
      rt.exec("mycontainer", ["bash"], env={"FOO": "bar"})
      cmd = m.call_args[0][0]
      assert cmd == [
        "/usr/bin/podman",
        "exec",
        "-it",
        "-e",
        "FOO=bar",
        "mycontainer",
        "bash",
      ]


class TestExecReady:
  """Test exec_ready() — CAPTURED readiness probe for the interactive exec."""

  def test_exec_ready_true_when_rc_zero(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.exec_ready("mycontainer") is True
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "exec", "mycontainer", "true"]

  def test_exec_ready_false_when_rc_nonzero(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.exec_ready("mycontainer") is False

  def test_exec_ready_captures_output_so_race_error_cannot_leak(self):
    """The probe MUST capture output, or podman's raw "container state
    improper" race line would leak to the user's TTY/stderr — the exact
    bug this method closes."""
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.exec_ready("mycontainer")
      assert m.call_args.kwargs.get("capture_output") is True

  def test_exec_succeeds_runs_the_command_captured(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.exec_succeeds("box", ["tmux", "has-session"]) is False
      assert m.call_args[0][0] == [
        "/usr/bin/podman",
        "exec",
        "box",
        "tmux",
        "has-session",
      ]
      assert m.call_args.kwargs.get("capture_output") is True


class TestContainerExists:
  """Test container_exists() method."""

  def test_exists_running(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.container_exists("mycontainer") is True
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "inspect", "mycontainer"]

  def test_not_exists(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.container_exists("nonexistent") is False


class TestRunInteractive:
  """Test run_interactive() command construction."""

  def test_basic_command(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rc = rt.run_interactive("img:latest")
      assert rc == 0
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "run", "-it", "img:latest"]

  def test_with_container_name(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      rt.run_interactive("img:latest", container_name="test-build")
      cmd = m.call_args[0][0]
      assert cmd == [
        "/usr/bin/podman",
        "run",
        "-it",
        "--name",
        "test-build",
        "img:latest",
      ]

  def test_returns_exit_code(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=42)
      assert rt.run_interactive("img:latest") == 42


class TestCommit:
  """Test commit() command construction."""

  def test_success(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stderr="")
      rt.commit("mycontainer", "myimage:latest")
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "commit", "mycontainer", "myimage:latest"]

  def test_failure_raises(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stderr="no such container")
      with pytest.raises(ContainerError, match="Failed to commit"):
        rt.commit("bad", "img")


class TestCpSaveLoadDiff:
  """Test cp(), save(), load(), diff() thin wrappers."""

  def test_cp_success(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.cp(Path("/x/y"), "ctr:/etc/") is True
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "cp", "/x/y", "ctr:/etc/"]

  def test_cp_failure(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.cp(Path("/x/y"), "ctr:/etc/") is False

  def test_save_success(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0)
      assert rt.save("img", Path("/o.tar")) is True
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "save", "-o", "/o.tar", "img"]

  def test_save_failure(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1)
      assert rt.save("img", Path("/o.tar")) is False

  def test_load_success_returns_ref(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(
        returncode=0,
        stdout="Loaded image: repo/app:1.0\n",
      )
      assert rt.load(Path("/a.tar")) == "repo/app:1.0"
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "load", "-i", "/a.tar"]

  def test_load_success_image_id_form(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(
        returncode=0,
        stdout="Loaded image(s): ghcr.io/x/y:tag\n",
      )
      assert rt.load(Path("/a.tar")) == "ghcr.io/x/y:tag"

  def test_load_success_untagged_returns_empty(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="")
      assert rt.load(Path("/a.tar")) == ""

  def test_load_failure_returns_none(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.load(Path("/a.tar")) is None

  def test_diff_returns_lines(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="C /etc\nA /etc/foo\n")
      result = rt.diff("img")
      assert result == ["C /etc", "A /etc/foo"]
      cmd = m.call_args[0][0]
      assert cmd == ["/usr/bin/podman", "diff", "img"]

  def test_diff_empty_stdout(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=0, stdout="")
      assert rt.diff("img") == []

  def test_diff_failure_returns_empty(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
      m.return_value = MagicMock(returncode=1, stdout="")
      assert rt.diff("img") == []


class TestRebuildBuildArgs:
  """Test rebuild() passes --build-arg flags."""

  def test_with_build_args(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="/usr/bin/podman")
    with patch("kanibako.runtime.container.subprocess.run") as m:
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
    with patch("kanibako.runtime.container.subprocess.run") as m:
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
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=False,
      vault_ro_path=tmp_path / "no-ro",
      vault_rw_path=tmp_path / "no-rw",
      tmpfs_masks=[],
    )
    assert (shell / "workspace").is_dir()
    # Vault disabled: no vault dest stubs are created.
    assert not (shell / "vault" / "ro").exists()
    assert not (shell / "vault" / "rw").exists()

  def test_vault_dirs_created_when_enabled(self, tmp_path):
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    vault_ro = tmp_path / "vault-ro"
    vault_ro.mkdir()
    vault_rw = tmp_path / "vault-rw"
    vault_rw.mkdir()
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=True,
      vault_ro_path=vault_ro,
      vault_rw_path=vault_rw,
      tmpfs_masks=["/home/agent/workspace/vault"],
    )
    assert (shell / "vault" / "ro").is_dir()
    assert (shell / "vault" / "rw").is_dir()
    # The default vault mask box-dest maps to project_path / "vault"
    # (byte-identical to the old single-vault stub).
    assert (project / "vault").is_dir()

  def test_mask_stub_under_home(self, tmp_path):
    """A mask box-dest under ~/ (not workspace) maps under shell_path."""
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    vault_ro = tmp_path / "vault-ro"
    vault_ro.mkdir()
    vault_rw = tmp_path / "vault-rw"
    vault_rw.mkdir()
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=True,
      vault_ro_path=vault_ro,
      vault_rw_path=vault_rw,
      tmpfs_masks=["/home/agent/.secret"],
    )
    assert (shell / ".secret").is_dir()

  def test_mask_workspace_prefix_keeps_its_separator(self, tmp_path):
    """``workspace_prefix`` is SLASH-TERMINATED, so a ``~/workspacefoo`` mask
    stays HOME-side and keeps its parent loosening.

    Regression guard for the trailing slash on the local ``workspace_prefix``.
    A slashless prefix reads ``~/workspacefoo/x`` as workspace-side, which
    passes ``traverse_root=None`` and silently drops the ``_loosen_parents``
    walk — invisible in the stub's LOCATION, visible only in its parent's mode.
    """
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # A home-side parent with NO search bits: only the loosen walk adds them.
    sibling = shell / "workspacefoo"
    sibling.mkdir()
    sibling.chmod(0o700)
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=False,
      vault_ro_path=tmp_path / "no-ro",
      vault_rw_path=tmp_path / "no-rw",
      tmpfs_masks=["/home/agent/workspacefoo/x"],
    )
    assert (sibling / "x").is_dir()
    assert sibling.stat().st_mode & 0o777 == 0o711

  def test_mask_stub_made_with_the_vault_disabled(self, tmp_path):
    """Mask stubs are made regardless of ``enable_vault`` — they pair with
    the emit, which no longer gates on it.

    Without the stub the tmpfs mount FAILS in LXC (the OCI runtime cannot
    mkdir the mountpoint inside a bind-mounted overlay), so re-nesting this
    loop under the vault arm would turn the repaired mask into a launch
    error on exactly the platform the stubs exist for.
    """
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=False,
      vault_ro_path=tmp_path / "no-ro",
      vault_rw_path=tmp_path / "no-rw",
      tmpfs_masks=["/home/agent/.secret", "/home/agent/workspace/build"],
    )
    assert (shell / ".secret").is_dir()
    assert (project / "build").is_dir()
    # Still no vault stubs: the vault arm itself is untouched.
    assert not (shell / "vault" / "ro").exists()
    assert not (shell / "vault" / "rw").exists()

  def test_vault_dirs_created_even_when_source_missing(self, tmp_path):
    """Vault is UNIVERSAL unless disabled: the box-side dest stubs are made
    whenever vault is enabled, regardless of whether the host source exists
    (the resolver creates the source if missing)."""
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _precreate_mount_stubs(
      shell,
      project,
      None,
      enable_vault=True,
      vault_ro_path=tmp_path / "missing-ro",
      vault_rw_path=tmp_path / "missing-rw",
      tmpfs_masks=[],
    )
    assert (shell / "vault" / "ro").is_dir()
    assert (shell / "vault" / "rw").is_dir()

  @staticmethod
  def _canon_mounts(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class FakeMount:
      source: Path
      destination: str
      options: str = ""

    src_dir = tmp_path / "chapter"
    src_dir.mkdir(exist_ok=True)
    src_file = tmp_path / "index.md"
    src_file.touch()
    other = tmp_path / "other"
    other.mkdir(exist_ok=True)
    return [
      FakeMount(source=src_dir, destination="/home/agent/canon/charter/general"),
      FakeMount(source=src_file, destination="/home/agent/canon/COLLECTION.md"),
      FakeMount(source=src_dir, destination="/home/agent/canon/handbook/box"),
      # A NON-canon dest in the same call must still be stubbed — the skip is
      # narrow, not a general opt-out of stub pre-creation.
      FakeMount(source=other, destination="/home/agent/comms"),
    ]

  def test_existing_canon_mountpoints_are_left_alone(self, tmp_path):
    """⚑ J-7 MACHINERY EXCLUSION. Where the box-create skeleton HAS run, the canon
    mountpoints are root-owned and unwritable, so the launch path cannot manage
    them and must not try. Nothing under ``~/canon`` may be created or modified.
    """
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    # Pre-create the skeleton mountpoints the four mounts land on, as create does.
    for rel in ("canon/charter/general", "canon/handbook/box"):
      (shell / rel).mkdir(parents=True)
    (shell / "canon" / "COLLECTION.md").touch()
    before = sorted(p.relative_to(shell) for p in (shell / "canon").rglob("*"))

    _precreate_mount_stubs(
      shell,
      project,
      self._canon_mounts(tmp_path),
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    after = sorted(p.relative_to(shell) for p in (shell / "canon").rglob("*"))
    assert after == before, "the launch path must not touch an existing skeleton"
    assert (shell / "comms").is_dir(), "non-canon stubs are unaffected"

  def test_absent_canon_mountpoints_ARE_stubbed(self, tmp_path):
    """⚑⚑ THE SKIP IS EXISTENCE-AWARE, NOT PATH-AWARE — and that is load-bearing.

    Skeleton-less boxes exist and keep arriving: every R1-era and pre-canon box,
    plus any box whose create hit the degraded path. An UNCONDITIONAL skip leaves
    their five-or-six canon binds with no mountpoints at all, and in LXC crun
    cannot mkdir inside a bind-mounted overlay — a launch failure (exit 126), not a
    degradation. Falling through to the tolerant stub helpers pre-creates exactly
    those mountpoints, which makes this a free self-healing migration.

    RED if the skip is made unconditional again.
    """
    from kanibako.runtime.container import _precreate_mount_stubs

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert not (shell / "canon").exists(), "a pre-R1b box home has no skeleton"

    _precreate_mount_stubs(
      shell,
      project,
      self._canon_mounts(tmp_path),
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    assert (shell / "canon" / "charter" / "general").is_dir()
    assert (shell / "canon" / "handbook" / "box").is_dir()
    assert (shell / "canon" / "COLLECTION.md").is_file()
    assert (shell / "comms").is_dir()

  def test_canon_dest_predicate_is_prefix_aware(self):
    """``~/canon-of-mine`` is NOT under ``~/canon``: a naive ``startswith``
    without the separator would wrongly skip a real bind's stub."""
    from kanibako.runtime.container import _is_managed_canon_dest

    assert _is_managed_canon_dest("/home/agent/canon")
    assert _is_managed_canon_dest("/home/agent/canon/charter/general")
    assert _is_managed_canon_dest("/home/agent/canon/COLLECTION.md")
    assert not _is_managed_canon_dest("/home/agent/canon-of-mine")
    assert not _is_managed_canon_dest("/home/agent/canonical")
    assert not _is_managed_canon_dest("/home/agent/workspace")

  def test_extra_dir_mount_under_home(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    assert (shell / "comms").is_dir()

  def test_extra_file_mount_under_home(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    assert (shell / ".local" / "bin").is_dir()
    assert (shell / ".local" / "bin" / "claude").is_file()

  def test_extra_mount_under_workspace(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    assert (project / "vault").is_dir()

  def test_mount_outside_home_skipped(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    # No dirs created under shell or project for /opt/ mounts
    assert list(shell.iterdir()) == [shell / "workspace"]

  def test_existing_file_not_overwritten(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    # File stub should NOT overwrite existing content
    assert existing.read_text() == "existing content"

  def test_oserror_is_swallowed(self, tmp_path):
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

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
        shell,
        project,
        mounts,
        enable_vault=False,
        vault_ro_path=tmp_path / "x",
        vault_rw_path=tmp_path / "y",
        tmpfs_masks=[],
      )
    finally:
      shell.chmod(0o755)

  def test_symlink_file_dest_cleared_to_real_mountpoint(self, tmp_path):
    """A baked symlink at a file dest is cleared so the bind lands clean.

    Mirrors a dirty image where ~/.local/bin/claude is a symlink into the
    install-dir subtree; the destination must become a real, non-symlink
    mountpoint before the bind.
    """
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

    @dataclass
    class FakeMount:
      source: Path
      destination: str
      options: str = ""

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # Pre-existing dest symlink (as a baked image would ship). The target is
    # under tmp_path so the link is live on every host, not only where that
    # absolute path happens to exist.
    bin_dir = shell / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    version_dir = tmp_path / "share" / "claude" / "versions" / "2.1.177"
    version_dir.mkdir(parents=True)
    link = bin_dir / "claude"
    link.symlink_to(version_dir)
    assert link.is_symlink()

    src_file = tmp_path / "claude-launcher"
    src_file.touch()
    mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/claude")]
    _precreate_mount_stubs(
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    # Symlink cleared; dest is now a real, non-symlink file mountpoint.
    assert not link.is_symlink()
    assert link.is_file()

  def test_symlink_dir_dest_cleared_to_real_mountpoint(self, tmp_path):
    """A baked symlink at a dir dest is cleared to a real directory."""
    from dataclasses import dataclass
    from kanibako.runtime.container import _precreate_mount_stubs

    @dataclass
    class FakeMount:
      source: Path
      destination: str
      options: str = ""

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    share_parent = shell / ".local" / "share"
    share_parent.mkdir(parents=True)
    link = share_parent / "claude"
    target_dir = tmp_path / "real-elsewhere"
    target_dir.mkdir()
    link.symlink_to(target_dir)
    assert link.is_symlink()

    src_dir = tmp_path / "share-src"
    src_dir.mkdir()
    mounts = [FakeMount(source=src_dir, destination="/home/agent/.local/share/claude")]
    _precreate_mount_stubs(
      shell,
      project,
      mounts,
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    assert not link.is_symlink()
    assert link.is_dir()

  # --- Parent-dir traversal loosening (crun openat2 needs +x on every parent
  # of a bind dest; pre-existing box homes ship XDG dirs like ~/.config at
  # 0700, which crun cannot traverse -> exit-126 launch death). ---

  @staticmethod
  def _mount(destination, source):
    from dataclasses import dataclass

    @dataclass
    class FakeMount:
      source: Path
      destination: str
      options: str = ""

    return FakeMount(source=source, destination=destination)

  def test_private_parent_loosened_for_file_bind(self, tmp_path):
    """A file bind under a pre-existing 0700 parent -> parent becomes 0711."""
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # Pre-existing private XDG dir (as gh/podman/XDG tools leave it).
    cfg = shell / ".config"
    cfg.mkdir()
    cfg.chmod(0o700)
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    _precreate_mount_stubs(
      shell,
      project,
      [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    import stat as _stat

    assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
    assert (shell / ".config" / "kanibako" / "kickoff.md").is_file()

  def test_multi_level_private_parents_all_loosened(self, tmp_path):
    """Both a 0700 grandparent and 0700 parent on the bind path loosen."""
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = shell / ".config"
    cfg.mkdir()
    sub = cfg / "kanibako"
    sub.mkdir()
    cfg.chmod(0o700)
    sub.chmod(0o700)
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    _precreate_mount_stubs(
      shell,
      project,
      [self._mount("/home/agent/.config/kanibako/seed/kickoff.md", src_file)],
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    import stat as _stat

    assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
    assert _stat.S_IMODE(sub.stat().st_mode) == 0o711
    assert (shell / ".config" / "kanibako" / "seed" / "kickoff.md").is_file()

  def test_private_dir_off_bind_path_untouched(self, tmp_path):
    """A 0700 dir with NO bind under it (e.g. ~/.ssh) stays 0700."""
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    ssh = shell / ".ssh"
    ssh.mkdir()
    ssh.chmod(0o700)
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    _precreate_mount_stubs(
      shell,
      project,
      [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    import stat as _stat

    # Off the bind path -> never visited -> mode preserved.
    assert _stat.S_IMODE(ssh.stat().st_mode) == 0o700

  def test_shell_path_root_never_chmodded(self, tmp_path):
    """shell_path itself is off-limits: it stays 0700 even as a bind root."""
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = shell / ".config"
    cfg.mkdir()
    cfg.chmod(0o700)
    shell.chmod(0o700)
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    try:
      _precreate_mount_stubs(
        shell,
        project,
        [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
        enable_vault=False,
        vault_ro_path=tmp_path / "x",
        vault_rw_path=tmp_path / "y",
        tmpfs_masks=[],
      )
      import stat as _stat

      # The walk stops AT the root; root's own mode is untouched.
      assert _stat.S_IMODE(shell.stat().st_mode) == 0o700
      # ...while a private parent BELOW the root is still loosened.
      assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
    finally:
      shell.chmod(0o755)

  def test_symlinked_parent_stops_walk_target_untouched(self, tmp_path):
    """A symlinked parent halts the walk; the symlink target is not chmodded."""
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # A private dir the .config symlink points at, INSIDE the box home.
    target = shell / "realtarget"
    target.mkdir()
    target.chmod(0o700)
    link = shell / ".config"
    link.symlink_to("realtarget")
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    _precreate_mount_stubs(
      shell,
      project,
      [self._mount("/home/agent/.config/kickoff.md", src_file)],
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    import stat as _stat

    # Walk stops at the symlinked parent -> the target dir keeps its 0700 mode.
    assert link.is_symlink()
    assert _stat.S_IMODE(target.stat().st_mode) == 0o700

  def test_loosening_logged_at_info(self, tmp_path, caplog):
    """The permission change is discoverable in the logs at INFO."""
    import logging

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = shell / ".config"
    cfg.mkdir()
    cfg.chmod(0o700)
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    with caplog.at_level(logging.INFO, logger="kanibako.container"):
      _precreate_mount_stubs(
        shell,
        project,
        [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
        enable_vault=False,
        vault_ro_path=tmp_path / "x",
        vault_rw_path=tmp_path / "y",
        tmpfs_masks=[],
      )
    assert any(
      "loosened box-home dir for bind traversal" in r.getMessage() for r in caplog.records
    )

  def test_already_traversable_parent_not_rechmodded(self, tmp_path, caplog):
    """A parent that already has search bits is left alone: no chmod, no log.

    Guards the ``perm & 0o011 != 0o011`` idempotence gate — a fresh-box
    0755 XDG dir must not be spuriously re-chmodded (and mode stays 0755, not
    widened) nor emit the loosen INFO line.
    """
    import logging

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = shell / ".config"
    cfg.mkdir()
    cfg.chmod(0o755)  # already traversable (fresh-box mkdir default).
    src_file = tmp_path / "kickoff-src"
    src_file.touch()
    from kanibako.runtime.container import _precreate_mount_stubs

    with caplog.at_level(logging.INFO, logger="kanibako.container"):
      _precreate_mount_stubs(
        shell,
        project,
        [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
        enable_vault=False,
        vault_ro_path=tmp_path / "x",
        vault_rw_path=tmp_path / "y",
        tmpfs_masks=[],
      )
    import stat as _stat

    # Mode is untouched (not widened past its existing search bits)...
    assert _stat.S_IMODE(cfg.stat().st_mode) == 0o755
    # ...and no loosen was logged, since nothing was chmodded.
    assert not any(
      "loosened box-home dir for bind traversal" in r.getMessage() for r in caplog.records
    )

  def test_dir_bind_under_private_parent_loosened(self, tmp_path):
    """A DIR bind (not just a file bind) under a 0700 parent loosens it too.

    ``_ensure_dir`` and ``_ensure_file`` share the loosen call, but the dir
    branch is otherwise untested; a dir-source extra mount must also make its
    private parent traversable.
    """
    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    cfg = shell / ".config"
    cfg.mkdir()
    cfg.chmod(0o700)
    src_dir = tmp_path / "seed-dir"
    src_dir.mkdir()
    from kanibako.runtime.container import _precreate_mount_stubs

    _precreate_mount_stubs(
      shell,
      project,
      [self._mount("/home/agent/.config/kanibako/seed", src_dir)],
      enable_vault=False,
      vault_ro_path=tmp_path / "x",
      vault_rw_path=tmp_path / "y",
      tmpfs_masks=[],
    )
    import stat as _stat

    assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
    assert (shell / ".config" / "kanibako" / "seed").is_dir()


class TestDetectShadowedMounts:
  """Test detect_shadowed_mounts: pure detection of binds shadowing content."""

  @staticmethod
  def _mount(source, destination, options=""):
    from kanibako.targets.base import Mount

    return Mount(source=source, destination=destination, options=options)

  def test_a_bind_in_place_shadows_nothing(self, tmp_path):
    """``~/canon`` and its rw books bind their OWN host stub: the content shown is
    the content there, so none is reported; another source at the same dest is."""
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    notebook = shell / "canon" / "notebook"
    notebook.mkdir(parents=True)
    (notebook / "n.md").write_text("mine")
    project = tmp_path / "project"
    project.mkdir()
    in_place = [
      self._mount(shell / "canon", "/home/agent/canon", "ro"),
      self._mount(notebook, "/home/agent/canon/notebook"),
    ]
    assert detect_shadowed_mounts(shell, project, in_place, enable_vault=False) == []
    elsewhere = [self._mount(tmp_path / "other", "/home/agent/canon/notebook")]
    assert detect_shadowed_mounts(shell, project, elsewhere, enable_vault=False) == [
      "/home/agent/canon/notebook"
    ]

  def test_vault_dir_shadow(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # rw vault stub has content; ro is empty (not stubbed at all).
    (shell / "vault" / "rw").mkdir(parents=True)
    (shell / "vault" / "rw" / "somefile").write_text("data")
    result = detect_shadowed_mounts(shell, project, None, enable_vault=True)
    assert "/home/agent/vault/rw" in result
    assert "/home/agent/vault/ro" not in result

  def test_empty_first_launch(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    result = detect_shadowed_mounts(shell, project, None, enable_vault=True)
    assert result == []

  def test_file_dest_shadow_nonempty_reported(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    host_stub = shell / ".local" / "bin" / "foo"
    host_stub.parent.mkdir(parents=True)
    host_stub.write_text("x")  # >0 bytes
    src = tmp_path / "src-foo"
    src.touch()
    mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert "/home/agent/.local/bin/foo" in result

  def test_file_dest_zero_byte_not_reported(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    host_stub = shell / ".local" / "bin" / "foo"
    host_stub.parent.mkdir(parents=True)
    host_stub.touch()  # 0 bytes
    src = tmp_path / "src-foo"
    src.touch()
    mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_base_roots_excluded(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # Give the host home + workspace bases content; they must NOT be warned.
    (shell / "existing").write_text("home content")
    (project / "existing").write_text("workspace content")
    src = tmp_path / "src"
    src.mkdir()
    mounts = [
      self._mount(src, "/home/agent"),
      self._mount(src, "/home/agent/workspace"),
    ]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_symlink_skipped(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # Host stub is a symlink (a precreate-cleared stub, not user content).
    bin_dir = shell / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    link = bin_dir / "foo"
    target = tmp_path / "elsewhere"
    target.write_text("data")
    link.symlink_to(target)
    src = tmp_path / "src-foo"
    src.touch()
    mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_pure_no_paths_created(self, tmp_path):
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    src = tmp_path / "src-foo"
    src.touch()
    mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
    before = set(shell.rglob("*")) | set(project.rglob("*"))
    detect_shadowed_mounts(shell, project, mounts, enable_vault=True)
    after = set(shell.rglob("*")) | set(project.rglob("*"))
    # No mkdir/touch: the candidate stub stays non-existent.
    assert not (shell / ".local" / "bin" / "foo").exists()
    assert not (shell / "vault").exists()
    assert before == after

  def test_managed_canon_charter_excluded(self, tmp_path):
    """Finding #7: the box-create skeleton owns canon/charter — never report it shadowed."""
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    chapter = shell / "canon" / "charter" / "general"
    chapter.mkdir(parents=True)
    (chapter / "existing.md").write_text("pre-existing skeleton content")
    src = tmp_path / "src-general"
    src.mkdir()
    mounts = [self._mount(src, "/home/agent/canon/charter/general")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_managed_canon_handbook_excluded(self, tmp_path):
    """Same guard, handbook side (canon/handbook is skeleton-owned, not seed-owned)."""
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    chapter = shell / "canon" / "handbook" / "box"
    chapter.mkdir(parents=True)
    (chapter / "existing.md").write_text("pre-existing skeleton content")
    src = tmp_path / "src-box"
    src.mkdir()
    mounts = [self._mount(src, "/home/agent/canon/handbook/box")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_managed_canon_collection_file_excluded(self, tmp_path):
    """The single-file managed dest (canon/COLLECTION.md) is excluded too."""
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (shell / "canon").mkdir()
    host_stub = shell / "canon" / "COLLECTION.md"
    host_stub.write_text("pre-existing skeleton content")
    src = tmp_path / "src-collection"
    src.touch()
    mounts = [self._mount(src, "/home/agent/canon/COLLECTION.md")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert result == []

  def test_canon_notebook_still_reported(self, tmp_path):
    """MUTATION PROOF (other direction): canon/notebook stays SEEDABLE (spec §2c), so a
    genuine shadow there is real user content and must still be reported — this is what
    would break if the exclusion were widened to all of ``~/canon`` instead of the
    seed-deny prefixes.
    """
    from kanibako.runtime.container import detect_shadowed_mounts

    shell = tmp_path / "shell"
    shell.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    notebook = shell / "canon" / "notebook"
    notebook.mkdir(parents=True)
    (notebook / "existing.md").write_text("real user content")
    src = tmp_path / "src-notebook"
    src.mkdir()
    mounts = [self._mount(src, "/home/agent/canon/notebook")]
    result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
    assert "/home/agent/canon/notebook" in result


class TestLocalImageMetadata:
  """get_local_created / get_local_tags / get_local_label (via image_inspect)."""

  @staticmethod
  def _inspect(data):
    import json
    from unittest.mock import MagicMock

    m = MagicMock(returncode=0, stdout=json.dumps([data]))
    return patch("kanibako.runtime.container.subprocess.run", return_value=m)

  def test_get_local_created(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"Created": "2026-06-01T00:00:00Z"}):
      assert rt.get_local_created("img:latest") == "2026-06-01T00:00:00Z"

  def test_get_local_created_missing(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({}):
      assert rt.get_local_created("img:latest") is None

  def test_get_local_created_empty_string(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"Created": ""}):
      assert rt.get_local_created("img:latest") is None

  def test_get_local_created_inspect_fail(self):
    from unittest.mock import MagicMock

    rt = ContainerRuntime(command="echo")
    with patch(
      "kanibako.runtime.container.subprocess.run",
      return_value=MagicMock(returncode=1, stdout=""),
    ):
      assert rt.get_local_created("img:latest") is None

  def test_get_local_tags(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"RepoTags": ["img:latest", "img:1.6.0"]}):
      assert rt.get_local_tags("img:latest") == ["img:latest", "img:1.6.0"]

  def test_get_local_tags_none(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"RepoTags": None}):
      assert rt.get_local_tags("img:latest") == []

  def test_get_local_label_from_config(self):
    rt = ContainerRuntime(command="echo")
    data = {"Config": {"Labels": {"org.opencontainers.image.version": "1.6.0"}}}
    with self._inspect(data):
      assert rt.get_local_label("img:latest", "org.opencontainers.image.version") == "1.6.0"

  def test_get_local_label_top_level_fallback(self):
    rt = ContainerRuntime(command="echo")
    data = {"Config": {}, "Labels": {"x": "y"}}
    with self._inspect(data):
      assert rt.get_local_label("img:latest", "x") == "y"

  def test_get_local_label_absent(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"Config": {"Labels": {}}}):
      assert rt.get_local_label("img:latest", "nope") is None

  def test_get_local_label_no_labels(self):
    rt = ContainerRuntime(command="echo")
    with self._inspect({"Config": {}}):
      assert rt.get_local_label("img:latest", "x") is None


class TestGuestDestToHost:
  """The single guest_dest -> host-path translator (audit P3 unification).

  Equivalence: the shared ``_guest_dest_to_host`` returns the SAME host path as
  each of the three former sites for the four cases, plus the ``map_home_root``
  fork (mount-stub callers vs seed/synced COPY callers) and the ``/workspace``
  split the copy callers now inherit.
  """

  def _paths(self):
    shell = Path("/host/shell")
    project = Path("/host/project")
    return shell, project

  def test_home_root_mount_callers_return_none(self):
    """map_home_root=False (mount stub/shadow callers): bare home -> None.

    Byte-identical to the former ``_mount_dest_to_host``: the base home bind
    is not a stub to pre-create.
    """
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    assert _guest_dest_to_host("/home/agent", shell, project) is None
    assert _guest_dest_to_host("/home/agent/", shell, project) == shell

  def test_home_root_copy_callers_map_to_shell(self):
    """map_home_root=True (seed/synced COPY callers): bare home -> shell root.

    Byte-identical to the former ``_host_dest`` / inline synced branch.
    """
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    assert _guest_dest_to_host("/home/agent", shell, project, map_home_root=True) == shell
    assert _guest_dest_to_host("/home/agent/", shell, project, map_home_root=True) == shell

  def test_home_subpath_maps_under_shell(self):
    """A ~/x dest maps under shell_path in every mode (all three former sites
    agreed)."""
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    for kw in ({}, {"map_home_root": True}):
      assert _guest_dest_to_host("/home/agent/.claude", shell, project, **kw) == shell / ".claude"

  def test_workspace_subpath_maps_under_project(self):
    """A ~/workspace/x dest maps under project_path (the workspace bind).

    The canonical mount behavior the COPY callers now inherit (P3 fix): both
    modes route ~/workspace/x to project_path/x, NOT the shadowed
    shell_path/workspace/x.
    """
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    for kw in ({}, {"map_home_root": True}):
      assert (
        _guest_dest_to_host("/home/agent/workspace/proj/f", shell, project, **kw)
        == project / "proj" / "f"
      )

  def test_outside_home_returns_none(self):
    """A dest outside the box home returns None (the skip case)."""
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    assert _guest_dest_to_host("/etc/passwd", shell, project) is None
    assert _guest_dest_to_host("/etc/passwd", shell, project, map_home_root=True) is None

  def test_workspace_prefix_keeps_its_separator(self):
    """The workspace prefix is SLASH-TERMINATED — the keyspec separator guard.

    Regression guard for the trailing slash on the ``GUEST_WORKSPACE`` prefix
    inside ``_guest_dest_to_host``.  A slashless prefix breaks BOTH ways: the
    BARE workspace dest stops falling through to the ``agent_home`` branch and
    maps to ``project_path / ""``, and a sibling whose name merely STARTS with
    ``workspace`` newly matches — the very case keyspec :120-121 forbids
    (``~/foobar`` is not inside ``~/foo``).
    """
    from kanibako.runtime.container import _guest_dest_to_host

    shell, project = self._paths()
    for kw in ({}, {"map_home_root": True}):
      # Bare workspace dest: the home branch, NOT project_path itself.
      assert (
        _guest_dest_to_host("/home/agent/workspace", shell, project, **kw) == shell / "workspace"
      )
      # Sibling sharing the prefix: home branch, NOT project_path / "foo".
      assert (
        _guest_dest_to_host("/home/agent/workspacefoo", shell, project, **kw)
        == shell / "workspacefoo"
      )
      assert (
        _guest_dest_to_host("/home/agent/workspacefoo/x", shell, project, **kw)
        == shell / "workspacefoo" / "x"
      )


class TestSymlinkedParentRefusal:
  """A mount dest nested under a symlinked parent is refused, not stubbed.

  ⚑ Every link target here is built under ``tmp_path``, so no test turns on
  whether some host happens to have a directory at a fixed absolute path.
  """

  GUEST_DEST = "/home/agent/canon/notebook"
  GUEST_WS_DEST = "/home/agent/workspace/canon/notebook"

  @staticmethod
  def _roots(tmp_path):
    """A home, a project dir and a source dir, with the workspace stub parent."""
    shell = tmp_path / "shell"
    project = tmp_path / "project"
    src = tmp_path / "src"
    for d in (shell, project, src):
      d.mkdir()
    (shell / "workspace").mkdir()  # GUEST_WORKSPACE_RELPATH exists, as on a real box
    return shell, project, src

  @staticmethod
  def _stubs(shell, project, src, dest):
    from kanibako.runtime.container import _precreate_mount_stubs
    from kanibako.targets.base import Mount

    _precreate_mount_stubs(
      shell,
      project,
      [Mount(source=src, destination=dest, options="")],
      enable_vault=False,
      vault_ro_path=shell / "no-ro",
      vault_rw_path=shell / "no-rw",
      tmpfs_masks=[],
    )

  def test_relative_dangling_symlinked_parent_refused_naming_parent_and_target(
    self,
    tmp_path,
  ):
    """A RELATIVE dangling parent resolving inside the home refuses, naming both.

    The stub's ``mkdir`` used to fail on the LINK, which was logged at debug and
    swallowed, so the launch continued and failed later with a podman error naming
    neither the link nor the destination.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "canon").symlink_to("workspace/wiki")  # absent, inside the home

    with pytest.raises(ContainerError) as exc:
      self._stubs(shell, project, src, self.GUEST_DEST)

    message = str(exc.value)
    assert str(shell / "canon") in message
    assert "workspace/wiki" in message
    # Nothing was created: the refusal is read-only.
    assert (shell / "canon").is_symlink()
    assert not (shell / "canon" / "notebook").exists()

  def test_absolute_dangling_symlinked_parent_inside_the_box_home_refused(self, tmp_path):
    """An ABSOLUTE dangling parent inside the home refuses, like the relative one."""
    shell, project, src = self._roots(tmp_path)
    (shell / "canon").symlink_to(shell / "realtarget")  # absolute, absent, inside

    with pytest.raises(ContainerError) as exc:
      self._stubs(shell, project, src, self.GUEST_DEST)

    message = str(exc.value)
    assert str(shell / "canon") in message
    assert str(shell / "realtarget") in message
    assert (shell / "canon").is_symlink()

  def test_workspace_side_dangling_parent_refused_by_the_project_directory(self, tmp_path):
    """A workspace-side dangling parent refuses, and is named as the project dir.

    The project directory is the OTHER root and the OTHER noun: calling it the box
    home named a root the bind was never under.
    """
    shell, project, src = self._roots(tmp_path)
    (project / "notes").mkdir()
    (project / "canon").symlink_to("notes/wiki")  # absent, inside the project dir

    with pytest.raises(ContainerError) as exc:
      self._stubs(shell, project, src, self.GUEST_WS_DEST)

    message = str(exc.value)
    assert f"the project directory is {project}" in message
    assert "box home" not in message
    assert (project / "canon").is_symlink()
    assert not (project / "canon" / "notebook").exists()

  def test_dangling_link_deeper_in_a_chain_refused_at_the_nearest_link(self, tmp_path):
    """A live link whose own target is dangling is refused, named at the NEAREST link.

    Only the first link on the way up is judged, so the message names the one the
    user has to replace.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "canon").symlink_to(shell / "b")
    (shell / "b").symlink_to(shell / "gone")  # live link, dangling resolution

    with pytest.raises(ContainerError) as exc:
      self._stubs(shell, project, src, self.GUEST_DEST)

    message = str(exc.value)
    assert str(shell / "canon") in message
    assert str(shell / "b") in message
    assert not (shell / "b" / "notebook").exists()

  def test_a_live_link_is_stubbed_where_a_dangling_one_is_refused(self, tmp_path):
    """Liveness is the only difference: the same shape, one link live, one not.

    A link resolving inside the box home puts the stub where the box home owns it,
    so it is stubbed. The refusal is about a link with nowhere to put it.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "realtarget").mkdir()
    (shell / "canon").symlink_to(shell / "realtarget")
    (shell / "dangle").symlink_to(shell / "nowhere")

    self._stubs(shell, project, src, self.GUEST_DEST)
    assert (shell / "realtarget" / "notebook").is_dir()

    with pytest.raises(ContainerError):
      self._stubs(shell, project, src, "/home/agent/dangle/notebook")

  def test_symlinked_parent_leading_outside_the_box_home_refused(self, tmp_path):
    """A live link that leaves the box home is refused, naming the box home."""
    shell, project, src = self._roots(tmp_path)
    (project / "realtarget").mkdir()
    (shell / "canon").symlink_to(project / "realtarget")

    with pytest.raises(ContainerError, match="is a symlink to") as exc:
      self._stubs(shell, project, src, self.GUEST_DEST)

    assert f"the box home is {shell}" in str(exc.value)
    assert (shell / "canon").is_symlink()

  def test_symlinked_parent_inside_the_box_home_is_not_refused(self, tmp_path):
    """A symlinked parent that still lands inside the box home is stubbed.

    The refusal is about WHERE the bind lands. A link resolving inside the
    box home puts the stub where the box home owns it, so it is stubbed —
    and ``_loosen_parents`` still stops at the link rather than chmodding
    through it.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "realtarget").mkdir()
    (shell / "canon").symlink_to(shell / "realtarget")

    self._stubs(shell, project, src, self.GUEST_DEST)

    assert (shell / "canon").is_symlink()
    assert (shell / "realtarget" / "notebook").is_dir()

  def test_symlink_at_the_dest_itself_is_still_cleared(self, tmp_path):
    """A symlink AT the dest is _clear_symlink's to remove, so it is not refused.

    The walk starts at the dest's PARENT, so a dangling link AT the dest is never
    judged — it is removed and the dir is made in its place.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "canon").symlink_to(shell / "gone")

    self._stubs(shell, project, src, "/home/agent/canon")

    assert (shell / "canon").is_dir()
    assert not (shell / "canon").is_symlink()

  def test_real_parents_are_stubbed(self, tmp_path):
    """A dest whose parents are real directories is stubbed as before."""
    shell, project, src = self._roots(tmp_path)

    self._stubs(shell, project, src, self.GUEST_DEST)

    assert (shell / "canon" / "notebook").is_dir()

  def test_run_refuses_a_dangling_symlinked_parent(self, tmp_path):
    """The production caller refuses too, so the launch never reaches podman."""
    from kanibako.runtime.container import ContainerRuntime
    from kanibako.targets.base import Mount

    shell, project, src = self._roots(tmp_path)
    (shell / "canon").symlink_to("workspace/wiki")
    rt = ContainerRuntime(command="/bin/true")

    with patch("kanibako.runtime.container.subprocess.run") as launched:
      with pytest.raises(ContainerError, match="is a symlink to"):
        rt.run(
          "img",
          shell_path=shell,
          project_path=project,
          vault_ro_path=None,
          vault_rw_path=None,
          extra_mounts=[Mount(source=src, destination=self.GUEST_DEST, options="")],
          tmpfs_masks=[],
          enable_vault=False,
        )

    launched.assert_not_called()
    assert not (shell / "canon" / "notebook").exists()

  def test_grandparent_symlink_leading_outside_the_box_home_refused(self, tmp_path):
    """A symlink ABOVE the immediate parent is refused, and nothing is written out.

    The walk is by path position, so a link at any depth between the stub and the
    root is judged. A link at the GRANDPARENT used to be reached by no refusal at
    all: the stub was then created under the link's target, outside the home.
    """
    shell, project, src = self._roots(tmp_path)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (shell / "dl").symlink_to(outside)  # the GRANDPARATH of dl/sub/notebook

    with pytest.raises(ContainerError, match="is a symlink to") as exc:
      self._stubs(shell, project, src, "/home/agent/dl/sub/notebook")

    message = str(exc.value)
    assert str(shell / "dl") in message
    assert str(outside) in message
    assert f"the box home is {shell}" in message
    # The refusal is read-only, and the write it prevents is the host path.
    assert (shell / "dl").is_symlink()
    assert not (outside / "sub" / "notebook").exists()

  def test_grandparent_dangling_symlink_outside_the_box_home_refused(self, tmp_path):
    """A grandparent link whose target is ABSENT is refused on the same terms.

    Its stub had nowhere to go, so ``mkdir`` failed on the link itself at
    ``[Errno 17]``, which the stub helpers log at debug and swallow — the launch
    then failed naming neither the link nor the destination.
    """
    shell, project, src = self._roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (shell / "dl").symlink_to(outside / "gone")  # absolute, absent, outside

    with pytest.raises(ContainerError, match="does not resolve to an existing directory") as exc:
      self._stubs(shell, project, src, "/home/agent/dl/sub/notebook")

    assert str(shell / "dl") in str(exc.value)
    assert (shell / "dl").is_symlink()

  def test_symlinked_parent_to_a_regular_file_refused(self, tmp_path):
    """A symlink pointing at a FILE is refused like a dangling one.

    A file is not a directory a stub can be made under, so ``mkdir`` failed on it
    at ``[Errno 20]``, logged at debug and swallowed.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "afile").write_text("x")
    (shell / "canon").symlink_to("afile")

    with pytest.raises(ContainerError, match="does not resolve to an existing directory") as exc:
      self._stubs(shell, project, src, self.GUEST_DEST)

    assert str(shell / "canon") in str(exc.value)
    assert (shell / "canon").is_symlink()

  def test_grandparent_symlink_inside_the_box_home_is_not_refused(self, tmp_path):
    """A grandparent link that still lands inside the home is stubbed.

    The walk is by path position, so it now judges links at every depth; only
    WHERE the link lands decides. A live in-home link is stubbed where it points.
    """
    shell, project, src = self._roots(tmp_path)
    (shell / "realtarget" / "sub").mkdir(parents=True)
    (shell / "dl").symlink_to(shell / "realtarget")

    self._stubs(shell, project, src, "/home/agent/dl/sub/notebook")

    assert (shell / "dl").is_symlink()
    assert (shell / "realtarget" / "sub" / "notebook").is_dir()

  def test_run_refuses_a_grandparent_symlinked_parent_leading_outside(self, tmp_path):
    """The production caller refuses a grandparent link too, before podman runs."""
    from kanibako.runtime.container import ContainerRuntime
    from kanibako.targets.base import Mount

    shell, project, src = self._roots(tmp_path)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (shell / "dl").symlink_to(outside)
    rt = ContainerRuntime(command="/bin/true")

    with patch("kanibako.runtime.container.subprocess.run") as launched:
      with pytest.raises(ContainerError, match="is a symlink to"):
        rt.run(
          "img",
          shell_path=shell,
          project_path=project,
          vault_ro_path=None,
          vault_rw_path=None,
          extra_mounts=[Mount(source=src, destination="/home/agent/dl/sub/notebook", options="")],
          tmpfs_masks=[],
          enable_vault=False,
        )

    launched.assert_not_called()
    assert not (outside / "sub" / "notebook").exists()


def _credential_engine(tmp_path, body):
  import sys
  import textwrap

  executable = tmp_path / "engine"
  executable.write_text(
    f"#!{sys.executable}\n"
    "import hashlib, json, os, signal, sys, time\n"
    "if sys.argv[1] != 'run':\n  raise SystemExit(0)\n" + textwrap.dedent(body)
  )
  executable.chmod(0o755)
  return ContainerRuntime(command=str(executable))


def _credential_run(runtime, tmp_path, payload, **options):
  return runtime.run(
    "image",
    shell_path=tmp_path / "home",
    project_path=tmp_path / "work",
    vault_ro_path=None,
    vault_rw_path=None,
    enable_vault=False,
    secret_payload=payload,
    **options,
  )


class TestCredentialRuntime:
  @pytest.mark.parametrize("detach", [False, True])
  def test_empty_payload_is_identical_to_native_calls(self, tmp_path, detach):
    from subprocess import CompletedProcess
    from kanibako.secret_delivery import SecretPayload

    runtime = ContainerRuntime(command="engine")
    results = []
    for payload in (None, SecretPayload()):
      with (
        patch("kanibako.runtime.container.subprocess.run") as run,
        patch("kanibako.runtime.container.subprocess.Popen") as popen,
      ):
        run.return_value = CompletedProcess([], 17, stdout="", stderr="")
        assert (
          _credential_run(
            runtime,
            tmp_path,
            payload,
            detach=detach,
            labels={"box": "unchanged"},
            publication_timeout=-1,
          )
          == 17
        )
        results.append(run.call_args)
        popen.assert_not_called()
    assert results[0] == results[1]
    assert "--preserve-fds=2" not in results[0].args[0]

  @pytest.mark.parametrize("detach", [False, True])
  @pytest.mark.parametrize(
    "value", [b"", b"secret\0\n'\xff", b"x" * (1024 * 1024)], ids=["empty", "binary", "large"]
  )
  def test_real_pipe_complete_frame_ack_and_label_copy(self, tmp_path, detach, value):
    import hashlib
    from kanibako.secret_delivery import SecretPayload, SecretValue, encode_payload

    payload = SecretPayload((SecretValue("TOKEN", value),))
    digest = hashlib.sha256(encode_payload(payload)).hexdigest()
    runtime = _credential_engine(
      tmp_path,
      f"""
      assert '--preserve-fds=2' in sys.argv
      data = bytearray()
      while True:
        chunk = os.read(3, 997)
        if not chunk:
          break
        data.extend(chunk)
      assert hashlib.sha256(data).hexdigest() == {digest!r}
      os.write(4, b'R')
    """,
    )
    labels = {"box": "keep"}
    assert _credential_run(runtime, tmp_path, payload, detach=detach, labels=labels) == 0
    assert labels == {"box": "keep"}

  def test_nonce_is_fresh_and_never_mutates_caller_labels(self, tmp_path):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    labels = {"org.kanibako.credential-launch": "caller", "box": "keep"}
    runtime = ContainerRuntime(command="engine")
    payload = SecretPayload((SecretValue("TOKEN", b"opaque"),))
    with patch.object(runtime, "_run_with_secrets", return_value=0) as launch:
      for _ in range(2):
        assert _credential_run(runtime, tmp_path, payload, labels=labels) == 0
    first, second = [call.args for call in launch.call_args_list]
    assert first[2] != second[2]
    for arguments in (first, second):
      assert f"org.kanibako.credential-launch={arguments[2]}" in arguments[0]
      assert "opaque" not in repr(arguments)
      assert "org.kanibako.credential-launch=caller" not in arguments[0]
    assert labels == {"org.kanibako.credential-launch": "caller", "box": "keep"}

  def test_detached_failure_discards_even_frame_echoes(self, tmp_path, capfd, caplog):
    import logging
    from kanibako.secret_delivery import SecretPayload, SecretValue

    caplog.set_level(logging.DEBUG, logger="kanibako.container")
    runtime = _credential_engine(
      tmp_path,
      """
      data = bytearray()
      while True:
        piece = os.read(3, 65536)
        if not piece:
          break
        data.extend(piece)
      os.write(1, data)
      os.write(2, data)
      os.write(4, b'X')
    """,
    )
    with pytest.raises(ContainerError) as error:
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"opaque-frame-marker"),)),
        detach=True,
      )
    output = capfd.readouterr()
    assert "opaque-frame-marker" not in output.out + output.err + caplog.text + str(error.value)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None

  def test_partial_nonblocking_writes_and_parent_fd_purity(self, tmp_path):
    import fcntl
    import os
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      """
      while os.read(3, 113):
        time.sleep(0.0001)
      os.write(4, b'R')
    """,
    )
    payload = SecretPayload((SecretValue("TOKEN", b"x" * 131072),))
    original_pipe = os.pipe2
    original_write = os.write
    sentinel = original_pipe(os.O_CLOEXEC)
    flags = {fd: fcntl.fcntl(fd, fcntl.F_GETFD) for fd in sentinel}
    allocated = []
    writes = []

    def sparse_pipe(flags):
      pair = original_pipe(flags)
      sparse = tuple(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 200) for fd in pair)
      for fd in pair:
        os.close(fd)
      allocated.extend(sparse)
      return sparse

    def short_write(fd, value):
      writes.append(len(value))
      if len(writes) % 3 == 0:
        raise BlockingIOError()
      return original_write(fd, value[:37])

    try:
      with (
        patch("kanibako.runtime.container.os.pipe2", side_effect=sparse_pipe),
        patch("kanibako.runtime.container.os.write", side_effect=short_write),
        patch("kanibako.runtime.container.os.dup2", side_effect=AssertionError("parent dup2")),
        patch(
          "kanibako.runtime.container.os.set_inheritable",
          side_effect=AssertionError("parent inheritable"),
        ),
      ):
        assert _credential_run(runtime, tmp_path, payload, publication_timeout=5) == 0
      assert len(writes) > 100
      for fd in allocated:
        with pytest.raises(OSError):
          os.fstat(fd)
      for fd in sentinel:
        assert fcntl.fcntl(fd, fcntl.F_GETFD) == flags[fd]
    finally:
      for fd in sentinel:
        os.close(fd)

  @pytest.mark.parametrize("delay,status", [(0.25, 0), (0.25, 27), (0, 129)])
  def test_foreground_lifetime_and_status_follow_readiness(self, tmp_path, delay, status):
    import time
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      f"""
      while os.read(3, 65536):
        pass
      os.write(4, b'R')
      time.sleep({delay})
      raise SystemExit({status})
    """,
    )
    started = time.monotonic()
    assert (
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"value"),)),
        publication_timeout=0.2,
      )
      == status
    )
    assert time.monotonic() - started >= delay

  @pytest.mark.parametrize(
    "body",
    [
      "os.close(3); time.sleep(0.1)",
      "raise SystemExit(43)",
      "while os.read(3, 65536):\n  pass\nos.close(4)",
      "while os.read(3, 65536):\n  pass\nos.write(4, b'X')",
      "while os.read(3, 65536):\n  pass\nos.write(4, b'RR')",
      "time.sleep(5)",
      "while os.read(3, 65536):\n  pass\ntime.sleep(5)",
    ],
  )
  def test_failed_publication_aborts_and_bounds_cleanup(self, tmp_path, body, caplog):
    import time
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(tmp_path, body)
    started = time.monotonic()
    with pytest.raises(ContainerError, match="Credential publication failed") as error:
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"hidden" * 65536),)),
        publication_timeout=0.2,
        detach=True,
      )
    assert time.monotonic() - started < 2
    assert "hidden" not in str(error.value)
    assert error.value.__context__ is None
    assert "cleanup incomplete" in caplog.text
    assert "hidden" not in caplog.text

  def test_readiness_is_required_even_after_engine_zero(self, tmp_path):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      """
      while os.read(3, 65536):
        pass
      raise SystemExit(0)
    """,
    )
    with pytest.raises(ContainerError, match="Credential publication failed"):
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"value"),)),
        detach=True,
        publication_timeout=0.2,
      )

  def test_detached_ack_does_not_hide_engine_failure(self, tmp_path):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      """
      while os.read(3, 65536):
        pass
      os.write(4, b'R')
      time.sleep(0.03)
      raise SystemExit(31)
    """,
    )
    with pytest.raises(ContainerError, match="Credential publication failed"):
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"value"),)),
        detach=True,
      )

  @pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), None, "bad"])
  def test_bad_deadline_never_spawns(self, tmp_path, timeout):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = ContainerRuntime(command="engine")
    with patch("kanibako.runtime.container.subprocess.Popen") as popen:
      with pytest.raises(ContainerError, match="finite and positive"):
        _credential_run(
          runtime,
          tmp_path,
          SecretPayload((SecretValue("TOKEN", b"value"),)),
          publication_timeout=timeout,
        )
      popen.assert_not_called()

  def test_large_finite_deadline_is_sliced(self, tmp_path):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      """
      while os.read(3, 65536):
        pass
      os.write(4, b'R')
    """,
    )
    assert (
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"value"),)),
        publication_timeout=1e308,
        detach=True,
      )
      == 0
    )

  def test_unrelated_inheritable_fd_closes_and_process_context_survives(self, tmp_path):
    import fcntl
    import os
    import signal
    from kanibako.secret_delivery import SecretPayload, SecretValue

    original = os.open("/dev/null", os.O_RDONLY)
    sentinel = fcntl.fcntl(original, fcntl.F_DUPFD, 300)
    os.close(original)
    expected_mask = sorted(int(sig) for sig in signal.pthread_sigmask(signal.SIG_BLOCK, []))
    runtime = _credential_engine(
      tmp_path,
      f"""
      assert os.getpgrp() == {os.getpgrp()}
      assert os.getsid(0) == {os.getsid(0)}
      assert sorted(int(sig) for sig in signal.pthread_sigmask(signal.SIG_BLOCK, [])) == {expected_mask!r}
      try:
        os.fstat({sentinel})
      except OSError:
        pass
      else:
        raise AssertionError('unrelated descriptor inherited')
      while os.read(3, 65536):
        pass
      os.write(4, b'R')
    """,
    )
    try:
      assert os.get_inheritable(sentinel) is True
      assert (
        _credential_run(
          runtime,
          tmp_path,
          SecretPayload((SecretValue("TOKEN", b"value"),)),
        )
        == 0
      )
      assert os.get_inheritable(sentinel) is True
    finally:
      os.close(sentinel)

  def test_child_trampoline_handles_cross_colliding_sources(self):
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime.container import _SECRET_EXEC

    driver = textwrap.dedent("""
      import fcntl, os, subprocess, sys
      data_read, data_write = os.pipe()
      ack_read, ack_write = os.pipe()
      data = fcntl.fcntl(data_read, fcntl.F_DUPFD_CLOEXEC, 20)
      ack = fcntl.fcntl(ack_write, fcntl.F_DUPFD_CLOEXEC, 20)
      writer = fcntl.fcntl(data_write, fcntl.F_DUPFD_CLOEXEC, 20)
      os.close(data_read)
      os.close(data_write)
      os.close(ack_write)
      os.dup2(data, 4)
      os.dup2(ack, 3)
      os.close(data)
      os.close(ack)
      os.write(writer, b'data')
      os.close(writer)
      child = subprocess.Popen([
        sys.executable, '-I', '-c', sys.argv[1], '4', '3', '3', '4', '0',
        sys.executable, '-I', '-c',
        "import os; assert os.read(3, 5) == b'data'; os.write(4, b'R')",
      ], pass_fds=(3, 4))
      os.close(3)
      os.close(4)
      assert os.read(ack_read, 2) == b'R'
      assert child.wait(timeout=2) == 0
      os.close(ack_read)
    """)
    result = subprocess.run(
      [sys.executable, "-I", "-c", driver, _SECRET_EXEC],
      capture_output=True,
      timeout=3,
    )
    assert result.returncode == 0, result.stderr.decode()

  def test_child_exec_restores_subprocess_sigpipe_default(self, tmp_path):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    executable = tmp_path / "shell-engine"
    executable.write_text(
      "#!/bin/sh\ncat <&3 >/dev/null\nprintf R >&4\nsleep 0.03\nkill -PIPE $$\nexit 43\n"
    )
    executable.chmod(0o755)
    runtime = ContainerRuntime(command=str(executable))
    assert (
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"value"),)),
      )
      == -13
    )

  def test_early_ack_still_waits_for_full_payload_pumping(self, tmp_path):
    import hashlib
    import time
    from kanibako.secret_delivery import SecretPayload, SecretValue, encode_payload

    payload = SecretPayload((SecretValue("TOKEN", b"x" * 131072),))
    digest = hashlib.sha256(encode_payload(payload)).hexdigest()
    runtime = _credential_engine(
      tmp_path,
      f"""
      os.write(4, b'R')
      time.sleep(0.1)
      data = bytearray()
      while True:
        piece = os.read(3, 65536)
        if not piece:
          break
        data.extend(piece)
      assert hashlib.sha256(data).hexdigest() == {digest!r}
    """,
    )
    started = time.monotonic()
    assert _credential_run(runtime, tmp_path, payload, detach=True) == 0
    assert time.monotonic() - started >= 0.1

  def test_failed_launcher_ignoring_term_is_killed_and_reaped(self, tmp_path):
    import subprocess
    import time
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      """
      signal.signal(signal.SIGTERM, signal.SIG_IGN)
      while os.read(3, 65536):
        pass
      os.write(4, b'X')
      time.sleep(10)
    """,
    )
    original = subprocess.Popen
    launchers = []

    def spawn(*arguments, **options):
      process = original(*arguments, **options)
      if "-I" in arguments[0]:
        launchers.append(process)
      return process

    started = time.monotonic()
    with patch("kanibako.runtime.container.subprocess.Popen", side_effect=spawn):
      with pytest.raises(ContainerError):
        _credential_run(
          runtime,
          tmp_path,
          SecretPayload((SecretValue("TOKEN", b"value"),)),
        )
    assert time.monotonic() - started < 2
    assert len(launchers) == 1
    assert launchers[0].returncode == -9

  def test_hanging_real_cleanup_query_is_bounded_and_reaped(self, tmp_path):
    import subprocess
    import sys
    import time
    from kanibako.secret_delivery import SecretPayload, SecretValue

    executable = tmp_path / "slow-query-engine"
    executable.write_text(
      f"#!{sys.executable}\nimport os, sys, time\n"
      "if sys.argv[1] == 'run':\n"
      "  while os.read(3, 65536):\n    pass\n"
      "  os.write(4, b'X')\n"
      "time.sleep(20)\n"
    )
    executable.chmod(0o755)
    runtime = ContainerRuntime(command=str(executable))
    original = subprocess.Popen
    processes = []

    def spawn(*arguments, **options):
      process = original(*arguments, **options)
      processes.append(process)
      return process

    started = time.monotonic()
    with patch("kanibako.runtime.container.subprocess.Popen", side_effect=spawn):
      with pytest.raises(ContainerError):
        _credential_run(
          runtime,
          tmp_path,
          SecretPayload((SecretValue("TOKEN", b"value"),)),
        )
    assert time.monotonic() - started < 7
    assert len(processes) == 2
    assert all(process.poll() is not None for process in processes)

  def test_cancel_closes_all_pipe_ends_and_reaps_launcher(self, tmp_path):
    import os
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(tmp_path, "time.sleep(5)")
    allocated = []
    processes = []
    original_pipe = os.pipe2
    import subprocess

    original_popen = subprocess.Popen

    def pipes(flags):
      pair = original_pipe(flags)
      allocated.extend(pair)
      return pair

    def spawn(*args, **kwargs):
      process = original_popen(*args, **kwargs)
      if "-I" in args[0]:
        processes.append(process)
      return process

    with (
      patch("kanibako.runtime.container.os.pipe2", side_effect=pipes),
      patch("kanibako.runtime.container.subprocess.Popen", side_effect=spawn),
      patch(
        "kanibako.runtime.container.selectors.EpollSelector.select", side_effect=KeyboardInterrupt
      ),
    ):
      with pytest.raises(KeyboardInterrupt):
        _credential_run(
          runtime,
          tmp_path,
          SecretPayload((SecretValue("TOKEN", b"value"),)),
        )
    assert len(processes) == 1
    assert processes[0].poll() is not None
    for fd in allocated:
      with pytest.raises(OSError):
        os.fstat(fd)


class TestCredentialOwnedCleanup:
  @pytest.mark.parametrize("mismatch", ["id", "nonce", "invalid", "failed", "missing"])
  def test_unverified_container_is_never_removed(self, mismatch):
    import json
    from subprocess import CompletedProcess

    runtime = ContainerRuntime(command="engine")
    candidate = "a" * 64
    nonce = "launch"

    def respond(cmd, **options):
      assert 0 < options["timeout"] <= 5
      if cmd[1] == "ps":
        return CompletedProcess(cmd, 0, stdout=candidate + "\n", stderr="")
      assert cmd[1] == "inspect"
      identifier = "b" * 64 if mismatch == "id" else candidate
      labels = {"org.kanibako.credential-launch": "other" if mismatch == "nonce" else nonce}
      result = json.dumps([identifier, labels])
      if mismatch == "invalid":
        result = "not json"
      if mismatch == "missing":
        result = json.dumps([identifier, {}])
      return CompletedProcess(cmd, 1 if mismatch == "failed" else 0, stdout=result, stderr="")

    with patch("kanibako.runtime.container.subprocess.run", side_effect=respond) as run:
      assert runtime._cancel_secret_launch(None, nonce) is False
    assert [call.args[0][1] for call in run.call_args_list] == ["ps", "inspect"]

  def test_name_reuse_cannot_redirect_force_remove(self):
    import json
    from subprocess import CompletedProcess

    runtime = ContainerRuntime(command="engine")
    candidate = "a" * 64

    def respond(cmd, **options):
      if cmd[1] == "ps":
        assert "--no-trunc" in cmd
        return CompletedProcess(cmd, 0, stdout=candidate + "\n", stderr="")
      if cmd[1] == "inspect":
        assert cmd[-1] == candidate
        return CompletedProcess(
          cmd,
          0,
          stdout=json.dumps(
            [
              candidate,
              {"org.kanibako.credential-launch": "launch", "name": "reused"},
            ]
          ),
          stderr="",
        )
      assert cmd == ["engine", "rm", "-f", candidate]
      return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("kanibako.runtime.container.subprocess.run", side_effect=respond) as run:
      assert runtime._cancel_secret_launch(None, "launch") is True
    assert len(run.call_args_list) == 3

  @pytest.mark.parametrize("failure", ["query", "inspect", "remove"])
  def test_cleanup_command_timeouts_are_bounded_and_not_retried(self, failure):
    import json
    import subprocess

    runtime = ContainerRuntime(command="engine")
    candidate = "a" * 64
    expected = {"query": "ps", "inspect": "inspect", "remove": "rm"}[failure]

    def respond(cmd, **options):
      assert options["timeout"] <= 5
      if cmd[1] == expected:
        raise subprocess.TimeoutExpired(cmd, options["timeout"], output=b"opaque")
      result = (
        candidate + "\n"
        if cmd[1] == "ps"
        else json.dumps(
          [
            candidate,
            {"org.kanibako.credential-launch": "launch"},
          ]
        )
      )
      return subprocess.CompletedProcess(cmd, 0, stdout=result, stderr="")

    with patch("kanibako.runtime.container.subprocess.run", side_effect=respond) as run:
      assert runtime._cancel_secret_launch(None, "launch") is False
    assert sum(call.args[0][1] == expected for call in run.call_args_list) == 1

  def test_empty_query_is_incomplete_not_proof_against_late_creation(self):
    from subprocess import CompletedProcess

    runtime = ContainerRuntime(command="engine")
    with patch("kanibako.runtime.container.subprocess.run") as run:
      run.return_value = CompletedProcess([], 0, stdout="", stderr="")
      assert runtime._cancel_secret_launch(None, "launch") is False
      assert run.call_count == 1


class TestCredentialDescriptorLimit:
  @pytest.mark.parametrize("detach", [False, True])
  def test_pipe_sources_near_descriptor_limit_use_free_lower_slots(self, tmp_path, detach):
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime import container

    runtime = _credential_engine(
      tmp_path,
      """
      while os.read(3, 65536):
        pass
      os.write(4, b'R')
    """,
    )
    program = textwrap.dedent("""
      import os, resource, subprocess, sys, threading
      from pathlib import Path
      from unittest.mock import patch

      root = Path(sys.argv[1])
      sys.path.insert(0, str(root))
      from kanibako.runtime import container
      from kanibako.secret_delivery import SecretPayload, SecretValue
      assert Path(container.__file__).is_relative_to(root)
      resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
      fillers = []
      while True:
        try:
          fillers.append(os.open('/dev/null', os.O_RDONLY))
        except OSError:
          break
      for _ in range(4):
        os.close(fillers.pop())
      release = threading.Event()
      released = threading.Event()
      def release_fillers():
        release.wait()
        for fd in fillers:
          os.close(fd)
        released.set()
      thread = threading.Thread(target=release_fillers)
      thread.start()
      original_pipe = os.pipe2
      original_popen = subprocess.Popen
      observed = []
      def real_pipe(flags):
        pair = original_pipe(flags)
        observed.append(pair)
        return pair
      def spawn(arguments, *args, **kwargs):
        if '-I' in arguments:
          release.set()
          assert released.wait(2)
        return original_popen(arguments, *args, **kwargs)
      try:
        with patch.object(container.os, 'pipe2', side_effect=real_pipe), patch.object(
          container.subprocess, 'Popen', side_effect=spawn
        ):
          status = container.ContainerRuntime(command=sys.argv[2]).run(
            'image', shell_path=Path(sys.argv[3]) / 'home',
            project_path=Path(sys.argv[3]) / 'work', vault_ro_path=None,
            vault_rw_path=None, enable_vault=False,
            secret_payload=SecretPayload((SecretValue('TOKEN', b'opaque'),)),
            publication_timeout=2, detach=sys.argv[4] == 'True',
          )
        assert status == 0
        assert observed == [(60, 61), (62, 63)], observed
      finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()
    """)
    result = subprocess.run(
      [
        sys.executable,
        "-I",
        "-c",
        program,
        str(Path(container.__file__).parents[2]),
        runtime.cmd,
        str(tmp_path),
        str(detach),
      ],
      capture_output=True,
      text=True,
      timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr

  @pytest.mark.parametrize("detach", [False, True])
  @pytest.mark.parametrize("closed", range(8))
  def test_real_pipes_publish_with_child_limit_six_and_closed_stdio(
    self, tmp_path, detach, closed
  ):
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime import container
    from kanibako.secret_delivery import SecretPayload, SecretValue, encode_payload

    frame = encode_payload(SecretPayload((SecretValue("TOKEN", b"opaque"),)))
    expected_stdio = [not (closed & (1 << fd)) or (detach and fd in (1, 2)) for fd in range(3)]
    executable = tmp_path / "engine"
    executable.write_text(
      f"#!{sys.executable} -S\nimport os,sys\n"
      "if sys.argv[1] != 'run': sys.exit(0)\n"
      f"expected = {expected_stdio!r}\n"
      "for fd in range(3):\n"
      "  try: os.fstat(fd)\n"
      "  except OSError: assert not expected[fd], fd\n"
      "  else: assert expected[fd], fd\n"
      "data = bytearray()\n"
      "while True:\n"
      "  piece = os.read(3, 65536)\n"
      "  if not piece: break\n"
      "  data.extend(piece)\n"
      f"assert data == {frame!r}\n"
      "os.write(4, b'R')\n"
    )
    executable.chmod(0o755)
    report = tmp_path / "result.json"
    program = textwrap.dedent(r"""
      import fcntl, json, os, resource, sys
      from pathlib import Path
      from unittest.mock import patch
      root, engine, directory, report = sys.argv[1:5]
      closed, detach = int(sys.argv[5]), sys.argv[6] == 'True'
      sys.path.insert(0, root)
      from kanibako.runtime import container
      from kanibako.secret_delivery import SecretPayload, SecretValue
      assert Path(container.__file__).is_relative_to(root)
      initial_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
      for fd in range(3):
        if closed & (1 << fd): os.close(fd)
      preserved = {fd: (os.fstat(fd), fcntl.fcntl(fd, fcntl.F_GETFD))
                   for fd in range(3) if not (closed & (1 << fd))}
      observed = []
      real_pipe = os.pipe2
      def pipe(flags):
        pair = real_pipe(flags)
        observed.append(pair)
        return pair
      parent_limit = 6 if closed == 7 else 32
      resource.setrlimit(resource.RLIMIT_NOFILE, (parent_limit, initial_limit[1]))
      script = 'import resource; resource.setrlimit(resource.RLIMIT_NOFILE, (6, 6))\n' + container._SECRET_EXEC
      try:
        with patch.object(container.os, 'pipe2', side_effect=pipe), patch.object(
          container, '_SECRET_EXEC', script
        ):
          status = container.ContainerRuntime(command=engine).run(
            'image', shell_path=Path(directory) / 'home',
            project_path=Path(directory) / 'work', vault_ro_path=None,
            vault_rw_path=None, enable_vault=False, detach=detach,
            secret_payload=SecretPayload((SecretValue('TOKEN', b'opaque'),)),
            publication_timeout=2,
          )
        assert status == 0
        assert len(observed) == 2
        if closed == 7: assert observed == [(0, 1), (2, 3)], observed
        for fd, original in preserved.items():
          assert (os.fstat(fd), fcntl.fcntl(fd, fcntl.F_GETFD)) == original
        record = {'status': status, 'pipes': observed, 'parent_limit': parent_limit}
      except BaseException as error:
        record = {'error': type(error).__name__, 'detail': str(error), 'pipes': observed}
      resource.setrlimit(resource.RLIMIT_NOFILE, initial_limit)
      Path(report).write_text(json.dumps(record))
      os._exit(0)
    """)
    result = subprocess.run(
      [
        sys.executable,
        "-I",
        "-c",
        program,
        str(Path(container.__file__).parents[2]),
        str(executable),
        str(tmp_path),
        str(report),
        str(closed),
        str(detach),
      ],
      capture_output=True,
      text=True,
      timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    import json

    record = json.loads(report.read_text())
    assert record.get("status") == 0, record

  @pytest.mark.parametrize(
    "sources,closed,limit,exhausted",
    [
      ((3, 4), 7, 5, False),
      ((4, 3), 7, 5, False),
      ((4, 3), 6, 5, False),
      ((4, 3), 0, 5, True),
      ((0, 3), 7, 5, False),
      ((3, 0), 7, 5, False),
      ((4, 8), 7, 6, False),
      ((8, 4), 7, 6, False),
      ((7, 9), 7, 6, False),
    ],
  )
  def test_trampoline_orders_real_pipe_edges_and_allocates_only_for_cycles(
    self, tmp_path, sources, closed, limit, exhausted
  ):
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime.container import _SECRET_EXEC

    report = tmp_path / "result"
    program = textwrap.dedent(r"""
      import errno, fcntl, os, resource, sys
      script, report = sys.argv[1:3]
      sources = [int(fd) for fd in sys.argv[3:5]]
      closed, limit = int(sys.argv[5]), int(sys.argv[6])
      copies = []
      for value in (b'a', b'b'):
        read, write = os.pipe2(os.O_CLOEXEC)
        os.write(write, value)
        os.close(write)
        copies.append(fcntl.fcntl(read, fcntl.F_DUPFD_CLOEXEC, 20))
        os.close(read)
      for fd in range(3):
        if closed & (1 << fd): os.close(fd)
      for source, copy in zip(sources, copies):
        os.dup2(copy, source, inheritable=False)
        os.close(copy)
      checker = (
        "import os; assert os.read(3, 2) == b'a'; assert os.read(4, 2) == b'b'; "
        "assert os.get_inheritable(3) and os.get_inheritable(4); "
        "from pathlib import Path; "
      )
      expected = {3, 4} | {fd for fd in range(3) if not closed & (1 << fd)}
      checker += '\nfor fd in range(10):\n'
      checker += '  try: os.fstat(fd)\n'
      checker += f'  except OSError: assert fd not in {expected!r}, fd\n'
      checker += f'  else: assert fd in {expected!r}, fd\n'
      checker += f'Path({report!r}).write_text("ok")\n'
      initial_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
      resource.setrlimit(resource.RLIMIT_NOFILE, (limit, initial_limit[1]))
      sys.argv = ['trampoline', *map(str, sources), '3', '4', '0',
                  sys.executable, '-I', '-c', checker]
      try:
        exec(script, {'__name__': '__main__'})
      except OSError as error:
        assert error.errno == errno.EMFILE, error
        resource.setrlimit(resource.RLIMIT_NOFILE, initial_limit)
        with open(report, 'w') as output: output.write('EMFILE')
        os._exit(0)
    """)
    result = subprocess.run(
      [
        sys.executable,
        "-I",
        "-c",
        program,
        _SECRET_EXEC,
        str(report),
        *map(str, sources),
        str(closed),
        str(limit),
      ],
      capture_output=True,
      text=True,
      timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report.read_text() == ("EMFILE" if exhausted else "ok")

  @pytest.mark.parametrize("closed", [0, 1, 7])
  def test_detached_output_reuses_its_slots_without_a_spare(self, tmp_path, closed):
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime.container import _SECRET_EXEC

    report = tmp_path / "result"
    driver = textwrap.dedent(r"""
      import fcntl, os, resource, signal, sys
      script, report, closed = sys.argv[1], sys.argv[2], int(sys.argv[3])
      copies = []
      for value in (b'a', b'b'):
        read, write = os.pipe2(os.O_CLOEXEC)
        os.write(write, value)
        os.close(write)
        copies.append(fcntl.fcntl(read, fcntl.F_DUPFD_CLOEXEC, 20))
        os.close(read)
      for target, copy in zip((3, 4), copies):
        os.dup2(copy, target, inheritable=False)
        os.close(copy)
      stdin = os.fstat(0)
      for fd in range(3):
        if closed & (1 << fd): os.close(fd)
      limit = resource.getrlimit(resource.RLIMIT_NOFILE)
      resource.setrlimit(resource.RLIMIT_NOFILE, (5, limit[1]))
      def observe(command, arguments):
        assert command == 'engine' and arguments == ['engine']
        assert os.read(3, 2) == b'a' and os.read(4, 2) == b'b'
        assert all(os.get_inheritable(fd) for fd in (1, 2, 3, 4))
        if closed & 1:
          try: os.fstat(0)
          except OSError: pass
          else: raise AssertionError('stdin reopened')
        else:
          assert os.fstat(0) == stdin
        expected = os.stat(os.devnull)
        for fd in (1, 2):
          actual = os.fstat(fd)
          assert (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
          os.write(fd, b'discarded-marker')
        resource.setrlimit(resource.RLIMIT_NOFILE, limit)
        with open(report, 'w') as output: output.write('ok')
        raise SystemExit(0)
      os.execvp = observe
      sys.argv = ['trampoline', '3', '4', '3', '4', '1', 'engine']
      exec(script, {'__name__': '__main__'})
    """)
    result = subprocess.run(
      [sys.executable, "-I", "-c", driver, _SECRET_EXEC, str(report), str(closed)],
      capture_output=True,
      text=True,
      timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report.read_text() == "ok"
    assert result.stdout == result.stderr == ""

  @pytest.mark.parametrize("detach", [False, True])
  @pytest.mark.parametrize("status", [0, 129])
  @pytest.mark.parametrize("ack", [b"R", b"", b"X", b"RR"])
  def test_queued_readiness_is_consumed_before_foreground_exit_status(
    self, tmp_path, detach, status, ack
  ):
    import selectors
    import subprocess
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(
      tmp_path,
      f"while os.read(3, 65536): pass\nos.write(4, {ack!r})\nraise SystemExit({status})",
    )
    original_popen = subprocess.Popen
    original_selector = selectors.DefaultSelector
    processes = []

    def spawn(*args, **kwargs):
      process = original_popen(*args, **kwargs)
      processes.append(process)
      return process

    class DelayedReadiness:
      def __init__(self):
        self.inner = original_selector()
        self.delayed = False

      def __enter__(self):
        return self

      def __exit__(self, *args):
        self.inner.close()

      def register(self, *args):
        return self.inner.register(*args)

      def unregister(self, *args):
        return self.inner.unregister(*args)

      def select(self, timeout):
        events = self.inner.select(timeout)
        if not self.delayed and any(key.events == selectors.EVENT_READ for key, _ in events):
          self.delayed = True
          assert processes[0].wait(timeout=2) == status
          return []
        return events

    with (
      patch("kanibako.runtime.container.subprocess.Popen", side_effect=spawn),
      patch("kanibako.runtime.container.selectors.DefaultSelector", DelayedReadiness),
    ):
      payload = SecretPayload((SecretValue("TOKEN", b"opaque"),))
      if ack == b"R" and (not detach or status == 0):
        assert _credential_run(runtime, tmp_path, payload, detach=detach) == status
      else:
        with pytest.raises(ContainerError, match="Credential publication failed"):
          _credential_run(runtime, tmp_path, payload, detach=detach)

  @pytest.mark.parametrize("detach", [False, True])
  def test_early_valid_ack_and_exit_do_not_replace_complete_pumping(self, tmp_path, detach):
    from kanibako.secret_delivery import SecretPayload, SecretValue

    runtime = _credential_engine(tmp_path, "os.read(3, 1)\nos.write(4, b'R')\nraise SystemExit(0)")
    with pytest.raises(ContainerError, match="Credential publication failed"):
      _credential_run(
        runtime,
        tmp_path,
        SecretPayload((SecretValue("TOKEN", b"x" * 1048576),)),
        detach=detach,
        publication_timeout=1,
      )

  @pytest.mark.parametrize(
    "detach,limit,ready", [(True, 5, True), (False, 5, False), (False, 6, True), (True, 6, True)]
  )
  def test_relocated_real_pipe_cycle_reclaims_only_detached_output(
    self, tmp_path, detach, limit, ready
  ):
    import platform
    import struct
    import subprocess
    import sys
    import textwrap
    from kanibako.runtime import container

    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
      pytest.skip("static syscall fixture requires Linux x86-64")
    code = bytes.fromhex(
      "4881ec0010000031c0bf030000004889e6ba001000000f054885c07fea7822"
      "c6042452b801000000bf040000004889e6ba010000000f054883f8017504"
      "31ffeb05bf01000000b83c0000000f05"
    )
    size = 120 + len(code)
    header = struct.pack(
      "<16sHHIQQQIHHHHHH",
      b"\x7fELF\x02\x01\x01" + b"\0" * 9,
      2,
      62,
      1,
      0x400078,
      64,
      0,
      0,
      64,
      56,
      1,
      0,
      0,
      0,
    )
    segment = struct.pack("<IIQQQQQQ", 1, 7, 0, 0x400000, 0x400000, size, size, 4096)
    engine = tmp_path / "native-ack-engine"
    engine.write_bytes(header + segment + code)
    engine.chmod(0o755)
    driver = textwrap.dedent(r"""
      import fcntl, os, stat, sys
      from pathlib import Path
      from unittest.mock import patch
      root, engine, directory = sys.argv[1:4]
      detach, limit, ready = sys.argv[4] == 'True', int(sys.argv[5]), sys.argv[6] == 'True'
      sys.path.insert(0, root)
      from kanibako.runtime import container
      from kanibako.errors import ContainerError
      from kanibako.secret_delivery import SecretPayload, SecretValue
      assert Path(container.__file__).is_relative_to(root)
      preserved = {fd: (os.fstat(fd), fcntl.fcntl(fd, fcntl.F_GETFD)) for fd in range(3)}
      actual_pipe = os.pipe2
      observed = []
      def pipe(flags):
        pair = actual_pipe(flags)
        copies = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 40) for fd in pair]
        for fd in pair: os.close(fd)
        targets = (4, 20) if not observed else (21, 3)
        for source, target in zip(copies, targets):
          os.dup2(source, target, inheritable=False)
          os.close(source)
        assert all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in targets)
        assert fcntl.fcntl(targets[0], fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert fcntl.fcntl(targets[1], fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY
        observed.append(targets)
        return targets
      script = f'import resource, signal; resource.setrlimit(resource.RLIMIT_NOFILE, ({limit}, {limit}))\n' + container._SECRET_EXEC
      try:
        with patch.object(container.os, 'pipe2', side_effect=pipe), patch.object(container, '_SECRET_EXEC', script):
          status = container.ContainerRuntime(command=engine).run(
            'image', shell_path=Path(directory) / 'home', project_path=Path(directory) / 'work',
            vault_ro_path=None, vault_rw_path=None, enable_vault=False,
            secret_payload=SecretPayload((SecretValue('TOKEN', b'opaque'),)),
            detach=detach, publication_timeout=1,
          )
      except ContainerError:
        assert not ready
      else:
        assert ready and status == 0
      assert observed == [(4, 20), (21, 3)], observed
      for fd, original in preserved.items():
        assert (os.fstat(fd), fcntl.fcntl(fd, fcntl.F_GETFD)) == original
    """)
    result = subprocess.run(
      [
        sys.executable,
        "-I",
        "-c",
        driver,
        str(Path(container.__file__).parents[2]),
        str(engine),
        str(tmp_path),
        str(detach),
        str(limit),
        str(ready),
      ],
      capture_output=True,
      text=True,
      timeout=8,
    )
    assert result.returncode == 0, result.stdout + result.stderr
