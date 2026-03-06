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
    def test_delete_success(self, capsys):
        from kanibako.commands.template_cmd import run_delete

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(name="jvm")
            rc = run_delete(args)
            assert rc == 0

            runtime.remove_image.assert_called_once_with("kanibako-template-jvm")

    def test_delete_failure(self, capsys):
        from kanibako.commands.template_cmd import run_delete
        from kanibako.errors import ContainerError

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.remove_image.side_effect = ContainerError("no such image")
            MockRT.return_value = runtime

            args = argparse.Namespace(name="nonexistent")
            rc = run_delete(args)
            assert rc == 1


class TestTemplateCreate:
    def test_create_runs_container_and_commits(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 0
            MockRT.return_value = runtime

            args = argparse.Namespace(name="jvm", base="kanibako-oci")
            rc = run_create(args)
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

    def test_create_commits_even_on_nonzero_exit(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            runtime.run_interactive.return_value = 1  # nonzero exit
            MockRT.return_value = runtime

            args = argparse.Namespace(name="tools", base="kanibako-min")
            rc = run_create(args)
            assert rc == 0  # still succeeds

            runtime.commit.assert_called_once()

    def test_create_rejects_invalid_name(self, capsys):
        from kanibako.commands.template_cmd import run_create

        with patch("kanibako.commands.template_cmd.ContainerRuntime") as MockRT:
            runtime = MagicMock()
            MockRT.return_value = runtime

            args = argparse.Namespace(name="../evil", base="kanibako-oci")
            rc = run_create(args)
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

            args = argparse.Namespace(name="bad", base="kanibako-oci")
            rc = run_create(args)
            assert rc == 1
