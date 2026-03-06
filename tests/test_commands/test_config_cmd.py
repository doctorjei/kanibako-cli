"""Tests for kanibako.commands.config_cmd."""

from __future__ import annotations

import argparse

import pytest

from unittest.mock import patch

from kanibako.config import (
    KanibakoConfig,
    load_config,
    write_project_config,
    write_project_config_key,
    load_project_overrides,
)


# ---------------------------------------------------------------------------
# Existing tests (updated with unset= attribute)
# ---------------------------------------------------------------------------

class TestConfigGet:
    def test_get_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="image", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in captured.out

    def test_get_image_via_full_key(self, config_file, tmp_home, credentials_dir, capsys):
        """``kanibako config container_image`` should work the same as ``config image``."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="container_image", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in captured.out

    def test_get_paths_boxes(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="paths_boxes", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "boxes" in captured.out

    def test_get_paths_shell(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="paths_shell", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "shell" in captured.out

    def test_get_target_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="target_name", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        # Default is empty string
        assert captured.out.strip() == ""

    def test_get_all_config_keys(self, config_file, tmp_home, credentials_dir, capsys):
        """Every field in KanibakoConfig should be gettable."""
        from kanibako.commands.config_cmd import run
        from dataclasses import fields

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        for fld in fields(KanibakoConfig):
            args = argparse.Namespace(
                key=fld.name, value=None, show=False, clear=False, unset=None,
                project=project_dir,
            )
            rc = run(args)
            assert rc == 0
            captured = capsys.readouterr()
            expected = getattr(KanibakoConfig(), fld.name)
            assert str(expected) in captured.out


class TestConfigSet:
    def test_set_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="image", value="new-image:v1", show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0

        # Verify the project.toml was written
        project_toml = proj.metadata_path / "project.toml"
        assert project_toml.exists()
        loaded = load_config(project_toml)
        assert loaded.container_image == "new-image:v1"

    def test_set_paths_boxes(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="paths_boxes", value="custom_boxes", show=False, clear=False,
            unset=None, project=project_dir,
        )
        rc = run(args)
        assert rc == 0

        project_toml = proj.metadata_path / "project.toml"
        assert project_toml.exists()
        loaded = load_config(project_toml)
        assert loaded.paths_boxes == "custom_boxes"

    def test_set_via_full_key(self, config_file, tmp_home, credentials_dir, capsys):
        """``kanibako config container_image myimg`` should work."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="container_image", value="full-key-image:v1", show=False,
            clear=False, unset=None, project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "container_image" in captured.out
        assert "full-key-image:v1" in captured.out

        project_toml = proj.metadata_path / "project.toml"
        loaded = load_config(project_toml)
        assert loaded.container_image == "full-key-image:v1"

    def test_set_multiple_keys(self, config_file, tmp_home, credentials_dir, capsys):
        """Setting multiple keys should accumulate in project.toml."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set image
        args = argparse.Namespace(
            key="container_image", value="multi:v1", show=False, clear=False,
            unset=None, project=project_dir,
        )
        run(args)

        # Set paths_boxes
        args = argparse.Namespace(
            key="paths_boxes", value="multi_boxes", show=False, clear=False,
            unset=None, project=project_dir,
        )
        run(args)

        project_toml = proj.metadata_path / "project.toml"
        loaded = load_config(project_toml)
        assert loaded.container_image == "multi:v1"
        assert loaded.paths_boxes == "multi_boxes"


class TestConfigUnknownKey:
    def test_unknown_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="nonexistent", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 1

    def test_unknown_key_shows_valid_keys(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key="bogus_key", value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "Valid keys" in captured.err
        assert "container_image" in captured.err


class TestConfigShow:
    def test_show_all(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key=None, value=None, show=True, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "container_image" in captured.out
        assert "paths_boxes" in captured.out
        assert "target_name" in captured.out

    def test_show_marks_project_overrides(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a project override
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "custom:v1")

        args = argparse.Namespace(
            key=None, value=None, show=True, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "(project)" in captured.out
        assert "custom:v1" in captured.out


class TestConfigClear:
    def test_clear_removes_project_toml(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a project override first
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "custom:v1")
        assert project_toml.exists()

        with patch("kanibako.commands.config_cmd.confirm_prompt"):
            args = argparse.Namespace(
                key=None, value=None, show=False, clear=True, unset=None,
                project=project_dir,
            )
            rc = run(args)
        assert rc == 0
        # project.toml still exists (has metadata), but no config overrides remain
        from kanibako.config import load_project_overrides
        assert load_project_overrides(project_toml) == {}
        captured = capsys.readouterr()
        assert "Cleared" in captured.out

    def test_clear_no_project_config(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=True, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No project config" in captured.out


class TestConfigNoArgs:
    def test_no_args_shows_config(self, config_file, tmp_home, credentials_dir, capsys):
        """With no arguments, 'kanibako config' now lists all config values."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False, unset=None,
            project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        # Should show all config values (same as --show)
        assert "container_image" in captured.out
        assert "paths_boxes" in captured.out
        assert "target_name" in captured.out


# ---------------------------------------------------------------------------
# New: --unset tests
# ---------------------------------------------------------------------------

class TestConfigUnset:
    def test_unset_existing_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # First set a value
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "override:v1")
        loaded = load_config(project_toml)
        assert loaded.container_image == "override:v1"

        # Now unset it
        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False,
            unset="container_image", project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Unset container_image" in captured.out
        assert "reverts to default" in captured.out

    def test_unset_alias(self, config_file, tmp_home, credentials_dir, capsys):
        """``--unset image`` should work as alias for ``--unset container_image``."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "alias-test:v1")

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False,
            unset="image", project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Unset" in captured.out

    def test_unset_nonexistent_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False,
            unset="container_image", project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No project-level override" in captured.out

    def test_unset_unknown_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False,
            unset="totally_bogus", project=project_dir,
        )
        rc = run(args)
        assert rc == 1

    def test_unset_then_get_shows_default(self, config_file, tmp_home, credentials_dir, capsys):
        """After unsetting, the default value should be returned by get."""
        from kanibako.commands.config_cmd import run

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set, then unset
        project_toml = proj.metadata_path / "project.toml"
        write_project_config(project_toml, "to-be-unset:v1")

        args = argparse.Namespace(
            key=None, value=None, show=False, clear=False,
            unset="container_image", project=project_dir,
        )
        run(args)
        capsys.readouterr()  # consume output

        # Now get should show default
        args = argparse.Namespace(
            key="container_image", value=None, show=False, clear=False,
            unset=None, project=project_dir,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in captured.out


# ---------------------------------------------------------------------------
# New: config.py utility function tests
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


# ---------------------------------------------------------------------------
# CLI argument parsing integration
# ---------------------------------------------------------------------------

class TestConfigArgParsing:
    def test_parser_accepts_unset(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["config", "--unset", "container_image"])
        assert args.unset == "container_image"
        assert args.key is None

    def test_parser_key_value(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["config", "paths_boxes", "new_value"])
        assert args.key == "paths_boxes"
        assert args.value == "new_value"

    def test_parser_key_only(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["config", "container_image"])
        assert args.key == "container_image"
        assert args.value is None

    def test_parser_no_args(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["config"])
        assert args.key is None
        assert args.value is None
        assert args.unset is None
