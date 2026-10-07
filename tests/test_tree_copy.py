"""Tests for kanibako.tree_copy: every symlink is copied verbatim, never followed (Q70/Q74)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kanibako.tree_copy import copy_tree_keeping_links, failed_entries


@pytest.fixture
def layout(tmp_path):
    """``src`` (one level deep) beside ``outside``; the copy goes two levels deeper."""
    src = tmp_path / "a" / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "inner.txt").write_text("inner")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "big.txt").write_text("outside data")
    (outside / "deep" / "nested.txt").write_text("nested")
    dst = tmp_path / "b" / "c" / "dst"
    dst.parent.mkdir(parents=True)
    return src, outside, dst


class TestCopyTreeKeepingLinks:
    def test_every_kind_of_link_is_copied_verbatim(self, layout):
        """Inside, escaping, absolute, dangling, directory: each lands with the text it had."""
        src, outside, dst = layout
        texts = {
            "in": "sub/inner.txt",
            "sub/up": "../sub/inner.txt",
            "sub/out": "../../../outside/big.txt",
            "abs": str(outside / "big.txt"),
            "gone_abs": str(outside / "no-such"),
            "gone_in": "no-such-inside",
            "gone_out": "../../outside/no-such",
            "dirlink": "../../outside",
        }
        for rel, text in texts.items():
            (src / rel).symlink_to(text)
        assert (src / "sub" / "out").read_text() == "outside data"
        copy_tree_keeping_links(src, dst)
        for rel, text in texts.items():
            assert (dst / rel).is_symlink(), rel
            assert os.readlink(dst / rel) == text, rel
        assert (dst / "in").resolve() == (dst / "sub" / "inner.txt").resolve()

    def test_linked_trees_are_not_traversed_or_materialized(self, layout):
        src, outside, dst = layout
        (src / "dirlink").symlink_to(Path("..") / ".." / "outside")
        (src / "absdir").symlink_to(outside)
        before = sorted(p.relative_to(outside) for p in outside.rglob("*"))
        copy_tree_keeping_links(src, dst)
        real_entries = [p.relative_to(dst) for p in dst.rglob("*") if not p.is_symlink()]
        assert sorted(real_entries) == [Path("sub"), Path("sub") / "inner.txt"]
        assert sorted(p.relative_to(outside) for p in outside.rglob("*")) == before

    def test_ignore_is_passed_through(self, layout):
        src, outside, dst = layout
        (src / "skip.lock").write_text("x")
        (src / "keep").symlink_to(outside / "big.txt")
        copy_tree_keeping_links(src, dst, ignore=shutil.ignore_patterns("*.lock"))
        assert not (dst / "skip.lock").exists()
        assert os.readlink(dst / "keep") == str(outside / "big.txt")

    def test_existing_destination_needs_dirs_exist_ok(self, layout):
        src, _outside, dst = layout
        dst.mkdir()
        with pytest.raises(FileExistsError):
            copy_tree_keeping_links(src, dst)
        copy_tree_keeping_links(src, dst, dirs_exist_ok=True)
        assert (dst / "sub" / "inner.txt").read_text() == "inner"

    def test_existing_link_in_a_merge_destination_is_reported_not_overwritten(self, layout):
        src, outside, dst = layout
        (src / "l").symlink_to(outside / "big.txt")
        dst.mkdir()
        (dst / "l").symlink_to("prior")
        with pytest.raises(shutil.Error):
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True)
        assert os.readlink(dst / "l") == "prior"
        # Everything else was still copied.
        assert (dst / "sub" / "inner.txt").read_text() == "inner"


class TestReplaceExisting:
    """``replace_existing`` is the ``--force`` contract: a link replaces a non-directory, never a directory."""

    def test_a_link_replaces_every_non_directory_entry(self, layout):
        src, outside, dst = layout
        texts = {"file": "../../outside/big.txt", "link": str(outside), "gone": "no-such", "todir": "sub"}
        for rel, text in texts.items():
            (src / rel).symlink_to(text)
        dst.mkdir()
        (dst / "file").write_text("old")
        (dst / "link").symlink_to("prior")
        (dst / "gone").symlink_to("also-gone")
        (dst / "todir").symlink_to(outside)
        copy_tree_keeping_links(src, dst, dirs_exist_ok=True, replace_existing=True)
        for rel, text in texts.items():
            assert os.readlink(dst / rel) == text, rel
        # Replacing a link to a directory unlinks the link only; its target is untouched.
        assert (outside / "deep" / "nested.txt").read_text() == "nested"

    def test_an_existing_directory_is_reported_and_never_removed(self, layout):
        src, outside, dst = layout
        (src / "d").symlink_to(outside)
        (src / "f").symlink_to("sub/inner.txt")
        (dst / "d").mkdir(parents=True)
        (dst / "d" / "keep.txt").write_text("keep")
        (dst / "f").write_text("old")
        with pytest.raises(shutil.Error) as exc:
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, replace_existing=True)
        assert [Path(entry[1]).name for entry in exc.value.args[0]] == ["d"]
        assert (dst / "d" / "keep.txt").read_text() == "keep"
        assert not (dst / "d").is_symlink()
        # The replaceable entry was still replaced.
        assert os.readlink(dst / "f") == "sub/inner.txt"

    def test_it_replaces_only_links(self, layout):
        """A real directory meeting a file is copytree's own error: reported, the file untouched."""
        src, _outside, dst = layout
        dst.mkdir()
        (dst / "sub").write_text("a file")
        with pytest.raises(shutil.Error):
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, replace_existing=True)
        assert (dst / "sub").read_text() == "a file"


class TestNeverWritesThroughADestinationLink:
    """A merge meeting a link in *dst* with a real file or directory reports it; nothing is followed."""

    @pytest.mark.parametrize("replace_existing", [False, True])
    def test_a_file_meeting_a_link_to_a_file(self, layout, replace_existing):
        src, outside, dst = layout
        (src / "f.txt").write_text("new")
        dst.mkdir()
        (dst / "f.txt").symlink_to(outside / "big.txt")
        with pytest.raises(shutil.Error) as exc:
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, replace_existing=replace_existing)
        assert [Path(entry[1]).name for entry in exc.value.args[0]] == ["f.txt"]
        assert os.readlink(dst / "f.txt") == str(outside / "big.txt")
        assert (outside / "big.txt").read_text() == "outside data"
        # Everything else was still copied.
        assert (dst / "sub" / "inner.txt").read_text() == "inner"

    @pytest.mark.parametrize("replace_existing", [False, True])
    def test_a_directory_meeting_a_link_to_a_directory(self, layout, replace_existing):
        src, outside, dst = layout
        dst.mkdir()
        (dst / "sub").symlink_to(outside / "deep")
        before = sorted(p.name for p in (outside / "deep").iterdir())
        with pytest.raises(shutil.Error) as exc:
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, replace_existing=replace_existing)
        assert [Path(entry[1]).name for entry in exc.value.args[0]] == ["sub"]
        assert os.readlink(dst / "sub") == str(outside / "deep")
        assert sorted(p.name for p in (outside / "deep").iterdir()) == before

    def test_a_dangling_destination_link_is_not_created_through(self, layout):
        src, outside, dst = layout
        (src / "f.txt").write_text("new")
        dst.mkdir()
        (dst / "f.txt").symlink_to(outside / "made-by-the-copy.txt")
        with pytest.raises(shutil.Error):
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True)
        assert not (outside / "made-by-the-copy.txt").exists()

    def test_a_file_meeting_a_real_directory_is_reported_not_copied_into_it(self, layout):
        src, _outside, dst = layout
        (src / "name").write_text("file")
        (dst / "name").mkdir(parents=True)
        (dst / "name" / "keep.txt").write_text("keep")
        with pytest.raises(shutil.Error) as exc:
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True)
        assert [Path(entry[1]).name for entry in exc.value.args[0]] == ["name"]
        assert sorted(p.name for p in (dst / "name").iterdir()) == ["keep.txt"]

    def test_the_callers_ignore_still_applies(self, layout):
        src, outside, dst = layout
        (src / "skip.lock").write_text("x")
        dst.mkdir()
        (dst / "skip.lock").symlink_to(outside / "big.txt")
        copy_tree_keeping_links(src, dst, ignore=shutil.ignore_patterns("*.lock"), dirs_exist_ok=True)
        assert (outside / "big.txt").read_text() == "outside data"


def test_failed_entries_names_each_source_and_caps_the_list():
    err = shutil.Error([(f"/s/{i}", f"/d/{i}", "why") for i in range(7)])
    assert failed_entries(err) == "\n".join(
        ["7 entries failed:", *(f"  /s/{i}: why" for i in range(5)), "  … and 2 more"],
    )
    assert failed_entries(shutil.Error("not a list")) is None


class TestALinkedTreeRoot:
    """``keep_root_link``: a root that IS a link is re-created as a link (Q102 (a)).

    Without it ``copytree`` lists the root THROUGH and the target tree is materialized —
    the pointer the user made silently becomes a copy of what it pointed at.
    """

    @staticmethod
    def _relink_root(src: Path, text: str) -> None:
        """The fixture's real ``src`` becomes a link reading *text*."""
        shutil.rmtree(src)
        os.symlink(text, src)

    def test_off_by_default_a_linked_root_is_still_materialized(self, layout):
        src, outside, dst = layout
        self._relink_root(src, "../outside")
        copy_tree_keeping_links(src, dst)
        assert not dst.is_symlink()
        assert (dst / "big.txt").read_text() == "outside data"

    def test_a_relative_root_link_is_rewritten_to_name_the_same_target(self, layout):
        """The old text read against the OLD parent; from the new one it would drift."""
        src, outside, dst = layout
        self._relink_root(src, "../outside")
        copy_tree_keeping_links(src, dst, keep_root_link=True)
        assert dst.is_symlink()
        assert os.readlink(dst) == "../../outside"
        assert (dst / "big.txt").read_text() == "outside data"
        assert (dst / "deep" / "nested.txt").read_text() == "nested"

    def test_an_absolute_root_link_is_carried_exactly(self, layout):
        src, outside, dst = layout
        self._relink_root(src, str(outside))
        copy_tree_keeping_links(src, dst, keep_root_link=True)
        assert os.readlink(dst) == str(outside)

    def test_a_dangling_relative_root_link_is_rewritten_not_left_pointing_elsewhere(self, layout):
        src, _outside, dst = layout
        self._relink_root(src, "../gone")
        copy_tree_keeping_links(src, dst, keep_root_link=True)
        assert os.readlink(dst) == "../../gone"
        assert not dst.resolve().exists()

    def test_a_real_directory_at_the_destination_is_refused_not_removed(self, layout):
        """Only the party that MADE that directory may delete it."""
        src, outside, dst = layout
        self._relink_root(src, str(outside))
        dst.mkdir(parents=True)
        (dst / "someone_elses.txt").write_text("keep me")
        with pytest.raises(shutil.Error):
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, keep_root_link=True)
        assert (dst / "someone_elses.txt").read_text() == "keep me"

    def test_an_existing_link_yields_only_to_replace_existing(self, layout):
        src, outside, dst = layout
        self._relink_root(src, str(outside))
        other = layout[1] / "deep"
        os.symlink(str(other), dst)
        with pytest.raises(shutil.Error):
            copy_tree_keeping_links(src, dst, dirs_exist_ok=True, keep_root_link=True)
        assert os.readlink(dst) == str(other)
        copy_tree_keeping_links(
            src, dst, dirs_exist_ok=True, replace_existing=True, keep_root_link=True)
        assert os.readlink(dst) == str(outside)
