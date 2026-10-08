"""Tests for kanibako.launch.box_identity (standalone box name generation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako import kuid
from kanibako.launch import box_identity

# A fixed VALID kuid prefix (odd parity) and a fixed INVALID one (even parity,
# in-alphabet, non-sentinel) used across the canonical/verbatim tests. See
# tests/test_kuid.py for the codec's parity contract.
_VALID_KUID = "abcde"    # kuid.is_valid("abcde") is True
_INVALID_KUID = "ab2c3"  # in-alphabet, 5 chars, even parity → is_valid False


# ---------------------------------------------------------------------------
# standalone_kuid (kuid-prefix extraction) + compose_standalone_name
# ---------------------------------------------------------------------------

class TestStandaloneKuidHelpers:
    def test_standalone_kuid_is_the_prefix(self) -> None:
        assert box_identity.standalone_kuid("abcde_proj") == "abcde"

    def test_standalone_kuid_stops_at_first_underscore(self) -> None:
        # The leaf itself may hold underscores; the kuid alphabet never does,
        # so the prefix is unambiguously everything up to the FIRST '_'.
        assert box_identity.standalone_kuid("abcde_my_proj") == "abcde"

    def test_compose_uses_kuid_and_live_leaf(self) -> None:
        name = box_identity.compose_standalone_name(_VALID_KUID, Path("/x/myproj"))
        assert name == f"{_VALID_KUID}_myproj"

    def test_compose_sanitizes_and_caps_leaf(self) -> None:
        # The SPACE is sanitized; the CASE is not (spec §0) — the live leaf carries
        # the source directory's own spelling.
        name = box_identity.compose_standalone_name(_VALID_KUID, Path("/x/Cool Proj"))
        assert name == f"{_VALID_KUID}_Cool_Proj"

    def test_compose_roundtrips_through_standalone_kuid(self) -> None:
        name = box_identity.compose_standalone_name(_VALID_KUID, Path("/x/proj"))
        assert box_identity.standalone_kuid(name) == _VALID_KUID


# ---------------------------------------------------------------------------
# sanitize_cap
# ---------------------------------------------------------------------------

class TestSanitizeCap:
    def test_passes_portable_chars(self) -> None:
        assert box_identity.sanitize_cap("my-app_1.0") == "my-app_1.0"

    def test_keeps_case(self) -> None:
        # Spec §0, ⚑ NAMING RULES: the leaf carries the SOURCE DIRECTORY's case, and
        # only the kuid half of <kuid>_<leaf> is lowercase. This folded until the
        # entry fold came out; a fold restored here is the retired [R171] cure.
        assert box_identity.sanitize_cap("MyProj") == "MyProj"
        assert box_identity.sanitize_cap("ALL-CAPS_1.0") == "ALL-CAPS_1.0"

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
    def test_shape_is_kuid_underscore_leaf(self) -> None:
        name = box_identity.make_standalone_box_name(Path("/x/myproj"), set())
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert len(prefix) == 5
        # The prefix is a REAL kuid (Crockford, odd parity), not RFC base32.
        assert kuid.is_valid(prefix)
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
        # Force the kuid generator to always return the same value → every
        # candidate collides with `existing` → bounded retries exhausted.
        monkeypatch.setattr(box_identity.kuid, "generate", lambda: "aaaaa")
        with pytest.raises(RuntimeError, match="unique standalone box name"):
            box_identity.make_standalone_box_name(Path("/x/p"), {"aaaaa_p"})


# ---------------------------------------------------------------------------
# is_canonical_standalone_name
# ---------------------------------------------------------------------------

class TestIsCanonicalStandaloneName:
    def test_accepts_well_formed(self) -> None:
        # Prefix must be a VALID kuid (odd parity), leaf a sanitized token.
        assert box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_proj")
        assert box_identity.is_canonical_standalone_name("abcd1_my-app_1.0")
        # Generated names round-trip through the matcher.
        for _ in range(20):
            name = box_identity.make_standalone_box_name(Path("/x/proj"), set())
            assert box_identity.is_canonical_standalone_name(name)

    def test_rejects_non_kuid_prefix_even_parity(self) -> None:
        # In-alphabet, right length, but EVEN parity → not a valid kuid.
        assert not box_identity.is_canonical_standalone_name(f"{_INVALID_KUID}_proj")
        assert not box_identity.is_canonical_standalone_name("aaaaa_proj")

    def test_rejects_wrong_prefix_length(self) -> None:
        assert not box_identity.is_canonical_standalone_name("abcd_proj")  # 4
        assert not box_identity.is_canonical_standalone_name("abcdef_proj")  # 6

    def test_rejects_out_of_alphabet_prefix_chars(self) -> None:
        # 'u' is EXCLUDED from the Crockford kuid alphabet and (unlike i/l/o) is
        # NOT input-folded, so a 'u' in the prefix is a hard-invalid kuid; a punct
        # char likewise fails decode.
        assert not box_identity.is_canonical_standalone_name("abcdu_proj")
        assert not box_identity.is_canonical_standalone_name("ab!cd_proj")

    def test_accepts_uppercase_leaf(self) -> None:
        # The leaf admits uppercase (spec §0): it carries a case the user chose, and
        # the grammar accepts exactly what sanitize_cap emits. It rejected uppercase
        # outright until the entry fold came out — the retired fold written into the
        # grammar itself.
        assert box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_Proj")
        assert box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_PROJ")

    def test_prefix_folds_crockford_input_rules(self) -> None:
        # kuid.is_valid canonicalizes the prefix (Crockford: i/l→1, o→0, case), so an
        # uppercase / i-l-o prefix over a valid kuid still matches the SHAPE. What is
        # stored is the canonicalized prefix — see TestResolveStandaloneName.
        assert kuid.is_valid("ABCDE")  # sanity: folds to the valid 'abcde'
        assert box_identity.is_canonical_standalone_name("ABCDE_proj")

    def test_rejects_illegal_leaf_chars(self) -> None:
        assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_my proj")
        assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_a/b")

    def test_rejects_over_long_leaf(self) -> None:
        leaf = "x" * 33  # > 32-char cap
        assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_{leaf}")
        # Exactly 32 is fine.
        assert box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_" + "x" * 32)

    def test_rejects_missing_leaf(self) -> None:
        assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_")
        assert not box_identity.is_canonical_standalone_name(_VALID_KUID)


# ---------------------------------------------------------------------------
# leaf grammar <-> generator coupling (_LEAF_CAP, _LEAF_CHARS)
# ---------------------------------------------------------------------------

class TestLeafGrammarTracksGenerator:
    """The leaf grammar is DERIVED from the generator's cap and survivor set.

    Changing :data:`box_identity._LEAF_CAP` or the safe character set stays a
    one-line edit.  Both expectations are read off the generator
    (:func:`sanitize_cap` / the module constants) rather than respelled here;
    a hard-coded ``32`` in this class would reintroduce the very drift it guards.
    """

    def test_grammar_admits_exactly_the_generators_cap(self) -> None:
        cap = box_identity._LEAF_CAP
        assert box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_" + "x" * cap)
        assert not box_identity.is_canonical_standalone_name(
            f"{_VALID_KUID}_" + "x" * (cap + 1)
        )
        # ...and the generator never emits past that cap, so the two agree.
        assert len(box_identity.sanitize_cap("x" * 200)) == cap

    def test_grammar_admits_exactly_the_generators_survivor_set(self) -> None:
        survivors = 0
        for cp in range(0x20, 0x7F):  # ASCII printable — the survivor set's range
            ch = chr(cp)
            kept = box_identity.sanitize_cap(ch) == ch
            accepted = box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_{ch}")
            assert accepted == kept, f"{ch!r} (U+{cp:04X}): sanitize kept={kept}"
            survivors += kept
        assert survivors > 0  # the scan really did find the survivor set

    def test_non_ascii_letters_are_replaced_and_then_refused(self) -> None:
        # Same rule as the scan above, outside the range it covers.
        for ch in ("é", "日"):
            assert box_identity.sanitize_cap(ch) == "_"
            assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_{ch}")


# ---------------------------------------------------------------------------
# resolve_standalone_name
# ---------------------------------------------------------------------------

class TestResolveStandaloneName:
    def test_empty_generates_from_root(self) -> None:
        name = box_identity.resolve_standalone_name(Path("/x/myproj"), "", set())
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert kuid.is_valid(prefix)
        assert leaf == "myproj"

    def test_no_match_uses_supplied_as_leaf(self) -> None:
        # A plain (non-canonical) --name becomes the leaf, with a fresh kuid prefix.
        name = box_identity.resolve_standalone_name(
            Path("/x/myproj"), "WeirdName", set()
        )
        prefix, sep, leaf = name.partition("_")
        assert sep == "_"
        assert kuid.is_valid(prefix)
        assert leaf == "WeirdName"  # sanitized, case KEPT (spec §0)

    def test_no_match_sanitizes_supplied(self) -> None:
        name = box_identity.resolve_standalone_name(
            Path("/x/p"), "has spaces!", set()
        )
        assert name.endswith("_has_spaces_")

    def test_over_long_supplied_is_not_canonical_so_used_as_leaf(self) -> None:
        # A valid-kuid prefix shape but an over-long leaf fails the matcher → the
        # WHOLE string is sanitized+capped into a fresh-prefixed name.
        supplied = f"{_VALID_KUID}_" + "x" * 40
        name = box_identity.resolve_standalone_name(Path("/x/p"), supplied, set())
        prefix, sep, leaf = name.partition("_")
        assert kuid.is_valid(prefix)
        # sanitize_cap caps the leaf at 32 chars.
        assert len(leaf) == 32
        # The result itself is a valid canonical name.
        assert box_identity.is_canonical_standalone_name(name)

    def test_match_and_free_returns_verbatim(self) -> None:
        supplied = f"{_VALID_KUID}_proj"
        name = box_identity.resolve_standalone_name(Path("/x/p"), supplied, set())
        assert name == supplied

    def test_match_canonicalizes_the_kuid_and_keeps_the_leaf(self) -> None:
        # The two halves go opposite ways (spec §0): the kuid has one spelling of its
        # own and is canonicalized, the leaf carries the case the user typed. Both
        # halves were folded until the entry fold came out.
        name = box_identity.resolve_standalone_name(
            Path("/x/p"), f"{_VALID_KUID.upper()}_Proj", set()
        )
        assert name == f"{_VALID_KUID}_Proj"

    def test_match_canonicalizes_a_crockford_alias_in_the_kuid(self) -> None:
        # ``i``/``l``/``o`` are INPUT ALIASES, not alphabet members: one id has one
        # stored spelling. A ``.lower()`` on the kuid half passes the case test above
        # and FAILS this one, which is why the two are separate.
        alias, canonical = "abcdi", "abcd1"
        name = box_identity.resolve_standalone_name(
            Path("/x/p"), f"{alias}_Proj", set()
        )
        assert name == f"{canonical}_Proj"

    def test_match_and_taken_raises(self) -> None:
        from kanibako.errors import ProjectError

        supplied = f"{_VALID_KUID}_proj"
        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.resolve_standalone_name(
                Path("/x/p"), supplied, {supplied}
            )


# ---------------------------------------------------------------------------
# validate_standalone_name (BUG-A pre-flight)
# ---------------------------------------------------------------------------

class TestValidateStandaloneName:
    def test_empty_is_noop(self) -> None:
        box_identity.validate_standalone_name("", {f"{_VALID_KUID}_proj"})

    def test_non_canonical_is_noop(self) -> None:
        # A plain string always becomes a fresh-prefixed name → never refusable.
        box_identity.validate_standalone_name("WeirdName", {"weirdname"})

    def test_free_canonical_is_noop(self) -> None:
        box_identity.validate_standalone_name(f"{_VALID_KUID}_proj", {"abcd1_other"})

    def test_taken_canonical_raises(self) -> None:
        from kanibako.errors import ProjectError

        taken = f"{_VALID_KUID}_proj"
        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.validate_standalone_name(taken, {taken})

    def test_taken_canonical_raises_case_insensitive(self) -> None:
        from kanibako.errors import ProjectError

        with pytest.raises(ProjectError, match="already a box with that name"):
            box_identity.validate_standalone_name(
                f"{_VALID_KUID.upper()}_Proj", {f"{_VALID_KUID}_proj"}
            )

    def test_match_and_taken_error_suggests_dropping_prefix(self) -> None:
        from kanibako.errors import ProjectError

        supplied = f"{_VALID_KUID}_proj"
        with pytest.raises(ProjectError, match=rf"'{_VALID_KUID}_' prefix"):
            box_identity.resolve_standalone_name(
                Path("/x/p"), supplied, {supplied}
            )


# ---------------------------------------------------------------------------
# Box-name BLOCKLIST validation (W1 Phase D, §Design 8)
# ---------------------------------------------------------------------------

import string  # noqa: E402

from kanibako.errors import ProjectError  # noqa: E402


class TestValidateBoxName:
    # --- accepted ---------------------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "myapp",
            "my-app",
            "my_app",
            "my.app",          # interior dot allowed
            "app1",
            "a",               # min length 1
            "x" * 64,          # max length 64
            "1.2.3",
            "a.b-c_d",
            "ok_name-1.x",
            "MyApp",
            "abcde_proj",      # a canonical standalone id is a valid name
        ],
    )
    def test_accepts_valid(self, name: str) -> None:
        assert box_identity.is_valid_box_name(name) is True
        assert box_identity.box_name_reason(name) is None
        box_identity.validate_box_name(name)  # does not raise

    # --- non-ASCII: the allowlist is ASCII-only (podman's container-name set) ---

    @pytest.mark.parametrize("name", ["café", "über_box", "日本語", "项目1", "ａbc"])
    def test_rejects_non_ascii(self, name: str) -> None:
        assert box_identity.is_valid_box_name(name) is False
        with pytest.raises(ProjectError, match=f"Invalid box name '{name}'"):
            box_identity.validate_box_name(name)

    def test_non_ascii_reason_names_the_character_and_the_allowed_set(self) -> None:
        assert box_identity.box_name_reason("café") == (
            "box name must not contain 'é': only ASCII letters, digits,"
            " '_', '-', and '.' are allowed")

    def test_unprintable_character_is_named_by_codepoint(self) -> None:
        reason = box_identity.box_name_reason("a\u00a0b")
        assert reason is not None and "U+00A0" in reason

    # --- control chars ----------------------------------------------------

    @pytest.mark.parametrize("cp", [0x00, 0x01, 0x09, 0x0A, 0x0D, 0x1F, 0x7F])
    def test_rejects_control_chars(self, cp: int) -> None:
        name = f"a{chr(cp)}b"
        assert box_identity.is_valid_box_name(name) is False
        with pytest.raises(ProjectError):
            box_identity.validate_box_name(name)

    # --- whitespace -------------------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "a b",             # ASCII space
            "a\tb",            # tab
            "a b",        # NBSP (unicode whitespace, not just ' ')
            "a b",        # em space
        ],
    )
    def test_rejects_whitespace(self, name: str) -> None:
        assert box_identity.is_valid_box_name(name) is False

    # --- ASCII punctuation: every blocked char rejected -------------------

    @pytest.mark.parametrize(
        "ch", sorted(set(string.punctuation) - set("_-."))
    )
    def test_rejects_each_blocked_punct(self, ch: str) -> None:
        name = f"a{ch}b"
        assert box_identity.is_valid_box_name(name) is False, ch

    @pytest.mark.parametrize("ch", ["_", "-", "."])
    def test_allows_surviving_punct_interior(self, ch: str) -> None:
        assert box_identity.is_valid_box_name(f"a{ch}b") is True

    # --- structural rules -------------------------------------------------

    @pytest.mark.parametrize("name", [".", ".."])
    def test_rejects_dot_and_dotdot(self, name: str) -> None:
        assert box_identity.is_valid_box_name(name) is False

    def test_rejects_leading_dash(self) -> None:
        assert box_identity.is_valid_box_name("-x") is False

    def test_rejects_leading_dot(self) -> None:
        assert box_identity.is_valid_box_name(".hidden") is False

    def test_rejects_trailing_dot(self) -> None:
        assert box_identity.is_valid_box_name("x.") is False

    def test_rejects_trailing_whitespace(self) -> None:
        assert box_identity.is_valid_box_name("x ") is False

    def test_rejects_empty(self) -> None:
        assert box_identity.is_valid_box_name("") is False

    def test_rejects_too_long(self) -> None:
        assert box_identity.is_valid_box_name("x" * 65) is False

    # --- uppercase is NOT blocked: a name is validated as the user typed it -----

    def test_uppercase_is_valid(self) -> None:
        # Spec §0: --name is validated AS TYPED, and the blocklist never covered case.
        # This asserted the FOLDED spelling while box create still folded on entry,
        # which left the real question — is `MyApp` a legal box name? — unasked.
        assert box_identity.is_valid_box_name("MyApp") is True

    def test_validate_raises_actionable_message(self) -> None:
        with pytest.raises(ProjectError, match=r"Invalid box name 'a/b'"):
            box_identity.validate_box_name("a/b")


class TestClassifyDesignation:
    """A box designation's kind, by shape (system-design § Detection & import)."""

    @pytest.mark.parametrize("value", [None, ""])
    def test_absent(self, value: str | None) -> None:
        assert box_identity.classify_designation(value) is box_identity.Designation.ABSENT

    @pytest.mark.parametrize("value", ["foo", "my.app", "MyApp", "a-b_c"])
    def test_valid_box_name_is_an_ambiguous_identifier(self, value: str) -> None:
        assert (box_identity.classify_designation(value)
                is box_identity.Designation.IDENTIFIER)

    @pytest.mark.parametrize(
        "value",
        [".hidden", "foo.", "..x", "a b", "café", "ws/proj", "./foo", ".", "..", "/abs/dir",
         "x" * 65],
    )
    def test_non_name_that_can_be_a_path_is_a_path(self, value: str) -> None:
        assert box_identity.classify_designation(value) is box_identity.Designation.PATH

    def test_nul_is_neither_name_nor_path(self) -> None:
        assert (box_identity.classify_designation("foo\0bar")
                is box_identity.Designation.INVALID)
