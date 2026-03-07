"""Tests for kanibako template CLI."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest


class TestTemplateRegistration:
    def test_template_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "template" in _SUBCOMMANDS

    def test_template_parser_exists(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["template", "list"])
        assert args.command == "template"

    def test_template_exempt_from_config_check(self):
        """Template should not require kanibako.toml to exist."""
        from kanibako.cli import build_parser
        parser = build_parser()
        # Verify 'template' is parsed as a command, not defaulted to 'start'
        args = parser.parse_args(["template", "create", "jvm"])
        assert args.command == "template"
        assert args.name == "jvm"

    def test_create_flags_are_mutually_exclusive(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["template", "create", "jvm", "--always-commit", "--no-commit-on-error"])


class TestTemplateList:
    def test_list_empty(self, capsys):
        from kanibako.commands.template_cmd import run_list

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = []
            MockRT.return_value = runtime

            args = argparse.Namespace()
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "No templates" in captured.out

    def test_list_shows_templates(self, capsys):
        from kanibako.commands.template_cmd import run_list

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.list_local_images.return_value = [
                ("kanibako-template-jvm", "1.2 GB"),
                ("kanibako-oci", "900 MB"),
            ]
            MockRT.return_value = runtime

            args = argparse.Namespace()
            rc = run_list(args)
            assert rc == 0

        captured = capsys.readouterr()
        assert "jvm" in captured.out
        assert "1.2 GB" in captured.out


class TestTemplateDelete:
    def test_delete_with_force(self, capsys):
        from kanibako.commands.template_cmd import run_delete

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(name="jvm", force=True)
            rc = run_delete(args)
            assert rc == 0

            runtime.remove_image.assert_called_once_with("kanibako-template-jvm")

    def test_delete_confirmed(self, capsys, monkeypatch):
        from kanibako.commands.template_cmd import run_delete

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(name="jvm", force=False)
            rc = run_delete(args)
            assert rc == 0
            runtime.remove_image.assert_called_once()

    def test_delete_cancelled(self, capsys, monkeypatch):
        from kanibako.commands.template_cmd import run_delete

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(name="jvm", force=False)
            rc = run_delete(args)
            assert rc == 0
            runtime.remove_image.assert_not_called()

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    def test_delete_failure(self, capsys):
        from kanibako.commands.template_cmd import run_delete
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.remove_image.side_effect = ContainerError("no such image")
            MockRT.return_value = runtime

            args = argparse.Namespace(name="nonexistent", force=True)
            rc = run_delete(args)
            assert rc == 1


class TestTemplateCreate:
    def _make_args(self, name="jvm", base="kanibako-oci",
                   always_commit=False, no_commit_on_error=False):
        return argparse.Namespace(
            name=name, base=base,
            always_commit=always_commit, no_commit_on_error=no_commit_on_error,
        )

    def test_create_runs_container_and_commits(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
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
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="tools", base="kanibako-min",
                                            always_commit=True))
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_no_commit_on_error(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
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
        from kanibako.commands.template_cmd import run_create

        monkeypatch.setattr("builtins.input", lambda _: "y")

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_prompt_confirm_no(self, capsys, monkeypatch):
        """Default behavior: prompt on error, user says no."""
        from kanibako.commands.template_cmd import run_create

        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 1
            runtime.commit.assert_not_called()

    def test_create_no_prompt_on_zero_exit(self, capsys, monkeypatch):
        """No prompt when container exits cleanly."""
        from kanibako.commands.template_cmd import run_create

        # input() should never be called
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("should not prompt")))

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            MockRT.return_value = runtime

            rc = run_create(self._make_args())
            assert rc == 0
            runtime.commit.assert_called_once()

    def test_create_rejects_invalid_name(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="../evil"))
            assert rc == 1
            runtime.run_interactive.assert_not_called()

        captured = capsys.readouterr()
        assert "Invalid template name" in captured.err

    def test_create_fails_on_commit_error(self, capsys):
        from kanibako.commands.template_cmd import run_create
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            runtime.commit.side_effect = ContainerError("commit failed")
            MockRT.return_value = runtime

            rc = run_create(self._make_args(name="bad"))
            assert rc == 1
