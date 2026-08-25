"""Tests for kanibako.commands.setup_cmd."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.setup_cmd import TemplateStep, run_setup


@pytest.fixture
def setup_args():
    """Minimal argparse.Namespace for run_setup."""
    return argparse.Namespace()


def _templates_current():
    """Patch Step 5 to the "nothing to do" outcome.

    These tests are about Steps 1-3 and the closing summary, not the template
    refresh — and Step 5's outcome now GATES both the completion marker and the
    summary banner, so an unstubbed step would make them assert Step 5's
    behaviour by accident.  Step 5 itself is covered in tests/test_setup_cmd.py.
    """
    return patch(
        "kanibako.commands.setup_cmd._run_template_refresh",
        return_value=TemplateStep.CURRENT,
    )


class TestSetupRuntime:
    """Step 1: container runtime detection."""

    def test_setup_detects_runtime(self, setup_args, capsys, tmp_path):
        """When a runtime is available, Step 1 shows [ok]."""
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman (podman version 5.0.0)"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
            patch("kanibako.settings.paths.xdg", return_value=tmp_path),
            _templates_current(),
        ):
            rc = run_setup(setup_args)

        captured = capsys.readouterr()
        assert "[ok]" in captured.out
        assert "podman" in captured.out
        # A run with nothing to do still COMPLETES, and the rc follows the
        # completion marker → 0.  (Step 5 is stubbed: unstubbed it would report
        # SKIPPED or CURRENT depending on the developer's real template store,
        # which would make this assertion host-dependent.)
        assert rc == 0

    def test_setup_no_runtime_exits_1(self, setup_args, capsys):
        """When no runtime is found, setup returns 1."""
        with patch(
            "kanibako.commands.diagnose._check_runtime",
            return_value=("!!", "not found"),
        ):
            rc = run_setup(setup_args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "No container runtime found" in captured.out


class TestSetupAgents:
    """Step 2: agent detection."""

    def test_setup_detects_agents(self, setup_args, capsys, tmp_path):
        """When an agent plugin is installed and detected, it shows [ok]."""
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = MagicMock()  # non-None = detected

        mock_cls = MagicMock(return_value=mock_target)

        # ``xdg()`` must return a REAL directory: ``_write_setup_marker``
        # resolves the config path off it and calls ``mkdir``.  A bare MagicMock
        # here leaks a ``<MagicMock name='xdg()'>`` dir into the CWD.  Point it at
        # tmp_path so the marker write lands in a throwaway location instead.
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={"claude": mock_cls},
            ),
            patch(
                "kanibako.commands.diagnose._check_image",
                return_value=("ok", "test:latest (available locally)"),
            ),
            patch("kanibako.settings.paths.xdg", return_value=tmp_path),
            _templates_current(),
        ):
            rc = run_setup(setup_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "[ok] Claude Code detected" in captured.out
        assert "You're ready to go" in captured.out

    def test_setup_no_agents(self, setup_args, capsys, tmp_path):
        """When no agent plugins are installed, it shows [!!]."""
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
            patch("kanibako.settings.paths.xdg", return_value=tmp_path),
            _templates_current(),
        ):
            rc = run_setup(setup_args)

        # No agents is a REPORT, not a refusal: the run still completes → rc 0.
        assert rc == 0
        captured = capsys.readouterr()
        assert "No agent plugins installed" in captured.out

    def test_setup_agent_not_detected(self, setup_args, capsys, tmp_path):
        """When a plugin exists but agent binary is not found, it shows [--]."""
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = None  # not found on system

        mock_cls = MagicMock(return_value=mock_target)

        # Real xdg() dir so the setup-marker write lands in tmp_path rather than
        # leaking a MagicMock-named dir into the CWD.
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={"claude": mock_cls},
            ),
            patch(
                "kanibako.commands.diagnose._check_image",
                return_value=("--", "not found"),
            ),
            patch("kanibako.settings.paths.xdg", return_value=tmp_path),
            _templates_current(),
        ):
            rc = run_setup(setup_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "[--] Claude Code not found" in captured.out
        assert "Install an agent plugin" in captured.out


class TestSetupParser:
    """Verify setup is properly wired into the CLI."""

    def test_setup_parser(self):
        """'setup' is parseable from build_parser."""
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
        assert hasattr(args, "func")

    def test_setup_in_subcommands(self):
        """'setup' is in _SUBCOMMANDS so it's recognized at the top level."""
        from kanibako.cli import _SUBCOMMANDS

        assert "setup" in _SUBCOMMANDS

    def test_setup_skips_ensure_initialized(self, tmp_path):
        """'setup' should work even before kanibako is initialized."""
        # Verify the skip list includes 'setup' by checking main() behavior.
        # We test the condition directly rather than running main().
        # The condition in cli.py is: args.command not in ("crab", "setup")
        from kanibako.cli import _ensure_initialized

        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
            # Step 5 is stubbed (and the marker write redirected at tmp_path) so
            # this stays a test OF the init skip: unstubbed, the rc would follow
            # the developer's real template store and the marker would be written
            # into the real config file.
            patch("kanibako.settings.paths.xdg", return_value=tmp_path),
            _templates_current(),
            patch.object(
                type(_ensure_initialized),
                "__call__",
                side_effect=AssertionError("should not be called"),
            ) if False else patch("kanibako.cli._ensure_initialized") as mock_init,
        ):
            from kanibako.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main(["setup"])
            # main calls sys.exit(0) on success
            assert exc_info.value.code == 0
            mock_init.assert_not_called()


@pytest.mark.writes_undeclared(
    "box.frobnicate",
    reason="the refusal this test drives is the §0 read gate itself, so the "
           "settings file it writes has to carry an undeclared key; the write "
           "happens inside the resolve that then refuses it.",
)
class TestSettingsRefusalStopsSetup:
    """A settings error must STOP setup, never be reported as "not initialized".

    Step 3's bare ``except Exception`` used to report every failure as
    ``configuration not initialized yet`` and run on.  Against the spec §0
    refusal of an undeclared key that INVERTS the cause (the configuration is
    initialized — it is the broken thing), promises a rig pull that will not
    happen, and closes with ``Setup Complete`` / ``You're ready to go!`` at rc 0
    over a store no command can resolve.

    These drive the REAL resolve — an undeclared entry written into the real
    system-tier settings file — through the REAL ``main(["setup"])`` entry, so
    they fail if the refusal stops reaching ``load_merged_config`` at all, and
    they pin the exit code a script would actually see.
    """

    UNDECLARED = "frobnicate"

    def _write_undeclared_key(self, config_file):
        """Put an undeclared `box.frobnicate` in the system-tier settings file."""
        from pathlib import Path

        from kanibako.settings.paths import load_system_config, xdg

        settings_path = load_system_config(
            config_file,
            data_home=xdg("XDG_DATA_HOME", ".local/share"),
            home=Path.home(),
        )["config.settings"]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(f"box:\n  {self.UNDECLARED}: yes\n")
        return settings_path

    def test_setup_stops_and_names_the_refusal(
        self, config_file, tmp_home, capsys
    ) -> None:
        """`kanibako setup` names the undeclared key and exits 1 at Step 3."""
        from kanibako.cli import main

        settings_path = self._write_undeclared_key(config_file)
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman (podman version 5.0.0)"),
            ),
            patch("kanibako.targets.discover_targets", return_value={}),
            patch(
                "kanibako.commands.setup_cmd._run_template_refresh",
            ) as m_templates,
            patch(
                "kanibako.commands.setup_cmd._write_setup_marker",
            ) as m_marker,
            patch(
                "kanibako.commands.setup_cmd._write_system_agent",
            ) as m_agent,
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup"])

        # The exit code a script sees: a refusal, not a success.
        assert exc_info.value.code == 1
        captured = capsys.readouterr()

        # The abort happens AT Step 3, in setup's own voice on stdout.
        assert "Step 3: Container Rig" in captured.out
        assert (
            "[!!] Settings error -- setup cannot continue (reported below)."
            in captured.out
        )
        # The inverted cause and the false promise are both gone.
        assert "configuration not initialized yet" not in captured.out
        assert "pulled automatically on first use" not in captured.out
        # And so is the claim of readiness.
        assert "Setup Complete" not in captured.out
        assert "You're ready to go" not in captured.out

        # The refusal itself reached the user intact via cli.py: the key, the
        # spec cite, and the file that carries it.
        assert f"box.{self.UNDECLARED}" in captured.err
        assert "spec §0" in captured.err
        assert str(settings_path) in captured.err

        # Nothing was written: the abort precedes Steps 4 and 5 and the marker.
        m_agent.assert_not_called()
        m_templates.assert_not_called()
        m_marker.assert_not_called()

    def test_non_kanibako_failure_still_reports_not_initialized(
        self, config_file, tmp_home, setup_args, capsys
    ) -> None:
        """The `(not initialized yet)` line survives for what it was written for."""
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch("kanibako.targets.discover_targets", return_value={}),
            patch(
                "kanibako.settings.config.load_merged_config",
                side_effect=RuntimeError("boom"),
            ),
            _templates_current(),
        ):
            rc = run_setup(setup_args)

        captured = capsys.readouterr()
        assert "[--] Cannot check (configuration not initialized yet)" in captured.out
        assert "Settings error -- setup cannot continue" not in captured.out
        # Unchanged behaviour: an unforeseen failure is a REPORT, and the run
        # still reaches its summary.
        assert rc == 0
        assert "Setup Complete" in captured.out
