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

    def test_lowercases(self) -> None:
        # R2: every box name is lowercase, so the leaf is folded too.
        assert box_identity.sanitize_cap("MyProj") == "myproj"
        assert box_identity.sanitize_cap("ALL-CAPS_1.0") == "all-caps_1.0"

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


# ---------------------------------------------------------------------------
# is_canonical_standalone_name
# ---------------------------------------------------------------------------

class TestIsCanonicalStandaloneName:
    def test_accepts_well_formed(self) -> None:
        assert box_identity.is_canonical_standalone_name("ab2c3_proj")
        assert box_identity.is_canonical_standalone_name("a2b3c_my-app_1.0")
        # Generated names round-trip through the matcher.
        for _ in range(20):
            name = box_identity.make_standalone_box_name(Path("/x/proj"), set())
            assert box_identity.is_canonical_standalone_name(name)

    def test_rejects_wrong_prefix_length(self) -> None:
        assert not box_identity.is_canonical_standalone_name("abcd_proj")  # 4
        assert not box_identity.is_canonical_standalone_name("abcde2_proj")  # 6

    def test_rejects_illegal_prefix_chars(self) -> None:
        # 0,1,8,9 are not in the base32 [a-z2-7] alphabet.
        assert not box_identity.is_canonical_standalone_name("ab01c_proj")
        assert not box_identity.is_canonical_standalone_name("ab89c_proj")

    def test_rejects_uppercase(self) -> None:
        # The matcher is case-sensitive; callers lowercase first.
        assert not box_identity.is_canonical_standalone_name("ab2c3_Proj")
        assert not box_identity.is_canonical_standalone_name("AB2C3_proj")

    def test_rejects_illegal_leaf_chars(self) -> None:
        assert not box_identity.is_canonical_standalone_name("ab2c3_my proj")
        assert not box_identity.is_canonical_standalone_name("ab2c3_a/b")

    def test_rejects_over_long_leaf(self) -> None:
        leaf = "x" * 33  # > 32-char cap
        assert not box_identity.is_canonical_standalone_name(f"ab2c3_{leaf}")
        # Exactly 32 is fine.
        assert box_identity.is_canonical_standalone_name("ab2c3_" + "x" * 32)

    def test_rejects_missing_leaf(self) -> None:
        assert not box_identity.is_canonical_standalone_name("ab2c3_")
        assert not box_identity.is_canonical_standalone_name("ab2c3")


# ---------------------------------------------------------------------------
# resolve_standalone_name
# ---------------------------------------------------------------------------

class TestResolveStandaloneName:
    def test_empty_generates_from_root(self) -> None:
        name = box_identity.resolve_standalone_name(Path("/x/myproj"), "", set())
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert len(prefix) == 5
        assert leaf == "myproj"

    def test_no_match_uses_supplied_as_leaf(self) -> None:
        # A plain (non-canonical) --name becomes the leaf, with a fresh prefix.
        name = box_identity.resolve_standalone_name(
            Path("/x/myproj"), "WeirdName", set()
        )
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert len(prefix) == 5
        assert leaf == "weirdname"  # lowercased + sanitized

    def test_no_match_sanitizes_supplied(self) -> None:
        name = box_identity.resolve_standalone_name(
            Path("/x/p"), "has spaces!", set()
        )
        assert name.endswith("_has_spaces_")

    def test_over_long_supplied_is_not_canonical_so_used_as_leaf(self) -> None:
        # A 5-char prefix shape but an over-long leaf fails the matcher → the
        # WHOLE string is sanitized+capped into a fresh-prefixed name.
        supplied = "ab2c3_" + "x" * 40
        name = box_identity.resolve_standalone_name(Path("/x/p"), supplied, set())
        prefix, sep, leaf = name.partition("_")
        assert len(prefix) == 5
        # sanitize_cap caps the leaf at 32 chars.
        assert len(leaf) == 32
        # The result itself is a valid canonical name.
        assert box_identity.is_canonical_standalone_name(name)

    def test_match_and_free_returns_verbatim(self) -> None:
        supplied = "ab2c3_proj"
        name = box_identity.resolve_standalone_name(Path("/x/p"), supplied, set())
        assert name == supplied

    def test_match_lowercased_before_verbatim(self) -> None:
        # Uppercase supplied is folded, then matched, then returned verbatim.
        name = box_identity.resolve_standalone_name(
            Path("/x/p"), "AB2C3_Proj", set()
        )
        assert name == "ab2c3_proj"

    def test_match_and_taken_raises(self) -> None:
        from kanibako.errors import ProjectError

        supplied = "ab2c3_proj"
        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.resolve_standalone_name(
                Path("/x/p"), supplied, {supplied}
            )


# ---------------------------------------------------------------------------
# validate_standalone_name (BUG-A pre-flight)
# ---------------------------------------------------------------------------

class TestValidateStandaloneName:
    def test_empty_is_noop(self) -> None:
        box_identity.validate_standalone_name("", {"ab2c3_proj"})

    def test_non_canonical_is_noop(self) -> None:
        # A plain string always becomes a fresh-prefixed name → never refusable.
        box_identity.validate_standalone_name("WeirdName", {"weirdname"})

    def test_free_canonical_is_noop(self) -> None:
        box_identity.validate_standalone_name("ab2c3_proj", {"zz9zz_other"})

    def test_taken_canonical_raises(self) -> None:
        from kanibako.errors import ProjectError

        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.validate_standalone_name("ab2c3_proj", {"ab2c3_proj"})

    def test_taken_canonical_raises_case_insensitive(self) -> None:
        from kanibako.errors import ProjectError

        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.validate_standalone_name("AB2C3_Proj", {"ab2c3_proj"})

    def test_match_and_taken_error_suggests_dropping_prefix(self) -> None:
        from kanibako.errors import ProjectError

        supplied = "ab2c3_proj"
        with pytest.raises(ProjectError, match=r"'ab2c3_' prefix"):
            box_identity.resolve_standalone_name(
                Path("/x/p"), supplied, {supplied}
            )
