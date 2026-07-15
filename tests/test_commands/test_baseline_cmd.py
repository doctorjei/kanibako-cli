"""Tests for kanibako.commands.baseline_cmd."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from kanibako.commands.baseline_cmd import (
    _filter_packages,
    run_install,
    run_list,
    run_verify,
)


class TestParsers:
    def test_baseline_list_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "list"])
        assert args.func == run_list
        assert args.executables is False

    def test_baseline_list_executables_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "list", "--executables"])
        assert args.func == run_list
        assert args.executables is True

    def test_baseline_bare_defaults_to_list(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline"])
        assert args.func == run_list

    def test_baseline_verify_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["baseline", "verify", "myimg", "--only", "tmux", "ripgrep"]
        )
        assert args.func == run_verify
        assert args.image == "myimg"
        assert args.only == ["tmux", "ripgrep"]

    def test_baseline_verify_all_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "verify", "--all", "--skip", "fd-find"])
        assert args.all_images is True
        assert args.skip == ["fd-find"]

    def test_baseline_install_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "install", "--dry-run"])
        assert args.func == run_install
        assert args.dry_run is True


class TestFilterPackages:
    def test_only(self) -> None:
        result = _filter_packages(["a", "b", "c"], ["a", "c"], None)
        assert result == ["a", "c"]

    def test_skip(self) -> None:
        result = _filter_packages(["a", "b", "c"], None, ["b"])
        assert result == ["a", "c"]

    def test_only_and_skip(self) -> None:
        result = _filter_packages(["a", "b", "c"], ["a", "b"], ["b"])
        assert result == ["a"]

    def test_no_filters(self) -> None:
        result = _filter_packages(["a", "b"], None, None)
        assert result == ["a", "b"]


class TestRunList:
    def test_list_default_clean_packages(self, capsys) -> None:
        """Default prints package names, space-separated, one line, clean stdout."""
        args = argparse.Namespace(executables=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Exactly one line.
        lines = out.splitlines()
        assert len(lines) == 1
        pkgs = lines[0].split()
        assert pkgs == sorted(pkgs)  # sorted, stable
        assert set(pkgs) == {
            "tmux", "inotify-tools", "ripgrep", "fd-find", "openssh-client",
            "bubblewrap",
        }

    def test_list_executables_format(self, capsys) -> None:
        args = argparse.Namespace(executables=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "tmux: tmux" in out
        assert "inotify-tools: inotifywait inotifywatch" in out
        assert "ripgrep: rg" in out


class TestRunVerify:
    def _runtime(self, present: set[str]):
        """Build a mock runtime whose ephemeral probe succeeds for *present* exes."""
        runtime = MagicMock()
        runtime.cmd = "podman"
        return runtime

    def test_verify_all_present_exit_0(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        with (
            patch(
                "kanibako.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None, skip=None,
            )
            rc = run_verify(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "all baseline executables present" in out

    def test_verify_missing_exit_1(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        # command -v for 'rg' fails, everything else passes.
        def fake_run(cmd, **kwargs):
            exe = cmd[-1].split()[-1]
            return MagicMock(returncode=1 if exe == "rg" else 0)

        with (
            patch(
                "kanibako.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None, skip=None,
            )
            rc = run_verify(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "missing 'rg'" in out

    def test_verify_only_filter(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        probed: list[str] = []

        def fake_run(cmd, **kwargs):
            probed.append(cmd[-1].split()[-1])
            return MagicMock(returncode=0)

        with (
            patch(
                "kanibako.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=["tmux"], skip=None,
            )
            rc = run_verify(args)
        assert rc == 0
        # Only tmux's executable should have been probed.
        assert probed == ["tmux"]

    def test_verify_skip_filter(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        probed: list[str] = []

        def fake_run(cmd, **kwargs):
            probed.append(cmd[-1].split()[-1])
            return MagicMock(returncode=0)

        with (
            patch(
                "kanibako.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None,
                skip=[
                    "ripgrep", "fd-find", "openssh-client", "inotify-tools",
                    "bubblewrap",
                ],
            )
            rc = run_verify(args)
        assert rc == 0
        assert probed == ["tmux"]

    def test_verify_default_image_from_config(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        used_images: list[str] = []

        def fake_run(cmd, **kwargs):
            used_images.append(cmd[3])  # podman run --rm <image> ...
            return MagicMock(returncode=0)

        merged = MagicMock()
        merged.box_image = "ghcr.io/x/kanibako-oci:latest"

        with (
            patch(
                "kanibako.container.ContainerRuntime", return_value=runtime
            ),
            patch(
                "kanibako.config.load_merged_config", return_value=merged
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image=None, all_images=False, only=["tmux"], skip=None,
            )
            rc = run_verify(args)
        assert rc == 0
        assert used_images == ["ghcr.io/x/kanibako-oci:latest"]


class TestRunInstall:
    def test_install_dry_run(self, capsys) -> None:
        args = argparse.Namespace(only=["tmux"], skip=None, dry_run=True)
        rc = run_install(args)
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "apt-get install -y --no-install-recommends tmux"

    def test_install_non_debian_warns(self, capsys) -> None:
        args = argparse.Namespace(only=None, skip=None, dry_run=False)
        with (
            patch("shutil.which", return_value=None),
        ):
            rc = run_install(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "apt/debian" in err

    def test_install_runs_apt(self) -> None:
        args = argparse.Namespace(only=["tmux"], skip=None, dry_run=False)
        with (
            patch("shutil.which", return_value="/usr/bin/apt-get"),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mrun,
        ):
            rc = run_install(args)
        assert rc == 0
        called = mrun.call_args[0][0]
        assert called[:4] == ["apt-get", "install", "-y", "--no-install-recommends"]
        assert "tmux" in called
