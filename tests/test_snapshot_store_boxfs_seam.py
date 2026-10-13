"""Snapshot-store moves go through the ``boxfs`` seam: a planted link is refused.

``relocate_snapshot_store``, ``adopt_standalone_store``, and the legacy-layout pass
move stores in a vault tree.  Through ``shutil`` they followed a symlink planted in a
parent (``.versions`` itself), and moved data in or out of whatever it pointed at.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanibako import boxfs, kuid
from kanibako.settings.paths import BoxMode
from kanibako.snapshots import (
    UNSORTED_DIRNAME,
    _versions_dir,
    adopt_standalone_store,
    create_snapshot,
    migrate_legacy_versions,
    relocate_snapshot_store,
)

TS = "20240101T000000Z"


def _rw(root: Path) -> Path:
    rw = root / "vault" / "rw"
    rw.mkdir(parents=True)
    return rw


def _snap(store: Path, name: str = TS, text: str = "mine") -> Path:
    (store / name).mkdir(parents=True)
    (store / name / "f").write_text(text)
    return store / name


def _tree(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    """Every entry under *root* (links not followed): kind and contents."""
    out: dict[str, tuple[str, bytes | str | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", None)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def _link_versions(rw: Path, outside: Path) -> None:
    """Plant ``.versions`` as a link to *outside*."""
    outside.mkdir(parents=True, exist_ok=True)
    _versions_dir(rw).symlink_to(outside, target_is_directory=True)


class TestAPlantedLinkIsRefused:

    def test_relocate_refuses_a_linked_old_base(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        outside = tmp_path / "outside"
        _snap(outside / "a")
        _link_versions(old, outside)
        before = _tree(outside)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b",
                                    old_base_exclusive=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(outside) == before
        assert not (_versions_dir(new) / "b").exists()

    def test_relocate_merge_refuses_a_linked_old_base(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        outside = tmp_path / "outside"
        _snap(outside / "a")
        _link_versions(old, outside)
        _snap(_versions_dir(new) / "b", "20230101T000000Z", "theirs")
        before = _tree(outside)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b",
                                    old_base_exclusive=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(outside) == before
        assert sorted(p.name for p in (_versions_dir(new) / "b").iterdir()) == [
            "20230101T000000Z"]

    def test_adopt_refuses_a_linked_base(self, tmp_path):
        k = kuid.generate()
        rw = _rw(tmp_path / "s")
        outside = tmp_path / "outside"
        _snap(outside / f"{k}_old")
        _link_versions(rw, outside)
        before = _tree(outside)
        with pytest.raises(OSError) as exc:
            adopt_standalone_store(rw, box_name=f"{k}_new")
        assert exc.value.errno == errno.ELOOP
        assert _tree(outside) == before

    def test_legacy_pass_refuses_a_linked_base(self, tmp_path):
        rw = _rw(tmp_path / "w")
        outside = tmp_path / "outside"
        _snap(outside)
        _link_versions(rw, outside)
        before = _tree(outside)
        with pytest.raises(OSError) as exc:
            migrate_legacy_versions(rw, box_name="b", store_exclusive=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(outside) == before

    def test_legacy_pass_writes_nothing_through_a_linked_base(self, tmp_path):
        """No legacy entry, so no move: the marker alone must not land outside."""
        exclusive = True
        rw = _rw(tmp_path / "w")
        outside = tmp_path / "outside"
        _snap(outside / "other", "20230101T000000Z")
        _link_versions(rw, outside)
        before = _tree(outside)
        with pytest.raises(OSError) as exc:
            migrate_legacy_versions(rw, box_name="b", store_exclusive=exclusive,
                                    create_base=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(outside) == before

    @pytest.mark.parametrize("exclusive", [False, True])
    def test_a_looped_base_is_refused_not_a_file_exists_error(self, tmp_path, exclusive):
        """Shared or not, a loop cannot be resolved: fail closed."""
        rw = _rw(tmp_path / "w")
        _versions_dir(rw).symlink_to(".versions")
        with pytest.raises(boxfs.SymlinkRefusedError) as exc:
            migrate_legacy_versions(rw, box_name="b", create_base=True,
                                    store_exclusive=exclusive)
        assert exc.value.link == _versions_dir(rw)

    def test_relocate_writes_nothing_into_a_linked_new_base(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        elsewhere = tmp_path / "elsewhere"
        _link_versions(new, elsewhere)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b",
                                    new_base_exclusive=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(elsewhere) == {}
        assert (_versions_dir(old) / "a" / TS / "f").read_text() == "mine"

    def test_relocate_writes_nothing_into_a_linked_new_store(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _versions_dir(new).mkdir()
        (_versions_dir(new) / "b").symlink_to(elsewhere)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b")
        assert exc.value.errno == errno.ELOOP
        assert _tree(elsewhere) == {}
        assert not (_versions_dir(new) / ".layout").exists()
        assert (_versions_dir(old) / "a" / TS / "f").read_text() == "mine"

    @pytest.mark.parametrize("where", ["base", "store", "shared-store"])
    def test_create_snapshot_copies_nothing_through_a_link(self, tmp_path, where):
        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        outside = tmp_path / "outside"
        if where == "base":
            _link_versions(rw, outside)
        else:
            outside.mkdir()
            _versions_dir(rw).mkdir()
            (_versions_dir(rw) / "b").symlink_to(outside)
        with pytest.raises(boxfs.SymlinkRefusedError):
            create_snapshot(rw, box_name="b", store_exclusive=where != "shared-store")
        assert _tree(outside) == {}


class TestARepointedSharedBaseIsUsed:
    """A primary/named base sits in the host-only parent no box can reach: a link AT it
    is the user's repoint (e.g. a bigger disk), resolved and used as before."""

    def _repoint(self, tmp_path, marked=True):
        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        big = tmp_path / "bigdisk"
        big.mkdir()
        if marked:
            (big / ".layout").write_text("per-box-v1\n")
        _link_versions(rw, big)
        return rw, big

    def test_snapshot_lands_in_the_repointed_base(self, tmp_path):
        rw, big = self._repoint(tmp_path)
        snap = create_snapshot(rw, box_name="b")
        assert snap is not None and snap.parent == big / "b"
        assert (snap / "data").read_text() == "live"

    def test_an_unmarked_repointed_base_is_migrated_in_place(self, tmp_path):
        rw, big = self._repoint(tmp_path, marked=False)
        _snap(big)
        migrate_legacy_versions(rw, box_name="b", create_base=True)
        assert (big / UNSORTED_DIRNAME / TS / "f").read_text() == "mine"
        assert (big / ".layout").is_file()

    def test_vault_list_and_snapshot_work(self, tmp_path, monkeypatch, capsys):
        from kanibako.commands import vault_cmd

        rw, big = self._repoint(tmp_path)
        monkeypatch.setattr(vault_cmd, "_resolve_vault_rw",
                            lambda *_a: (rw, "b", False, {}, "b"))
        args = SimpleNamespace(project=None, quiet=True, name=TS, force=True, keep=5)
        assert vault_cmd.run_snapshot(args) == 0
        assert vault_cmd.run_list(args) == 0
        out = capsys.readouterr()
        assert out.err == ""
        assert [p.name for p in (big / "b").iterdir()] == [out.out.split()[-1]]

    def test_a_link_below_it_is_still_refused(self, tmp_path):
        rw, big = self._repoint(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (big / "b").symlink_to(elsewhere)
        with pytest.raises(boxfs.SymlinkRefusedError) as exc:
            create_snapshot(rw, box_name="b")
        assert exc.value.link == big / "b"
        assert _tree(elsewhere) == {}

    def test_relocate_out_of_and_into_repointed_bases(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        big_old, big_new = tmp_path / "bigold", tmp_path / "bignew"
        _snap(big_old / "a")
        big_new.mkdir()
        _link_versions(old, big_old)
        _link_versions(new, big_new)
        assert relocate_snapshot_store(old, new, old_box="a", new_box="b") == big_new / "b"
        assert (big_new / "b" / TS / "f").read_text() == "mine"
        assert not (big_old / "a").exists()

    def test_an_unopenable_base_says_so(self, tmp_path, monkeypatch, capsys):
        """first_symlink fails closed on EACCES; the message must not call it a link."""
        from kanibako.commands import vault_cmd

        if os.geteuid() == 0:
            pytest.skip("root opens any directory")
        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        base = _versions_dir(rw)
        base.mkdir()
        (base / ".layout").write_text("per-box-v1\n")
        base.chmod(0)
        monkeypatch.setattr(vault_cmd, "_resolve_vault_rw",
                            lambda *_a: (rw, "b", False, {}, "b"))
        try:
            rc = vault_cmd.run_snapshot(SimpleNamespace(project=None))
        finally:
            base.chmod(0o755)
        assert rc == 1
        err = capsys.readouterr().err
        assert f"Error: {base} cannot be opened" in err and "symlink" not in err


class TestAStartWithALinkedBaseSkipsTheSnapshot:
    """``kanibako start`` warns, naming the link, and starts without a snapshot."""

    def _start(self, start_mocks, tmp_path, plant, mode=BoxMode.standalone):
        from kanibako.commands.start import _run_container
        from unittest.mock import MagicMock

        project = tmp_path / "p"
        (project / ".kanibako" / "shell").mkdir(parents=True)
        rw = project / "vault" / "rw"
        rw.mkdir(parents=True)
        (rw / "data").write_text("live")
        link = plant(rw)
        with start_mocks() as m:
            proj = MagicMock()
            proj.is_new = False
            proj.mode = mode
            proj.group = None
            proj.name = "b"
            proj.project_path = project
            proj.metadata_path = project / ".kanibako"
            proj.shell_path = project / ".kanibako" / "shell"
            proj.vault_ro_path = project / "vault" / "ro"
            proj.vault_rw_path = rw
            m.resolve_any_project.return_value = proj
            rc = _run_container(
                project_dir=str(project), entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
            )
        return rc, link

    def test_a_linked_base(self, start_mocks, tmp_path, capsys):
        outside = tmp_path / "outside"
        _snap(outside, "20230101T000000Z")
        before = _tree(outside)

        def plant(rw):
            _link_versions(rw, outside)
            return _versions_dir(rw)

        rc, link = self._start(start_mocks, tmp_path, plant)
        assert rc == 0
        err = capsys.readouterr().err
        assert f"Warning: no vault snapshot was taken: {link} is a symlink" in err
        assert "Vault snapshot:" not in err
        assert _tree(outside) == before

    def test_a_looped_base(self, start_mocks, tmp_path, capsys):
        def plant(rw):
            _versions_dir(rw).symlink_to(".versions")
            return _versions_dir(rw)

        rc, link = self._start(start_mocks, tmp_path, plant)
        assert rc == 0
        assert f"no vault snapshot was taken: {link} is a symlink" in capsys.readouterr().err

    def test_a_looped_shared_base_fails_closed(self, start_mocks, tmp_path, capsys):
        def plant(rw):
            _versions_dir(rw).symlink_to(".versions")
            return _versions_dir(rw)

        rc, link = self._start(start_mocks, tmp_path, plant, mode=BoxMode.primary)
        assert rc == 0
        assert f"no vault snapshot was taken: {link} is a symlink" in capsys.readouterr().err

    def test_a_repointed_shared_base_snapshots(self, start_mocks, tmp_path, capsys):
        big = tmp_path / "bigdisk"

        def plant(rw):
            big.mkdir()
            (big / ".layout").write_text("per-box-v1\n")
            _link_versions(rw, big)

        rc, _ = self._start(start_mocks, tmp_path, plant, mode=BoxMode.primary)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Vault snapshot:" in err and "Warning: no vault snapshot" not in err
        assert len(list((big / "b").iterdir())) == 1

    def test_a_plain_base_still_snapshots(self, start_mocks, tmp_path, capsys):
        rc, _ = self._start(start_mocks, tmp_path, lambda rw: None)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Vault snapshot:" in err and "Warning: no vault snapshot" not in err


class TestTheVaultVerbsNameTheLink:
    """``vault snapshot``/``list``/``restore``/``prune`` print ``Error:`` and rc 1."""

    @pytest.mark.parametrize("verb", ["snapshot", "list", "restore", "prune"])
    def test_a_linked_base(self, verb, tmp_path, monkeypatch, capsys):
        from kanibako.commands import vault_cmd

        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        outside = tmp_path / "outside"
        _snap(outside)  # an unmarked legacy entry behind the link
        _link_versions(rw, outside)
        before = _tree(outside)
        monkeypatch.setattr(vault_cmd, "_resolve_vault_rw",
                            lambda *_a: (rw, "b", True, {}, "b"))
        args = SimpleNamespace(project=None, quiet=False, name=TS, force=True, keep=1)
        rc = getattr(vault_cmd, f"run_{verb}")(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert err.startswith(f"Error: {_versions_dir(rw)} is a symlink")
        assert _tree(outside) == before


class TestTheMoveIsOtherwiseUnchanged:

    def test_relocate_across_trees_moves_the_store(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        assert relocate_snapshot_store(old, new, old_box="a", new_box="b") == \
            _versions_dir(new) / "b"
        assert (_versions_dir(new) / "b" / TS / "f").read_text() == "mine"
        assert not (_versions_dir(old) / "a").exists()

    def test_a_cross_device_move_copies_links_verbatim(self, tmp_path, monkeypatch):
        """EXDEV: a no-follow copy, then the source goes; a link inside stays a link."""
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        snap = _snap(_versions_dir(old) / "a")
        (snap / "ln").symlink_to("/etc/passwd")

        def exdev(*a, **kw):
            raise OSError(errno.EXDEV, "cross-device")

        monkeypatch.setattr(boxfs, "rename", exdev)
        relocate_snapshot_store(old, new, old_box="a", new_box="b")
        moved = _versions_dir(new) / "b" / TS
        assert (moved / "f").read_text() == "mine"
        assert (moved / "ln").is_symlink() and str((moved / "ln").readlink()) == "/etc/passwd"
        assert not (_versions_dir(old) / "a").exists()

    def test_merge_keeps_both_and_names_the_owner(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        _snap(_versions_dir(new) / "b", TS, "theirs")
        relocate_snapshot_store(old, new, old_box="a", new_box="b")
        store = _versions_dir(new) / "b"
        assert (store / TS / "f").read_text() == "theirs"
        assert (store / f"{TS}-a" / "f").read_text() == "mine"
        assert not (_versions_dir(old) / "a").exists()

    def test_adopt_moves_the_renamed_store(self, tmp_path):
        k = kuid.generate()
        rw = _rw(tmp_path / "s")
        _snap(_versions_dir(rw) / f"{k}_old")
        assert adopt_standalone_store(rw, box_name=f"{k}_new") == \
            _versions_dir(rw) / f"{k}_new"
        assert (_versions_dir(rw) / f"{k}_new" / TS / "f").read_text() == "mine"


class TestAVerbNeverReadsThroughALinkedStore:
    """A box-writable store link: ``list``/``restore``/``prune``/``snapshot`` name it,
    rc 1 -- never "No snapshots found.", never a listing or a delete through it."""

    @pytest.mark.parametrize("verb", ["snapshot", "list", "restore", "prune"])
    @pytest.mark.parametrize("exclusive", [False, True])
    def test_a_linked_store(self, verb, exclusive, tmp_path, monkeypatch, capsys):
        from kanibako.commands import vault_cmd

        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        outside = tmp_path / "outside"
        _snap(outside, TS)
        _snap(outside, "20230101T000000Z")
        _versions_dir(rw).mkdir()
        (_versions_dir(rw) / ".layout").write_text("per-box-v1\n")
        (_versions_dir(rw) / "b").symlink_to(outside)
        before = _tree(outside)
        monkeypatch.setattr(vault_cmd, "_resolve_vault_rw",
                            lambda *_a: (rw, "b", exclusive, {}, "b"))
        args = SimpleNamespace(project=None, quiet=False, name=TS, force=True, keep=1)
        rc = getattr(vault_cmd, f"run_{verb}")(args)
        out = capsys.readouterr()
        assert rc == 1
        assert out.err.startswith(f"Error: {_versions_dir(rw) / 'b'} is a symlink")
        assert TS not in out.out and "No snapshots" not in out.out
        assert _tree(outside) == before


class TestTheCureFollowsNoLink:
    """The hand cure for a stranded store prints no command that would follow a link."""

    def _states(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        o = SimpleNamespace(vault_rw=old, name="a", mode=BoxMode.named)
        n = SimpleNamespace(vault_rw=new, name="b", mode=BoxMode.named)
        return old, new, o, n

    def _cure(self, o, n):
        from kanibako.commands.box._lifecycle import _snapshot_cure
        return _snapshot_cure(o, n)

    def test_a_linked_old_store_is_not_picked(self, tmp_path):
        """Relocate's pick: a store that is a link is never carried."""
        old, _new, o, n = self._states(tmp_path)
        secrets = tmp_path / "hostsecrets"
        secrets.mkdir()
        _versions_dir(old).mkdir()
        (_versions_dir(old) / "a").symlink_to(secrets)
        assert self._cure(o, n) is None

    @pytest.mark.parametrize(
        "side", ["old-exclusive-base", "new-exclusive-base", "new-store", "new-looped-base"])
    def test_a_link_is_named_with_no_command(self, tmp_path, side):
        """A link a box could plant: at or below an exclusive (standalone) base, below a
        shared one, or a shared base that cannot be resolved."""
        old, new, o, n = self._states(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        if side == "old-exclusive-base":
            o.mode = BoxMode.standalone
            _snap(elsewhere / "a")
            _link_versions(old, elsewhere)
            link = _versions_dir(old)
        else:
            _snap(_versions_dir(old) / "a")
            if side == "new-exclusive-base":
                n.mode = BoxMode.standalone
                _link_versions(new, elsewhere)
                link = _versions_dir(new)
            elif side == "new-looped-base":
                _versions_dir(new).symlink_to(".versions")
                link = _versions_dir(new)
            else:
                elsewhere.mkdir()
                _versions_dir(new).mkdir()
                (_versions_dir(new) / "b").symlink_to(elsewhere)
                link = _versions_dir(new) / "b"
        cure = self._cure(o, n)
        assert cure is not None
        assert f"{link} is a symlink" in cure
        assert "run:" not in cure and "mv " not in cure

    def test_a_repointed_shared_base_gets_the_command(self, tmp_path):
        """A link AT a shared base is the user's own repoint: resolved, not refused."""
        old, _new, o, n = self._states(tmp_path)
        big = tmp_path / "bigdisk"
        _snap(big / "a")
        _link_versions(old, big)
        cure = self._cure(o, n)
        assert cure is not None and "run: mkdir -p -- " in cure
        assert str(big / "a") in cure

    def test_link_refusal_says_cannot_be_opened_for_a_non_link(self, tmp_path):
        from kanibako.snapshots import link_refusal
        (tmp_path / "d").mkdir()
        (tmp_path / "l").symlink_to("d")
        assert link_refusal(tmp_path / "d") == f"{tmp_path / 'd'} cannot be opened; make it readable"
        assert link_refusal(tmp_path / "l").startswith(f"{tmp_path / 'l'} is a symlink")

    def test_a_merge_names_the_entries_mv_n_leaves(self, tmp_path):
        old, new, o, n = self._states(tmp_path)
        _snap(_versions_dir(old) / "a", TS)
        _snap(_versions_dir(old) / "a", "20230101T000000Z")
        _snap(_versions_dir(new) / "b", TS, "theirs")
        cure = self._cure(o, n)
        assert cure is not None and "mv -n -- " in cure
        assert f"It leaves {TS} in {_versions_dir(old) / 'a'}" in cure
        assert "20230101T000000Z" not in cure

    def test_a_merge_without_collisions_names_none(self, tmp_path):
        old, new, o, n = self._states(tmp_path)
        _snap(_versions_dir(old) / "a", TS)
        _snap(_versions_dir(new) / "b", "20230101T000000Z", "theirs")
        cure = self._cure(o, n)
        assert cure is not None and "mv -n -- " in cure and "leaves" not in cure

    def test_an_absent_store_gets_the_move(self, tmp_path):
        old, _new, o, n = self._states(tmp_path)
        _snap(_versions_dir(old) / "a", TS)
        cure = self._cure(o, n)
        assert cure is not None and "mkdir -p -- " in cure and " mv -- " in cure
