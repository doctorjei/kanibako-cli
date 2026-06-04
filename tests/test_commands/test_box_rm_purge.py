"""Tests for _purge_dir(): resilient box-metadata removal in `kanibako rm`.

A box's shell dir can contain files a rootless --userns=keep-id container
created under mapped subuids, which the host user cannot unlink directly. A
plain shutil.rmtree then fails with EACCES; _purge_dir falls back to
`podman unshare rm -rf` and, failing that, reports rather than crashing.
"""

from __future__ import annotations

from unittest.mock import patch

from kanibako.commands.box._parser import _purge_dir


class TestPurgeDir:
    def test_removes_normal_dir(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        (d / "project.toml").write_text("x")
        assert _purge_dir(d) is True
        assert not d.exists()

    def test_falls_back_to_unshare_on_permission_error(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = True
            assert _purge_dir(d) is True
            mock_rt.return_value.unshare_rm.assert_called_once_with(d)

    def test_returns_false_when_unshare_fails_and_dir_remains(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = False
            assert _purge_dir(d) is False  # dir still present
            assert d.exists()

    def test_returns_false_when_no_runtime(self, tmp_path):
        from kanibako.container import ContainerError
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.container.ContainerRuntime",
                   side_effect=ContainerError("no podman")):
            assert _purge_dir(d) is False
