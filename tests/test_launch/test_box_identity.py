"""Tests for kanibako.launch.box_identity (standalone box name generation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako import kuid
from kanibako.errors import BoxNameError, DerivedBoxNameError
from kanibako.launch import box_identity
from kanibako.launch.box_identity import unrenderable_box_name_refusal

# A fixed VALID kuid prefix (odd parity) and a fixed INVALID one (even parity,
# in-alphabet, non-sentinel) used across the canonical/verbatim tests. See
# tests/test_kuid.py for the codec's parity contract.
_VALID_KUID = "abcde"  # kuid.is_valid("abcde") is True
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
  @pytest.mark.parametrize(
    ("leaf", "expected"),
    [
      # Spec §0: a derived name writes '_' for each '-' and '.'.
      ("my-app_1.0", "my_app_1_0"),
      ("my-app", "my_app"),
      ("a.b", "a_b"),
      ("kanibako-cli", "kanibako_cli"),
      ("my__app", "my__app"),  # the user's own underscores are not separators
      # Spec §0, ⚑ NAMING RULES: the leaf carries the SOURCE DIRECTORY's case.
      ("MyProj", "MyProj"),
      ("ALL-CAPS_1.0", "ALL_CAPS_1_0"),
      # Latin letters drop their diacritics.
      ("café", "cafe"),
      ("Zürich Notes", "Zurich_Notes"),
      # ...decomposed too: a combining mark after a spelled letter is dropped.
      ("cafe\u0301", "cafe"),
      ("Zu\u0308rich Notes", "Zurich_Notes"),
      ("cafe\u0301nfd", "cafenfd"),
      # ...and so do the table letters and kana (romanize.to_ascii, the one spelling).
      ("Straße", "Strasse"),
      ("ø", "o"),
      ("æ", "ae"),
      ("ı", "i"),
      ("かにばこ", "kanibako"),
      ("カニ Notes", "kani_Notes"),
      ("が", "ga"),
      ("か\u3099", "ga"),
      ("ジョン・スミス", "jon_sumisu"),
      ("きんや", "kin_ya"),
      ("ほんや ノート", "hon_ya_nooto"),
      # Unicode punctuation and separators are separators.
      ("Bob\u2019s Notes", "Bob_s_Notes"),
      ("a\u2014b\u2026c\uff01d", "a_b_c_d"),
      ("a\u3000b", "a_b"),
      # A run of separators is one '_'; the ends lose '_'.
      ("a  b!!c", "a_b_c"),
      ("my app!@#", "my_app"),
      ("a/b c", "a_b_c"),
      ("a _b", "a_b"),
      ("..-foo-.", "foo"),
      # Empty after trimming falls back to "box".
      ("", "box"),
      ("!!!", "box"),
      ("///", "box"),
    ],
  )
  def test_leaf(self, leaf: str, expected: str) -> None:
    assert box_identity.sanitize_cap(leaf) == expected

  def test_caps_at_32_chars(self) -> None:
    assert box_identity.sanitize_cap("x" * 100) == "x" * 32

  def test_ends_are_trimmed_after_the_cap(self) -> None:
    assert box_identity.sanitize_cap("x" * 31 + " y") == "x" * 31

  @pytest.mark.parametrize(
    "leaf",
    [
      "日本語プロジェクト",
      "my 日本語 app",
      "東京",
      "かに東",
      # Symbols (S*) and fractions have no spelling.
      "\u00bd",
      "\U0001f600",
      # A combining mark with no spelled letter before it.
      "日\u0301",
      "\u0301x",
      "_\u0301x",
    ],
  )
  def test_no_ascii_spelling_refuses(self, leaf: str) -> None:
    with pytest.raises(DerivedBoxNameError, match="cannot spell in ASCII"):
      box_identity.sanitize_cap(leaf)


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
    name = box_identity.make_standalone_box_name(Path("/home/user/cool project"), set())
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
    assert box_identity.is_canonical_standalone_name("abcd1_my_app_1_0")
    # A leaf the name rule no longer allows is not the generator's shape.
    assert not box_identity.is_canonical_standalone_name("abcd1_my-app_1.0")
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
    assert not box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_" + "x" * (cap + 1))
    # ...and the generator never emits past that cap, so the two agree.
    assert len(box_identity.sanitize_cap("x" * 200)) == cap

  def test_grammar_admits_exactly_the_generators_survivor_set(self) -> None:
    survivors = 0
    for cp in range(0x20, 0x7F):  # ASCII printable — the survivor set's range
      ch = chr(cp)
      # Inside a word, so the end trim does not hide a kept character.
      kept = box_identity.sanitize_cap(f"a{ch}a") == f"a{ch}a"
      accepted = box_identity.is_canonical_standalone_name(f"{_VALID_KUID}_{ch}")
      assert accepted == kept, f"{ch!r} (U+{cp:04X}): sanitize kept={kept}"
      survivors += kept
    assert survivors > 0  # the scan really did find the survivor set

  def test_non_ascii_letters_are_spelled_or_refused(self) -> None:
    # Outside the range the scan above covers: the generator never emits them.
    assert box_identity.sanitize_cap("é") == "e"
    with pytest.raises(DerivedBoxNameError):
      box_identity.sanitize_cap("日")
    for ch in ("é", "日"):
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
    name = box_identity.resolve_standalone_name(Path("/x/myproj"), "WeirdName", set())
    prefix, sep, leaf = name.partition("_")
    assert sep == "_"
    assert kuid.is_valid(prefix)
    assert leaf == "WeirdName"  # sanitized, case KEPT (spec §0)

  def test_no_match_sanitizes_supplied(self) -> None:
    name = box_identity.resolve_standalone_name(Path("/x/p"), "has spaces!", set())
    assert name.endswith("_has_spaces")

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
    name = box_identity.resolve_standalone_name(Path("/x/p"), f"{_VALID_KUID.upper()}_Proj", set())
    assert name == f"{_VALID_KUID}_Proj"

  def test_match_canonicalizes_a_crockford_alias_in_the_kuid(self) -> None:
    # ``i``/``l``/``o`` are INPUT ALIASES, not alphabet members: one id has one
    # stored spelling. A ``.lower()`` on the kuid half passes the case test above
    # and FAILS this one, which is why the two are separate.
    alias, canonical = "abcdi", "abcd1"
    name = box_identity.resolve_standalone_name(Path("/x/p"), f"{alias}_Proj", set())
    assert name == f"{canonical}_Proj"

  def test_match_and_taken_raises(self) -> None:
    from kanibako.errors import ProjectError

    supplied = f"{_VALID_KUID}_proj"
    with pytest.raises(ProjectError, match="already a box with that name"):
      box_identity.resolve_standalone_name(Path("/x/p"), supplied, {supplied})


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
      box_identity.validate_standalone_name(f"{_VALID_KUID.upper()}_Proj", {f"{_VALID_KUID}_proj"})

  def test_match_and_taken_error_suggests_dropping_prefix(self) -> None:
    from kanibako.errors import ProjectError

    supplied = f"{_VALID_KUID}_proj"
    with pytest.raises(ProjectError, match=rf"'{_VALID_KUID}_' prefix"):
      box_identity.resolve_standalone_name(Path("/x/p"), supplied, {supplied})


# ---------------------------------------------------------------------------
# refuse_nonleaf_standalone_name / refuse_standalone_rename
# The door rule ([Jei:2026-10-08]: Reading A, and a typed flag is never
# silently ignored).  RED ON BASE: neither helper exists there.
# ---------------------------------------------------------------------------


class TestRefuseNonleafStandaloneName:
  """A standalone ``--name`` is refused unless it IS the name the box gets."""

  _MSG = r"after its directory; rename the directory to rename the box"

  def test_empty_is_noop(self) -> None:
    box_identity.refuse_nonleaf_standalone_name("", Path("/x/proj"))

  def test_bare_leaf_is_a_noop(self) -> None:
    box_identity.refuse_nonleaf_standalone_name("proj", Path("/x/proj"))

  def test_composed_name_with_kuid_is_a_noop(self) -> None:
    box_identity.refuse_nonleaf_standalone_name(
      f"{_VALID_KUID}_proj", Path("/x/proj"), box_kuid=_VALID_KUID
    )

  def test_divergent_name_is_refused(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=self._MSG):
      box_identity.refuse_nonleaf_standalone_name("sabox", Path("/x/proj"))

  def test_uppercase_leaf_is_refused_equality_is_exact(self) -> None:
    from kanibako.errors import ProjectError

    # PROJ is not what directory ``proj`` composes, so it is not a no-op.
    with pytest.raises(ProjectError, match=self._MSG):
      box_identity.refuse_nonleaf_standalone_name("PROJ", Path("/x/proj"))

  def test_composed_shape_without_a_kuid_is_refused(self) -> None:
    from kanibako.errors import ProjectError

    # With no stored kuid there is nothing to check the prefix against.
    with pytest.raises(ProjectError, match=self._MSG):
      box_identity.refuse_nonleaf_standalone_name(f"{_VALID_KUID}_proj", Path("/x/proj"))

  def test_refusal_names_the_box_when_kuid_known(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=f"'{_VALID_KUID}_proj'"):
      box_identity.refuse_nonleaf_standalone_name("sabox", Path("/x/proj"), box_kuid=_VALID_KUID)

  def test_refusal_shows_the_placeholder_when_kuid_unknown(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=r"'<kuid>_proj'"):
      box_identity.refuse_nonleaf_standalone_name("sabox", Path("/x/proj"))


class TestRefuseStandaloneRename:
  """The same rule for a door holding the composed NAME instead of a root."""

  _MSG = r"after its directory; rename the directory to rename the box"

  def test_empty_is_noop(self) -> None:
    box_identity.refuse_standalone_rename("", f"{_VALID_KUID}_proj")

  def test_current_name_is_a_noop(self) -> None:
    box_identity.refuse_standalone_rename(f"{_VALID_KUID}_proj", f"{_VALID_KUID}_proj")

  def test_leaf_half_is_a_noop(self) -> None:
    box_identity.refuse_standalone_rename("proj", f"{_VALID_KUID}_proj")

  def test_any_other_name_is_refused(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=self._MSG):
      box_identity.refuse_standalone_rename("sabox", f"{_VALID_KUID}_proj")

  def test_case_difference_is_refused(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=self._MSG):
      box_identity.refuse_standalone_rename(f"{_VALID_KUID.upper()}_PROJ", f"{_VALID_KUID}_proj")

  def test_refusal_names_the_current_box(self) -> None:
    from kanibako.errors import ProjectError

    with pytest.raises(ProjectError, match=f"'{_VALID_KUID}_proj'"):
      box_identity.refuse_standalone_rename("sabox", f"{_VALID_KUID}_proj")


# ---------------------------------------------------------------------------
# Box-name BLOCKLIST validation (W1 Phase D; ``box_identity.box_name_reason``)
# ---------------------------------------------------------------------------

import string  # noqa: E402

from kanibako.errors import ProjectError  # noqa: E402


class TestValidateBoxName:
  # --- accepted ---------------------------------------------------------

  @pytest.mark.parametrize(
    "name",
    [
      "myapp",
      "my_app",
      "_my_app",  # a leading '_' is a letter of the rule
      "app1",
      "a",  # min length 1
      "x" * 64,  # max length 64
      "a_b_c_d",
      "MyApp",
      "abcde_proj",  # a canonical standalone id is a valid name
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
      "box name must not contain U+00E9; names are 1 to 64 ASCII letters, digits,"
      " or '_', and do not start with a digit"
    )

  # --- the name rule (spec §0): no '-', no '.', no leading digit ---------

  @pytest.mark.parametrize(
    "name, shown",
    [
      ("my-app", "'-'"),
      ("my.app", "'.'"),
      ("1.2.3", "'.'"),
      ("a.b-c_d", "'.'"),
    ],
  )
  def test_rejects_dash_and_dot(self, name: str, shown: str) -> None:
    assert box_identity.box_name_reason(name).startswith(f"box name must not contain {shown};")

  @pytest.mark.parametrize("name", ["1app", "2fa", "0"])
  def test_rejects_a_leading_digit(self, name: str) -> None:
    assert box_identity.box_name_reason(name).startswith("box name must not start with a digit;")

  def test_a_generated_standalone_name_may_start_with_a_digit(self) -> None:
    """The kuid prefix is kanibako's choice (keyspec §2c), and a kuid may start with one."""
    prefix = next(
      k
      for k in (kuid.encode(v) for v in range(1, 1 << 25, 4099))
      if k[0].isdigit() and kuid.is_valid(k)
    )
    assert box_identity.is_valid_box_name(f"{prefix}_proj") is True
    assert box_identity.is_valid_box_name(f"{prefix}_my-proj") is False

  def test_unprintable_character_is_named_by_codepoint(self) -> None:
    reason = box_identity.box_name_reason("a\u00a0b")
    assert reason is not None and "U+00A0" in reason

  @pytest.mark.parametrize(
    ("name", "shown"),
    [
      ("\u212aelvin", "U+212A"),  # Kelvin sign, prints as 'K'
      ("a\u0301b", "U+0301"),  # lone combining acute accent
    ],
  )
  def test_non_ascii_is_shown_by_codepoint_never_quoted(self, name: str, shown: str) -> None:
    reason = box_identity.box_name_reason(name)
    assert reason is not None
    assert reason.startswith(f"box name must not contain {shown};")

  def test_printable_ascii_is_quoted(self) -> None:
    assert box_identity.box_name_reason("a/b").startswith("box name must not contain '/';")

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
      "a b",  # ASCII space
      "a\tb",  # tab
      "a b",  # NBSP (unicode whitespace, not just ' ')
      "a b",  # em space
    ],
  )
  def test_rejects_whitespace(self, name: str) -> None:
    assert box_identity.is_valid_box_name(name) is False

  # --- ASCII punctuation: every blocked char rejected -------------------

  @pytest.mark.parametrize("ch", sorted(set(string.punctuation) - set("_")))
  def test_rejects_each_blocked_punct(self, ch: str) -> None:
    name = f"a{ch}b"
    assert box_identity.is_valid_box_name(name) is False, ch

  def test_allows_underscore_interior(self) -> None:
    assert box_identity.is_valid_box_name("a_b") is True

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
    # Spec §0: --name is validated AS TYPED, and the allowlist admits either case.
    # This asserted the FOLDED spelling while box create still folded on entry,
    # which left the real question — is `MyApp` a legal box name? — unasked.
    assert box_identity.is_valid_box_name("MyApp") is True

  def test_validate_raises_actionable_message(self) -> None:
    with pytest.raises(ProjectError, match=r"Invalid box name 'a/b'"):
      box_identity.validate_box_name("a/b")


class TestBoxNameCure:
  """The cure offers the ASCII spelling (spec §0, ⚑ NAMING RULES), never applies it."""

  @pytest.mark.parametrize(
    "name, cure",
    [
      ("café", "Its ASCII spelling works:\n  kanibako create /h/p --name cafe"),
      ("かに", "Its ASCII spelling works:\n  kanibako create /h/p --name kani"),
      ("東京", "Give the box a valid name:\n  kanibako create /h/p --name <new-name>"),
      # The ASCII spelling is written in '_' too (spec §0).
      ("my café", "Its ASCII spelling works:\n  kanibako create /h/p --name my_cafe"),
      # An ASCII name is offered its '_' spelling.
      ("-x", "Its '_' spelling works:\n  kanibako create /h/p --name _x"),
      ("my-box", "Its '_' spelling works:\n  kanibako create /h/p --name my_box"),
      ("my.box", "Its '_' spelling works:\n  kanibako create /h/p --name my_box"),
      ("2fa", "Its '_' spelling works:\n  kanibako create /h/p --name _2fa"),
      ("x" * 65, "Give the box a valid name:\n  kanibako create /h/p --name <new-name>"),
    ],
  )
  def test_cure(self, name: str, cure: str) -> None:
    assert box_identity.box_name_cure("kanibako create /h/p", name) == cure

  def test_validate_carries_the_name(self) -> None:
    with pytest.raises(BoxNameError) as exc:
      box_identity.validate_box_name("かに")
    assert exc.value.name == "かに"


class TestClassifyDesignation:
  """A box designation's kind, by shape (system-design § Detection & import)."""

  @pytest.mark.parametrize("value", [None, ""])
  def test_absent(self, value: str | None) -> None:
    assert box_identity.classify_designation(value) is box_identity.Designation.ABSENT

  @pytest.mark.parametrize("value", ["foo", "my_app", "MyApp", "a_b_c"])
  def test_valid_box_name_is_an_ambiguous_identifier(self, value: str) -> None:
    assert box_identity.classify_designation(value) is box_identity.Designation.IDENTIFIER

  @pytest.mark.parametrize(
    "value",
    [
      ".hidden",
      "foo.",
      "..x",
      "a b",
      "café",
      "ws/proj",
      "./foo",
      ".",
      "..",
      "/abs/dir",
      "x" * 65,
      "my.app",
      "a-b_c",
    ],
  )
  def test_non_name_that_can_be_a_path_is_a_path(self, value: str) -> None:
    assert box_identity.classify_designation(value) is box_identity.Designation.PATH

  def test_nul_is_neither_name_nor_path(self) -> None:
    assert box_identity.classify_designation("foo\0bar") is box_identity.Designation.INVALID


class TestCarryStandaloneName:
  """``:811`` — the kuid is the stable prefix; re-mint only on a whole-name collision."""

  def test_the_carried_kuid_survives_a_new_leaf(self, tmp_path):
    root = tmp_path / "elsewhere"
    name = box_identity.carry_standalone_name("abcde", root, set())
    assert name == "abcde_elsewhere"

  def test_a_collision_with_ANOTHER_box_re_mints_the_kuid(self, tmp_path):
    root = tmp_path / "proj"
    taken = {"zzzzz_proj"}
    # ⚑ The collision is on the WHOLE composed name.  Another box holding
    # ``zzzzz_proj`` does NOT block ``abcde_proj`` — only the same name does.
    assert box_identity.carry_standalone_name("abcde", root, taken) == "abcde_proj"
    name = box_identity.carry_standalone_name("abcde", root, {"abcde_proj"})
    assert name != "abcde_proj"
    assert name.endswith("_proj")
    assert len(name.partition("_")[0]) == 5

  def test_the_movers_OWN_name_is_not_a_collision(self, tmp_path):
    """⚑ The no-op move.  ``/a/proj`` → ``/b/proj`` composes the box's OWN
    registered name; treating that as a collision would throw away a good kuid
    for a conflict that does not exist."""
    root = tmp_path / "b" / "proj"
    own = "abcde_proj"
    name = box_identity.carry_standalone_name("abcde", root, {own}, own_name=own)
    assert name == own

  def test_the_collision_test_is_case_blind(self, tmp_path):
    """``abcde_Proj`` and ``ABCDE_proj`` are one name (§0), so the second is a
    collision and must re-mint rather than land a case-variant twin."""
    root = tmp_path / "Proj"
    name = box_identity.carry_standalone_name("abcde", root, {"ABCDE_proj"})
    assert name != "abcde_Proj"
    assert name.endswith("_Proj")


# ---------------------------------------------------------------------------
# unrenderable_box_name_refusal (start/stop)
# ---------------------------------------------------------------------------


class TestStartUnrenderableBoxNameRefusal:
  """The shared text names the rule and the command that gives a valid name.

  ⚑ ONE carrier in ``box_identity`` for every door that reports it — ``start`` and ``stop``.
  """

  RULE = "box name must not contain '-'; names are 1 to 64 ASCII letters"
  PATH = "/home/user/myproj"

  @staticmethod
  def _text(mode: str = "primary") -> str:
    return unrenderable_box_name_refusal(
      "-droste",
      mode,
      TestStartUnrenderableBoxNameRefusal.PATH,
      None,
    )

  def test_it_names_the_box_name_rule(self):
    assert self.RULE in self._text()

  def test_a_primary_box_is_cured_by_moving_it_to_its_spelling(self):
    assert f"kanibako box move {self.PATH} <new-path> --name _droste" in (self._text("primary"))

  def test_a_named_box_is_cured_by_moving_it_to_its_spelling(self):
    assert f"kanibako box move {self.PATH} <new-path> --name _droste" in (self._text("named"))

  def test_a_connected_named_box_is_renamed_in_place_by_convert(self):
    """``box move`` refuses to relocate a member connected from outside its workset."""
    text = unrenderable_box_name_refusal("my-box", "named", self.PATH, None, connected="ws.1")
    assert text.endswith(f"kanibako box convert {self.PATH} --workset ws.1 --name my_box")

  def test_a_legacy_my_box_is_offered_my_underscore_box(self):
    text = unrenderable_box_name_refusal("my-box", "primary", self.PATH, None)
    assert "box name must not contain '-'" in text
    assert f"kanibako box move {self.PATH} <new-path> --name my_box" in text

  def test_a_taken_spelling_is_suffixed_as_a_derived_name_is(self):
    """``a_b`` and ``a_b2`` are held in the box's workset, so the cure offers ``a_b3``."""
    text = unrenderable_box_name_refusal(
      "a-b", "primary", self.PATH, None, taken=lambda name: name in {"a_b", "a_b2"}
    )
    assert text.endswith(f"kanibako box move {self.PATH} <new-path> --name a_b3")

  def test_a_box_with_no_spelling_keeps_the_placeholder(self):
    text = unrenderable_box_name_refusal("\u6f22\u5b57", "primary", self.PATH, None)
    assert "--name <new-name>" in text

  def test_a_legacy_workset_is_refused_for_the_workset(self):
    text = unrenderable_box_name_refusal("droste", "named", self.PATH, None, workset="my-set")
    assert "its workset name 'my-set' must not contain '-'" in text
    assert "kanibako workset create <new-root> --name my_set" in text
    assert f"kanibako box convert {self.PATH} --workset my_set" in text

  def test_a_standalone_box_is_cured_by_moving_its_directory(self):
    text = self._text("standalone")
    assert text.endswith(f"\n  kanibako box move {self.PATH} <new-path>")
    assert "--name" not in text

  def test_the_cure_never_addresses_the_box_by_the_refused_name(self):
    """A leading ``-`` is read as a flag, so naming the box would not reach it."""
    for mode in ("primary", "named", "standalone"):
      text = self._text(mode)
      assert "kanibako box move -droste" not in text
      assert "kanibako box convert -droste" not in text

  @pytest.mark.parametrize("mode", ["named", "standalone"])
  def test_a_box_with_no_project_path_is_named_by_the_path_that_reached_it(
    self,
    mode,
    tmp_path,
    monkeypatch,
  ):
    """``box move`` needs both <old> and <new>, so the cure always names an <old>."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PWD", raising=False)
    for designation, old in (
      (str(tmp_path / "ws"), tmp_path / "ws"),
      ("ws", tmp_path / "ws"),
      (None, tmp_path),
    ):
      text = unrenderable_box_name_refusal("-droste", mode, None, designation)
      assert f"kanibako box move {old} <new-path>" in text
      assert "None" not in text

  def test_it_never_names_a_rename_command(self):
    assert "rename" not in self._text("primary")
    assert "rename" not in self._text("standalone")
