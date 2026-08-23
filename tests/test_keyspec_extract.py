"""The keyspec carve-up: every requested section, and NOTHING BETWEEN THEM, gets read.

``scripts/keyspec-extract.py`` slices the keyspace spec into per-section extraction
requests, and its output becomes the ORACLE the manifest-enforcer programme is judged
against.  So a span that no requested section asks for is not a cosmetic gap: it is key
surface nobody read, wearing the appearance of coverage.  That is strictly worse than
having no oracle, because nothing downstream can tell the difference.

⚑ THE SHAPE THAT CAUSED IT, and what these tests pin: a parent heading whose children
are themselves numbered sections owns prose of its own, before the first child.  Asking
for the children misses it; asking for the PARENT instead re-sends every child.  It
needs its own id.  A parent whose children are UNNUMBERED is the opposite case — nothing
else claims them, the parent's own range already covers them, and minting a preamble
there would only overlap the parent.

The spec lives in the canon (``~/canon/workbook/specs/``), outside this repo and absent
from CI, so the binding tests here run against a fixture that reproduces both shapes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "keyspec-extract.py"


def _load_script():
  """Import the hyphenated script by path -- it is not a package module."""
  spec = importlib.util.spec_from_file_location("keyspec_extract", _SCRIPT)
  assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT}"
  module = importlib.util.module_from_spec(spec)
  # ⚑ REGISTERED BEFORE EXECUTION, not after: @dataclass resolves annotations through
  # ``sys.modules[cls.__module__]`` and raises AttributeError on an absent entry.
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


keyspec = _load_script()


#: A miniature of the real spec's structure, and every clause of it is load-bearing:
#: two childless top sections; one parent (§2) with prose of its own, a FENCED block
#: whose lines start with ``#``, and numbered children; and a child (§2b) that owns only
#: UNNUMBERED subheadings.  Heading text is deliberately unlike the real spec's -- this
#: is a shape, not a copy of an authority.
FIXTURE = """# Fixture — NOT the keyspace spec

## 1. FIRST — no children
prose

## 1A. FIRST-A — no children either
prose

## 2. SECOND — the parent
preamble prose that declares a key nothing else mentions
```yaml
# not a heading: this line is inside a fence
### neither is this one
```
more preamble prose

### 2a. SECOND-A
prose

### 2b. SECOND-B — owns UNNUMBERED subheadings only
prose

#### `access` — unnumbered
prose

#### `agent.claude.*` — unnumbered
prose

## 3. THIRD
prose
"""

#: Every numbered leaf plus the preamble -- the set a default run must request.
LEAF_REQUEST = ("1", "1A", "2-pre", "2a", "2b", "3")

#: The same request WITHOUT the preamble: what the carve-up asked for before this was
#: fixed, kept so the gap detector is pinned against a known hole and not only a zero.
REQUEST_MISSING_THE_PREAMBLE = ("1", "1A", "2a", "2b", "3")


@pytest.fixture
def lines() -> list[str]:
  """The fixture spec as ``readlines()`` would hand it over."""
  return FIXTURE.splitlines(keepends=True)


def _line_of(lines: list[str], prefix: str) -> int:
  """The 1-based line number of the only line starting with *prefix*."""
  hits = [n for n, raw in enumerate(lines, start=1) if raw.startswith(prefix)]
  assert len(hits) == 1, f"{prefix!r} matches {len(hits)} lines, expected exactly 1"
  return hits[0]


def test_requested_sections_tile_their_span(lines: list[str]) -> None:
  """THE INVARIANT: nothing between the first and last requested line goes unrequested."""
  found = keyspec.parse_sections(lines)
  absent = [sid for sid in LEAF_REQUEST if sid not in found]
  assert not absent, (
    f"the carve-up has no id for {absent}, so those lines can be requested by nothing"
  )
  assert keyspec.coverage_gaps(found, LEAF_REQUEST) == []


def test_a_gap_between_requested_sections_is_reported_not_swallowed(lines: list[str]) -> None:
  """Drop the preamble and the detector must name the exact span, and who it falls between."""
  found = keyspec.parse_sections(lines)
  parent = _line_of(lines, "## 2.")
  first_child = _line_of(lines, "### 2a.")
  assert keyspec.coverage_gaps(found, REQUEST_MISSING_THE_PREAMBLE) == [
    (parent, first_child - 1, "1A", "2a")
  ]
  summary = keyspec.coverage_summary(found, REQUEST_MISSING_THE_PREAMBLE)
  assert "GAP" in summary and f"{parent}-{first_child - 1}" in summary


def test_preamble_runs_from_the_parent_heading_to_its_first_child(lines: list[str]) -> None:
  """Derived from the headings, exactly as every other range is -- never a stored pair."""
  found = keyspec.parse_sections(lines)
  parent = _line_of(lines, "## 2.")
  first_child = _line_of(lines, "### 2a.")
  assert (found["2-pre"].start, found["2-pre"].end) == (parent, first_child - 1)
  # The fenced `###` line must have been ignored, not taken as the first child.
  fenced = _line_of(lines, "### neither is this one")
  assert found["2-pre"].start < fenced < found["2-pre"].end


def test_the_preamble_and_the_children_reconstruct_the_parent_exactly(lines: list[str]) -> None:
  """The parent is never requested, so its whole range must be covered by ids that are."""
  found = keyspec.parse_sections(lines)
  children = ("2-pre", "2a", "2b")
  assert min(found[sid].start for sid in children) == found["2"].start
  assert max(found[sid].end for sid in children) == found["2"].end
  assert keyspec.coverage_gaps(found, children) == []


def test_unnumbered_children_mint_no_preamble(lines: list[str]) -> None:
  """§2d's shape: its four ``####`` blocks claim nothing, so a ``2b-pre`` would only overlap."""
  found = keyspec.parse_sections(lines)
  assert "2b-pre" not in found, (
    "a parent whose children are unnumbered already covers them; a preamble here would "
    "duplicate lines its own parent requests"
  )
  assert found["2b"].end == _line_of(lines, "## 3.") - 1


def test_a_preamble_id_cannot_collide_with_a_declared_section() -> None:
  """By construction, not by luck: the id grammar cannot mint the suffix's spelling."""
  assert keyspec._SECTION_ID.match(f"2{keyspec._PREAMBLE_SUFFIX}. Anything") is None


def test_the_default_request_covers_the_real_spec() -> None:
  """_EXPECTED against the ACTUAL spec, where it is mounted.

  ⚑ SKIPS OFF-BOX. The canon is not in this repo and is not in CI, so this cannot be the
  test that holds the invariant -- the fixture tests above are. It is here because it is
  the only thing that catches the real spec growing a heading nobody re-checked.
  """
  spec_path = keyspec._DEFAULT_SPEC
  if not spec_path.is_file():
    pytest.skip(f"the keyspace spec is not mounted at {spec_path}")
  spec_lines = spec_path.open(encoding="utf-8").readlines()
  found = keyspec.parse_sections(spec_lines)
  keyspec.require_expected(found, keyspec._EXPECTED)
  assert keyspec.coverage_gaps(found, keyspec._EXPECTED) == []
