"""Extended tests for kanibako.paths: recovery paths, edge cases."""

from __future__ import annotations


import pytest

from kanibako.config import load_config
from kanibako.errors import ConfigError
from kanibako.paths import ProjectMode, load_std_paths, resolve_project


# ---------------------------------------------------------------------------
# Path recovery (initialize=True repairs missing shell_path)
# ---------------------------------------------------------------------------

class TestPathRecovery:
    def test_missing_shell_path_recovered(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        # First init
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        # Delete shell_path
        import shutil
        shutil.rmtree(proj.shell_path)
        assert not proj.shell_path.exists()

        # Re-resolve with initialize=True should recover
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj2.shell_path.is_dir()

    def test_no_initialize_skips_recovery(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        import shutil
        shutil.rmtree(proj.shell_path)

        # Without initialize, no recovery
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert not proj2.shell_path.is_dir()


# ---------------------------------------------------------------------------
# Edge cases: spaces, unicode, symlinks, legacy .rc detection
# ---------------------------------------------------------------------------

class TestPathEdgeCases:
    def test_path_with_spaces(self, tmp_home, config_file, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        spaced = tmp_home / "my project"
        spaced.mkdir()
        proj = resolve_project(std, config, project_dir=str(spaced), initialize=True)
        assert proj.project_path == spaced.resolve()
        assert proj.metadata_path.is_dir()

    def test_path_with_unicode(self, tmp_home, config_file, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        uni = tmp_home / "projeçt_ñ"
        uni.mkdir()
        proj = resolve_project(std, config, project_dir=str(uni), initialize=True)
        assert proj.project_path == uni.resolve()

    def test_symlink_resolved(self, tmp_home, config_file, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        real = tmp_home / "real_project"
        real.mkdir()
        link = tmp_home / "link_project"
        link.symlink_to(real)
        proj = resolve_project(std, config, project_dir=str(link), initialize=True)
        assert proj.project_path == real.resolve()

    def test_missing_config_detection(self, tmp_home):
        """load_std_paths raises ConfigError when no config file exists."""
        with pytest.raises(ConfigError, match="is missing"):
            load_std_paths()


# ---------------------------------------------------------------------------
# ProjectPaths.mode default behavior
# ---------------------------------------------------------------------------

class TestProjectPathsModeDefault:
    def test_mode_defaults_to_local(self, config_file, tmp_home, credentials_dir):
        """Existing ProjectPaths construction (without explicit mode) defaults correctly."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj.mode is ProjectMode.local

    def test_mode_field_present_on_dataclass(self):
        """ProjectPaths has a mode field with the expected default."""
        from dataclasses import fields
        from kanibako.paths import ProjectPaths

        field_names = [f.name for f in fields(ProjectPaths)]
        assert "mode" in field_names

        mode_field = next(f for f in fields(ProjectPaths) if f.name == "mode")
        assert mode_field.default is ProjectMode.local


# ---------------------------------------------------------------------------
# Vault optional (vault_enabled=False skips vault dirs)
# ---------------------------------------------------------------------------

class TestVaultOptional:
    def test_local_vault_disabled_skips_dirs(self, config_file, tmp_home, credentials_dir):
        """Local project with vault_enabled=False skips vault directory creation."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, vault_enabled=False,
        )

        assert proj.vault_enabled is False
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()

    def test_local_vault_enabled_creates_dirs(self, config_file, tmp_home, credentials_dir):
        """Local project with default vault_enabled=True creates vault dirs."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        assert proj.vault_enabled is True
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()

    def test_vault_disabled_persists_in_metadata(self, config_file, tmp_home, credentials_dir):
        """vault_enabled=False is stored in project.toml and read back."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, vault_enabled=False,
        )

        # Second resolve reads metadata, should still be False.
        proj2 = resolve_project(
            std, config, project_dir=project_dir, initialize=False,
        )
        assert proj2.vault_enabled is False

    def test_standalone_vault_disabled(self, config_file, tmp_home, credentials_dir):
        """Standalone project with vault_enabled=False skips vault dirs."""
        from kanibako.paths import resolve_standalone_project
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_standalone_project(
            std, config, project_dir=project_dir,
            initialize=True, vault_enabled=False,
        )

        assert proj.vault_enabled is False
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()
