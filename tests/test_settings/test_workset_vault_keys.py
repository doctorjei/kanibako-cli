"""``workset.{vault_ro,vault_rw}`` are SETTABLE *and* HONOURED — spec §2c ALL PROJECTS.

⚑ Both keys are declared ONCE FOR EVERY MODE (``@meta.workset.path/vault/{ro,rw}``, R-29):
there is no ``standalone: <None>`` carve-out, and only the box BIND differs per mode (the
per-box ``/@meta.box.name`` leaf a lone box does not need).  These tests pin BOTH halves:
the resolver faces, and the three per-mode paths that actually CREATE the directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project.workset import (
    add_project,
    create_workset,
    load_workset_settings_doc,
    remove_project,
    resolve_workset_vault_ro,
    resolve_workset_vault_rw,
)
from kanibako.settings.config import load_config
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    WorksetSpec,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.settings.settings_resolve import SettingsError


def _repoint(root, key, value):
    """Write ``workset.<key> = value`` into *root*'s workset.yaml."""
    write_nested_key(root / "workset.yaml", ("workset",), key, value)


# ---------------------------------------------------------------------------
# The two resolver faces
# ---------------------------------------------------------------------------

class TestVaultResolverFaces:
    def test_unset_takes_the_declared_default_leaf(self, tmp_path):
        assert resolve_workset_vault_ro(tmp_path, None) == tmp_path / "vault" / "ro"
        assert resolve_workset_vault_rw(tmp_path, None) == tmp_path / "vault" / "rw"

    def test_declared_default_written_out_degenerates_to_the_default(self, tmp_path):
        _repoint(tmp_path, "vault_ro", "@meta.workset.path/vault/ro")
        _repoint(tmp_path, "vault_rw", "@meta.workset.path/vault/rw")
        doc = load_workset_settings_doc(tmp_path)
        assert resolve_workset_vault_ro(tmp_path, doc) == tmp_path / "vault" / "ro"
        assert resolve_workset_vault_rw(tmp_path, doc) == tmp_path / "vault" / "rw"

    def test_absolute_repoint_is_honoured(self, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        _repoint(tmp_path, "vault_ro", str(elsewhere / "ro"))
        _repoint(tmp_path, "vault_rw", str(elsewhere / "rw"))
        doc = load_workset_settings_doc(tmp_path)
        assert resolve_workset_vault_ro(tmp_path, doc) == elsewhere / "ro"
        assert resolve_workset_vault_rw(tmp_path, doc) == elsewhere / "rw"

    def test_bare_relative_repoint_is_refused_naming_both_readings(self, tmp_path):
        # ⚑ INVERTED BY [R147]: this used to assert the value anchored under the
        # workset root.  A vault is exactly the case the ruling is about — the
        # directory gets created and then holds the user's data.
        _repoint(tmp_path, "vault_ro", "store/readonly")
        doc = load_workset_settings_doc(tmp_path)
        with pytest.raises(SettingsError) as exc:
            resolve_workset_vault_ro(tmp_path, doc)
        message = str(exc.value)
        assert "workset.vault_ro" in message
        assert str(tmp_path / "store" / "readonly") in message
        assert str(Path.cwd() / "store" / "readonly") in message

    def test_tilde_repoint_expands_host_side(self, tmp_path, tmp_home):
        _repoint(tmp_path, "vault_rw", "~/outside/rw")
        doc = load_workset_settings_doc(tmp_path)
        # ``tmp_home`` returns the tmp ROOT; the isolated $HOME is its ``home`` child.
        assert resolve_workset_vault_rw(tmp_path, doc) == tmp_home / "home" / "outside" / "rw"

    def test_workset_path_ref_with_a_repointed_leaf(self, tmp_path):
        _repoint(tmp_path, "vault_ro", "@meta.workset.path/data/ro")
        doc = load_workset_settings_doc(tmp_path)
        assert resolve_workset_vault_ro(tmp_path, doc) == tmp_path / "data" / "ro"

    def test_unresolvable_ref_refuses_and_names_the_key(self, tmp_path):
        _repoint(tmp_path, "vault_ro", "@config.registry/ro")
        doc = load_workset_settings_doc(tmp_path)
        with pytest.raises(SettingsError) as exc:
            resolve_workset_vault_ro(tmp_path, doc)
        assert "workset.vault_ro" in str(exc.value)
        assert "@config.registry" in str(exc.value)


# ---------------------------------------------------------------------------
# NAMED mode — the workset root's own workset.yaml
# ---------------------------------------------------------------------------

class TestNamedModeVault:
    def test_unrepointed_lands_exactly_where_it_does_today(self, std, config, tmp_home):
        ws = create_workset("plain", tmp_home / "worksets" / "plain", std)
        source = tmp_home / "src-plain"
        source.mkdir()
        add_project(ws, "app", source)
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "app", std, config)
        assert proj.vault_ro_path == ws.root / "vault" / "ro" / "app"
        assert proj.vault_rw_path == ws.root / "vault" / "rw" / "app"
        assert (ws.root / "vault" / "ro" / "app").is_dir()
        assert (ws.root / "vault" / "rw" / "app").is_dir()

    def test_repoint_moves_both_the_resolved_path_and_the_created_dir(
        self, std, config, tmp_home,
    ):
        ws_root = tmp_home / "worksets" / "moved"
        ws = create_workset("moved", ws_root, std)
        elsewhere = tmp_home / "vaultstore"
        _repoint(ws_root, "vault_ro", str(elsewhere / "ro"))
        _repoint(ws_root, "vault_rw", str(elsewhere / "rw"))
        source = tmp_home / "src-moved"
        source.mkdir()
        add_project(ws, "app", source)

        # The DIRECTORY landed at the repoint, and NOT at the default.
        assert (elsewhere / "ro" / "app").is_dir()
        assert (elsewhere / "rw" / "app").is_dir()
        assert not (ws_root / "vault" / "ro" / "app").exists()
        assert not (ws_root / "vault" / "rw" / "app").exists()

        # And the launch-side path agrees with it.
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "app", std, config)
        assert proj.vault_ro_path == elsewhere / "ro" / "app"
        assert proj.vault_rw_path == elsewhere / "rw" / "app"

    def test_one_arm_repointed_moves_only_that_arm(self, std, config, tmp_home):
        ws_root = tmp_home / "worksets" / "half"
        ws = create_workset("half", ws_root, std)
        _repoint(ws_root, "vault_ro", str(tmp_home / "roonly"))
        source = tmp_home / "src-half"
        source.mkdir()
        add_project(ws, "app", source)
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "app", std, config)
        assert proj.vault_ro_path == tmp_home / "roonly" / "app"
        assert proj.vault_rw_path == ws_root / "vault" / "rw" / "app"

    def test_remove_files_deletes_the_repointed_leaves(self, std, tmp_home):
        ws_root = tmp_home / "worksets" / "rm"
        ws = create_workset("rm", ws_root, std)
        elsewhere = tmp_home / "rmstore"
        _repoint(ws_root, "vault_ro", str(elsewhere / "ro"))
        _repoint(ws_root, "vault_rw", str(elsewhere / "rw"))
        source = tmp_home / "src-rm"
        source.mkdir()
        add_project(ws, "app", source)
        assert (elsewhere / "ro" / "app").is_dir()

        remove_project(ws, "app", remove_files=True, std=std)
        assert not (elsewhere / "ro" / "app").exists()
        assert not (elsewhere / "rw" / "app").exists()
        # The shared arms themselves are never removed.
        assert (elsewhere / "ro").is_dir()


# ---------------------------------------------------------------------------
# PRIMARY mode — the PRIMARY workset's workset.yaml (there are no system.vault_* keys)
# ---------------------------------------------------------------------------

class TestPrimaryModeVault:
    def test_unrepointed_std_paths_are_unchanged(self, std):
        assert std.primary_vault_ro == std.primary_workset / "vault" / "ro"
        assert std.primary_vault_rw == std.primary_workset / "vault" / "rw"

    def test_primary_workset_repoint_moves_the_std_roots(self, std, config_file, tmp_home):
        _repoint(std.primary_workset, "vault_ro", str(tmp_home / "pv" / "ro"))
        _repoint(std.primary_workset, "vault_rw", str(tmp_home / "pv" / "rw"))
        reloaded = load_std_paths(load_config(config_file))
        assert reloaded.primary_vault_ro == tmp_home / "pv" / "ro"
        assert reloaded.primary_vault_rw == tmp_home / "pv" / "rw"

    def test_primary_box_create_lands_at_the_repoint(self, config_file, tmp_home, std):
        _repoint(std.primary_workset, "vault_ro", str(tmp_home / "pv" / "ro"))
        _repoint(std.primary_workset, "vault_rw", str(tmp_home / "pv" / "rw"))
        reloaded = load_std_paths(load_config(config_file))
        workspace = tmp_home / "code" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(reloaded, load_config(config_file), str(workspace),
                               initialize=True)
        assert proj.vault_ro_path == tmp_home / "pv" / "ro" / proj.name
        assert proj.vault_rw_path == tmp_home / "pv" / "rw" / proj.name
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()
        assert not (reloaded.primary_workset / "vault" / "ro" / proj.name).exists()

    def test_primary_unrepointed_box_create_is_unchanged(self, std, config, tmp_home):
        workspace = tmp_home / "code" / "plainapp"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)
        assert proj.vault_ro_path == std.primary_workset / "vault" / "ro" / proj.name
        assert proj.vault_rw_path == std.primary_workset / "vault" / "rw" / proj.name
        assert proj.vault_ro_path.is_dir()


# ---------------------------------------------------------------------------
# STANDALONE mode — the project root IS the workset root (no per-box leaf)
# ---------------------------------------------------------------------------

class TestStandaloneModeVault:
    def test_unrepointed_lands_exactly_where_it_does_today(self, std, config, project_dir):
        proj = resolve_standalone_project(std, config, str(project_dir), initialize=True)
        root = project_dir.resolve()
        assert proj.vault_ro_path == root / "vault" / "ro"
        assert proj.vault_rw_path == root / "vault" / "rw"
        assert proj.vault_ro_path.is_dir()
        # The default layout still gets its vault-level .gitignore.
        assert (root / "vault" / ".gitignore").read_text() == "rw/\n"

    def test_repoint_moves_the_created_dir(self, std, config, project_dir, tmp_home):
        root = project_dir.resolve()
        elsewhere = tmp_home / "sa-store"
        _repoint(root, "vault_ro", str(elsewhere / "ro"))
        _repoint(root, "vault_rw", str(elsewhere / "rw"))
        proj = resolve_standalone_project(std, config, str(project_dir), initialize=True)
        assert proj.vault_ro_path == elsewhere / "ro"
        assert proj.vault_rw_path == elsewhere / "rw"
        assert (elsewhere / "ro").is_dir()
        assert (elsewhere / "rw").is_dir()
        assert not (root / "vault" / "ro").exists()

    def test_out_of_root_repoint_writes_no_gitignore_beside_the_user_dir(
        self, std, config, project_dir, tmp_home,
    ):
        """⚑ The vault ``.gitignore`` belongs to the workset's own ``vault/`` skeleton dir."""
        root = project_dir.resolve()
        outside = tmp_home / "outside"
        _repoint(root, "vault_ro", str(outside / "ro"))
        _repoint(root, "vault_rw", str(outside / "rw"))
        proj = resolve_standalone_project(std, config, str(project_dir), initialize=True)
        # Anti-vacuity: the repoint really did take effect here.
        assert proj.vault_ro_path == outside / "ro"
        assert (outside / "ro").is_dir()
        assert not (outside / ".gitignore").exists()
