"""Tests for kanibako box config subcommand and config.py utility functions."""

from __future__ import annotations

import argparse

import pytest

from kanibako.config import (
    KanibakoConfig,
    load_config,
    load_project_overrides,
    write_project_config,
    write_project_config_key,
)


# ---------------------------------------------------------------------------
# box config command tests
# ---------------------------------------------------------------------------

class TestBoxConfigShow:
    def test_show_no_overrides(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir], effective=True, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "container_image" in captured.out

    def test_show_with_override(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a project override
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "custom:v1")

        args = argparse.Namespace(
            args=[project_dir], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "custom:v1" in captured.out


class TestBoxConfigGet:
    def test_get_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "image"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in captured.out

    def test_get_known_key_without_project(self, config_file, tmp_home, credentials_dir, capsys):
        """``box config image`` (no project arg) should use cwd."""
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # known key as first arg => get operation (project defaults to cwd)
        # In tests the project_dir fixture is not cwd, so use 2-arg form.
        args2 = argparse.Namespace(
            args=[project_dir, "image"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args2)
        assert rc == 0

    def test_get_env_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write an env var
        env_path = proj.metadata_path / "env"
        env_path.write_text("MY_VAR=hello\n")

        args = argparse.Namespace(
            args=[project_dir, "env.MY_VAR"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "hello" in captured.out


class TestBoxConfigSet:
    def test_set_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "image=new-image:v1"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set" in captured.out
        assert "new-image:v1" in captured.out

    def test_set_env_var(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "env.EDITOR=vim"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set EDITOR=vim" in captured.out

    def test_set_model(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "model=sonnet"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set model=sonnet" in captured.out

    def test_set_resource(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "resource.plugins=/my/plugins"], effective=False,
            reset=None, reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set resource.plugins=/my/plugins" in captured.out


class TestBoxConfigReset:
    def test_reset_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set first
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "to-reset:v1")

        # Reset
        args = argparse.Namespace(
            args=[project_dir], effective=False, reset="container_image",
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_reset_all(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set a value first
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "override:v1")

        # Reset all with --force (skip confirmation)
        args = argparse.Namespace(
            args=[project_dir], effective=False, reset="__ALL__",
            reset_all=True, force=True, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_reset_nonexistent(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir], effective=False, reset="container_image",
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No override" in captured.out


class TestBoxConfigLocal:
    def test_local_flag_on_resource_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "resource.plugins"], effective=False, reset=None,
            reset_all=False, force=False, local=True,
        )
        rc = run_config(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set resource.plugins=project" in captured.out

    def test_local_flag_on_non_resource_key_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "image"], effective=False, reset=None,
            reset_all=False, force=False, local=True,
        )
        rc = run_config(args)
        assert rc == 1
        assert "--local only applies" in capsys.readouterr().err


class TestBoxConfigArgParsing:
    """Test the known-key heuristic arg parsing."""

    def test_parser_config_no_args(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config"])
        assert args.command == "box"
        assert args.box_command == "config"
        assert args.args == []

    def test_parser_config_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "image"])
        assert args.args == ["image"]

    def test_parser_config_key_equals_value(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "image=myimg:v1"])
        assert args.args == ["image=myimg:v1"]

    def test_parser_config_project_and_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "myproject", "image"])
        assert args.args == ["myproject", "image"]

    def test_parser_config_effective(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "--effective"])
        assert args.effective is True

    def test_parser_config_reset_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "--reset", "model"])
        assert args.reset == "model"

    def test_parser_config_reset_all(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "--reset", "--all"])
        assert args.reset_all is True

    def test_parser_config_force(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "--force"])
        assert args.force is True

    def test_parser_config_local(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "config", "--local"])
        assert args.local is True


class TestBoxConfigTooManyArgs:
    def test_three_args_returns_error(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_config

        args = argparse.Namespace(
            args=["a", "b", "c"], effective=False, reset=None,
            reset_all=False, force=False, local=False,
        )
        rc = run_config(args)
        assert rc == 1
        assert "too many arguments" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# config.py utility function tests (carried forward from old test file)
# ---------------------------------------------------------------------------

class TestWriteProjectConfigKey:
    def test_write_paths_key(self, tmp_path):
        p = tmp_path / "project.toml"
        write_project_config_key(p, "paths_boxes", "custom_boxes")
        loaded = load_config(p)
        assert loaded.paths_boxes == "custom_boxes"
        text = p.read_text()
        assert "[paths]" in text
        assert 'boxes = "custom_boxes"' in text

    def test_write_container_key(self, tmp_path):
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "myimg:v1")
        loaded = load_config(p)
        assert loaded.container_image == "myimg:v1"
        text = p.read_text()
        assert "[container]" in text
        assert 'image = "myimg:v1"' in text

    def test_write_target_key(self, tmp_path):
        p = tmp_path / "project.toml"
        write_project_config_key(p, "target_name", "my-target")
        loaded = load_config(p)
        assert loaded.target_name == "my-target"
        text = p.read_text()
        assert "[target]" in text
        assert 'name = "my-target"' in text

    def test_write_multiple_sections(self, tmp_path):
        """Writing keys from different sections should create both."""
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "multi:v1")
        write_project_config_key(p, "paths_boxes", "multi_boxes")
        loaded = load_config(p)
        assert loaded.container_image == "multi:v1"
        assert loaded.paths_boxes == "multi_boxes"

    def test_update_existing_key(self, tmp_path):
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "old:v1")
        write_project_config_key(p, "container_image", "new:v2")
        loaded = load_config(p)
        assert loaded.container_image == "new:v2"
        text = p.read_text()
        assert "old:v1" not in text

    def test_backward_compat_with_write_project_config(self, tmp_path):
        """write_project_config (old API) should still work."""
        p = tmp_path / "project.toml"
        write_project_config(p, "compat:v1")
        loaded = load_config(p)
        assert loaded.container_image == "compat:v1"


class TestUnsetProjectConfigKey:
    def test_unset_removes_key(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "remove-me:v1")
        assert unset_project_config_key(p, "container_image") is True
        loaded = load_config(p)
        # Should revert to default
        assert loaded.container_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_unset_nonexistent_key(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "keep:v1")
        assert unset_project_config_key(p, "paths_boxes") is False
        # Original key should still be there
        loaded = load_config(p)
        assert loaded.container_image == "keep:v1"

    def test_unset_no_file(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "nonexistent.toml"
        assert unset_project_config_key(p, "container_image") is False

    def test_unset_preserves_other_keys(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "img:v1")
        write_project_config_key(p, "paths_boxes", "my_boxes")
        assert unset_project_config_key(p, "container_image") is True
        loaded = load_config(p)
        assert loaded.paths_boxes == "my_boxes"
        assert loaded.container_image == "ghcr.io/doctorjei/kanibako-oci:latest"


class TestLoadProjectOverrides:
    def test_empty_when_no_file(self, tmp_path):
        p = tmp_path / "nonexistent.toml"
        assert load_project_overrides(p) == {}

    def test_returns_only_overrides(self, tmp_path):
        p = tmp_path / "project.toml"
        write_project_config_key(p, "container_image", "override:v1")
        overrides = load_project_overrides(p)
        assert "container_image" in overrides
        assert overrides["container_image"] == "override:v1"
        # Other keys should not appear (they are defaults)
        assert "paths_boxes" not in overrides


class TestSplitConfigKey:
    def test_container_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("container_image") == ("container", "image")

    def test_paths_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("paths_boxes") == ("paths", "boxes")

    def test_paths_key_with_underscores(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("paths_data_path") == ("paths", "data_path")

    def test_target_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("target_name") == ("target", "name")

    def test_unknown_prefix_raises(self):
        from kanibako.config import _split_config_key
        with pytest.raises(ValueError, match="Cannot determine TOML section"):
            _split_config_key("unknown_prefix_key")


class TestConfigKeys:
    def test_returns_all_fields(self):
        from kanibako.config import config_keys
        from dataclasses import fields
        expected = [fld.name for fld in fields(KanibakoConfig)]
        assert config_keys() == expected

    def test_includes_known_keys(self):
        from kanibako.config import config_keys
        keys = config_keys()
        assert "container_image" in keys
        assert "paths_boxes" in keys
        assert "paths_shell" in keys
        assert "paths_data_path" in keys
        assert "target_name" in keys
