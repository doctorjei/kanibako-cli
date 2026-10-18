"""SPEC ↔ REGISTRY parity at §2d's DEFAULT TIER — the third arrow, and the missing one.

``keyspace-manifest.yaml`` is DERIVED from ``specs/settings-keyspace-1.8.0.md`` and must
reflect it precisely, but the derivation is performed by hand and nothing detected
divergence.  ``agent.default.template`` is what that costs: a legal key, carried by the
code, with no manifest row and no spec row for EIGHT DAYS, tracked only as prose in three
plan files.  Prose does not fail a gate.  This file is the mechanical form of the rule
(P15) — the derivation now reds when it stops holding.

⚑ THE ARROW IS SPEC ⇄ REGISTRY, AND IT IS NEITHER OF THIS DIRECTORY'S OTHER TWO.
``test_manifest_conformance.py`` is CODE ← REGISTRY ("does the registry still describe
the code?"); ``test_manifest_enforces.py`` is CODE ← MANIFEST ("does the code still obey
the registry?").  Both compare the manifest to ``src/``.  Neither has ever read the
spec — which is the document both of them are downstream of.  🛑 DO NOT MERGE this into
either: a finding here means the registry and its OWN SOURCE disagree, and the fix is
always to move the registry, never the spec (the spec is HIGH CANON).

⚑ SKIPS OFF-BOX, AND THAT MATTERS FOR HOW A CI GREEN IS READ.  The spec lives in the
canon (``~/canon/workbook/specs/``), outside this repo and absent from CI, so a green run
in CI is NOT evidence that this parity holds — it is evidence that the file did not run.
The pin holds only where the canon is mounted.  Precedent and the same disclosure:
``tests/test_keyspec_extract.py::test_the_default_request_covers_the_real_spec``.

⚑ SCOPE: KEY ROWS AND DEFAULT VALUES.  Nothing here asserts anything about prose,
commentary or provenance on either side — the description column of a spec row and every
non-``default`` field of a manifest row are read past, deliberately.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/``.
"""

from __future__ import annotations

import re

import pytest

from kanibako.settings.core_defaults import CANON_ACTIVE_AGENT_TOKEN
from kanibako.settings.keyspace_manifest import manifest_doc

# The spec locator and the heading parser both come from ``scripts/keyspec-extract.py``,
# reached through the sibling test that already loads that hyphenated script by path.
# Re-deriving either here would be a second copy of the spec's location (P10).
from tests.test_keyspec_extract import keyspec

# ⚑ THE FENCE READER IS `kinemata_views`'s, NOT THIS FILE'S.  The row notation, the
#: brace expansion and the value notation moved to that module when the §2d per-node
#: descriptor views (`test_agent_descriptor_spec_parity.py`) needed the same three rules,
#: so this fence and those views cannot drift apart.  The names below are the ones this
#: file already used; its SCOPE is unchanged -- §2d's DEFAULT tier against the
#: manifest, and the per-node arm is still somebody else's question.
from kinemata_views import (  # noqa: F401  (this file's own readers use these names)
  SPEC_EMPTY as _EMPTY,
  SPEC_NULL as _NULL,
  SPEC_SENTINELS as _SENTINELS,
  expand_braces as _expand_braces,
  spec_cell_value as _spec_cell_value,
  spec_fence as _spec_fence,
  spec_fence_rows as _spec_fence_rows,
  spec_next_version_rows as _spec_next_version_rows,
  spec_notation as _canonical,
  spec_self_notation as _spelled_out,
)

#: The tier this file pins.  Every row on both sides is one of these; the per-node arm
#: (``agent.<agent>.*``) is a different subsection and a different question.
TIER = "agent.default."

#: The §2d subsection whose fenced block IS the Default tier, and the marker that opens
#: it.  Both are read as HEADINGS/markers, never as line numbers: the spec is edited
#: constantly and any stored offset would be wrong within the week.
#: ⚑ THE MARKER IS §2d's ``#### Default settings`` SUBHEADING, matched as the START of
#: the line.  A heading owns its subsection, so the prose between it and the fence is
#: read past: the fence is the first to open before the next heading, and a heading
#: reached first fails closed (``kinemata_views.spec_fence``).
#: ⚑ A RETITLE STILL REDS.  The walk then locates 0 markers and every case ERRORS — the
#: pin working, loudly.
SECTION = "2d"
MARKER = "#### Default settings"

#: The registry's spelling of the active node.  At the Default tier the value applies
#: to the ACTIVE agent, so ``meta.agent.<active>.`` (core_defaults.CANON_ACTIVE_AGENT_TOKEN,
#: the runtime token) is the spec's ``meta.agent.<agent>.``.
_ACTIVE_NODE = f"meta.agent.{CANON_ACTIVE_AGENT_TOKEN}."
_SPEC_NODE = "meta.agent.<agent>."


# --------------------------------------------------------------------------- #
# The spec side
# --------------------------------------------------------------------------- #


def _spec_lines() -> list[str]:
  """The spec as ``readlines()`` hands it over, or a skip saying where it is not."""
  path = keyspec._DEFAULT_SPEC
  if not path.is_file():
    pytest.skip(f"the keyspace spec is not mounted at {path}")
  return path.open(encoding="utf-8").readlines()


def _default_tier_fence(lines: list[str]) -> list[str]:
  """The lines INSIDE §2d's Default-tier fenced block.

  ⛑ THE WALK IS `kinemata_views.spec_fence`'s, moved there from this file when the
  per-node views needed the same three steps: §2d's span from the extraction script's
  own heading parser, the marker located within that span, and the fence that opens on
  the next non-blank line.  It FAILS CLOSED on each -- a missing section, a marker that moved
  or was renamed, a fence that never opens or closes -- so the reformat that used to
  red HERE still reds, from the one implementation.
  """
  return _spec_fence(SECTION, MARKER)


def _spec_rows(lines: list[str]) -> dict[str, str]:
  """``{key: value-as-written}`` for every ``agent.default.*`` row in the fence.

  ⛑ THE ROW NOTATION IS `kinemata_views`'s TOO (``spec_fence_rows`` /
  ``spec_cell_value``).  A row is ``<key> | <value>[  <description>]``; continuation
  and comment lines start with ``#`` and are not rows; and §2d's fence also states
  two ``meta.agent.*`` rows that are not this file's corpus.

  ⛑ DOUBLE QUOTES ARE THE FENCE'S STRING DELIMITER, not part of the value — the fourth
  notation this file translates, beside ``<None>`` / ``true`` / ``{}``.  ``label`` is
  the first Default-tier value to use it (its value has spaces, so the spec quotes it
  where ``bootstrap | tmux`` needs no quoting), and YAML cannot spell a string with its
  delimiters retained.  No declared value in this fence contains a quote character, so
  a stripped pair is the same string.

  ⛑ THE STRIP IS CONDITIONAL, and the condition is the whole point.  ``"<None>"`` is
  the literal six-character STRING; ``<None>`` is the fence's spelling for null.  They
  are different declarations, so a quoted sentinel keeps its quotes and compares
  unequal to a native one rather than silently passing as it.

  ⛑ ONE-SIDED, DELIBERATELY.  ``_canonical`` renders the literal string ``<None>`` and
  null IDENTICALLY, so the registry cannot state the distinction this side now draws:
  a quoted sentinel reds against EITHER manifest spelling.  No §2d row states one; if
  one ever does, the fix is on the manifest side.

  ⛑ ``self.`` / ``meta.self.`` ARE THE SPEC'S NOTATION for ``agent.<agent>.`` /
  ``meta.agent.<agent>.`` (its header says so), and the registry spells them out;
  ``_spelled_out`` translates both key and value before any comparison.
  """
  return {
    _spelled_out(key): _spelled_out(_spec_cell_value(cell))
    for key, cell in _spec_fence_rows(SECTION, MARKER)
    if key.startswith(TIER)
  }


#: A §2d row naming one ENTRY of a dest-keyed category: ``<tier><category>[<dest>]``.
_ENTRY_ROW = re.compile(r"^(?P<family>[^\[]+)\[(?P<dest>.+)\]$")

#: A §2d row naming the parametric member of a category: ``<tier><category>.<VAR>``.
_PARAMETRIC_ROW = re.compile(r"^(?P<family>.+)\.<[^<>]+>$")


def _placeholder(text: str) -> bool:
  """True where *text* carries a ``<...>`` placeholder: a PATTERN, not a member."""
  return "<" in text and ">" in text


def _family_of(key: str) -> str | None:
  """The category family a PATTERN row belongs to, or ``None`` for any other row.

  ⚑ A PATTERN ROW IS NOT A DEFAULT ENTRY.  ``caches[~/.kanibako/cache/<dir>]`` and
  ``secret_path.<VAR>`` say what shape a plugin's or user's entry takes; no entry
  ships at that dest, so the registry's carrier is the category family itself.  A
  row naming a CONCRETE dest (``seeded[~/]``) is a real entry and is NOT mapped
  here: it must be a ``category_default_entries`` row, value and all.
  """
  entry = _ENTRY_ROW.match(key)
  if entry is not None:
    return entry["family"] if _placeholder(entry["dest"]) else None
  parametric = _PARAMETRIC_ROW.match(key)
  return parametric["family"] if parametric is not None else None


# --------------------------------------------------------------------------- #
# The registry side
# --------------------------------------------------------------------------- #


def _manifest_rows() -> dict[str, str]:
  """``{key: value-in-spec-notation}`` for every ``agent.default.*`` ``keys:`` row."""
  return {
    str(key): _canonical(row["default"])
    for key, row in manifest_doc()["keys"].items()
    if str(key).startswith(TIER) and isinstance(row, dict) and "default" in row
  }


def _manifest_tier_categories() -> dict[str, str | None]:
  """The category families the manifest declares AT the ``agent.default`` scope.

  ⚑ NOT AN ALLOWLIST — read out of the manifest's own ``categories:`` table and the
  ``category_scopes:`` tokens they exist at.  A category is how the
  registry declares ``agent.default.bindings.ro`` and its siblings; they are real rows
  of the Default tier and simply live in a different table from the scalars, so a
  spec-side lookup that consulted only ``keys:`` would report them missing.  The value
  is the family's ``default:`` where it states one, and ``None`` where it does not.
  """
  table = manifest_doc()["categories"]
  scope = TIER.rstrip(".")
  if scope not in manifest_doc()["category_scopes"]["tokens"]:
    return {}
  return {
    f"{TIER}{name}": (_canonical(row["default"]) if "default" in row else None)
    for name, row in table.items()
    if "value" in row
  }


def _manifest_tier_entries() -> dict[str, str | None]:
  """``{<tier><category>[<dest>]: default}`` for the Default tier's declared entries.

  Read out of the manifest's ``category_default_entries:`` table, where a CONCRETE
  entry of a dest-keyed family lives (``agent.default.seeded`` at ``~/``).
  """
  scope = TIER.rstrip(".")
  return {
    f"{arm}[{dest}]": (_canonical(row["default"]) if "default" in row else None)
    for arm, entries in manifest_doc()["category_default_entries"].items()
    if str(arm).startswith(f"{scope}.") and isinstance(entries, dict)
    for dest, row in entries.items()
  }


def _declared() -> set[str]:
  return set(_manifest_rows()) | set(_manifest_tier_categories()) | set(_manifest_tier_entries())


# --------------------------------------------------------------------------- #
# The pins
# --------------------------------------------------------------------------- #


@pytest.fixture
def spec_rows() -> dict[str, str]:
  return _spec_rows(_default_tier_fence(_spec_lines()))


@pytest.fixture
def next_version_keys(spec_rows) -> set[str]:
  """Default-tier keys tagged ``⏭ <version>``: a later release's, never a gap."""
  return {_spelled_out(key) for key in _spec_next_version_rows(SECTION, MARKER)} & set(spec_rows)


class TestTheCorporaAreNotEmpty:
  """⚑⚑ THE ANTI-VACUITY CASE, and the reason the rest of the file can be believed.

  Every assertion below this class is a set difference, and a set difference between
  two empty sets is green.  A spec reformat that stopped the row regex matching, or a
  manifest section rename, would therefore leave a PERMANENTLY GREEN NO-OP wearing the
  appearance of coverage — the exact failure this file exists to prevent.  So the
  counts are asserted first, and every message carries the count it measured.
  """

  def test_the_spec_fence_yields_rows(self, spec_rows):
    assert len(spec_rows) > 0, (
      f"parsed §{SECTION}'s Default-tier fence and found {len(spec_rows)} "
      f"{TIER}* rows — the fence's row notation changed and this file is now "
      f"checking nothing"
    )

  def test_the_manifest_yields_rows(self):
    rows = _manifest_rows()
    assert len(rows) > 0, (
      f"the manifest declares {len(rows)} {TIER}* rows with a default — the "
      f"registry's shape changed and this file is now checking nothing"
    )

  def test_the_category_families_resolve(self):
    """The second registry table, asserted non-empty for the same reason."""
    families = _manifest_tier_categories()
    assert len(families) > 0, (
      f"the manifest's categories: table declares {len(families)} families at "
      f"the {TIER.rstrip('.')} scope — a renamed table or scope would silently "
      f"turn every category row into a spec-side false positive"
    )

  def test_the_category_entries_resolve(self):
    """The third registry table, asserted non-empty for the same reason."""
    entries = _manifest_tier_entries()
    assert len(entries) > 0, (
      f"the manifest's category_default_entries: table declares {len(entries)} "
      f"entries at the {TIER.rstrip('.')} scope — a renamed table would turn "
      f"every concrete-dest spec row into a false positive"
    )

  def test_self_notation_is_spelled_out(self):
    """The spec header's notation, exercised on synthetic text."""
    assert _spelled_out("({self.template}/box/home)") == ("({agent.<agent>.template}/box/home)")
    assert _spelled_out("{meta.self.path}/auth") == "{meta.agent.<agent>.path}/auth"
    assert _spelled_out("{x.self.y}") == "{x.self.y}"

  def test_brace_notation_is_expanded(self):
    """The one notation rule, exercised on a synthetic row rather than a real one."""
    assert _expand_braces("a.{x,y}.z") == ["a.x.z", "a.y.z"]
    assert _expand_braces("a.plain") == ["a.plain"]


class TestTheTiersAreOneSet:
  """Both directions, each naming the rows it found on the other side."""

  def test_every_manifest_row_is_a_spec_row(self, spec_rows):
    missing = sorted(set(_manifest_rows()) - set(spec_rows))
    assert not missing, (
      f"the registry declares Default-tier keys §{SECTION} does not: {missing} — "
      f"the manifest is DERIVED from the spec, so either the spec row was dropped "
      f"or the manifest invented a key (closed keyspace, §0)"
    )

  def test_every_spec_row_is_a_manifest_row(self, spec_rows, next_version_keys):
    declared = _declared()
    missing = sorted(
      key
      for key in spec_rows
      if key not in declared and _family_of(key) not in declared and key not in next_version_keys
    )
    assert not missing, (
      f"§{SECTION}'s Default tier declares keys the registry has no row for: "
      f"{missing} — this is the agent.default.template failure verbatim; add the "
      f"row to keyspace-manifest.yaml"
    )

  def test_no_next_version_row_is_declared(self, next_version_keys):
    landed = sorted(next_version_keys & _declared())
    assert not landed, (
      f"§{SECTION} tags {landed} ⏭ <version>, but the registry declares them — the "
      f"row landed; drop its tag"
    )


class TestTheValuesAgree:
  """Where BOTH carriers state a default, it is the same default."""

  def test_the_stated_defaults_are_identical(self, spec_rows):
    registry: dict[str, str] = dict(_manifest_rows())
    for key, value in [*_manifest_tier_categories().items(), *_manifest_tier_entries().items()]:
      if value is not None:
        registry[key] = value
    # ⚑ THE ONE ACTIVE-NODE NORMALIZATION (registry side, Default tier only).
    registry = {key: value.replace(_ACTIVE_NODE, _SPEC_NODE) for key, value in registry.items()}

    shared = sorted(set(spec_rows) & set(registry))
    assert shared, (
      f"no Default-tier key is stated by both carriers (spec {len(spec_rows)} "
      f"rows, registry {len(registry)} rows) — the value comparison below is "
      f"vacuous"
    )
    disagree = {
      key: (spec_rows[key], registry[key]) for key in shared if spec_rows[key] != registry[key]
    }
    assert not disagree, (
      f"spec and registry state different defaults (spec, registry): {disagree} "
      f"— the spec is authority; move the manifest"
    )
