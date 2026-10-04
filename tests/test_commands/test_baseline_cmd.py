"""Tests for kanibako.commands.baseline_cmd."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.baseline_cmd import (
    _filter_packages,
    run_install,
    run_list,
    run_verify,
)
from kanibako.runtime import baseline as baseline_mod

from tests.support.repo import REPO_ROOT


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
            "bubblewrap", "jq",
        }

    def test_list_executables_format(self, capsys) -> None:
        args = argparse.Namespace(executables=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "tmux: tmux" in out
        assert "inotify-tools: inotifywait inotifywatch" in out
        assert "ripgrep: rg" in out


class TestSharedImageContainerfile:
    """The shared all-variant Containerfile installs the baseline by DERIVING it.

    It has no hardcoded package list: the apt step consumes ``$(kanibako
    baseline list)``, so a package declared in the baseline reaches every
    variant — ``min`` included, which takes the same shared step — with no
    edit here. This pins that derivation, so a future hand-written list
    cannot silently drop a declared baseline package again.
    """

    def _containerfile(self) -> str:
        return (
            REPO_ROOT / "images" / "containers" / "Containerfile.kanibako"
        ).read_text()

    def test_apt_step_derives_from_baseline_list(self) -> None:
        assert 'baseline="$(kanibako baseline list)"' in self._containerfile()

    def test_every_baseline_package_is_not_hardcoded(self) -> None:
        """The shared apt step must not spell out a package list of its own.

        jq is the regression: it was in no image, so nothing installed it.
        Deriving the list is what makes the next declared package free.
        """
        text = self._containerfile()
        apt_step = text.split("baseline=\"$(kanibako baseline list)\"", 1)[1]
        apt_step = apt_step.split("rm -rf /var/lib/apt/lists", 1)[0]
        for pkg in baseline_mod.packages():
            assert pkg not in apt_step, f"{pkg} is hardcoded, not derived"

    def test_min_variant_takes_the_shared_baseline_step(self) -> None:
        """min is a VARIANT of the shared Containerfile, not a separate file.

        Its own block adds sshpass only, so it inherits the baseline step.
        """
        text = self._containerfile()
        assert 'if [ "$VARIANT" = "min" ]' in text
        assert 'if [ "$VARIANT" = "lxc" ]' in text
        # The min-only block adds sshpass and nothing baseline-derived.
        min_block = text.split('if [ "$VARIANT" = "min" ]', 1)[1]
        min_block = min_block.split("fi", 1)[0]
        assert "baseline list" not in min_block


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
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
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
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
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

    def test_verify_names_jq_when_the_image_lacks_it(self, capsys) -> None:
        """`baseline verify` reports a jq-less image by name and exits 1."""
        runtime = MagicMock()
        runtime.cmd = "podman"

        def fake_run(cmd, **kwargs):
            exe = cmd[-1].split()[-1]
            return MagicMock(returncode=1 if exe == "jq" else 0)

        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None, skip=None,
            )
            rc = run_verify(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "[!!] jq: missing 'jq'" in out

    def test_verify_jq_present_exits_0(self, capsys) -> None:
        """An image carrying every baseline tool, jq included, verifies clean."""
        runtime = MagicMock()
        runtime.cmd = "podman"
        probed: list[str] = []

        def fake_run(cmd, **kwargs):
            probed.append(cmd[-1].split()[-1])
            return MagicMock(returncode=0)

        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None, skip=None,
            )
            rc = run_verify(args)
        assert rc == 0
        assert "jq" in probed
        assert "all baseline executables present" in capsys.readouterr().out

    def test_verify_only_filter(self, capsys) -> None:
        runtime = MagicMock()
        runtime.cmd = "podman"
        probed: list[str] = []

        def fake_run(cmd, **kwargs):
            probed.append(cmd[-1].split()[-1])
            return MagicMock(returncode=0)

        with (
            patch(
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
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
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image="img", all_images=False, only=None,
                skip=[
                    "ripgrep", "fd-find", "openssh-client", "inotify-tools",
                    "bubblewrap", "jq",
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
                "kanibako.runtime.container.ContainerRuntime", return_value=runtime
            ),
            patch(
                "kanibako.settings.settings_launch.load_merged_config", return_value=merged
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            args = argparse.Namespace(
                image=None, all_images=False, only=["tmux"], skip=None,
            )
            rc = run_verify(args)
        assert rc == 0
        assert used_images == ["ghcr.io/x/kanibako-oci:latest"]


class TestProbeTreatsNameAsOneWord:
    """A baseline executable name is quoted into ONE shell word: nothing else runs."""

    def test_semicolon_in_name_runs_nothing(self, tmp_path) -> None:
        from kanibako.commands.baseline_cmd import _make_probe
        from tests.support.probe_shim import local_sh_runtime

        canary = tmp_path / "PWNED"
        probe = _make_probe(local_sh_runtime(tmp_path), "img")

        # Not a hit: the whole string is one word, so there is no such command.
        assert probe(f"x; touch {canary}") is False
        assert not canary.exists()

    def test_quote_in_name_runs_nothing(self, tmp_path) -> None:
        from kanibako.commands.baseline_cmd import _make_probe
        from tests.support.probe_shim import local_sh_runtime

        canary = tmp_path / "PWNED"
        probe = _make_probe(local_sh_runtime(tmp_path), "img")

        assert probe(f'x" ; touch {canary} ; echo "') is False
        assert not canary.exists()

    def test_name_is_quoted_in_the_probe_argv(self, tmp_path) -> None:
        from kanibako.commands.baseline_cmd import _make_probe
        from tests.support.probe_shim import local_sh_runtime

        probe = _make_probe(local_sh_runtime(tmp_path), "img")
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mrun:
            probe("weird name;rm -rf /")

        script = mrun.call_args[0][0][-1]
        assert script == "command -v 'weird name;rm -rf /'"
        # Still a LOGIN shell: -l is what makes the probe see a session's PATH.
        assert mrun.call_args[0][0][-3:-1] == ["sh", "-lc"]


class TestRunVerifyRefusesUnprobeableNames:
    """``kanibako baseline verify`` refuses a crafted overlay name before it probes."""

    def test_nul_in_an_overlay_name_never_reaches_the_probe(
        self, tmp_home, monkeypatch
    ) -> None:
        """Real XDG overlay, real ``run_verify``: the refusal lands, the probe never runs.

        Without the refusal the NUL reaches ``subprocess.run`` as an argv entry and
        the whole command dies on an embedded-null ValueError instead of naming the
        file and the package.
        """
        from kanibako.errors import ConfigError

        overlay = tmp_home / "config" / "kanibako" / "image-baseline.yaml"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text('git: ["rg\\0sh"]\n')

        runtime = MagicMock()
        runtime.cmd = "podman"
        monkeypatch.setenv("HOME", str(tmp_home / "home"))
        args = argparse.Namespace(
            only=None, skip=None, all_images=False, image="img:latest"
        )
        with (
            patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
            patch("subprocess.run") as mock_run,
            pytest.raises(ConfigError) as exc,
        ):
            run_verify(args)
        assert str(exc.value).startswith(
            f"the config file {overlay} sets 'git' to a value that is not an "
        )
        mock_run.assert_not_called()


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
