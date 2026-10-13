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
        """An exclusive (standalone) new base: a link at the store is refused."""
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _versions_dir(new).mkdir()
        (_versions_dir(new) / "b").symlink_to(elsewhere)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b",
                                    new_base_exclusive=True)
        assert exc.value.errno == errno.ELOOP
        assert _tree(elsewhere) == {}
        assert not (_versions_dir(new) / ".layout").exists()
        assert (_versions_dir(old) / "a" / TS / "f").read_text() == "mine"

    @pytest.mark.parametrize("where", ["base", "store"])
    def test_create_snapshot_copies_nothing_through_a_link(self, tmp_path, where):
        """An exclusive (standalone) base: a link at it or at the store is refused."""
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
            create_snapshot(rw, box_name="b", store_exclusive=True)
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

    @pytest.mark.parametrize("repointed_base", [False, True])
    def test_a_repointed_store_is_used_too(self, tmp_path, repointed_base):
        """``.versions/<box>`` in a shared base is as host-only as the base itself."""
        if repointed_base:
            rw, base = self._repoint(tmp_path)
        else:
            rw = _rw(tmp_path / "w")
            (rw / "data").write_text("live")
            base = _versions_dir(rw)
            base.mkdir()
            (base / ".layout").write_text("per-box-v1\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (base / "b").symlink_to(elsewhere)
        snap = create_snapshot(rw, box_name="b")
        assert snap is not None and snap.parent == elsewhere
        assert (snap / "data").read_text() == "live"
        assert (base / "b").is_symlink()

    @pytest.mark.parametrize("target", ["dangling", "loop", "file"])
    @pytest.mark.parametrize("where", ["base", "store"])
    def test_a_broken_repoint_fails_closed(self, tmp_path, where, target):
        """A repoint that leads to no directory is refused, as a broken repoint."""
        from kanibako.snapshots import BrokenRepointError, refusal_text

        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        link = _versions_dir(rw)
        if where == "store":
            link.mkdir()
            (link / ".layout").write_text("per-box-v1\n")
            link = link / "b"
        if target == "dangling":
            link.symlink_to(tmp_path / "unmounted")
        elif target == "loop":
            link.symlink_to(link.name)
        else:
            (tmp_path / "f").write_text("")
            link.symlink_to(tmp_path / "f")
        before = _tree(tmp_path)
        with pytest.raises(BrokenRepointError) as exc:
            create_snapshot(rw, box_name="b")
        assert exc.value.link == link
        assert refusal_text(exc.value) == (
            f"{link} is a link that does not lead to a directory; fix the link or mount "
            f"its target")
        assert _tree(tmp_path) == before

    def test_relocate_into_a_repointed_store_merges_there(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        elsewhere = tmp_path / "elsewhere"
        _snap(elsewhere, "20230101T000000Z", "theirs")
        _versions_dir(new).mkdir()
        (_versions_dir(new) / "b").symlink_to(elsewhere)
        assert relocate_snapshot_store(old, new, old_box="a", new_box="b") == elsewhere
        assert sorted(p.name for p in elsewhere.iterdir()) == ["20230101T000000Z", TS]
        assert (elsewhere / TS / "f").read_text() == "mine"
        assert (_versions_dir(new) / "b").is_symlink()
        assert not (_versions_dir(old) / "a").exists()

    @pytest.mark.parametrize("aliased", [False, True])
    def test_relocate_into_a_repoint_at_the_old_store_carries_nothing(self, tmp_path, aliased):
        """*aliased*: the vault is reached through a linked parent, so the lexical old
        store and the resolved new one are two spellings of one directory."""
        (tmp_path / "real").mkdir()
        (tmp_path / "alias").symlink_to("real")
        old = _rw(tmp_path / ("alias" if aliased else "real") / "w")
        _snap(_versions_dir(old) / "a")
        (_versions_dir(old) / "b").symlink_to("a")
        assert relocate_snapshot_store(old, old, old_box="a", new_box="b") is None
        assert [p.name for p in (_versions_dir(old) / "a").iterdir()] == [TS]
        assert (_versions_dir(old) / "a" / TS / "f").read_text() == "mine"

    def test_relocate_into_a_repoint_inside_the_old_store_moves_nothing(self, tmp_path):
        """``.versions/b -> a/<TS>``: no move at all, never a partial move that nests the
        older snapshot inside the newer."""
        rw = _rw(tmp_path / "w")
        _snap(_versions_dir(rw) / "a", TS)
        _snap(_versions_dir(rw) / "a", "20230101T000000Z", "older")
        (_versions_dir(rw) / "b").symlink_to(Path("a") / TS)
        before = _tree(tmp_path)
        assert relocate_snapshot_store(rw, rw, old_box="a", new_box="b") is None
        assert _tree(tmp_path) == before

    def test_nesting_counts_either_way(self, tmp_path):
        from kanibako.snapshots import stores_overlap

        (tmp_path / "s" / "inner").mkdir(parents=True)
        (tmp_path / "t").mkdir()
        (tmp_path / "alias").symlink_to("s")
        assert stores_overlap(tmp_path / "s", tmp_path / "s" / "inner")
        assert stores_overlap(tmp_path / "alias" / "inner", tmp_path / "s")
        assert stores_overlap(tmp_path / "alias", tmp_path / "s")
        assert not stores_overlap(tmp_path / "s", tmp_path / "t")
        assert not stores_overlap(tmp_path / "s", tmp_path / "s2")

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
        assert "replace it with a directory to resume snapshots.\n" in err
        assert "Vault snapshot:" not in err
        assert _tree(outside) == before

    def test_a_looped_base(self, start_mocks, tmp_path, capsys):
        def plant(rw):
            _versions_dir(rw).symlink_to(".versions")
            return _versions_dir(rw)

        rc, link = self._start(start_mocks, tmp_path, plant)
        assert rc == 0
        assert f"no vault snapshot was taken: {link} is a symlink" in capsys.readouterr().err

    @pytest.mark.parametrize("target", ["loop", "dangling"])
    def test_a_broken_shared_repoint_fails_closed(self, start_mocks, tmp_path, capsys, target):
        """The repoint is followed, so the fault named is where it leads, not the link."""
        def plant(rw):
            versions = _versions_dir(rw)
            versions.symlink_to(".versions" if target == "loop" else tmp_path / "unmounted")
            return versions

        rc, link = self._start(start_mocks, tmp_path, plant, mode=BoxMode.primary)
        assert rc == 0
        err = capsys.readouterr().err
        assert (f"Warning: no vault snapshot was taken: {link} is a link that does not lead "
                f"to a directory; fix the link or mount its target to resume snapshots.") in err
        assert "symlink" not in err and "Vault snapshot:" not in err

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
        assert "replace it with a directory and try again. Nothing was changed.\n" in err
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
    """A store link in an exclusive (standalone) base is box-writable:
    ``list``/``restore``/``prune``/``snapshot`` name it, rc 1 -- never "No snapshots
    found.", never a listing or a delete through it.  In a shared base the link is the
    user's repoint, and every verb uses it."""

    def _linked(self, tmp_path, exclusive, monkeypatch):
        from kanibako.commands import vault_cmd

        rw = _rw(tmp_path / "w")
        (rw / "data").write_text("live")
        outside = tmp_path / "outside"
        _snap(outside, TS)
        _snap(outside, "20230101T000000Z")
        _versions_dir(rw).mkdir()
        (_versions_dir(rw) / ".layout").write_text("per-box-v1\n")
        (_versions_dir(rw) / "b").symlink_to(outside)
        monkeypatch.setattr(vault_cmd, "_resolve_vault_rw",
                            lambda *_a: (rw, "b", exclusive, {}, "b"))
        return rw, outside

    @pytest.mark.parametrize("verb", ["snapshot", "list", "restore", "prune"])
    def test_a_shared_repointed_store_is_used(self, verb, tmp_path, monkeypatch, capsys):
        from kanibako.commands import vault_cmd

        rw, outside = self._linked(tmp_path, False, monkeypatch)
        args = SimpleNamespace(project=None, quiet=False, name=TS, force=True, keep=1)
        rc = getattr(vault_cmd, f"run_{verb}")(args)
        out = capsys.readouterr()
        assert rc == 0, out.err
        assert "symlink" not in out.err
        names = sorted(p.name for p in outside.iterdir())
        if verb == "list":
            assert TS in out.out and "20230101T000000Z" in out.out
        elif verb == "prune":
            assert names == [TS]
        else:  # a new snapshot (restore's safety copy) lands behind the repoint
            assert len(names) == 3
        if verb == "restore":
            assert (rw / "f").read_text() == "mine" and not (rw / "data").exists()
        assert (_versions_dir(rw) / "b").is_symlink()

    @pytest.mark.parametrize("verb", ["snapshot", "list", "restore", "prune"])
    def test_a_linked_store(self, verb, tmp_path, monkeypatch, capsys):
        from kanibako.commands import vault_cmd

        rw, outside = self._linked(tmp_path, True, monkeypatch)
        before = _tree(outside)
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
        "side", ["old-exclusive-base", "new-exclusive-base", "new-exclusive-store"])
    def test_a_link_is_named_with_no_command(self, tmp_path, side):
        """A link a box could plant: at or below an exclusive (standalone) base."""
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
            else:
                n.mode = BoxMode.standalone
                elsewhere.mkdir()
                _versions_dir(new).mkdir()
                (_versions_dir(new) / "b").symlink_to(elsewhere)
                link = _versions_dir(new) / "b"
        cure = self._cure(o, n)
        assert cure is not None
        assert f"{link} is a symlink" in cure
        assert "run:" not in cure and "mv " not in cure

    @pytest.mark.parametrize("target", ["loop", "dangling"])
    @pytest.mark.parametrize("where", ["base", "store"])
    def test_a_broken_shared_repoint_is_named_with_no_command(self, tmp_path, where, target):
        old, new, o, n = self._states(tmp_path)
        _snap(_versions_dir(old) / "a")
        link = _versions_dir(new)
        if where == "store":
            link.mkdir()
            link = link / "b"
        link.symlink_to(link.name if target == "loop" else tmp_path / "unmounted")
        cure = self._cure(o, n)
        assert cure is not None
        assert (f"{link} is a link that does not lead to a directory; fix the link or mount "
                f"its target, then move them by hand.") in cure
        assert "symlink" not in cure and "run:" not in cure and "mv " not in cure

    def test_a_repoint_into_the_stranded_store_gets_none(self, tmp_path):
        """``.versions/b -> a/<TS>``: a ``mv`` would nest a's snapshots in its own child."""
        old, new, o, n = self._states(tmp_path)
        n.vault_rw = old
        _snap(_versions_dir(old) / "a")
        (_versions_dir(old) / "b").symlink_to(Path("a") / TS)
        assert self._cure(o, n) is None

    def test_a_repoint_at_the_stranded_store_gets_none(self, tmp_path):
        (tmp_path / "real").mkdir()
        (tmp_path / "alias").symlink_to("real")
        rw = _rw(tmp_path / "alias" / "w")
        o = SimpleNamespace(vault_rw=rw, name="a", mode=BoxMode.named)
        n = SimpleNamespace(vault_rw=rw, name="b", mode=BoxMode.named)
        _snap(_versions_dir(rw) / "a")
        (_versions_dir(rw) / "b").symlink_to("a")
        assert self._cure(o, n) is None

    def test_a_repointed_shared_store_gets_the_command(self, tmp_path):
        old, new, o, n = self._states(tmp_path)
        _snap(_versions_dir(old) / "a")
        elsewhere = tmp_path / "elsewhere"
        _snap(elsewhere, "20230101T000000Z", "theirs")
        _versions_dir(new).mkdir()
        (_versions_dir(new) / "b").symlink_to(elsewhere)
        cure = self._cure(o, n)
        assert cure is not None
        assert f"run: mv -n -- {_versions_dir(old) / 'a'}/* {elsewhere}/" in cure

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


class TestTheRelocationWarningNamesABrokenRepoint:
    """``_relocate_snapshot_store``'s best-effort warning prints the exception: for a
    broken shared repoint it says what is wrong, not "refusing to follow the symlink"."""

    @pytest.mark.parametrize("where", ["base", "store"])
    def test_a_dangling_shared_repoint(self, tmp_path, capsys, where):
        from kanibako.commands.box._lifecycle import _relocate_snapshot_store

        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        link = _versions_dir(new)
        if where == "store":
            link.mkdir()
            link = link / "b"
        link.symlink_to(tmp_path / "unmounted")
        o = SimpleNamespace(vault_rw=old, name="a", mode=BoxMode.named)
        n = SimpleNamespace(vault_rw=new, name="b", mode=BoxMode.named)
        assert _relocate_snapshot_store(o, n) == _versions_dir(old) / "a"
        err = capsys.readouterr().err
        assert (f"Warning: could not move the vault snapshots from 'a' to 'b': {link} is a "
                f"link that does not lead to a directory; fix the link or mount its "
                f"target. They are still on disk under the old name") in err
        assert "refusing to follow" not in err
        assert (_versions_dir(old) / "a" / TS / "f").read_text() == "mine"

    def test_its_str_is_the_fault(self, tmp_path):
        from kanibako.snapshots import BrokenRepointError

        exc = BrokenRepointError(tmp_path / "l", tmp_path / "l")
        assert str(exc) == (f"{tmp_path / 'l'} is a link that does not lead to a directory; "
                            f"fix the link or mount its target")
        assert exc.errno == errno.ELOOP and exc.link == tmp_path / "l"


class TestALinkedOldStoreIsNamed:
    """Relocate never carries an old store that is itself a link (as before); the move
    now says so, naming the link and its target, instead of listing none silently."""

    def _move(self, tmp_path, capsys, *, target, new_mode=BoxMode.named,
              old_mode=BoxMode.named):
        from kanibako.commands.box._lifecycle import _relocate_snapshot_store

        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _versions_dir(old).mkdir()
        _versions_dir(new).mkdir()
        link = _versions_dir(old) / "a"
        link.symlink_to(target)
        o = SimpleNamespace(vault_rw=old, name="a", mode=old_mode)
        n = SimpleNamespace(vault_rw=new, name="b", mode=new_mode)
        _relocate_snapshot_store(o, n)
        return link, _versions_dir(new) / "b", capsys.readouterr().err

    def test_the_note_and_its_relink_cure(self, tmp_path, capsys):
        import subprocess

        from kanibako.snapshots import list_snapshots

        big = tmp_path / "bigdisk"
        _snap(big)
        link, new_store, err = self._move(tmp_path, capsys, target=big)
        assert err.startswith(
            f"Note: 'b' lists none of its old vault snapshots: {link} is a link to {big}, "
            f"and a linked snapshot store is never moved. The snapshots stay at {big}. "
            f"To give them to 'b', run: [ ! -e {new_store} ] && [ ! -L {new_store} ] && "
            f"ln -s -- {big} {new_store} && rm -- {link}\n")
        assert not os.path.lexists(new_store) and _tree(big) == {TS: ("dir", None),
                                                                 f"{TS}/f": ("file", b"mine")}
        cmd = err.split("run: ", 1)[1].strip()
        subprocess.run(cmd, shell=True, check=True)
        assert [s[0] for s in list_snapshots(new_store.parent.parent / "rw", box_name="b")] == [TS]
        assert not os.path.lexists(link)

    def test_a_dangling_link_gets_no_cure(self, tmp_path, capsys):
        gone = tmp_path / "unmounted"
        link, _new_store, err = self._move(tmp_path, capsys, target=gone)
        assert err == (f"Note: 'b' lists none of its old vault snapshots: {link} is a link "
                       f"to {gone}, and a linked snapshot store is never moved. The "
                       f"snapshots stay at {gone}.\n")

    def test_a_standalone_landing_gets_no_cure(self, tmp_path, capsys):
        big = tmp_path / "bigdisk"
        _snap(big)
        _link, _new_store, err = self._move(tmp_path, capsys, target=big,
                                            new_mode=BoxMode.standalone)
        assert err.startswith("Note: 'b' lists none of its old vault snapshots:")
        assert "run:" not in err

    @pytest.mark.parametrize("appeared", ["dir", "link"])
    def test_the_cure_fails_if_the_new_store_appeared(self, tmp_path, capsys, appeared):
        """A store made under the new name since (e.g. a start's auto-snapshot): the
        command fails and changes nothing -- no link filed inside it, the old one kept."""
        import subprocess

        big = tmp_path / "bigdisk"
        _snap(big)
        link, new_store, err = self._move(tmp_path, capsys, target=big)
        cmd = err.split("run: ", 1)[1].strip()
        if appeared == "dir":
            _snap(new_store, "20250101T000000Z", "since")
        else:
            new_store.symlink_to(tmp_path / "nowhere")
        before = _tree(tmp_path)
        assert subprocess.run(cmd, shell=True).returncode != 0
        assert _tree(tmp_path) == before
        assert link.is_symlink()

    def test_a_link_in_an_exclusive_old_base_gets_no_cure(self, tmp_path, capsys):
        """A box could have planted it: the Note names it, no command adopts it."""
        big = tmp_path / "bigdisk"
        _snap(big)
        link, _new_store, err = self._move(tmp_path, capsys, target=big,
                                           old_mode=BoxMode.standalone)
        assert err.startswith(f"Note: 'b' lists none of its old vault snapshots: {link} is "
                              f"a link to {big},")
        assert "run:" not in err


class TestANestedRepointIsNamed:
    """A new store repointed onto the old one's base, an ancestor, or inside it: nothing
    is moved (``stores_overlap``), and a Note says where the snapshots stay."""

    @pytest.mark.parametrize("into", ["inside-old-store", "old-base", "ancestor"])
    def test_the_note(self, tmp_path, capsys, into):
        from kanibako.commands.box._lifecycle import _relocate_snapshot_store

        rw = _rw(tmp_path / "w")
        store = _versions_dir(rw) / "a"
        _snap(store)
        target = {"inside-old-store": Path("a") / TS, "old-base": Path("."),
                  "ancestor": Path("..")}[into]
        (_versions_dir(rw) / "b").symlink_to(target)
        new_rw = rw.parent / "b"
        new_rw.mkdir()
        before = _tree(tmp_path)
        o = SimpleNamespace(vault_rw=rw, name="a", mode=BoxMode.named)
        n = SimpleNamespace(vault_rw=new_rw, name="b", mode=BoxMode.named)
        assert _relocate_snapshot_store(o, n) == store
        err = capsys.readouterr().err
        real = (_versions_dir(rw) / "b").resolve()
        assert err == (f"Note: 'b' was not given its old vault snapshots: its store "
                       f"{_versions_dir(rw) / 'b'} leads to {real}, which overlaps the old "
                       f"store {store}, so nothing was moved. The snapshots stay at "
                       f"{store}.\n")
        assert _tree(tmp_path) == before

    def test_an_alias_of_the_old_store_says_nothing(self, tmp_path, capsys):
        """``.versions/b -> a``: the new name lists the same snapshots; nothing to say."""
        from kanibako.commands.box._lifecycle import _relocate_snapshot_store

        rw = _rw(tmp_path / "w")
        _snap(_versions_dir(rw) / "a")
        (_versions_dir(rw) / "b").symlink_to("a")
        new_rw = rw.parent / "b"
        new_rw.mkdir()
        o = SimpleNamespace(vault_rw=rw, name="a", mode=BoxMode.named)
        n = SimpleNamespace(vault_rw=new_rw, name="b", mode=BoxMode.named)
        _relocate_snapshot_store(o, n)
        assert capsys.readouterr().err == ""
