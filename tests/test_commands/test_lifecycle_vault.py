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

import os
import shutil

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


def _seed_links(vault_rw, tmp_home):
    """Put an absolute, an internal, an outside-relative and a directory link in *vault_rw*."""
    outside = tmp_home / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "big.txt").write_text("outside data")
    (outside / "deep" / "nested.txt").write_text("nested")
    texts = {
        "abs": str(outside / "big.txt"),
        "in": "sub/deep.txt",
        "out": os.path.relpath(outside / "big.txt", os.path.realpath(vault_rw)),
        "dirlink": os.path.relpath(outside, os.path.realpath(vault_rw)),
    }
    for rel, text in texts.items():
        (vault_rw / rel).symlink_to(text)
    assert (vault_rw / "out").read_text() == "outside data"
    return texts


def _assert_links_carried(vault_rw, texts):
    """Every seeded link is still a link with its exact text; nothing outside materialized."""
    for rel, text in texts.items():
        assert (vault_rw / rel).is_symlink(), rel
        assert os.readlink(vault_rw / rel) == text, rel
    assert not [
        p for p in vault_rw.rglob("*")
        if p.name in ("big.txt", "nested.txt") and not p.is_symlink()
    ]


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
        # The workset source leaves are gone (remove_member_store ran).
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
        assert any(p.name == "b1" for p in load_workset(
            ws_b.root, ws_b.name, early_system=std.early_system).projects)

    def test_workset_to_workset_unwind_restores_source_vault(self, env, monkeypatch):
        """A leg-2 failure restores membership AND the source vault from the stash."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        (internal / "ws-note.txt").write_text("workspace")
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
        # Membership is restored, and the workspace was never deleted ...
        assert any(p.name == "b1" for p in load_workset(
            ws_a.root, ws_a.name, early_system=std.early_system).projects)
        assert (internal / "ws-note.txt").read_text() == "workspace"
        # ... and the source vault is restored whole, byte for byte.
        assert (src_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        assert (src_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        assert (src_rw / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]

    def test_dangling_symlink_carries_as_a_link(self, env):
        """Q70: a dangling link is a link like any other — carried, never followed."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        (state.vault_rw / "gone").symlink_to(tmp_home / "no-such-target")
        new = execute_lifecycle(
            state, TargetSpec(location=tmp_home / "newhome", ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        _assert_carried(new, seed)
        assert (new.vault_rw / "gone").is_symlink()
        assert os.readlink(new.vault_rw / "gone") == str(tmp_home / "no-such-target")

    def test_primary_to_standalone_convert_keeps_symlinks(self, env):
        """Q70/Q74 on the real relocation path: every link lands with its exact text."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        texts = _seed_links(state.vault_rw, tmp_home)
        dest = tmp_home / "combo_dest"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        _assert_carried(new, seed)
        _assert_links_carried(new.vault_rw, texts)
        assert not state.vault_rw.exists()

    def test_workset_to_workset_move_keeps_symlinks_through_the_stash(self, env):
        """Both stash legs copy every link verbatim, escaping relative links included."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "elsewhere" / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        seed = _seed_vault(state)
        texts = _seed_links(state.vault_rw, tmp_home)
        new = execute_lifecycle(
            state, TargetSpec(location=ws_b.workspaces_dir / "b1", ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        _assert_carried(new, seed)
        # wsb sits one level deeper than wsa: the escaping links are STILL verbatim (Q74).
        _assert_links_carried(new.vault_rw, texts)

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

    def test_dangling_symlink_is_copied_as_a_link(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "gone").symlink_to(tmp_path / "no-such-target")
        dst = tmp_path / "dst"
        _copy_vault_leaf_contents(src, dst)
        assert os.readlink(dst / "gone") == str(tmp_path / "no-such-target")

    def test_a_linked_source_leaf_is_shared_as_a_link_not_materialized(self, tmp_path):
        """Q102 (a): the carry re-creates the pointer, so the new box shares the store
        instead of duplicating the whole outside tree it names."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "v.txt").write_text("v")
        src = tmp_path / "src"
        src.symlink_to(real)
        dst = tmp_path / "dst"
        _copy_vault_leaf_contents(src, dst)
        assert dst.is_symlink()
        assert os.readlink(dst) == str(real)
        assert (dst / "v.txt").read_text() == "v"

    def test_a_linked_source_still_merges_into_a_leaf_that_holds_content(self, tmp_path):
        """The trade is only ever for an empty placeholder; a destination with data in it
        keeps its data and takes the source's contents beside it, as before."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "v.txt").write_text("v")
        src = tmp_path / "src"
        src.symlink_to(real)
        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "prior.txt").write_text("prior")
        _copy_vault_leaf_contents(src, dst)
        assert not dst.is_symlink()
        assert (dst / "prior.txt").read_text() == "prior"
        assert (dst / "v.txt").read_text() == "v"

    def test_uncopyable_entry_raises_named_project_error(self, tmp_path):
        """An entry that cannot land (a link already at that name) fails the carry by NAME."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        link = src / "l"
        link.symlink_to("somewhere")
        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "l").symlink_to("prior")
        with pytest.raises(ProjectError) as exc:
            _copy_vault_leaf_contents(src, dst)
        msg = str(exc.value)
        assert f"vault contents of {src} to {dst}" in msg
        assert f"  {link}: " in msg
        assert "The relocation was aborted." in msg
        # Neither side is overwritten.
        assert (src / "a.txt").read_text() == "a"
        assert os.readlink(link) == "somewhere"
        assert os.readlink(dst / "l") == "prior"


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


class TestNullArmAcrossTheLifecycle:
    """``workset.{vault_ro,vault_rw}: null`` on the relocation paths — no such dir.

    ⚑ A null arm is a DECLARED value (spec §2a), so every verb here completes and the
    relocation creates nothing under the nulled arm.  Each case pins one place that
    composes the per-box leaf: a null arm that is not recognised as null raises
    ``TypeError`` (or deletes a sibling box's vault) instead of completing.
    """

    def _reload(self, env, config_file):
        """The primary arms are resolved ONCE at std load; rebuild after a null."""
        config, std, _tmp_home = env
        config2 = load_config(config_file)
        return config2, load_std_paths(config2)

    def test_the_remap_fallback_yields_no_leaf_for_a_null_arm(
        self, env, config_file, tmp_home,
    ):
        """``remap`` on a primary box whose workspace dir is gone reads the registered
        metadata alone — and a nulled arm must leave that state without a vault leaf."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        _repoint(std.primary_workset, "vault_ro", None)
        config2, std2 = self._reload(env, config_file)
        shutil.rmtree(pdir)  # the workspace dir is what sent us to the fallback
        state = resolve_lifecycle_target(str(pdir), std2, config2)
        assert state.mode == BoxMode.primary
        assert state.vault_ro is None
        assert state.vault_rw == std2.primary_vault_rw / state.name

    def test_primary_to_standalone_convert_completes_over_a_null_arm(self, env, config_file):
        """The source teardown walks ``(state.vault_*, std.primary_vault_*)``: a null
        arm names no dir to delete, and the other arm's leaf is still removed."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        (state.vault_rw / "keep.txt").write_text("rw store")
        _repoint(std.primary_workset, "vault_ro", None)
        config2, std2 = self._reload(env, config_file)
        fresh = resolve_lifecycle_target(str(pdir), std2, config2)
        assert fresh.vault_ro is None  # anti-vacuity: the null reached the state
        new = execute_lifecycle(
            fresh, TargetSpec(location=tmp_home / "dest", ownership="standalone"),
            std2, config2, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        # The armed side was torn down; the nulled arm named no leaf to delete.
        assert not (std2.primary_vault_rw / fresh.name).exists()

    def test_standalone_to_primary_convert_creates_no_leaf_for_a_null_arm(
        self, env, config_file, tmp_home, capsys,
    ):
        """The destination's own leaves come from the primary arms — a null one gets no
        leaf and no mkdir, and the box is still a primary member.  The source's store for
        that side is left in place and named: the destination is the only other place it
        could be, and it is not there."""
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        source_ro, source_rw = state.vault_ro, state.vault_rw
        _repoint(std.primary_workset, "vault_rw", None)
        config2, std2 = self._reload(env, config_file)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std2, config2, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert new.vault_ro == std2.primary_vault_ro / new.name
        assert new.vault_ro.is_dir()
        assert new.vault_rw is None
        assert not (std2.primary_workset / "vault" / "rw").exists()
        # The received side moved and its source went with it; the unreceived side did
        # not move, so it is still the store — the whole point of not deleting it.
        assert (new.vault_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        assert not source_ro.exists()
        assert (source_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        assert (source_rw / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]
        err = capsys.readouterr().err
        assert f"Note: left the vault at {source_rw} in place" in err
        assert "workset.vault_rw is null at the destination" in err

    def test_a_named_member_of_a_null_vault_workset_still_tears_down(
        self, env, tmp_home,
    ):
        """A NAMED source's leaves come off its OWN workset's resolved arms: with the
        ro arm nulled, the member store goes and the armed leaf goes with it."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        external = tmp_home / "ext_repo"
        external.mkdir()
        add_project(ws, "ep", external, std)
        _repoint(ws.root, "vault_ro", None)
        state = resolve_lifecycle_target(str(external), std, config)
        assert state.vault_ro is None  # anti-vacuity: the null reached the state
        (state.vault_rw / "keep.txt").write_text("rw store")
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        # The armed leaf is gone, and no ``ep`` tree remains under the workset.
        assert not (ws.root / "vault" / "rw" / "ep").exists()
        assert not (ws.root / "box_data" / "ep").exists()

    def test_a_move_into_a_null_vault_workset_registers_no_leaf(self, env, tmp_home):
        """The destination workset's own arms gate the membership it records: with the
        ro arm nulled, the move lands and no ``<name>`` leaf is claimed under it."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        _seed_vault(state)
        _repoint(ws_b.root, "vault_ro", None)
        dest = ws_b.workspaces_dir / "b1"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wsb"
        assert new.vault_ro is None  # anti-vacuity: the null reached the new member
        assert not (ws_b.root / "vault" / "ro" / "b1").exists()
        assert any(p.name == "b1" for p in load_workset(
            ws_b.root, ws_b.name, early_system=std.early_system).projects)


class TestNullDestinationArmRetainsTheSourceVault:
    """A source vault leaf the destination has NO leaf for is left in place, and named.

    ⚑⚑ A DESTINATION ARM SET TO ``<None>`` GETS NO LEAF (spec §2a, ALL PROJECTS), so the
    carry drops that side.  A teardown that then deletes the source's leaf for it
    destroys the box's store — silently, at rc 0, with the box's registration intact.
    These pin the retention per source mode.
    """

    def _assert_retained(self, source, new, seed, err, kept_side, key):
        """*kept_side*'s source store is intact and named; the other side was carried."""
        other = "rw" if kept_side == "ro" else "ro"
        kept, carried = source[kept_side], source[other]
        assert not carried.exists()  # the received side was carried, then torn down
        assert (kept / f"{kept_side}-note.txt").read_text() == seed[f"{kept_side}-note.txt"]
        assert (getattr(new, f"vault_{other}") / f"{other}-note.txt").read_text() == (
            seed[f"{other}-note.txt"]
        )
        assert f"Note: left the vault at {kept} in place" in err
        assert f"{key} is null at the destination" in err

    def test_a_primary_source_retains_its_ro_store_over_a_null_destination_arm(
        self, env, tmp_home, capsys,
    ):
        """primary → named: the ro leaf the target workset cannot hold stays, and is named."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="p2")
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        _repoint(ws_b.root, "vault_ro", None)
        new = execute_lifecycle(
            state, TargetSpec(ownership="wsb"), std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wsb"
        assert new.vault_ro is None  # anti-vacuity: the null reached the new member
        self._assert_retained(
            {"ro": state.vault_ro, "rw": state.vault_rw}, new, seed,
            capsys.readouterr().err, "ro", "workset.vault_ro",
        )

    def test_a_primary_source_retains_its_rw_store_over_a_null_standalone_arm(
        self, env, tmp_home, capsys,
    ):
        """primary → standalone: the rw leaf the root has no arm for stays, and is named."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="p3")
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        # The standalone root is the box's own project dir (drift H), so the root's
        # own ``workset.yaml`` is the destination's arm to read.
        _repoint(pdir, "vault_rw", None)
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        assert new.vault_rw is None  # anti-vacuity: the null reached the new box
        self._assert_retained(
            {"ro": state.vault_ro, "rw": state.vault_rw}, new, seed,
            capsys.readouterr().err, "rw", "workset.vault_rw",
        )

    def test_a_named_source_retains_its_ro_store_over_a_null_destination_arm(
        self, env, tmp_home, capsys,
    ):
        """ws→ws: the release is a teardown too — the ro leaf the target cannot hold
        stays, is named by the retained-vault Note, and is not reported as a failure."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        seed = _seed_vault(state)
        source_ro, source_rw = state.vault_ro, state.vault_rw
        _repoint(ws_b.root, "vault_ro", None)
        new = execute_lifecycle(
            state, TargetSpec(location=ws_b.workspaces_dir / "b1", ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        assert new.vault_ro is None  # anti-vacuity: the null reached the new member
        err = capsys.readouterr().err
        self._assert_retained({"ro": source_ro, "rw": source_rw}, new, seed, err,
                              "ro", "workset.vault_ro")
        # The box tree IS the retirement's to delete, and it went: only the vault leaf
        # the destination could not receive is left — named once, as a keep.
        assert not (ws_a.projects_dir / "b1").exists()
        assert "could not remove the old store of 'b1'" not in err


class TestRetentionSurvivesASymlinkAndASourceNull:
    """The retention holds when the vault path runs through a symlink, and when the
    SOURCE arm is the null one."""

    def test_a_symlinked_primary_arm_still_retains_an_unreceived_leaf(
        self, env, tmp_home, capsys,
    ):
        """The unreceived set and its lookup must compare in ONE form: a symlink in
        the primary arm would otherwise miss the lookup and ``rmtree`` the leaf."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="p_link")
        real = tmp_home / "real_ro"
        shutil.move(str(std.primary_vault_ro), str(real))
        std.primary_vault_ro.symlink_to(real)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.vault_ro.resolve() != state.vault_ro  # anti-vacuity: via the link
        seed = _seed_vault(state)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        _repoint(ws_b.root, "vault_ro", None)
        execute_lifecycle(
            state, TargetSpec(ownership="wsb"), std, config, confirm=_conf_yes(),
        )
        assert (real / state.name / "ro-note.txt").read_text() == seed["ro-note.txt"]
        assert f"Note: left the vault at {state.vault_ro} in place" in capsys.readouterr().err

    def test_a_null_standalone_source_arm_keeps_its_old_data(
        self, env, config_file, tmp_home, capsys,
    ):
        """Data stored in ``vault/rw`` before the root's ``vault_rw`` was set to null is
        no arm of the box any more — the convert leaves the skeleton holding it, and
        names it."""
        config, std, tmp_home = env
        pdir = _make_standalone(env, name="sa_srcnull")
        state = resolve_lifecycle_target(str(pdir), std, config)
        seed = _seed_vault(state)
        old_rw = state.vault_rw
        _repoint(pdir, "vault_rw", None)
        fresh = resolve_lifecycle_target(str(pdir), std, config)
        assert fresh.vault_rw is None  # anti-vacuity: the null reached the state
        new = execute_lifecycle(
            fresh, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert (old_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        assert (old_rw / "sub" / "deep.txt").read_text() == seed["sub/deep.txt"]
        assert (new.vault_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        err = capsys.readouterr().err
        assert f"Note: left the vault at {old_rw} in place" in err

    def test_a_null_named_source_arm_names_the_store_it_orphaned(
        self, env, tmp_home, capsys,
    ):
        """A NAMED source arm nulled after its data was stored: the retirement has no
        base under the arm and no key to name it by, so the store is named from the
        arm's own default location instead."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        # The member's workspace sits OUTSIDE the workset tree, so an in-place convert
        # to the default workset has no in-tree landing to refuse.
        external = tmp_home / "extb1"
        external.mkdir(parents=True)
        add_project(ws_a, "b1", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        seed = _seed_vault(state)
        old_rw = state.vault_rw
        _repoint(ws_a.root, "vault_rw", None)
        fresh = resolve_lifecycle_target(str(external), std, config)
        assert fresh.vault_rw is None  # anti-vacuity: the null reached the state
        new = execute_lifecycle(
            fresh, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert (old_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        err = capsys.readouterr().err
        assert f"Note: left the vault at {old_rw} in place" in err
        assert "workset.vault_rw is null" in err
        # The received side was carried, and the box tree went: only the orphaned
        # store is left, and it is left as a keep rather than a failure.
        assert (new.vault_ro / "ro-note.txt").read_text() == seed["ro-note.txt"]
        assert not (ws_a.projects_dir / "b1").exists()
        assert "could not remove the old store of 'b1'" not in err

    def test_a_null_named_source_arm_is_named_on_a_workset_convert(
        self, env, tmp_home, capsys,
    ):
        """The same orphaned store through the named-to-named relocation, which retires
        the source by the same sequence."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        create_workset("wo", tmp_home / "wo_root", std)
        external = tmp_home / "extd3"
        external.mkdir(parents=True)
        add_project(ws_a, "d3", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        seed = _seed_vault(state)
        old_rw = state.vault_rw
        _repoint(ws_a.root, "vault_rw", None)
        fresh = resolve_lifecycle_target(str(external), std, config)
        assert fresh.vault_rw is None  # anti-vacuity: the null reached the state
        new = execute_lifecycle(
            fresh, TargetSpec(ownership="wo"), std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wo"
        assert (old_rw / "rw-note.txt").read_text() == seed["rw-note.txt"]
        err = capsys.readouterr().err
        assert f"Note: left the vault at {old_rw} in place" in err
        assert "workset.vault_rw is null" in err
        assert not (ws_a.projects_dir / "d3").exists()
        assert "could not remove the old store of 'd3'" not in err


class TestNullArmTeardownGuards:
    """Guards on the null-arm teardown edges, each reachable through the real verb."""

    def test_a_null_canon_root_leaves_the_root_sweep_deciding(
        self, env, tmp_home,
    ):
        """In-place convert to standalone: ``workset.canon: null`` names no dir, so the
        root's artifact census holds no path for it — and the sweep of the user's own
        top-level files goes on to a decision instead of raising on the absent one."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="canon_null")
        state = resolve_lifecycle_target(str(pdir), std, config)
        _repoint(pdir, "canon", None)
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        # The sweep ran: the user's own top-level file moved into the workspace dir.
        assert not (pdir / "file.txt").exists()
        assert any(p.is_dir() and (p / "file.txt").is_file()
                   for p in pdir.iterdir())

    def test_a_null_standalone_rw_arm_writes_no_vault_gitignore(
        self, env, tmp_home,
    ):
        """In-place convert to standalone over a pre-existing ``vault/``: a null
        ``workset.vault_rw`` is no arm, so the skeleton file — whose whole content is a
        claim about that arm — is not written beside the directory the user named."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="gitignore_null")
        state = resolve_lifecycle_target(str(pdir), std, config)
        (pdir / "vault").mkdir(parents=True)
        (pdir / "vault" / "user.txt").write_text("the user's own store")
        _repoint(pdir, "vault_rw", None)
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.vault_rw is None  # anti-vacuity: the null reached the new box
        assert not (pdir / "vault" / ".gitignore").exists()
        assert (pdir / "vault" / "user.txt").read_text() == "the user's own store"


class TestDisabledVaultDataGuard:
    """Q64: a disabled vault that still holds data is refused, ``--force`` keeps it in place.

    A box with ``box.enable_vault`` false carries no vault, so a relocation leaves the
    source leaves behind; without the guard the teardown deleted them at rc 0.
    """

    def _disabled_with_data(self, env, name):
        config, std, _tmp_home = env
        pdir = _make_default(env, name=name, enable_vault=False)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.enable_vault is False
        state.vault_rw.mkdir(parents=True, exist_ok=True)
        (state.vault_rw / "keep.txt").write_text("stale store")
        return state

    @pytest.mark.parametrize("spec", [
        pytest.param({"location": "dest", "ownership": UNCHANGED}, id="move"),
        pytest.param({"ownership": "standalone"}, id="convert"),
    ])
    def test_refused_without_force_and_the_data_survives(self, env, spec):
        config, std, tmp_home = env
        state = self._disabled_with_data(env, "dv1")
        dest = tmp_home / "dest"
        kwargs = {k: (dest if v == "dest" else v) for k, v in spec.items()}
        with pytest.raises(ProjectError) as exc:
            execute_lifecycle(state, TargetSpec(**kwargs), std, config, confirm=_conf_yes())
        assert str(state.vault_rw) in str(exc.value)
        assert "--force" in str(exc.value)
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"
        assert not dest.exists()
        assert resolve_lifecycle_target(str(state.workspace_path), std, config).mode == (
            BoxMode.primary
        )

    @pytest.mark.parametrize("spec", [
        pytest.param({"location": "dest", "ownership": UNCHANGED}, id="move"),
        pytest.param({"ownership": "standalone"}, id="convert"),
    ])
    def test_force_proceeds_and_leaves_the_data_in_place(self, env, spec, capsys):
        config, std, tmp_home = env
        state = self._disabled_with_data(env, "dv2")
        kwargs = {k: (tmp_home / "dest" if v == "dest" else v) for k, v in spec.items()}
        new = execute_lifecycle(
            state, TargetSpec(**kwargs), std, config, force=True, confirm=_conf_yes(),
        )
        assert new.workspace_path != state.workspace_path or new.mode != state.mode
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"
        err = capsys.readouterr().err
        assert f"Note: left the vault at {state.vault_rw} in place" in err
        assert "box.enable_vault is false" in err

    @pytest.mark.parametrize("spec", [
        pytest.param({"location": "dest", "ownership": UNCHANGED}, id="move"),
        pytest.param({"ownership": "standalone"}, id="convert"),
    ])
    def test_an_empty_disabled_vault_is_not_refused(self, env, spec, capsys):
        """Negative control: nothing stored ⇒ nothing to strand, no refusal, no Note."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="dv3", enable_vault=False)
        state = resolve_lifecycle_target(str(pdir), std, config)
        state.vault_rw.mkdir(parents=True, exist_ok=True)
        kwargs = {k: (tmp_home / "dest" if v == "dest" else v) for k, v in spec.items()}
        execute_lifecycle(state, TargetSpec(**kwargs), std, config, confirm=_conf_yes())
        assert "left the vault" not in capsys.readouterr().err

    def test_a_remap_after_the_user_moved_the_tree_is_refused(self, env):
        """``box remap``: the files are already at *dest*, but the vault is not carried."""
        config, std, tmp_home = env
        state = self._disabled_with_data(env, "dv4")
        dest = tmp_home / "dv4-moved"
        state.workspace_path.rename(dest)
        spec = TargetSpec(location=dest, ownership=UNCHANGED, records_only=True)
        with pytest.raises(ProjectError) as exc:
            execute_lifecycle(state, spec, std, config, confirm=_conf_yes())
        assert str(state.vault_rw) in str(exc.value)
        assert "--force" in str(exc.value)
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"

    def test_a_same_path_remap_is_not_refused(self, env, capsys):
        """A remap onto the path the box is registered at reuses it in place, vault
        included, so there is nothing to strand."""
        config, std, _tmp_home = env
        state = self._disabled_with_data(env, "dv5")
        spec = TargetSpec(
            location=state.workspace_path, ownership=UNCHANGED, records_only=True,
        )
        execute_lifecycle(state, spec, std, config, confirm=_conf_yes())
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"
        assert "left the vault" not in capsys.readouterr().err

    def test_a_named_source_keeps_its_disabled_vault_data_under_force(
        self, env, tmp_home, capsys,
    ):
        """ws→ws: the release deletes the member store; the disabled vault's data stays."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        state.enable_vault = False
        state.vault_rw.mkdir(parents=True, exist_ok=True)
        (state.vault_rw / "keep.txt").write_text("stale store")
        spec = TargetSpec(location=ws_b.workspaces_dir / "b1", ownership="wsb")
        with pytest.raises(ProjectError, match="--force"):
            execute_lifecycle(state, spec, std, config, confirm=_conf_yes())
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"
        execute_lifecycle(state, spec, std, config, force=True, confirm=_conf_yes())
        assert (state.vault_rw / "keep.txt").read_text() == "stale store"
        err = capsys.readouterr().err
        assert f"Note: left the vault at {state.vault_rw} in place" in err
        assert "could not remove the old store of 'b1'" not in err
        assert not (ws_a.projects_dir / "b1").exists()
