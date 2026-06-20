"""Tests for kanibako.box_identity (standalone box name generation)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kanibako import box_identity


# ---------------------------------------------------------------------------
# random24
# ---------------------------------------------------------------------------

class TestRandom24:
    def test_fixed_width_five_chars(self) -> None:
        for _ in range(50):
            tok = box_identity.random24()
            assert len(tok) == 5

    def test_lowercase_base32_alphabet(self) -> None:
        alphabet = re.compile(r"^[a-z2-7]{5}$")
        for _ in range(50):
            assert alphabet.match(box_identity.random24())

    def test_varies(self) -> None:
        # 24 bits → collisions are rare; a handful of draws should differ.
        toks = {box_identity.random24() for _ in range(20)}
        assert len(toks) > 1


# ---------------------------------------------------------------------------
# sanitize_cap
# ---------------------------------------------------------------------------

class TestSanitizeCap:
    def test_passes_portable_chars(self) -> None:
        assert box_identity.sanitize_cap("my-app_1.0") == "my-app_1.0"

    def test_replaces_illegal_chars(self) -> None:
        assert box_identity.sanitize_cap("my app!@#") == "my_app___"

    def test_replaces_slashes_and_spaces(self) -> None:
        assert box_identity.sanitize_cap("a/b c") == "a_b_c"

    def test_caps_at_32_chars(self) -> None:
        long_leaf = "x" * 100
        out = box_identity.sanitize_cap(long_leaf)
        assert len(out) == 32
        assert out == "x" * 32

    def test_empty_leaf_falls_back_to_box(self) -> None:
        assert box_identity.sanitize_cap("") == "box"

    def test_all_illegal_then_capped_is_not_empty(self) -> None:
        # All-illegal sanitizes to underscores (not empty), so no fallback.
        assert box_identity.sanitize_cap("///") == "___"


# ---------------------------------------------------------------------------
# make_standalone_box_name
# ---------------------------------------------------------------------------

class TestMakeStandaloneBoxName:
    def test_shape_is_random_underscore_leaf(self) -> None:
        name = box_identity.make_standalone_box_name(Path("/x/myproj"), set())
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert len(prefix) == 5
        assert leaf == "myproj"

    def test_uses_root_basename_as_leaf(self) -> None:
        name = box_identity.make_standalone_box_name(
            Path("/home/user/cool project"), set()
        )
        assert name.endswith("_cool_project")

    def test_empty_basename_falls_back(self) -> None:
        # Path("/") has an empty name → "box" leaf.
        name = box_identity.make_standalone_box_name(Path("/"), set())
        assert name.endswith("_box")

    def test_avoids_collision_with_existing(self) -> None:
        root = Path("/x/proj")
        # Generate one real name, then forbid it and confirm a regen differs
        # (the whole-name collision check must regenerate the random prefix).
        first = box_identity.make_standalone_box_name(root, set())
        second = box_identity.make_standalone_box_name(root, {first})
        assert second != first
        assert second.endswith("_proj")

    def test_regen_eventually_unique(self) -> None:
        # Generate a batch with a growing forbidden set; each must be unique.
        root = Path("/x/proj")
        seen: set[str] = set()
        for _ in range(50):
            name = box_identity.make_standalone_box_name(root, seen)
            assert name not in seen
            seen.add(name)

    def test_raises_when_no_unique_name_possible(self, monkeypatch) -> None:
        # Force random24 to always return the same value → every candidate
        # collides with `existing` → bounded retries exhausted → RuntimeError.
        monkeypatch.setattr(box_identity, "random24", lambda: "aaaaa")
        with pytest.raises(RuntimeError, match="unique standalone box name"):
            box_identity.make_standalone_box_name(Path("/x/p"), {"aaaaa_p"})
