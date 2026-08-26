"""Extended tests for kanibako.settings.paths: recovery paths, edge cases."""

from __future__ import annotations


import pytest

from kanibako.settings.config import load_config
from kanibako.errors import ConfigError
from kanibako.settings.paths import BoxMode, load_std_paths, resolve_project


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
    def test_mode_defaults_to_default(self, config_file, tmp_home, credentials_dir):
        """Existing ProjectPaths construction (without explicit mode) defaults correctly."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj.mode is BoxMode.primary

    def test_mode_field_present_on_dataclass(self):
        """ProjectPaths has a mode field with the expected default."""
        from dataclasses import fields
        from kanibako.settings.paths import ProjectPaths

        field_names = [f.name for f in fields(ProjectPaths)]
        assert "mode" in field_names

        mode_field = next(f for f in fields(ProjectPaths) if f.name == "mode")
        assert mode_field.default is BoxMode.primary


# ---------------------------------------------------------------------------
# Vault optional (enable_vault=False skips vault dirs)
# ---------------------------------------------------------------------------

class TestVaultOptional:
    def test_local_vault_disabled_skips_dirs(self, config_file, tmp_home, credentials_dir):
        """Default-mode project with enable_vault=False skips vault directory creation."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, enable_vault=False,
        )

        assert proj.enable_vault is False
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()

    def test_local_vault_enabled_creates_dirs(self, config_file, tmp_home, credentials_dir):
        """Default-mode project with default enable_vault=True creates vault dirs."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        assert proj.enable_vault is True
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()

    def test_vault_disabled_persists_in_metadata(self, config_file, tmp_home, credentials_dir):
        """enable_vault=False is stored in box.yaml and read back."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, enable_vault=False,
        )

        # P2: the flag is stored SPARSELY as the box-scope key box.enable_vault
        # (a real bool), NOT in the [project] section.
        from kanibako.settings.config import BOX_META_FILE, load_doc
        on_disk = load_doc(proj.metadata_path / BOX_META_FILE)
        assert on_disk["box"]["enable_vault"] is False
        assert "enable_vault" not in on_disk.get("project", {})

        # Second resolve reads metadata, should still be False.
        proj2 = resolve_project(
            std, config, project_dir=project_dir, initialize=False,
        )
        assert proj2.enable_vault is False

    def test_standalone_vault_disabled(self, config_file, tmp_home, credentials_dir):
        """Standalone project with enable_vault=False skips vault dirs."""
        from kanibako.settings.paths import resolve_standalone_project
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_standalone_project(
            std, config, project_dir=project_dir,
            initialize=True, enable_vault=False,
        )

        assert proj.enable_vault is False
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()


# ---------------------------------------------------------------------------
# R2 downward-defaults: a PRIMARY box inherits ``box.enable_vault`` from the
# PRIMARY workset tier (spec §0 "Directional view/set across CONTAINMENT
# levels"; §2c gives PRIMARY the same ``meta.workset.settings`` as NAMED).
# ---------------------------------------------------------------------------

class TestPrimaryEnableVaultDownwardDefault:
    """The primary workset's ``workset.yaml`` is a real tier — ``paths.py`` already
    resolves ``workset.registry`` from it — so ``box.*`` keys in it are defaults.
    """

    @staticmethod
    def _write_primary_enable_vault(std, value):
        """Write ``box.enable_vault`` at the PRIMARY workset tier."""
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import _default_project_group, workset_settings_path

        path = workset_settings_path(_default_project_group(std))
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = load_doc(path) if path.is_file() else {}
        doc.setdefault("box", {})["enable_vault"] = value
        dump_doc(path, doc)
        return path

    def test_primary_workset_tier_false_reaches_the_box(self, config_file, tmp_home,
                                                        credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_primary_enable_vault(std, False)

        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True)
        assert proj.enable_vault is False
        assert not proj.vault_rw_path.exists()

    def test_box_tier_true_overrides_primary_workset_false(self, config_file, tmp_home,
                                                           credentials_dir):
        """The contained scope always wins per the cascade (spec §0)."""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        # Materialize the box first, then pin its own tier True and re-resolve.
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        dump_doc(proj.metadata_path / BOX_META_FILE, {"box": {"enable_vault": True}})
        self._write_primary_enable_vault(std, False)

        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.enable_vault is True

    def test_explicit_param_still_wins(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_primary_enable_vault(std, False)

        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True, enable_vault=True)
        assert proj.enable_vault is True

    def test_absent_everywhere_still_defaults_true(self, config_file, tmp_home,
                                                   credentials_dir):
        """MUTATION-GUARD: the False above comes from the workset tier, not a moved floor."""
        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True)
        assert proj.enable_vault is True

    def test_create_does_not_pin_the_inherited_default(self, config_file, tmp_home,
                                                       credentials_dir):
        """Spec ``:868``: sparse — absent from the box file unless THE USER sets it there."""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_primary_enable_vault(std, False)

        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True)
        assert proj.enable_vault is False

        box_tier = proj.metadata_path / BOX_META_FILE
        stored = (load_doc(box_tier).get("box") or {}) if box_tier.is_file() else {}
        assert "enable_vault" not in stored

    def test_create_does_pin_an_explicit_box_flag(self, config_file, tmp_home,
                                                  credentials_dir):
        """``kanibako create --no-vault`` IS the user setting it at box scope — persist it."""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True, enable_vault=False)

        assert load_doc(proj.metadata_path / BOX_META_FILE)["box"]["enable_vault"] is False
