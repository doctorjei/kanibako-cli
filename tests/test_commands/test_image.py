"""Tests for kanibako.commands.image."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.image import _extract_ghcr_owner, _list_remote_packages
from kanibako.config import load_config
from kanibako.paths import load_std_paths


class TestImage:
    def test_runs_without_error(self, config_file, tmp_home, credentials_dir, capsys):
        """Smoke test: image list runs without crashing."""
        from kanibako.commands.image import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0

        captured = capsys.readouterr()
        assert "Current rig:" in captured.out

    def test_quiet_mode(self, config_file, tmp_home, credentials_dir, capsys):
        """Quiet mode prints only image names."""
        from kanibako.commands.image import run_list

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("kanibako-oci:latest", "1 GB"),
                ("kanibako-lxc:latest", "2 GB"),
            ]
            MockRT.return_value = runtime

            args = argparse.Namespace(quiet=True)
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines == ["kanibako-oci:latest", "kanibako-lxc:latest"]
        # Should NOT contain section headers
        assert "Built-in" not in captured.out
        assert "Current rig:" not in captured.out

    def test_quiet_mode_no_runtime(self, config_file, tmp_home, credentials_dir, capsys):
        """Quiet mode with no runtime returns empty output."""
        from kanibako.commands.image import run_list
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime", side_effect=ContainerError("none")):
            args = argparse.Namespace(quiet=True)
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert captured.out.strip() == ""


class TestImageInfo:
    def test_info_shows_details(self, config_file, tmp_home, credentials_dir, capsys):
        """image info displays image metadata."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc123def456789",
                "Created": "2026-03-01T10:00:00Z",
                "Size": 1_500_000_000,
                "Labels": {"org.example.key": "value"},
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "Name:" in captured.out
        assert "ID:" in captured.out
        assert "Created:" in captured.out
        assert "1.5 GB" in captured.out
        assert "org.example.key=value" in captured.out

    def test_info_not_found(self, config_file, tmp_home, credentials_dir, capsys):
        """image info returns 1 for missing image."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_inspect.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(image="nosuch")
            rc = run_info(args)
            assert rc == 1

        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_info_size_mb(self, config_file, tmp_home, credentials_dir, capsys):
        """image info displays MB for smaller images."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc",
                "Size": 500_000_000,
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "500.0 MB" in captured.out

    def test_info_no_labels(self, config_file, tmp_home, credentials_dir, capsys):
        """image info works without labels."""
        from kanibako.commands.image import run_info

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.image_inspect.return_value = {
                "Id": "sha256:abc",
            }
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci")
            rc = run_info(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "Labels" not in captured.out


class TestImageRm:
    def test_rm_with_force(self, config_file, tmp_home, credentials_dir, capsys):
        """image rm --force removes without prompting."""
        from kanibako.commands.image import run_rm

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=True)
            rc = run_rm(args)
            assert rc == 0

            runtime.remove_image.assert_called_once()

    def test_rm_confirmed(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm prompts and proceeds on 'y'."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0
            runtime.remove_image.assert_called_once()

    def test_rm_cancelled(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm prompts and cancels on 'n'."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0
            runtime.remove_image.assert_not_called()

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    def test_rm_template_warns_local_only(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm warns that template images are not recoverable."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-template-jvm", force=False)
            rc = run_rm(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "not recoverable" in captured.out

    def test_rm_non_template_hints_rebuild(self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """image rm hints at rebuild for registry-backed images."""
        from kanibako.commands.image import run_rm

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(image="kanibako-oci", force=False)
            rc = run_rm(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "recoverable" in captured.out

    def test_rm_failure(self, config_file, tmp_home, credentials_dir, capsys):
        """image rm returns 1 on remove failure."""
        from kanibako.commands.image import run_rm
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.remove_image.side_effect = ContainerError("no such image")
            MockRT.return_value = runtime

            args = argparse.Namespace(image="nosuch", force=True)
            rc = run_rm(args)
            assert rc == 1


class TestImageRebuild:
    def test_pull_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """Default rebuild pulls from registry when no Containerfile matches."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="ghcr.io/foo/kanibako-oci:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.pull.assert_called_once()
            runtime.rebuild.assert_not_called()

    def test_local_build_one_success(self, tmp_home, config_file, credentials_dir, capsys):
        """Auto-detect triggers local build when Containerfile matches."""
        from kanibako.commands.image import run_rebuild

        config = load_config(config_file)
        std = load_std_paths(config)
        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)
        (containers_dir / "Containerfile.kanibako").write_text("FROM ubuntu\n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = "kanibako"
            runtime.get_base_image.return_value = "ghcr.io/doctorjei/droste-fiber:latest"
            runtime.get_variant.return_value = "oci"
            runtime.rebuild.return_value = 0
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="kanibako-oci:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            runtime.rebuild.assert_called_once()
            # Verify build_args passed
            call_kwargs = runtime.rebuild.call_args
            assert call_kwargs[1]["build_args"] == {
                "BASE_IMAGE": "ghcr.io/doctorjei/droste-fiber:latest",
                "VARIANT": "oci",
            }

    def test_local_build_unknown_image(self, tmp_home, config_file, credentials_dir, capsys):
        """Unknown image pattern falls back to pull."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.guess_containerfile.return_value = None
            runtime.pull.return_value = True
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="unknown:latest",
                all_images=False,
            )
            rc = run_rebuild(args)
            assert rc == 0
            # Should fall back to pull since no Containerfile found
            runtime.pull.assert_called_once()

    def test_pull_all(self, tmp_home, config_file, credentials_dir, capsys):
        """--all updates all local images."""
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("ghcr.io/foo/kanibako-oci:latest", "1GB"),
                ("ghcr.io/foo/kanibako-lxc:latest", "2GB"),
            ]
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image=None, all_images=True,
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
        from kanibako.commands.image import run_rebuild

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.pull.return_value = True
            runtime.guess_containerfile.return_value = None
            MockRT.return_value = runtime

            args = argparse.Namespace(
                image="min",
                all_images=False,
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


# ---------------------------------------------------------------------------
# Template create / list / delete (absorbed from template_cmd)
# ---------------------------------------------------------------------------

class TestImageCreate:
    def _make_args(self, name="jvm", base="kanibako-oci",
                   always_commit=False, no_commit_on_error=False):
        return argparse.Namespace(
            name=name, base=base,
            always_commit=always_commit, no_commit_on_error=no_commit_on_error,
        )

    def test_create_runs_container_and_commits(self, capsys):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0

            runtime.run_interactive.assert_called_once_with(
                "kanibako-oci",
                container_name="kanibako-template-build-jvm",
            )
            runtime.commit.assert_called_once_with(
                "kanibako-template-build-jvm",
                "kanibako-template-jvm",
            )
            # Build container should be cleaned up
            runtime.rm.assert_called_once_with("kanibako-template-build-jvm")

    def test_create_always_commit_on_nonzero_exit(self, capsys):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="tools", base="kanibako-min",
                                            always_commit=True))
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_no_commit_on_error(self, capsys):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args(no_commit_on_error=True))
            assert rc == 1
            runtime.commit.assert_not_called()

        captured = capsys.readouterr()
        assert "Skipping commit" in captured.err

    def test_create_prompt_confirm_yes(self, capsys, monkeypatch):
        """Default behavior: prompt on error, user says yes."""
        from kanibako.commands.image import run_create

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_prompt_confirm_no(self, capsys, monkeypatch):
        """Default behavior: prompt on error, user says no."""
        from kanibako.commands.image import run_create

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 1
            runtime.commit.assert_not_called()

    def test_create_no_prompt_on_zero_exit(self, capsys, monkeypatch):
        """No prompt when container exits cleanly."""
        from kanibako.commands.image import run_create

        # input() should never be called
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("should not prompt")))

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_rejects_invalid_name(self, capsys):
        from kanibako.commands.image import run_create

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="../evil"))
            assert rc == 1
            runtime.run_interactive.assert_not_called()

        captured = capsys.readouterr()
        assert "Invalid template name" in captured.err

    def test_create_fails_on_commit_error(self, capsys):
        from kanibako.commands.image import run_create
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.image.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            runtime.commit.side_effect = ContainerError("commit failed")
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="bad"))
            assert rc == 1


class TestImageCreateFlags:
    def test_create_flags_are_mutually_exclusive(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "create", "jvm", "--always-commit", "--no-commit-on-error"])


class TestImageRegistration:
    def test_template_not_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "template" not in _SUBCOMMANDS

    def test_rig_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "rig" in _SUBCOMMANDS

    def test_image_alias_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "image" in _SUBCOMMANDS

    def test_rig_create_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "create", "jvm"])
        assert args.command == "rig"
        assert args.rig_command == "create"
        assert args.name == "jvm"

    def test_rig_info_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "info", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_inspect_alias(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "inspect", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_rm_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "rm", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_delete_alias(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "delete", "kanibako-oci"])
        assert args.command == "rig"
        assert args.image == "kanibako-oci"

    def test_rig_list_quiet_flag(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "list", "-q"])
        assert args.command == "rig"
        assert args.quiet is True

    def test_rig_rebuild_no_local_flag(self):
        """--local flag should no longer exist on rebuild."""
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "rebuild", "--local"])

    def test_rig_list_no_project_flag(self):
        """-p/--project flag should no longer exist on list."""
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["rig", "list", "-p", "foo"])


# ---------------------------------------------------------------------------
# ContainerRuntime.image_inspect
# ---------------------------------------------------------------------------

class TestContainerRuntimeImageInspect:
    def test_image_inspect_returns_dict(self):
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([{"Id": "sha256:abc", "Size": 100}]),
            )
            rt = ContainerRuntime(command="podman")
            result = rt.image_inspect("test-image")
            assert result == {"Id": "sha256:abc", "Size": 100}

    def test_image_inspect_not_found(self):
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
            rt = ContainerRuntime(command="podman")
            result = rt.image_inspect("nosuch")
            assert result is None

    def test_image_inspect_dict_response(self):
        """Docker returns a dict, not a list."""
        from kanibako.container import ContainerRuntime

        with patch("kanibako.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"Id": "sha256:def", "Size": 200}),
            )
            rt = ContainerRuntime(command="docker")
            result = rt.image_inspect("test-image")
            assert result == {"Id": "sha256:def", "Size": 200}
