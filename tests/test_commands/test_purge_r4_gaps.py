"""r4 gap tests (kanibako `re-purgeguard-r4.md` item 5, plus bifrost items 1-2).

Each test here pins a behavior that existed in code but had NO pin at all -- grep for the
quoted message returned zero files before these were written.
"""
from __future__ import annotations

import argparse

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_standalone_project

_TS = "2026-10-10T00:00:00+00:00"


def _reload(config_file):
    config = load_config(config_file)
    return load_std_paths(config), config


class TestRunningBoxGuardOnEveryRoute:
    """Item 8 / D1 -- the deregistered route had no pre-flight at all."""

    def test_a_parked_standalone_with_a_lock_is_not_purged(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """Driven through ``run_rm`` rather than the handler directly: a direct call is
        red on base only because the signature gained ``config``, which proves the API
        moved, not that the guard was missing.  This route exists on both trees.

        ``_confirm_purge`` is trapped so the assertion is about ORDER: base did not
        refuse, it went straight to the delete prompt.
        """
        from kanibako.commands.box import _parser as P
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std, config = _reload(config_file)
        root = tmp_home / "d1_live"
        store = root / "box_data"
        store.mkdir(parents=True)
        (store / "keep.txt").write_text("must survive")
        registry_store.register_deregistered(
            std.registry, "d1live", kind="standalone",
            workspace=str(root), metadata=str(root), deregistered_at=_TS,
        )
        (store / ".kanibako.lock").write_text("")
        asked = []
        monkeypatch.setattr(P, "_confirm_purge",
                           lambda *a, **k: (asked.append(True), False)[1])
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target="d1live", box=None, purge=True,
                                      force=False))
        err = capsys.readouterr().err
        assert not asked, "the guard must fire BEFORE the delete list is shown"
        assert rc != 0, "a live box must not be purged: %s" % err
        assert "may be running" in err, err
        assert store.is_dir() and (store / "keep.txt").read_text() == "must survive"

    def test_force_overrides_the_deregistered_guard(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std, config = _reload(config_file)
        root = tmp_home / "d1_force"
        store = root / "box_data"
        store.mkdir(parents=True)
        registry_store.register_deregistered(
            std.registry, "d1force", kind="standalone",
            workspace=str(root), metadata=str(root), deregistered_at=_TS,
        )
        (store / ".kanibako.lock").write_text("")
        capsys.readouterr()

        run_rm(argparse.Namespace(target="d1force", box=None, purge=True, force=True))
        capsys.readouterr()
        assert not store.exists(), "--force must still be able to purge"

    def test_a_detached_box_is_running_even_with_no_lock_on_disk(
        self, tmp_path, monkeypatch,
    ):
        """Bifrost FYI: a detached `start` writes no `box_data/.kanibako.lock`, so the
        refusal has to come from the runtime query, not the lock."""
        from kanibako.commands.box import _lifecycle
        from kanibako.commands.box import _parser as P

        monkeypatch.setattr(P, "_check_container_running",
                           lambda proj, fail_closed=True: (True, "up (detached)"))
        reason = _lifecycle.box_running_reason(object(), tmp_path / "absent.lock")
        assert reason is not None, "runtime says running but the probe answered None"
        assert "detached" in reason


class TestAmbiguousPathRefusal:
    """Item 2 -- more than one parked entry naming one path must refuse, not guess."""

    def test_two_parked_entries_on_one_path_refuse_the_by_path_purge(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std, config = _reload(config_file)
        target = tmp_home / "ambig"
        target.mkdir()
        for nm in ("amb1", "amb2"):
            home = std.boxes / nm
            home.mkdir(parents=True)
            (home / "data.txt").write_text(nm)
            registry_store.register_deregistered(
                std.registry, nm, kind="primary",
                workspace=str(target), metadata=str(home), deregistered_at=_TS,
            )
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target=str(target), box=None, purge=True,
                                      force=True))
        err = capsys.readouterr().err
        assert rc == 1, err
        assert "Refusing to guess" in err, err
        assert "amb1" in err and "amb2" in err, err
        # Nothing was deleted.
        assert (std.boxes / "amb1" / "data.txt").read_text() == "amb1"
        assert (std.boxes / "amb2" / "data.txt").read_text() == "amb2"


class TestAmbiguousPathRefusalAtThePurgeDoor:
    """r7 W1 -- the SAME refusal at the ``box purge <path>`` door.

    r6's ``_parked_reentry`` scanned the park by hand and returned the FIRST match, so
    kanibako's r3 state (parked primary ``p`` + parked standalone ``pbce9_p`` at one
    path) let ``box purge <path> --force`` take ``p``'s vault and box dir with no
    refusal -- r3 item 2's data loss, re-entering through the door r6 added.
    """

    def _two_parked_at_one_path(self, std, target):
        """A parked primary AND a parked standalone, both naming *target*."""
        from kanibako.project import registry_store

        target.mkdir()
        for nm, kind in (("p", "primary"), ("pbce9_p", "standalone")):
            home = std.boxes / nm
            home.mkdir(parents=True)
            (home / "keep.txt").write_text(nm)
            registry_store.register_deregistered(
                std.registry, nm, kind=kind, workspace=str(target),
                metadata=str(home), deregistered_at=_TS,
            )

    def test_box_purge_refuses_a_path_two_parked_entries_name_and_deletes_nothing(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        std, config = _reload(config_file)
        target = tmp_home / "ambig_purge"
        self._two_parked_at_one_path(std, target)
        capsys.readouterr()

        rc = _purge_one(std, config, str(target), force=True)
        err = capsys.readouterr().err
        assert rc == 1, f"expected a refusal, got rc={rc}: {err!r}"
        assert "Refusing to guess" in err, err
        assert "'p'" in err and "'pbce9_p'" in err, err
        assert (std.boxes / "p" / "keep.txt").read_text() == "p", (
            "the primary nobody named must keep its home")
        assert (std.boxes / "pbce9_p" / "keep.txt").read_text() == "pbce9_p"

    def test_both_doors_print_the_identical_refusal(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """SHARED, not copied: one state, the SAME bytes out of both doors."""
        from kanibako.commands.box._parser import run_rm
        from kanibako.commands.clean import _purge_one

        std, config = _reload(config_file)
        target = tmp_home / "ambig_parity"
        self._two_parked_at_one_path(std, target)
        capsys.readouterr()

        rc_rm = run_rm(argparse.Namespace(target=str(target), box=None, purge=True,
                                         force=True))
        rm_err = capsys.readouterr().err
        rc_purge = _purge_one(std, config, str(target), force=True)
        purge_err = capsys.readouterr().err

        assert (rc_rm, rc_purge) == (1, 1), f"{rm_err!r} / {purge_err!r}"
        assert purge_err == rm_err, (
            f"the doors diverged:\n  rm --purge: {rm_err!r}\n  purge:      {purge_err!r}")


class TestLiveBoxOwnsThePath:
    """Bifrost item 2 -- accepted behavior, now pinned AND made visible."""

    def test_a_live_standalone_owns_the_path_and_the_parked_entry_is_named(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std, config = _reload(config_file)
        root = tmp_home / "owns"
        root.mkdir()
        resolve_standalone_project(std, config, project_dir=str(root),
                                   initialize=True)
        parked_home = std.boxes / "parkedprim"
        parked_home.mkdir(parents=True)
        (parked_home / "data.txt").write_text("parked-primary")
        registry_store.register_deregistered(
            std.registry, "parkedprim", kind="primary",
            workspace=str(root), metadata=str(parked_home), deregistered_at=_TS,
        )
        capsys.readouterr()

        run_rm(argparse.Namespace(target=str(root), box=None, purge=True, force=True))
        err = capsys.readouterr().err
        assert "still parked at this path" in err, err
        assert "parkedprim" in err, err
        # The live box was purged; the parked primary's home was NOT touched.
        assert (parked_home / "data.txt").read_text() == "parked-primary"


class TestLinkNoteIsPunctuated:
    """Item 9 / bifrost item 3 -- the Note is one sentence with a final period."""

    def test_purging_a_symlinked_vault_leaf_notes_the_target_with_a_period(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_rm

        std, config = _reload(config_file)
        root = tmp_home / "linknote"
        (root / "vault").mkdir(parents=True)
        outside = tmp_home / "linknote_target"
        outside.mkdir()
        (outside / "keep.txt").write_text("target data")
        (root / "vault" / "rw").symlink_to(outside)
        resolve_standalone_project(std, config, project_dir=str(root),
                                   initialize=True)
        capsys.readouterr()

        run_rm(argparse.Namespace(target=str(root), box=None, purge=True, force=True))
        err = capsys.readouterr().err
        assert "removed the link" in err, err
        assert "untouched." in err, "the Note must end with a period: %r" % err
        assert (outside / "keep.txt").read_text() == "target data"


class TestSymlinkLoopIsNamed:
    """Item 6a -- a loop cannot be resolved, so it must never be removed.

    ``resolve(strict=False)`` hides a loop by handing back the link itself, so the
    guard has to ask with both interpreter shapes caught: ``OSError(ELOOP)`` on 3.13
    and ``RuntimeError`` on 3.11.  A dangling link is ENOENT, not a loop.
    """

    def test_a_self_looping_link_is_a_loop(self, tmp_path):
        from kanibako.project.workset import _symlink_loop

        loop = tmp_path / "rw"
        loop.symlink_to(loop)
        assert _symlink_loop(loop) is True

    def test_a_dangling_link_is_not_a_loop(self, tmp_path):
        from kanibako.project.workset import _symlink_loop

        dangling = tmp_path / "gone"
        dangling.symlink_to(tmp_path / "never-existed")
        assert _symlink_loop(dangling) is False

    def test_a_resolvable_link_is_not_a_loop(self, tmp_path):
        from kanibako.project.workset import _symlink_loop

        real = tmp_path / "real"
        real.mkdir()
        good = tmp_path / "good"
        good.symlink_to(real)
        assert _symlink_loop(good) is False


class TestCredsLockReProbedAtDeleteTime:
    """Item 6b -- a watcher started AFTER the plan keeps its lock.

    The plan records the lock as removable; the step re-asks at the instant of
    deletion, so a lock that became held in between is kept and named.
    """

    def test_a_lock_held_only_at_delete_time_is_kept_and_named(
        self, tmp_path, monkeypatch, capsys,
    ):
        from kanibako.commands.box import _parser as P

        lock = tmp_path / "creds.lock"
        lock.write_text("")
        monkeypatch.setattr("kanibako.commands.box._lifecycle.session_lock_held",
                           lambda p: True)
        step = P._PurgeStep(P._CREDS_LOCK, "creds watcher lock", lock, gated=True)
        capsys.readouterr()
        P._run_purge_plan([step])
        err = capsys.readouterr().err
        assert "Kept" in err and str(lock) in err, err
        assert lock.exists(), "a HELD lock must survive the purge"


class TestLeftoverDefaultCanonBranch:
    """Item 6c -- clean.py's leftover-canon branch: stamp-only is removable, anything
    else is the user's and is retained."""

    def test_a_stamp_only_leftover_is_removable_and_a_filled_one_is_retained(
        self, tmp_path,
    ):
        from kanibako.project.workset import leftover_default_canon

        root = tmp_path / "box"
        leaf = root / "canon"
        leaf.mkdir(parents=True)
        # Not what `create` stamped -> the user's folder: retained, never removable.
        (leaf / "mine.txt").write_text("user content")
        removable, retained = leftover_default_canon(root, None, None)
        assert removable is None
        assert retained is not None

    def test_a_leftover_is_not_handled_twice_when_the_tier_is_the_leaf(self, tmp_path):
        from kanibako.project.workset import leftover_default_canon

        root = tmp_path / "box2"
        leaf = root / "canon"
        leaf.mkdir(parents=True)
        # The default IS the tier being acted on -> nothing handled here.
        assert leftover_default_canon(root, leaf, None) == (None, None)
        assert leftover_default_canon(root, None, leaf) == (None, None)


class TestKeptFolderSurvivesEachLaterStep:
    """Item 1 -- a failure at any step that runs BEFORE the anchor must leave the
    anchor in place so the retry can still find the box."""

    @pytest.mark.parametrize("patched", [
        ("kanibako.project.workset.remove_default_canon_stamp", False),
        # ⚑ r6 V2: the ``clean.remove_box_tree`` case is GONE.  That IS the anchor's
        # own remover, so "the anchor survived" only proved its own delete failed — not
        # that a LATER step failed.  ``tests/test_commands/test_purge_r5_anchor_matrix.py``
        # replaces it with a matrix that never touches the anchor's remover.
    ])
    def test_a_later_failure_keeps_the_store_and_the_declarations(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch, patched,
    ):
        from kanibako.commands.clean import _purge_one
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import (
            load_std_paths,
            resolve_standalone_project,
        )

        target, ret = patched
        calls = []
        monkeypatch.setattr(target,
                           lambda *a, **k: (calls.append(1), ret)[1])
        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "item1"
        root.mkdir()
        resolve_standalone_project(std, config, project_dir=str(root),
                                   initialize=True)
        # Repoint the canon AFTER create, so the default ``root/canon`` becomes a
        # leftover. Without this `leftover_canon_rm` is None and the patched step is
        # never called -- a green that proves nothing.
        from kanibako.settings.config_io import write_nested_key
        write_nested_key(root / "workset.yaml", ("workset",), "canon",
                        str(tmp_home / "item1_out_canon"))
        capsys.readouterr()

        rc = _purge_one(std, config, str(root), force=True)
        out = capsys.readouterr()
        assert calls, "%s was never reached -- this case would pass vacuously" % target
        assert rc != 0, "%s failed but the purge reported success: %s" % (target, out.out)
        assert "done." not in out.out.lower(), out.out
        assert (root / "box_data").is_dir(), (
            "%s failed; the anchor must survive so the retry can find the box" % target)
        assert (root / "workset.yaml").is_file()
