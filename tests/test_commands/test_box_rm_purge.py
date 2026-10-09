"""Tests for remove_box_tree(): resilient box-metadata removal in `kanibako rm`.

A box's shell dir can contain files a rootless --userns=keep-id container
created under mapped subuids, which the host user cannot unlink directly. A
plain shutil.rmtree then fails with EACCES; remove_box_tree falls back to
`podman unshare rm -rf` and, failing that, reports rather than crashing.
"""

from __future__ import annotations

from unittest.mock import patch

from kanibako.runtime.container import remove_box_tree


class TestPurgeDir:
    def test_removes_normal_dir(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        (d / "box.yaml").write_text("x")
        assert remove_box_tree(d) is True
        assert not d.exists()

    def test_falls_back_to_unshare_on_permission_error(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.runtime.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = True
            assert remove_box_tree(d) is True
            mock_rt.return_value.unshare_rm.assert_called_once_with(d)

    def test_returns_false_when_unshare_fails_and_dir_remains(self, tmp_path):
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.runtime.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = False
            assert remove_box_tree(d) is False  # dir still present
            assert d.exists()

    def test_returns_false_when_no_runtime(self, tmp_path):
        from kanibako.runtime.container import ContainerError
        d = tmp_path / "box"
        d.mkdir()
        with patch("shutil.rmtree", side_effect=PermissionError("denied")), \
             patch("kanibako.runtime.container.ContainerRuntime",
                   side_effect=ContainerError("no podman")):
            assert remove_box_tree(d) is False
