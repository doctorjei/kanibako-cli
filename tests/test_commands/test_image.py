"""Tests for kanibako.commands.image."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch


from kanibako.commands.image import _extract_ghcr_owner, _list_remote_packages
from kanibako.config import load_config
from kanibako.paths import load_std_paths, resolve_project


class TestImage:
    def test_runs_without_error(self, config_file, tmp_home, credentials_dir, capsys):
        """Smoke test: image list runs without crashing."""
        from kanibako.commands.image import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(project=project_dir)
        rc = run_list(args)
        assert rc == 0

        captured = capsys.readouterr()
        assert "Current image:" in captured.out


class TestImageRebuild:
    def test_pull_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """Default rebuild pulls from registry."""
        from unittest.mock import MagicMock, patch
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="ghcr.io/foo/kanibako-oci:latest",
                all_images=False, local_build=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.pull.assert_called_once()
            runtime.rebuild.assert_not_called()

    def test_local_build_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """--local flag triggers local build from Containerfile."""
        from unittest.mock import MagicMock, patch
        from kanibako.commands.image import run_rebuild

        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        config = load_config(config_file)
        std = load_std_paths(config)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)
        (containers_dir / "Containerfile.kanibako").write_text("FROM ubuntu\n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = "kanibako"
            runtime.get_base_image.return_value = "ghcr.io/doctorjei/droste-fiber:latest"
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="kanibako-oci:latest",
                all_images=False, local_build=True,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.rebuild.assert_called_once()
            # Verify build_args passed
            call_kwargs = runtime.rebuild.call_args
            assert call_kwargs[1]["build_args"] == {"BASE_IMAGE": "ghcr.io/doctorjei/droste-fiber:latest"}

    def test_local_build_unknown_image(self, tmp_home, config_file, credentials_dir, capsys):
        """--local with unknown image pattern errors."""
        from unittest.mock import MagicMock, patch
        from kanibako.commands.image import run_rebuild

        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        config = load_config(config_file)
        std = load_std_paths(config)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="unknown:latest",
                all_images=False, local_build=True,
            )
            rc = run_rebuild(args)
            assert rc == 1
            captured = capsys.readouterr()
            assert "cannot determine Containerfile" in captured.err

    def test_pull_all(self, tmp_home, config_file, credentials_dir, capsys):
        """Default --all pulls all local images from registry."""
        from unittest.mock import MagicMock, patch
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("ghcr.io/foo/kanibako-oci:latest", "1GB"),
                ("ghcr.io/foo/kanibako-lxc:latest", "2GB"),
            ]
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image=None, all_images=True, local_build=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            assert runtime.pull.call_count == 2


class TestExtractGhcrOwner:
    def test_valid_ghcr_url(self):
        from kanibako.commands.image import _extract_ghcr_owner

        assert _extract_ghcr_owner("ghcr.io/doctorjei/kanibako-oci:latest") == "doctorjei"

    def test_non_ghcr_url(self):
        from kanibako.commands.image import _extract_ghcr_owner

        assert _extract_ghcr_owner("docker.io/library/ubuntu:latest") is None


class TestResolveImageName:
    def test_suffix_expansion(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("oci", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_suffix_min(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("min", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-min:latest"

    def test_kanibako_prefix_expansion(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("kanibako-custom", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ghcr.io/doctorjei/kanibako-custom:latest"

    def test_full_path_passthrough(self):
        from kanibako.commands.image import resolve_image_name

        full = "ghcr.io/other/kanibako-oci:v2"
        result = resolve_image_name(full, "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == full

    def test_unknown_name_passthrough(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("ubuntu", "ghcr.io/doctorjei/kanibako-oci:latest")
        assert result == "ubuntu"

    def test_prefix_derived_from_configured(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("lxc", "ghcr.io/myowner/kanibako-oci:latest")
        assert result == "ghcr.io/myowner/kanibako-lxc:latest"

    def test_no_prefix_extractable(self):
        from kanibako.commands.image import resolve_image_name

        result = resolve_image_name("oci", "localimage:latest")
        assert result == "oci"


class TestExtractRegistryPrefix:
    def test_ghcr(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("ghcr.io/doctorjei/kanibako-oci:latest") == "ghcr.io/doctorjei"

    def test_two_parts_returns_none(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("library/ubuntu:latest") is None

    def test_single_part_returns_none(self):
        from kanibako.commands.image import _extract_registry_prefix

        assert _extract_registry_prefix("ubuntu:latest") is None


class TestRebuildWithShorthand:
    def test_shorthand_resolved_in_rebuild(self, tmp_home, config_file, credentials_dir, capsys):
        """Shorthand name gets resolved to full image in rebuild."""
        from unittest.mock import MagicMock, patch
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="min",
                all_images=False, local_build=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            # Should have resolved "min" to the full image path
            call_args = runtime.pull.call_args[0]
            assert "kanibako-min" in call_args[0]
            assert call_args[0].startswith("ghcr.io/")


# ---------------------------------------------------------------------------
# _list_remote_packages
# ---------------------------------------------------------------------------

class TestListRemotePackages:
    def test_successful_api_response(self, capsys):
        response_data = [
            {"name": "kanibako-oci"},
            {"name": "kanibako-lxc"},
            {"name": "unrelated-pkg"},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("myowner")

        out = capsys.readouterr().out
        assert "ghcr.io/myowner/kanibako-oci" in out
        assert "ghcr.io/myowner/kanibako-lxc" in out
        assert "unrelated-pkg" not in out

    def test_api_timeout(self, capsys):
        import urllib.error
        with patch(
            "kanibako.commands.image.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "could not reach" in out.lower()

    def test_empty_package_list(self, capsys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "no kanibako packages" in out.lower()

    def test_invalid_json_response(self, capsys):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.commands.image.urllib.request.urlopen", return_value=mock_resp):
            _list_remote_packages("owner")

        out = capsys.readouterr().out
        assert "could not reach" in out.lower()


# ---------------------------------------------------------------------------
# _extract_ghcr_owner edge cases
# ---------------------------------------------------------------------------

class TestExtractGhcrOwnerExtended:
    def test_non_ghcr_image_returns_none(self):
        assert _extract_ghcr_owner("docker.io/library/ubuntu:latest") is None

    def test_ghcr_no_slash_after_owner(self):
        """ghcr.io/owner without a slash after owner returns None."""
        assert _extract_ghcr_owner("ghcr.io/justowner") is None

    def test_ghcr_with_nested_path(self):
        assert _extract_ghcr_owner("ghcr.io/org/repo/image:tag") == "org"
