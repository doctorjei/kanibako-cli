"""The DESTRUCTIVE and lifecycle verbs honour a repointed ``workset.{vault_ro,vault_rw}``.

``57742a05`` made both keys resolve on the paths side.  This file pins the CONSUMERS —
the verbs that DELETE, MOVE or REPORT a vault — because a key that resolves everywhere
except where a path is ``rm -rf``\\ ed is worse than one that never resolved: the box's
real vault is orphaned while a directory it never used is what gets removed.

⚑ Two rules these tests hold the code to, and they are NOT the same rule:

* A per-box LEAF under a resolved arm is kanibako's to delete — it followed the repoint,
  so the deletion must follow it too (PRIMARY, NAMED).
* An ARM ITSELF that the user pointed OUT OF the box root is the USER'S store, and no
  verb may ``rm -rf`` it (STANDALONE, whose arm *is* the box's vault, with no leaf).
  It is reported and left, never silently skipped and never deleted.
"""

from __future__ import annotations

import argparse

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)


def _repoint(root, key, value):
    """Write ``workset.<key> = value`` into *root*'s workset.yaml."""
    write_nested_key(root / "workset.yaml", ("workset",), key, value)


def _reload(config_file):
    """A std/config pair rebuilt AFTER a repoint — the primary vault roots are resolved once."""
    config = load_config(config_file)
    return load_std_paths(config), config


# ---------------------------------------------------------------------------
# ITEM 1 — the PRIMARY move/convert source cleanup (``_remove_old_metadata``)
# ---------------------------------------------------------------------------

class TestPrimarySourceCleanupFollowsTheRepoint:
    """``_lifecycle._remove_old_metadata`` guards its vault ``rmtree`` with a containment
    test.  The subject WAS the workset root, which could never fail while the arms were
    composed under it; a repoint out of the root now makes it skip."""

    def _box_with_repointed_vault(self, config_file, tmp_home, *, repoint=True):
        std, config = _reload(config_file)
        if repoint:
            _repoint(std.primary_workset, "vault_ro", str(tmp_home / "pv" / "ro"))
            _repoint(std.primary_workset, "vault_rw", str(tmp_home / "pv" / "rw"))
        std, config = _reload(config_file)
        workspace = tmp_home / "code" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)
        return std, config, workspace, proj

    def test_repointed_per_box_vault_is_removed_with_the_box(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.box._lifecycle import (
            _default_state_from_meta,
            _remove_old_metadata,
        )

        std, config, workspace, proj = self._box_with_repointed_vault(config_file, tmp_home)
        # Anti-vacuity: the repoint really took effect, and there is real content to lose.
        assert proj.vault_ro_path == tmp_home / "pv" / "ro" / proj.name
        (proj.vault_ro_path / "keep.txt").write_text("box data")
        (proj.vault_rw_path / "keep.txt").write_text("box data")

        state = _default_state_from_meta(workspace, std)
        assert state is not None
        _remove_old_metadata(state, std, config)

        # The per-box LEAVES went with the box...
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()
        # ...and the SHARED ARMS, which hold every other box's vault, did not.
        assert (tmp_home / "pv" / "ro").is_dir()
        assert (tmp_home / "pv" / "rw").is_dir()

    def test_unrepointed_behaviour_is_exactly_unchanged(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Anti-vacuity twin: the default layout must behave as it does today."""
        from kanibako.commands.box._lifecycle import (
            _default_state_from_meta,
            _remove_old_metadata,
        )

        std, config, workspace, proj = self._box_with_repointed_vault(
            config_file, tmp_home, repoint=False,
        )
        assert proj.vault_ro_path == std.primary_workset / "vault" / "ro" / proj.name
        (proj.vault_ro_path / "keep.txt").write_text("box data")

        state = _default_state_from_meta(workspace, std)
        _remove_old_metadata(state, std, config)

        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()
        assert (std.primary_workset / "vault" / "ro").is_dir()

    def test_a_path_that_is_the_arm_itself_is_never_removed(
        self, config_file, tmp_home, credentials_dir,
    ):
        """🛑 STRICT containment.  A leafless vault path is the SHARED arm — deleting it
        would take every box's vault.  ``relative_to`` alone ACCEPTS an equal path."""
        from kanibako.commands.box._lifecycle import (
            _default_state_from_meta,
            _remove_old_metadata,
        )

        std, config, workspace, proj = self._box_with_repointed_vault(
            config_file, tmp_home, repoint=False,
        )
        (proj.vault_ro_path / "keep.txt").write_text("box data")
        other = std.primary_vault_ro / "someone-else"
        other.mkdir(parents=True, exist_ok=True)
        (other / "keep.txt").write_text("another box's data")

        state = _default_state_from_meta(workspace, std)
        # Degenerate state: the arm with no per-box leaf.
        state.vault_ro = std.primary_vault_ro
        state.vault_rw = std.primary_vault_rw
        _remove_old_metadata(state, std, config)

        assert std.primary_vault_ro.is_dir()
        assert (other / "keep.txt").read_text() == "another box's data"


# ---------------------------------------------------------------------------
# ITEM 2a — ``box move`` INTO a workset whose vault is repointed
# ---------------------------------------------------------------------------

class TestMoveIntoRepointedWorksetReportsTheRealVault:
    def test_moved_box_vault_is_the_dir_add_project_actually_created(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``_to_workset`` composed ``target_ws.vault_dir/'ro'/<name>`` while
        ``add_project`` created the leaf under the RESOLVED arm — two answers for one
        box.  ⚑ The probe is the RETURNED ``ProjectState``: that is where the composed
        literal lives, and asserting on the resolver instead would pass either way."""
        from kanibako.commands.box._lifecycle import (
            INPLACE,
            TargetSpec,
            execute_lifecycle,
            resolve_lifecycle_target,
        )
        from kanibako.project.workset import create_workset, load_workset
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project

        std, config = _reload(config_file)
        ws_root = tmp_home / "ws_root"
        create_workset("ws", ws_root, std)
        store = tmp_home / "wsvault"
        _repoint(ws_root, "vault_ro", str(store / "ro"))
        _repoint(ws_root, "vault_rw", str(store / "rw"))

        pdir = tmp_home / "proj"
        pdir.mkdir()
        (pdir / "file.txt").write_text("hi")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        state = resolve_lifecycle_target(str(pdir), std, config)
        new_state = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="ws", name=None),
            std, config, force=True,
        )

        ws = load_workset(ws_root, "ws")
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "proj", std, config,
                                       initialize=False)
        # The dir that EXISTS is the one under the resolved arm...
        assert (store / "ro" / "proj").is_dir()
        assert not (ws_root / "vault" / "ro" / "proj").exists()
        # ...the box resolves to it...
        assert proj.vault_ro_path == store / "ro" / "proj"
        assert proj.vault_rw_path == store / "rw" / "proj"
        # ...and so does the state the lifecycle HANDS BACK.
        assert new_state.vault_ro == store / "ro" / "proj"
        assert new_state.vault_rw == store / "rw" / "proj"

    def test_unrepointed_move_into_a_workset_is_unchanged(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.box._lifecycle import (
            INPLACE,
            TargetSpec,
            execute_lifecycle,
            resolve_lifecycle_target,
        )
        from kanibako.project.workset import create_workset, load_workset
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project

        std, config = _reload(config_file)
        ws_root = tmp_home / "ws_plain"
        create_workset("wsp", ws_root, std)
        pdir = tmp_home / "projp"
        pdir.mkdir()
        (pdir / "file.txt").write_text("hi")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        state = resolve_lifecycle_target(str(pdir), std, config)
        new_state = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="wsp", name=None),
            std, config, force=True,
        )

        ws = load_workset(ws_root, "wsp")
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "projp", std, config,
                                       initialize=False)
        assert proj.vault_ro_path == ws_root / "vault" / "ro" / "projp"
        assert (ws_root / "vault" / "ro" / "projp").is_dir()
        assert new_state.vault_ro == ws_root / "vault" / "ro" / "projp"
        assert new_state.vault_rw == ws_root / "vault" / "rw" / "projp"


# ---------------------------------------------------------------------------
# ITEMS 2b / 5 / 6 — the three STANDALONE teardown paths
# ---------------------------------------------------------------------------

def _standalone_with_vault(config_file, tmp_home, name, vault_repoint=None):
    """A materialized standalone box at ``tmp_home/<name>``, vault optionally repointed.

    Returns ``(std, config, root, vault_ro, vault_rw)`` with a marker file in each arm.
    """
    std, config = _reload(config_file)
    root = tmp_home / name
    root.mkdir()
    (root / "file.txt").write_text("x")
    if vault_repoint is not None:
        # ⚑ The root workset.yaml IS the standalone workset tier — write the repoint
        # BEFORE the resolver first reads it, exactly as a user would.
        _repoint(root, "vault_ro", str(vault_repoint / "ro"))
        _repoint(root, "vault_rw", str(vault_repoint / "rw"))
    proj = resolve_standalone_project(std, config, project_dir=str(root), initialize=True)
    (proj.vault_ro_path / "keep.txt").write_text("vault data")
    (proj.vault_rw_path / "keep.txt").write_text("vault data")
    return std, config, root, proj.vault_ro_path, proj.vault_rw_path


class TestStandalonePurgeFollowsAnInRootRepoint:
    """An IN-ROOT repoint (``vault_ro: store/ro``) is unambiguously kanibako's to delete:
    it is inside the box root the purge is already clearing."""

    def test_purge_removes_an_in_root_repoint(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import _rm_standalone

        std, config = _reload(config_file)
        root = tmp_home / "sa_store"
        root.mkdir()
        _repoint(root, "vault_ro", "store/ro")
        _repoint(root, "vault_rw", "store/rw")
        proj = resolve_standalone_project(std, config, project_dir=str(root),
                                          initialize=True)
        assert proj.vault_ro_path == root / "store" / "ro"
        (proj.vault_ro_path / "keep.txt").write_text("vault data")

        _rm_standalone(std, "sa_store", root,
                       argparse.Namespace(purge=True, force=True))
        capsys.readouterr()
        assert not (root / "store" / "ro").exists()
        assert not (root / "store" / "rw").exists()

    def test_purge_of_the_default_layout_is_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import _rm_standalone

        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "sa_plain",
        )
        assert vro == root / "vault" / "ro"
        _rm_standalone(std, "sa_plain", root,
                       argparse.Namespace(purge=True, force=True))
        capsys.readouterr()
        assert not (root / "vault").exists()
        assert not (root / "box_data").exists()
        # The user's own tree is never taken.
        assert (root / "file.txt").is_file()

    def test_out_of_root_arm_is_reported_and_left(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """🛑 The arm IS the box's vault here — no per-box leaf.  An absolute repoint
        names the USER'S store, so purge reports it and does not ``rm -rf`` it."""
        from kanibako.commands.box._parser import _rm_standalone

        outside = tmp_home / "my-store"
        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "sa_out", vault_repoint=outside,
        )
        assert vro == outside / "ro"
        _rm_standalone(std, "sa_out", root,
                       argparse.Namespace(purge=True, force=True))
        out = capsys.readouterr().out
        assert (outside / "ro" / "keep.txt").read_text() == "vault data"
        assert (outside / "rw" / "keep.txt").read_text() == "vault data"
        # Not silent: the retained path is named.
        assert str(outside / "ro") in out
        assert not (root / "box_data").exists()


class TestTeardownResolvesBeforeItDeletes:
    """⚑ The resolve is a PRE-FLIGHT.  An unresolvable ``workset.vault_*`` makes the
    resolver refuse and name the key; that refusal must land while the box is still
    WHOLE.  Resolving mid-teardown left a half-purged box behind the traceback."""

    def _poisoned(self, config_file, tmp_home, name):
        std, config = _reload(config_file)
        root = tmp_home / name
        root.mkdir()
        (root / "file.txt").write_text("x")
        resolve_standalone_project(std, config, project_dir=str(root), initialize=True)
        # ``@config.registry`` is not reachable on the no-snapshot paths side.
        _repoint(root, "vault_ro", "@config.registry/ro")
        return std, config, root

    def test_unresolvable_vault_refuses_the_purge_and_touches_nothing(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import _rm_standalone
        from kanibako.settings.settings_resolve import SettingsError

        std, config, root = self._poisoned(config_file, tmp_home, "poisoned")
        with pytest.raises(SettingsError) as exc:
            _rm_standalone(std, "poisoned", root,
                           argparse.Namespace(purge=True, force=True))
        capsys.readouterr()
        # The error NAMES the key...
        assert "workset.vault_ro" in str(exc.value)
        # ...and the box is untouched, not half-removed.
        assert (root / "box_data").is_dir()
        assert (root / "vault").is_dir()
        assert (root / "workset.yaml").is_file()

    def test_a_missing_workset_yaml_still_purges_the_default_layout(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Anti-vacuity: no settings file is not an error — it is the declared default."""
        from kanibako.commands.box._parser import _rm_standalone

        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "nofile",
        )
        (root / "workset.yaml").unlink()
        _rm_standalone(std, "nofile", root,
                       argparse.Namespace(purge=True, force=True))
        capsys.readouterr()
        assert not (root / "vault").exists()


class TestCleanPurgeFollowsTheRepoint:
    def test_clean_purge_removes_an_in_root_repoint(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        std, config = _reload(config_file)
        root = tmp_home / "cl_store"
        root.mkdir()
        _repoint(root, "vault_ro", "store/ro")
        _repoint(root, "vault_rw", "store/rw")
        proj = resolve_standalone_project(std, config, project_dir=str(root),
                                          initialize=True)
        (proj.vault_ro_path / "keep.txt").write_text("vault data")

        assert _purge_one(std, config, str(root), force=True) == 0
        capsys.readouterr()
        assert not (root / "store" / "ro").exists()

    def test_clean_purge_of_the_default_layout_is_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "cl_plain",
        )
        assert _purge_one(std, config, str(root), force=True) == 0
        capsys.readouterr()
        assert not (root / "vault").exists()
        assert (root / "file.txt").is_file()

    def test_clean_purge_leaves_an_out_of_root_arm(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        outside = tmp_home / "cl-store"
        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "cl_out", vault_repoint=outside,
        )
        assert _purge_one(std, config, str(root), force=True) == 0
        out = capsys.readouterr().out
        assert (outside / "ro" / "keep.txt").read_text() == "vault data"
        assert str(outside / "ro") in out


class TestStandaloneMoveSourceCleanupFollowsTheRepoint:
    """``_remove_old_metadata``'s STANDALONE branch — the same composition, on move."""

    def test_move_out_of_a_standalone_root_takes_an_in_root_repointed_vault(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._lifecycle import run_move

        std, config = _reload(config_file)
        root = tmp_home / "mv_store"
        root.mkdir()
        (root / "file.txt").write_text("x")
        _repoint(root, "vault_ro", "store/ro")
        _repoint(root, "vault_rw", "store/rw")
        proj = resolve_standalone_project(std, config, project_dir=str(root),
                                          initialize=True)
        assert proj.vault_ro_path == root / "store" / "ro"

        dest = tmp_home / "mv_dest"
        assert run_move(argparse.Namespace(
            old=str(root), new=str(dest), force=True, to_default=True,
            to_standalone=False, to_workset=None, name=None,
        )) == 0
        capsys.readouterr()
        assert not (root / "store" / "ro").exists()

    def test_move_out_of_a_default_layout_standalone_is_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._lifecycle import run_move

        std, config, root, vro, vrw = _standalone_with_vault(
            config_file, tmp_home, "mv_plain",
        )
        dest = tmp_home / "mv_plain_dest"
        assert run_move(argparse.Namespace(
            old=str(root), new=str(dest), force=True, to_default=True,
            to_standalone=False, to_workset=None, name=None,
        )) == 0
        capsys.readouterr()
        assert not (root / "vault").exists()


# ---------------------------------------------------------------------------
# ITEM 3 — ``archive``'s gone-path stub
# ---------------------------------------------------------------------------

class TestArchiveStubNamesTheRealVault:
    """``_stub_project`` composed the vault off the WORKSPACE.  That was never a vault
    path in any mode — it is filler added (``840f7907``) only to satisfy a required
    dataclass field, and it is read by nothing on the archive path."""

    def test_stub_vault_is_the_primary_arm_not_the_workspace(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.archive import _stub_project

        std, config = _reload(config_file)
        _repoint(std.primary_workset, "vault_ro", str(tmp_home / "av" / "ro"))
        _repoint(std.primary_workset, "vault_rw", str(tmp_home / "av" / "rw"))
        std, config = _reload(config_file)

        workspace = tmp_home / "gone" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)

        stub = _stub_project(proj.metadata_path, workspace, std, config)
        assert stub.vault_ro_path == std.primary_vault_ro / stub.name
        assert stub.vault_rw_path == std.primary_vault_rw / stub.name
        # The workspace composition is gone.
        assert stub.vault_ro_path != workspace / "vault" / "ro"

    def test_stub_vault_unrepointed_is_the_default_primary_arm(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.archive import _stub_project

        std, config = _reload(config_file)
        workspace = tmp_home / "gone2" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)

        stub = _stub_project(proj.metadata_path, workspace, std, config)
        assert stub.vault_ro_path == std.primary_workset / "vault" / "ro" / stub.name

    def test_stub_for_a_vanished_workspace_still_names_a_real_arm(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The ``project_path is None`` branch — no workspace to compose off at all."""
        from kanibako.commands.archive import _stub_project

        std, config = _reload(config_file)
        workspace = tmp_home / "gone3" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)

        stub = _stub_project(proj.metadata_path, None, std, config)
        assert stub.vault_ro_path == std.primary_vault_ro / stub.name
        assert "unknown-" not in str(stub.vault_ro_path)


# ---------------------------------------------------------------------------
# ITEM 4 — helper vaults answer NO workset key (measured, not assumed)
# ---------------------------------------------------------------------------

class TestHelperVaultIsNotAWorksetVault:
    """MEASURED: ``start.py`` derives ``helpers_dir = proj.shell_path / "helpers"`` — the
    DIRECTOR box's HOME bind source.  A helper's vault is a private leaf of that home, is
    created by ``create_helper_dirs`` at the same literal, and is not a workset member, so
    it answers no ``workset.vault_*`` key and must NOT be resolved."""

    def test_helper_vault_tracks_the_box_home_not_the_repointed_workset_vault(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.channels.helpers import create_helper_dirs

        std, config = _reload(config_file)
        _repoint(std.primary_workset, "vault_ro", str(tmp_home / "hv" / "ro"))
        _repoint(std.primary_workset, "vault_rw", str(tmp_home / "hv" / "rw"))
        std, config = _reload(config_file)

        workspace = tmp_home / "hcode" / "app"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, str(workspace), initialize=True)

        # The derivation start.py uses, and the one helper_listener re-composes.
        helpers_dir = proj.shell_path / "helpers"
        create_helper_dirs(helpers_dir, 1)
        listener_ro = helpers_dir / "1" / "vault" / "ro"

        # The two halves agree with each other...
        assert listener_ro.is_dir()
        # ...it lives under the box HOME...
        assert proj.shell_path in listener_ro.parents
        # ...and it is untouched by the workset repoint that moved the box's own vault.
        assert proj.vault_ro_path == tmp_home / "hv" / "ro" / proj.name
        assert tmp_home / "hv" / "ro" not in listener_ro.parents


# ---------------------------------------------------------------------------
# ITEM 2c — ``_duplicate``'s vault ``.gitignore`` names the SKELETON PARENT (a non-key)
# ---------------------------------------------------------------------------

class TestDuplicateGitignoreTargetsTheSkeletonParent:
    def test_a_fresh_duplicate_root_carries_no_vault_repoint(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``establish_standalone`` writes the duplicate's ROOT workset.yaml with
        ``workset.kuid`` alone, so ``new_path/'vault'`` IS the resolved skeleton parent
        by construction — there is no key for the literal to disagree with."""
        from kanibako.project.workset import resolve_workset_vault_pair
        from kanibako.settings.paths import establish_standalone

        std, config = _reload(config_file)
        new_path = tmp_home / "dupdest"
        new_path.mkdir()
        establish_standalone(std, new_path, enable_vault=True)

        ro, rw = resolve_workset_vault_pair(new_path)
        assert ro == new_path / "vault" / "ro"
        assert rw == new_path / "vault" / "rw"
        assert ro.parent == new_path / "vault"
