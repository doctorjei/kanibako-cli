"""Tests for kanibako.commands.diagnose."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from kanibako.commands.diagnose import (
    _check_agents,
    _check_image,
    _check_runtime,
    _check_storage,
    _diagnose_baseline,
    _format_check,
    probe_missing_executables,
    run_box_diagnose,
    run_crab_diagnose,
    run_rig_diagnose,
    run_system_diagnose,
)


class TestFormatCheck:
    def test_format_ok(self) -> None:
        result = _format_check("ok", "Runtime", "podman 5.0")
        assert result == "[ok] Runtime: podman 5.0"

    def test_format_error(self) -> None:
        result = _format_check("!!", "Runtime", "not found")
        assert result == "[!!] Runtime: not found"

    def test_format_skip(self) -> None:
        result = _format_check("--", "Storage", "cannot check")
        assert result == "[--] Storage: cannot check"


class TestCheckRuntime:
    def test_check_runtime_no_runtime(self) -> None:
        """When ContainerRuntime raises, returns error status."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.container.ContainerRuntime",
            side_effect=ContainerError("no runtime"),
        ):
            status, detail = _check_runtime()
        assert status == "!!"
        assert "not found" in detail

    def test_check_runtime_found(self) -> None:
        """When ContainerRuntime succeeds, returns ok status."""
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        with (
            patch(
                "kanibako.container.ContainerRuntime",
                return_value=mock_runtime,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="podman version 5.0.0"
            )
            status, detail = _check_runtime()
        assert status == "ok"
        assert "podman" in detail
        assert "5.0.0" in detail


class TestCheckAgents:
    def test_check_agents_none(self) -> None:
        """No agent plugins returns error status."""
        with patch(
            "kanibako.targets.discover_targets", return_value={}
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert results[0][1] == "Agents"
        assert "no agent plugins" in results[0][2]

    def test_check_agents_found(self) -> None:
        """Discovered agent with detect() returning install shows ok."""
        mock_install = MagicMock()
        mock_install.binary = Path("/usr/bin/claude")

        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = mock_install

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "ok"
        assert "Claude Code" in results[0][1]
        assert "/usr/bin/claude" in results[0][2]

    def test_check_agents_not_detected(self) -> None:
        """Agent plugin installed but binary not found."""
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = None

        mock_cls = MagicMock(return_value=mock_target)
        with patch(
            "kanibako.targets.discover_targets",
            return_value={"claude": mock_cls},
        ):
            results = _check_agents()
        assert len(results) == 1
        assert results[0][0] == "!!"
        assert "not found" in results[0][2]


class TestCheckStorage:
    def test_check_storage(self, tmp_path: Path) -> None:
        """Test with a real temporary path."""
        status, detail = _check_storage(tmp_path)
        assert status in ("ok", "!!")
        assert "GB" in detail
        assert str(tmp_path) in detail

    def test_check_storage_nonexistent(self) -> None:
        """Non-existent path returns skip status."""
        status, detail = _check_storage(Path("/nonexistent/path/xyz"))
        assert status == "--"
        assert "cannot check" in detail


class TestCheckImage:
    def test_check_image_found(self) -> None:
        """Image available locally returns ok."""
        mock_config = MagicMock()
        mock_config.box_image = "kanibako-oci:latest"

        mock_runtime = MagicMock()
        mock_runtime.image_inspect.return_value = {"Id": "abc123"}

        with patch(
            "kanibako.container.ContainerRuntime",
            return_value=mock_runtime,
        ):
            status, detail = _check_image(mock_config)
        assert status == "ok"
        assert "available locally" in detail

    def test_check_image_not_found(self) -> None:
        """Image not locally available returns error."""
        mock_config = MagicMock()
        mock_config.box_image = "kanibako-oci:latest"

        mock_runtime = MagicMock()
        mock_runtime.image_inspect.return_value = None

        with patch(
            "kanibako.container.ContainerRuntime",
            return_value=mock_runtime,
        ):
            status, detail = _check_image(mock_config)
        assert status == "!!"
        assert "not found locally" in detail


class TestRunSystemDiagnose:
    def test_run_system_diagnose(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """System diagnose runs and returns 0."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace()
            rc = run_system_diagnose(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Kanibako System Diagnostics" in captured.out
        assert "[" in captured.out


class TestRunCrabDiagnose:
    def test_run_crab_diagnose(self, capsys) -> None:
        """Crab diagnose runs and returns 0."""
        with patch(
            "kanibako.targets.discover_targets", return_value={}
        ):
            args = argparse.Namespace()
            rc = run_crab_diagnose(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Crab (Agent) Diagnostics" in captured.out
        assert "no agent plugins" in captured.out


class TestRunRigDiagnose:
    def test_run_rig_diagnose(
        self, config_file, tmp_home, credentials_dir, capsys
    ) -> None:
        """Rig diagnose runs and returns 0."""
        from kanibako.errors import ContainerError

        with patch(
            "kanibako.container.ContainerRuntime",
            side_effect=ContainerError("none"),
        ):
            args = argparse.Namespace()
            rc = run_rig_diagnose(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Rig (Image) Diagnostics" in captured.out


class TestProbeMissingExecutables:
    """probe_missing_executables: one ephemeral run, partition the result."""

    def test_single_run_partitions_present_and_missing(self) -> None:
        from kanibako.commands.diagnose import _PROBE_HIT_PREFIX

        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        # The probe reports tmux + rg present, fdfind missing.
        out = f"{_PROBE_HIT_PREFIX}tmux\n{_PROBE_HIT_PREFIX}rg\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=out, stderr="")
            missing = probe_missing_executables(
                mock_runtime, "img:latest", ["tmux", "rg", "fdfind"]
            )
        assert missing == ["fdfind"]
        # Exactly ONE container spin-up for all three executables.
        assert mock_run.call_count == 1
        # Must override the image ENTRYPOINT, else kanibako-entrypoint swallows
        # the probe script and every executable looks missing.
        argv = mock_run.call_args[0][0]
        assert argv[:5] == ["podman", "run", "--rm", "--entrypoint", "sh"]
        assert argv[5] == "img:latest"

    def test_empty_list_no_run(self) -> None:
        mock_runtime = MagicMock()
        with patch("subprocess.run") as mock_run:
            assert probe_missing_executables(mock_runtime, "img", []) == []
        mock_run.assert_not_called()

    def test_runtime_failure_all_missing(self) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        with patch("subprocess.run", side_effect=OSError("boom")):
            missing = probe_missing_executables(mock_runtime, "img", ["a", "b"])
        assert missing == ["a", "b"]


class TestDiagnoseBaseline:
    """_diagnose_baseline filtering (--only/--skip) and single-vs-all images."""

    def _patch_baseline(self):
        # Three packages with one executable each.
        return patch(
            "kanibako.baseline.load_baseline",
            return_value={"tmux": ["tmux"], "ripgrep": ["rg"], "fd-find": ["fdfind"]},
        )

    def test_only_filters_packages(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=["ripgrep"], skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        # Only ripgrep's exe (rg) should be probed.
        assert mock_probe.call_args[0][2] == ["rg"]

    def test_skip_filters_packages(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=["fd-find"], all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        probed = mock_probe.call_args[0][2]
        assert "fdfind" not in probed
        assert set(probed) == {"tmux", "rg"}

    def test_default_single_configured_image(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.config.load_merged_config",
                return_value=MagicMock(box_image="configured:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        # Single configured image probed.
        assert mock_probe.call_count == 1
        assert mock_probe.call_args[0][1] == "configured:latest"
        mock_runtime.list_local_images.assert_not_called()

    def test_all_images_probes_each(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        mock_runtime.list_local_images.return_value = [
            ("kanibako-oci:latest", "1GB"),
            ("kanibako-min:latest", "0.5GB"),
        ]
        args = argparse.Namespace(only=None, skip=None, all_images=True)
        with (
            self._patch_baseline(),
            patch("kanibako.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=[],
            ) as mock_probe,
        ):
            _diagnose_baseline(args)
        assert mock_probe.call_count == 2

    def test_missing_executable_reported(self, capsys) -> None:
        mock_runtime = MagicMock()
        mock_runtime.cmd = "podman"
        args = argparse.Namespace(only=None, skip=None, all_images=False)
        with (
            self._patch_baseline(),
            patch("kanibako.container.ContainerRuntime", return_value=mock_runtime),
            patch(
                "kanibako.config.load_merged_config",
                return_value=MagicMock(box_image="img:latest"),
            ),
            patch(
                "kanibako.commands.diagnose.probe_missing_executables",
                return_value=["rg"],
            ),
        ):
            _diagnose_baseline(args)
        out = capsys.readouterr().out
        assert "[!!]" in out
        assert "ripgrep:rg" in out


class TestParsers:
    """Verify that diagnose subcommands are parseable."""

    def test_rig_diagnose_baseline_flags(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["rig", "diagnose", "--all", "--only", "ripgrep", "--skip", "fd-find"]
        )
        assert args.func == run_rig_diagnose
        assert args.all_images is True
        assert args.only == ["ripgrep"]
        assert args.skip == ["fd-find"]

    def test_system_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["system", "diagnose"])
        assert args.func == run_system_diagnose

    def test_crab_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["crab", "diagnose"])
        assert args.func == run_crab_diagnose

    def test_rig_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["rig", "diagnose"])
        assert args.func == run_rig_diagnose

    def test_box_diagnose_parser(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "diagnose"])
        assert args.func == run_box_diagnose

    def test_box_diagnose_with_project(self) -> None:
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "diagnose", "myproject"])
        assert args.func == run_box_diagnose
        assert args.project == "myproject"
