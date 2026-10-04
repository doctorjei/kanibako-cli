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

import pytest

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
    spec_notation as _canonical,
)

#: The tier this file pins.  Every row on both sides is one of these; the per-node arm
#: (``agent.<agent>.*``) is a different subsection and a different question.
TIER = "agent.default."

#: The §2d subsection whose fenced block IS the Default tier, and the marker that opens
#: it.  Both are read as HEADINGS/markers, never as line numbers: the spec is edited
#: constantly and any stored offset would be wrong within the week.
#: ⚑ THE MARKER IS THE BOLD NAME ALONE, matched as the START of the line: the name is a
#: RESERVED pseudo-agent name §2d is committed to, while the gloss beside it —
#: ``**default** (holds default values for agents)`` — is prose, rewordable at any time.
#: ⚑ THE PREFIX BUYS THE GLOSS ONLY.  The 2026-09-08 outage was a RETITLE of the NAME
#: (``**Default tier**``): the walk located 0 markers and every case ERRORED — the pin
#: working, loudly — and a rename still reds here.  Pinning the gloss too, as this
#: constant briefly did, would have bought a second outage that size for prose churn.
SECTION = "2d"
MARKER = "**default**"



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
    """
    return {
        key: _spec_cell_value(cell)
        for key, cell in _spec_fence_rows(SECTION, MARKER)
        if key.startswith(TIER)
    }


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

    ⚑ NOT AN ALLOWLIST — read out of the manifest's own ``categories:`` table, which
    carries the family names and the ``scopes`` they exist at.  A category is how the
    registry declares ``agent.default.bindings.ro`` and its siblings; they are real rows
    of the Default tier and simply live in a different table from the scalars, so a
    spec-side lookup that consulted only ``keys:`` would report them missing.  The value
    is the family's ``default:`` where it states one, and ``None`` where it does not.
    """
    table = manifest_doc()["categories"]
    scope = TIER.rstrip(".")
    if scope not in table["scopes"]:
        return {}
    return {
        f"{TIER}{name}": (_canonical(row["default"]) if "default" in row else None)
        for name, row in table.items()
        if isinstance(row, dict) and "value" in row
    }


# --------------------------------------------------------------------------- #
# The pins
# --------------------------------------------------------------------------- #


@pytest.fixture
def spec_rows() -> dict[str, str]:
    return _spec_rows(_default_tier_fence(_spec_lines()))


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

    def test_every_spec_row_is_a_manifest_row(self, spec_rows):
        declared = set(_manifest_rows()) | set(_manifest_tier_categories())
        missing = sorted(set(spec_rows) - declared)
        assert not missing, (
            f"§{SECTION}'s Default tier declares keys the registry has no row for: "
            f"{missing} — this is the agent.default.template failure verbatim; add the "
            f"row to keyspace-manifest.yaml"
        )


class TestTheValuesAgree:
    """Where BOTH carriers state a default, it is the same default."""

    def test_the_stated_defaults_are_identical(self, spec_rows):
        registry: dict[str, str] = dict(_manifest_rows())
        for key, value in _manifest_tier_categories().items():
            if value is not None:
                registry[key] = value

        shared = sorted(set(spec_rows) & set(registry))
        assert shared, (
            f"no Default-tier key is stated by both carriers (spec {len(spec_rows)} "
            f"rows, registry {len(registry)} rows) — the value comparison below is "
            f"vacuous"
        )
        disagree = {
            key: (spec_rows[key], registry[key])
            for key in shared if spec_rows[key] != registry[key]
        }
        assert not disagree, (
            f"spec and registry state different defaults (spec, registry): {disagree} "
            f"— the spec is authority; move the manifest"
        )
