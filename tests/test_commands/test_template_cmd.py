"""Tests confirming template_cmd was absorbed into image command."""

from __future__ import annotations


class TestTemplateRemoved:
    def test_template_not_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "template" not in _SUBCOMMANDS

    def test_template_module_does_not_exist(self):
        import importlib
        try:
            importlib.import_module("kanibako.commands.template_cmd")
            assert False, "template_cmd module should not exist"
        except ModuleNotFoundError:
            pass

    def test_rig_prep_replaces_template_create(self):
        """The live template path is 'rig prep' (W2a removed the 'rig create' shim)."""
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["rig", "prep", "jvm"])
        assert args.command == "rig"
        assert args.rig_command == "prep"
        assert args.name == "jvm"
