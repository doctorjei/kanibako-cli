"""Tests for kanibako box get/set/resource commands."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from kanibako.commands.box._parser import (
    run_get,
    run_resource_list,
    run_resource_set,
    run_resource_unset,
    run_set,
    _validate_path_override,
)
from kanibako.config import load_config, read_project_meta, read_resource_overrides
from kanibako.paths import load_std_paths, resolve_project
from kanibako.targets.base import ResourceMapping, ResourceScope

import pytest


class TestBoxGet:
    def test_get_mode(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="mode", project=project_dir)
        rc = run_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "account_centric"

    def test_get_shell(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="shell", project=project_dir)
        rc = run_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == str(proj.shell_path)

    def test_get_vault_enabled_bool(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="vault_enabled", project=project_dir)
        rc = run_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "true"

    def test_get_no_metadata_returns_error(self, config_file, tmp_home, capsys):
        """Getting a key on a non-initialized project returns an error."""
        args = argparse.Namespace(key="mode", project=str(tmp_home / "project"))
        rc = run_get(args)
        assert rc == 1
        assert "No project metadata" in capsys.readouterr().err


class TestBoxSet:
    def test_set_layout(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="layout", value="robust", project=project_dir)
        rc = run_set(args)
        assert rc == 0

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta["layout"] == "robust"

    def test_set_vault_enabled_false(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="vault_enabled", value="false", project=project_dir)
        rc = run_set(args)
        assert rc == 0

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta["vault_enabled"] is False

    def test_set_auth_distinct(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="auth", value="distinct", project=project_dir)
        rc = run_set(args)
        assert rc == 0

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta["auth"] == "distinct"

    def test_set_shell_path(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        custom_shell = tmp_home / "custom_shell"
        custom_shell.mkdir()
        args = argparse.Namespace(key="shell", value=str(custom_shell), project=project_dir)
        rc = run_set(args)
        assert rc == 0

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta["shell"] == str(custom_shell)

    def test_set_invalid_layout_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="layout", value="banana", project=project_dir)
        rc = run_set(args)
        assert rc == 1
        assert "invalid value" in capsys.readouterr().err

    def test_set_relative_path_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="shell", value="relative/path", project=project_dir)
        rc = run_set(args)
        assert rc == 1
        assert "must be absolute" in capsys.readouterr().err

    def test_set_no_metadata_returns_error(self, config_file, tmp_home, capsys):
        args = argparse.Namespace(key="auth", value="distinct", project=str(tmp_home / "project"))
        rc = run_set(args)
        assert rc == 1
        assert "No project metadata" in capsys.readouterr().err

    def test_set_invalid_auth_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="auth", value="banana", project=project_dir)
        rc = run_set(args)
        assert rc == 1
        assert "shared/distinct" in capsys.readouterr().err


class TestValidatePathOverride:
    def test_absolute_path_passes(self, tmp_path):
        result = _validate_path_override("shell", str(tmp_path / "new_shell"))
        assert result.parent == tmp_path

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="must be absolute"):
            _validate_path_override("shell", "relative/path")

    def test_missing_parent_rejected(self):
        with pytest.raises(ValueError, match="parent directory does not exist"):
            _validate_path_override("vault_ro", "/nonexistent/parent/child")


def _mock_target_with_mappings():
    """Return a mock target with resource_mappings."""
    target = MagicMock()
    target.resource_mappings.return_value = [
        ResourceMapping("plugins/", ResourceScope.SHARED, "Plugins"),
        ResourceMapping("projects/", ResourceScope.PROJECT, "Sessions"),
        ResourceMapping("settings.json", ResourceScope.SEEDED, "Settings"),
    ]
    return target


class TestBoxResourceList:
    def test_lists_resources(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        with patch(
            "kanibako.commands.box._parser._resolve_target_for_project",
            return_value=_mock_target_with_mappings(),
        ):
            args = argparse.Namespace(project=project_dir)
            rc = run_resource_list(args)

        assert rc == 0
        output = capsys.readouterr().out
        assert "plugins/" in output
        assert "shared" in output
        assert "project" in output


class TestBoxResourceSet:
    def test_set_valid_resource(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        with patch(
            "kanibako.commands.box._parser._resolve_target_for_project",
            return_value=_mock_target_with_mappings(),
        ):
            args = argparse.Namespace(path="plugins/", scope="project", project=project_dir)
            rc = run_resource_set(args)

        assert rc == 0
        overrides = read_resource_overrides(proj.metadata_path / "project.toml")
        assert overrides["plugins/"] == "project"

    def test_set_invalid_path_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        with patch(
            "kanibako.commands.box._parser._resolve_target_for_project",
            return_value=_mock_target_with_mappings(),
        ):
            args = argparse.Namespace(path="nonexistent/", scope="shared", project=project_dir)
            rc = run_resource_set(args)

        assert rc == 1
        assert "not a valid resource path" in capsys.readouterr().err


class TestBoxResourceUnset:
    def test_unset_existing_override(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set first, then unset.
        from kanibako.config import write_resource_override
        project_toml = proj.metadata_path / "project.toml"
        write_resource_override(project_toml, "plugins/", "project")

        args = argparse.Namespace(path="plugins/", project=project_dir)
        rc = run_resource_unset(args)
        assert rc == 0
        assert "Removed" in capsys.readouterr().out

        overrides = read_resource_overrides(project_toml)
        assert "plugins/" not in overrides

    def test_unset_nonexistent(self, config_file, tmp_home, credentials_dir, capsys):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(path="nonexistent/", project=project_dir)
        rc = run_resource_unset(args)
        assert rc == 0
        assert "No override" in capsys.readouterr().out
