"""Tests for kanibako.tree_copy: every symlink is copied verbatim, never followed (Q70/Q74)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kanibako.tree_copy import copy_tree_keeping_links


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
