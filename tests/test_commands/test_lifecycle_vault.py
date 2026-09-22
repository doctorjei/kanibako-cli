"""Vault-carry tests for the lifecycle engine (commands/box/_lifecycle.py).

The destination vault leaves are created EMPTY and the source leaves are then
deleted, so without the carry in ``_carry_vault_contents`` a move/convert
destroys the vault's contents.  His ruling (96th) prices the vault as a STORE:
contents must survive a relocation.  These tests pin the carry on every path
that relocates the vault, plus the two reuse-in-place edges (whose teardown is
skipped — nothing to carry) and the shared-arm guard (warn-on-skip, never copy
and never delete foreign ground).
"""

from __future__ import annotations

import pytest

from kanibako.commands.box._lifecycle import (
    INPLACE,
    UNCHANGED,
    ProjectState,
    TargetSpec,
    _copy_vault_leaf_contents,
    _vault_carry_pairs,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.errors import ProjectError
from kanibako.settings.config import load_config
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    BoxMode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.project.workset import (
    add_project,
    create_workset,
    load_workset,
)


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    """Loaded config + std + temp home."""
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std, tmp_home


def _conf_yes():
    return lambda: True


def _make_default(env, name="proj", contents="hello", enable_vault=None):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    kwargs = {"project_dir": str(project_dir), "initialize": True}
    if enable_vault is not None:
        kwargs["enable_vault"] = enable_vault
    resolve_project(std, config, **kwargs)
    return project_dir


def _make_standalone(env, name="sa", contents="hi"):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    resolve_standalone_project(
        std, config, project_dir=str(project_dir), initialize=True,
    )
    return project_dir


def _seed_vault(state):
    """Write ro + rw contents incl. a nested tree; return the ``{rel: text}`` map."""
    seed = {
        "ro-note.txt": "read-only store",
        "rw-note.txt": "read-write store",
        "sub/deep.txt": "nested store",
    }
    state.vault_ro.mkdir(parents=True, exist_ok=True)
    (state.vault_ro / "ro-note.txt").write_text(seed["ro-note.txt"])
    state.vault_rw.mkdir(parents=True, exist_ok=True)
    (state.vault_rw / "rw-note.txt").write_text(seed["rw-note.txt"])
    (state.vault_rw / "sub").mkdir(parents=True, exist_ok=True)
    (state.vault_rw / "sub" / "deep.txt").write_text(seed["sub/deep.txt"])
    return seed


def _assert_carried(new_state, seed):
    """The destination leaves hold every seeded file, byte for byte."""
    assert (new_state.vault_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
    assert (new_state.vault_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
    assert (new_state.vault_rw / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]


def _repoint(root, key, value):
    write_nested_key(root / "workset.yaml", ("workset",), key, value)


class TestVaultCarry:
    def test_primary_move_carries_vault(self, env):
        """Headline repro: a same-owner rename emptied the vault; now it carries."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        dest = tmp_home / "newhome"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        _assert_carried(new, seed)
        # Source leaves deleted (teardown ran) — contents survive at the destination.
        assert not state.vault_ro.exists()
        assert not state.vault_rw.exists()

    def test_same_name_primary_move_keeps_vault_in_place(self, env):
        """Reuse-in-place edge: an explicit same-name move reuses the source leaf."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        dest = tmp_home / "newhome2"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership=UNCHANGED, name="proj"),
            std, config, confirm=_conf_yes(),
        )
        assert new.name == "proj"
        # Same name ⇒ the destination leaf IS the source leaf (teardown skipped).
        assert new.vault_ro.resolve() == state.vault_ro.resolve()
        assert new.vault_rw.resolve() == state.vault_rw.resolve()
        _assert_carried(new, seed)

    def test_standalone_to_primary_convert_carries_vault(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        _assert_carried(new, seed)
        assert not state.vault_ro.exists()
        assert not state.vault_rw.exists()

    def test_primary_to_standalone_convert_carries_vault(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        dest = tmp_home / "combo_dest"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        _assert_carried(new, seed)
        assert not state.vault_ro.exists()
        assert not state.vault_rw.exists()

    def test_standalone_to_standalone_move_carries_vault(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        dest = tmp_home / "sa_moved"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        _assert_carried(new, seed)
        assert not state.vault_ro.exists()
        assert not state.vault_rw.exists()

    def test_standalone_rename_in_place_keeps_vault(self, env):
        """Reuse-in-place edge: a rename at its own root reuses the vault arms."""
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone", name="sa2"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        assert new.vault_ro.resolve() == state.vault_ro.resolve()
        _assert_carried(new, seed)

    def test_named_to_primary_convert_carries_vault(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        external = tmp_home / "ext_repo"
        external.mkdir()
        add_project(ws, "ep", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        seed = _seed_vault(state)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        _assert_carried(new, seed)
        # The workset source leaves are gone (remove_project ran).
        assert not (ws.root / "vault" / "ro" / "ep").exists()
        assert not (ws.root / "vault" / "rw" / "ep").exists()

    def test_workset_to_workset_move_carries_vault(self, env):
        """ws→ws goes through the stash conduit: copy out before release, land after."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        seed = _seed_vault(state)
        dest = ws_b.workspaces_dir / "b1"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wsb"
        _assert_carried(new, seed)
        assert not (ws_a.root / "vault" / "ro" / "b1").exists()
        assert not (ws_a.root / "vault" / "rw" / "b1").exists()
        assert any(p.name == "b1" for p in load_workset(ws_b.root, ws_b.name).projects)

    def test_workset_to_workset_unwind_restores_source_vault(self, env, monkeypatch):
        """A leg-2 failure restores membership AND the source vault from the stash."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        seed = _seed_vault(state)
        src_ro, src_rw = state.vault_ro, state.vault_rw
        dest = ws_b.workspaces_dir / "b1"
        real_copy = _copy_vault_leaf_contents

        def _fail_leg2(src, dst):
            # Leg 1 lands inside the mkdtemp stash; leg 2 lands under ws_b.
            if ws_b.root.resolve() in dst.resolve().parents:
                raise OSError("injected leg-2 copy failure")
            return real_copy(src, dst)

        monkeypatch.setattr(
            "kanibako.commands.box._lifecycle._copy_vault_leaf_contents",
            _fail_leg2,
        )
        with pytest.raises(OSError, match="injected leg-2"):
            execute_lifecycle(
                state, TargetSpec(location=dest, ownership="wsb"),
                std, config, confirm=_conf_yes(),
            )
        # Membership is restored ...
        assert any(p.name == "b1" for p in load_workset(ws_a.root, ws_a.name).projects)
        # ... and the source vault is restored whole, byte for byte.
        assert (src_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        assert (src_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        assert (src_rw / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]

    def test_disabled_vault_move_completes_without_vault(self, env):
        """No destination vault exists when disabled — the carry stays hands-off."""
        config, std, tmp_home = env
        pdir = _make_default(env, enable_vault=False)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.enable_vault is False
        dest = tmp_home / "novault_dest"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert not new.vault_ro.exists()
        assert not new.vault_rw.exists()

    def test_repoint_orphan_survives_move(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A repoint orphans the old leaf; the move neither copies nor deletes it."""
        config = load_config(config_file)
        std = load_std_paths(config)
        env = (config, std, tmp_home)
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        old_rw_leaf = state.vault_rw
        _repoint(std.primary_workset, "vault_rw", str(tmp_home / "pv" / "rw"))
        # ⚑ The primary vault roots are resolved ONCE at std load — rebuild after
        # the repoint, or the move runs entirely on the old arms (and correctly
        # carries + deletes there, which is NOT this test).
        config2 = load_config(config_file)
        std2 = load_std_paths(config2)
        fresh = resolve_lifecycle_target(str(pdir), std2, config2)
        assert fresh.vault_rw == tmp_home / "pv" / "rw" / fresh.name
        dest = tmp_home / "repoint_dest"
        new = execute_lifecycle(
            fresh, TargetSpec(location=dest, ownership=UNCHANGED),
            std2, config2, confirm=_conf_yes(),
        )
        # The untouched arm still carries normally.
        assert (new.vault_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        # The orphaned leaf survives on disk, byte for byte — never deleted.
        assert (old_rw_leaf / "rw-note.txt").read_text() == seed["rw-note.txt"]
        assert (old_rw_leaf / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]


class TestCopyVaultLeafContents:
    def test_same_path_is_a_noop(self, tmp_path):
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        (leaf / "f.txt").write_text("x")
        _copy_vault_leaf_contents(leaf, leaf)
        assert (leaf / "f.txt").read_text() == "x"

    def test_missing_source_is_a_noop(self, tmp_path):
        _copy_vault_leaf_contents(tmp_path / "absent", tmp_path / "dst")
        assert not (tmp_path / "dst").exists()

    def test_merges_into_existing_destination(self, tmp_path):
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("a")
        (src / "sub" / "b.txt").write_text("b")
        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "prior.txt").write_text("prior")
        _copy_vault_leaf_contents(src, dst)
        assert (dst / "a.txt").read_text() == "a"
        assert (dst / "sub" / "b.txt").read_text() == "b"
        assert (dst / "prior.txt").read_text() == "prior"

    def test_destination_inside_source_refuses(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        with pytest.raises(ProjectError, match="inside the source"):
            _copy_vault_leaf_contents(src, src / "child")
        # Nothing was created inside the source.
        assert not (src / "child").exists()


class TestCarryGuardContract:
    """The carry mirrors the teardown's risk model: hands off foreign ground, loudly."""

    def _primary_state(self, tmp_path, vault_ro, vault_rw):
        return ProjectState(
            owner="primary", mode=BoxMode.primary, name="x",
            workspace_path=tmp_path / "ws", metadata_path=tmp_path / "meta",
            shell_path=tmp_path / "meta" / "home",
            vault_ro=vault_ro, vault_rw=vault_rw,
        )

    def test_foreign_leaf_is_skipped_with_warning(self, env, tmp_path, capsys):
        """A primary source outside its arm is not carried — and says so."""
        _config, std, _tmp_home = env
        foreign = tmp_path / "foreign"
        (foreign / "ro").mkdir(parents=True)
        (foreign / "ro" / "keep.txt").write_text("keep")
        (foreign / "rw").mkdir(parents=True)
        state = self._primary_state(tmp_path, foreign / "ro", foreign / "rw")
        pairs = _vault_carry_pairs(state, std, tmp_path / "dst_ro", tmp_path / "dst_rw")
        assert pairs == []
        err = capsys.readouterr().err
        assert "not carrying vault contents" in err
        assert str(foreign / "ro") in err

    def test_empty_foreign_leaf_skips_silently(self, env, tmp_path, capsys):
        """No contents, no warning — an empty skip is not news."""
        _config, std, _tmp_home = env
        foreign = tmp_path / "foreign"
        (foreign / "ro").mkdir(parents=True)
        (foreign / "rw").mkdir(parents=True)
        state = self._primary_state(tmp_path, foreign / "ro", foreign / "rw")
        assert _vault_carry_pairs(state, std, tmp_path / "d_ro", tmp_path / "d_rw") == []
        assert capsys.readouterr().err == ""

    def test_retained_standalone_arm_is_noted_not_duplicated(
        self, env, tmp_path, capsys,
    ):
        """A standalone store outside the root stays put; the new vault starts empty."""
        _config, std, _tmp_home = env
        root = tmp_path / "saroot"
        root.mkdir()
        outside = tmp_path / "store"
        (outside / "ro").mkdir(parents=True)
        (outside / "ro" / "keep.txt").write_text("keep")
        (outside / "rw").mkdir(parents=True)
        _repoint(root, "vault_ro", str(outside / "ro"))
        _repoint(root, "vault_rw", str(outside / "rw"))
        state = ProjectState(
            owner="standalone", mode=BoxMode.standalone, name="sa",
            workspace_path=root / "workspace", metadata_path=root,
            shell_path=root / "box_data" / "home",
            vault_ro=outside / "ro", vault_rw=outside / "rw",
        )
        pairs = _vault_carry_pairs(state, std, tmp_path / "dst_ro", tmp_path / "dst_rw")
        assert pairs == []
        assert "starts empty" in capsys.readouterr().err

    def test_disabled_vault_carries_nothing(self, env, tmp_path, capsys):
        """A disabled vault has no destination leaves — hands off, silently."""
        _config, std, _tmp_home = env
        src = tmp_path / "src"
        (src / "ro").mkdir(parents=True)
        (src / "ro" / "stale.txt").write_text("stale")
        state = self._primary_state(tmp_path, src / "ro", src / "rw")
        state.enable_vault = False
        assert _vault_carry_pairs(state, std, tmp_path / "d_ro", tmp_path / "d_rw") == []
        assert capsys.readouterr().err == ""
