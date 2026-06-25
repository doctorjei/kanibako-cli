"""Unit tests for the CORE box-mount route (start.py ``_core_default_categories``
/ ``_build_core_mounts``).

The home / workspace / vault binds USED to be hardwired raw ``-v`` strings in
``container.py``; they now route through the settings category system (spec §2c
``box.bindings.*``) exactly like channels/masks/shares.  These tests assert:

* the resolved guest dests + mount options are byte-identical to the old
  hardwired binds (home == ``/home/agent`` with NO trailing slash);
* the enable_vault + source-``is_dir()`` GATE is reproduced at the injection
  site (no vault key when vault is disabled or the source dir is absent);
* the core mounts are emitted through the reconcile path (depth-sorted).
"""

from __future__ import annotations

import pytest

from kanibako.commands.start import (
    _build_core_mounts,
    _core_default_categories,
)
from kanibako.paths import resolve_project


@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


def _build(std, proj):
    """Resolve the core mounts as a {box_dest: (host_src, options)} map."""
    mounts = _build_core_mounts(
        std=std,
        proj=proj,
        agent_name="general",
        global_config_path=None,
        project_toml=None,
        workset_config_path=None,
        agent_config_path=None,
    )
    return {m.destination: (str(m.source), m.options) for m in mounts}, mounts


class TestCoreDefaultCategories:
    def test_home_and_workspace_keys(self, primary_proj, std):
        cats = _core_default_categories(std, primary_proj)
        # The home dest is the GUEST_HOME literal (``/home/agent``) with NO
        # trailing slash — byte-identical to the old hardwired ``-v`` dest.
        assert cats["box.bindings.rw.home"] == (
            f"{primary_proj.shell_path}:/home/agent"
        )
        assert cats["box.bindings.rw.workspace"] == (
            f"{primary_proj.project_path}:/home/agent/workspace"
        )

    def test_vault_keys_present_when_enabled_and_dirs_exist(
        self, primary_proj, std
    ):
        # A resolved primary proj (initialize=True) has vault enabled + dirs made.
        assert primary_proj.enable_vault
        assert primary_proj.vault_ro_path.is_dir()
        assert primary_proj.vault_rw_path.is_dir()
        cats = _core_default_categories(std, primary_proj)
        assert cats["box.bindings.ro.vault"] == (
            f"{primary_proj.vault_ro_path}:/home/agent/vault/ro"
        )
        assert cats["box.bindings.rw.vault"] == (
            f"{primary_proj.vault_rw_path}:/home/agent/vault/rw"
        )

    def test_vault_keys_omitted_when_disabled(self, primary_proj, std):
        """GATE: enable_vault=False ⇒ NO vault keys injected (even if dirs exist).

        Reconcile's L7 rw-branch would mkdir a missing rw source unconditionally,
        so the gate MUST live at the injection site to preserve skip-if-missing.
        """
        import dataclasses

        proj = dataclasses.replace(primary_proj, enable_vault=False)
        cats = _core_default_categories(std, proj)
        assert "box.bindings.ro.vault" not in cats
        assert "box.bindings.rw.vault" not in cats
        # Home/workspace are unconditional.
        assert "box.bindings.rw.home" in cats
        assert "box.bindings.rw.workspace" in cats

    def test_vault_keys_omitted_when_source_missing(self, primary_proj, std):
        """GATE: a vault source dir that does not exist ⇒ its key is NOT injected
        (matches the old hardwired ``is_dir()`` guard)."""
        import dataclasses

        missing_ro = primary_proj.metadata_path / "nonexistent-vault-ro"
        missing_rw = primary_proj.metadata_path / "nonexistent-vault-rw"
        assert not missing_ro.exists()
        assert not missing_rw.exists()
        proj = dataclasses.replace(
            primary_proj, vault_ro_path=missing_ro, vault_rw_path=missing_rw
        )
        cats = _core_default_categories(std, proj)
        assert "box.bindings.ro.vault" not in cats
        assert "box.bindings.rw.vault" not in cats


class TestCoreMountAssembly:
    def test_dests_and_options_match_legacy(self, primary_proj, std):
        by_dest, _ = _build(std, primary_proj)
        assert by_dest["/home/agent"][0] == str(primary_proj.shell_path)
        assert by_dest["/home/agent"][1] == "Z,U"
        assert by_dest["/home/agent/workspace"][0] == str(primary_proj.project_path)
        assert by_dest["/home/agent/workspace"][1] == "Z,U"
        assert by_dest["/home/agent/vault/ro"][0] == str(primary_proj.vault_ro_path)
        assert by_dest["/home/agent/vault/ro"][1] == "ro"
        assert by_dest["/home/agent/vault/rw"][0] == str(primary_proj.vault_rw_path)
        assert by_dest["/home/agent/vault/rw"][1] == "Z,U"

    def test_home_dest_has_no_trailing_slash(self, primary_proj, std):
        by_dest, _ = _build(std, primary_proj)
        assert "/home/agent" in by_dest
        assert "/home/agent/" not in by_dest

    def test_mounts_depth_sorted_home_first(self, primary_proj, std):
        _, mounts = _build(std, primary_proj)
        dests = [m.destination for m in mounts]
        assert dests[0] == "/home/agent"
        assert dests.index("/home/agent") < dests.index("/home/agent/workspace")
        assert dests.index("/home/agent/workspace") < dests.index(
            "/home/agent/vault/ro"
        )

    def test_kanibako_cli_binds_are_ro(self, primary_proj, std):
        """The kanibako package + entry script route as ro core binds
        (box.bindings.ro.kani_{pkg,bin}), replacing the old hardwired
        _kanibako_mounts() raw Mounts in the MAIN launch path."""
        cats = _core_default_categories(std, primary_proj)
        assert "box.bindings.ro.kani_pkg" in cats
        assert "box.bindings.ro.kani_bin" in cats
        assert cats["box.bindings.ro.kani_pkg"].endswith(":/opt/kanibako/kanibako")
        assert cats["box.bindings.ro.kani_bin"].endswith(
            ":/home/agent/.local/bin/kanibako"
        )
        # Emitted (sources exist in the dev install) as ro mounts.
        by_dest, _ = _build(std, primary_proj)
        assert by_dest["/opt/kanibako/kanibako"][1] == "ro"
        assert by_dest["/home/agent/.local/bin/kanibako"][1] == "ro"

    def test_vault_disabled_drops_only_vault_binds(self, primary_proj, std):
        """enable_vault=False removes the two vault dests but keeps home /
        workspace / the kanibako CLI binds."""
        import dataclasses

        proj = dataclasses.replace(primary_proj, enable_vault=False)
        by_dest, _ = _build(std, proj)
        assert "/home/agent/vault/ro" not in by_dest
        assert "/home/agent/vault/rw" not in by_dest
        assert "/home/agent" in by_dest
        assert "/home/agent/workspace" in by_dest
