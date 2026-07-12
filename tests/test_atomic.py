"""Tests for kanibako._atomic (atomic_write_text / atomic_write_yaml).

These verify the durability contract that the registry/state writers rely on:
content lands correctly, overwrites work, no temp residue on success, and a
failure mid-write leaves the ORIGINAL file intact (never truncated).
"""

from __future__ import annotations

import os

import pytest

from kanibako._atomic import atomic_write_text


def _temp_residue(directory):
    """Return any leftover atomic-write temp files in *directory*."""
    return [p for p in directory.iterdir() if ".tmp" in p.name]


def test_writes_content(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello world\n")
    assert target.read_text() == "hello world\n"


def test_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c" / "out.txt"
    atomic_write_text(target, "nested\n")
    assert target.read_text() == "nested\n"


def test_overwrites_existing(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old content")
    atomic_write_text(target, "new content")
    assert target.read_text() == "new content"


def test_no_temp_residue_on_success(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "data")
    # Only the target should remain; no .tmp scratch files left behind.
    assert _temp_residue(tmp_path) == []
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]


def test_failure_leaves_original_intact(tmp_path, monkeypatch):
    """If os.replace raises, the original file must NOT be truncated/lost."""
    target = tmp_path / "out.txt"
    target.write_text("ORIGINAL")

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(target, "REPLACEMENT")

    # Original content survives untouched.
    assert target.read_text() == "ORIGINAL"
    # And the failed temp file was cleaned up.
    assert _temp_residue(tmp_path) == []


def test_failure_leaves_no_target_when_absent(tmp_path, monkeypatch):
    """If the target didn't exist and the write fails, no torn file appears."""
    target = tmp_path / "out.txt"

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(target, "data")

    assert not target.exists()
    assert _temp_residue(tmp_path) == []


def test_fsync_failure_leaves_original_intact(tmp_path, monkeypatch):
    """A failing fsync must also leave the original intact and clean up."""
    target = tmp_path / "out.txt"
    target.write_text("ORIGINAL")

    def boom(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError):
        atomic_write_text(target, "REPLACEMENT")

    assert target.read_text() == "ORIGINAL"
    assert _temp_residue(tmp_path) == []
