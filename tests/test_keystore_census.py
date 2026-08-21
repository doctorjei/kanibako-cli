"""The KeyStore write census & its ENFORCEMENT, tested with the census DISARMED.

⚑ These run in the ordinary suite, where ``KANI_KEYSTORE_CENSUS`` is unset and the
plugin is inert.  They drive :mod:`tests._keystore_census`'s classification, its
exemption set and its interposition DIRECTLY, so the layer that fails a run is
itself covered rather than only exercised by the armed run that uses it.

⚑ Every case restores the module's globals; ``_keystore_census`` is a live pytest
plugin in this same process and a leaked ``_rows`` entry or a leaked patch would
follow the rest of the session.
"""

from __future__ import annotations

import pytest

from kanibako.settings import settings_assemble
from kanibako.settings.keystore import KeyStore
from tests import _keystore_census as census


@pytest.fixture
def clean_census():
  """Snapshot & restore every mutable global the plugin owns."""
  saved = (
    dict(census._rows), dict(census._pending), dict(census._verdicts),
    list(census._collector_errors), census._original_setitem, census._finalized,
  )
  census._rows.clear()
  census._pending.clear()
  yield census
  census._rows.clear()
  census._rows.update(saved[0])
  census._pending.clear()
  census._pending.update(saved[1])
  census._verdicts.clear()
  census._verdicts.update(saved[2])
  census._collector_errors[:] = saved[3]
  if census._original_setitem is None and saved[4] is not None:  # pragma: no cover
    KeyStore.__setitem__ = saved[4]  # type: ignore[method-assign]
  census._original_setitem = saved[4]
  census._finalized = saved[5]


def _judge(segments, origin="CODE"):
  return census.classify(tuple(segments), origin=origin)


class TestClassification:
  """Which class a written path lands in — only ``UNDECLARED`` fails a run."""

  def test_a_declared_key_is_accepted(self, clean_census):
    judged = _judge(("box", "image"))
    assert judged.verdict == census.Verdict.DECLARED
    assert judged.key == "box.image"

  def test_a_dest_inside_a_terminal_category_is_a_value_address(self, clean_census):
    """``box.caches`` is TERMINAL: its destinations are DATA, not key segments."""
    judged = _judge(("box", "caches", "~/.cache/uv"))
    assert judged.verdict == census.Verdict.VALUE
    assert judged.key == "box.caches"

  def test_a_dest_is_never_rendered_as_a_dotted_key(self, clean_census):
    """⚑ THE COLLECTOR BUG 2C FIXES: a dest must not be forged into a key path."""
    segments = ("box", "caches", "~/.cache/uv")
    judged = _judge(segments)
    rendered = census.render(segments, judged.key_len)
    assert rendered == "box.caches ⟨~/.cache/uv⟩"
    assert "box.caches.~/.cache/uv" != rendered

  def test_a_dotted_segment_stops_the_walk_and_is_never_asked_as_a_key(
    self, clean_census,
  ):
    """The oracle must not be handed a path forged out of a dotted DATA segment.

    ⚑ This is the ``env.<VAR>`` question, which is UNRULED: ``ENV_KEY_RE`` forbids a
    dot in a VAR name, the persona path never checks, and ``test_settings_launch``
    pins ``env.WEIRD.VAR`` surviving as one literal leaf.  Unruled means REPORTED,
    not enforced — so it lands here and never in ``UNDECLARED``.
    """
    judged = _judge(("agent", "claude", "env", "WEIRD.VAR"))
    assert judged.verdict == census.Verdict.DATA_SEGMENT
    assert "agent.claude.env.WEIRD.VAR" not in census._verdicts

  def test_a_fragment_store_is_unrooted_rather_than_guessed_at(self, clean_census):
    """A scope-LOCAL store's root IS the scope contents; its key path is unknowable."""
    assert _judge(("workspace",)).verdict == census.Verdict.UNROOTED
    assert _judge(("caches", "/x")).verdict == census.Verdict.UNROOTED

  def test_a_key_the_code_fabricated_is_a_violation(self, clean_census):
    judged = _judge(("box", "invented_leaf"))
    assert judged.verdict == census.Verdict.UNDECLARED
    assert "invented_leaf" in judged.note

  @pytest.mark.parametrize("origin,expected", [("FILE", "settings FILE"),
                                               ("TEST", "test's own input")])
  def test_the_same_key_from_a_file_or_a_test_is_reported_not_enforced(
    self, clean_census, origin, expected,
  ):
    """A key that CROSSED a boundary is a different question from a fabricated one."""
    judged = _judge(("box", "invented_leaf"), origin=origin)
    assert judged.verdict == census.Verdict.UNJUDGED
    assert expected in judged.note


class TestContainerRule:
  """A flagged NODE is judged by what landed underneath it, not by a list."""

  def _row(self, segments, *, verdict, is_node):
    census._rows[tuple(segments)] = {
      "path": census.render(segments), "segments": list(segments),
      "verdict": verdict, "key": "", "note": "", "origin": "CODE", "count": 1,
      "site": "x:1", "outer_site": "y:2", "value_type": "KeyStore" if is_node
      else "str", "is_node": is_node,
    }

  def test_a_node_carrying_a_declared_key_becomes_scaffolding(self, clean_census):
    self._row(("box",), verdict=census.Verdict.UNDECLARED, is_node=True)
    self._row(("box", "image"), verdict=census.Verdict.DECLARED, is_node=False)
    census._apply_container_rule()
    assert census._rows[("box",)]["verdict"] == census.Verdict.CONTAINER

  def test_an_empty_fabricated_node_stays_a_violation(self, clean_census):
    """Nothing declared underneath ⇒ not scaffolding.  ``box.bogus = {}`` still reds."""
    self._row(("box", "bogus"), verdict=census.Verdict.UNDECLARED, is_node=True)
    census._apply_container_rule()
    assert census._rows[("box", "bogus")]["verdict"] == census.Verdict.UNDECLARED

  def test_a_leaf_is_never_rescued_by_the_rule(self, clean_census):
    self._row(("box", "bogus"), verdict=census.Verdict.UNDECLARED, is_node=False)
    self._row(("box", "bogus", "image"), verdict=census.Verdict.DECLARED,
              is_node=False)
    census._apply_container_rule()
    assert census._rows[("box", "bogus")]["verdict"] == census.Verdict.UNDECLARED


class TestExemptions:
  """The exemption set is the deliverable's other half, so it is pinned like one."""

  def test_every_exemption_still_reproduces(self, clean_census):
    """⚑ GO RED WHEN AN EXEMPTION STOPS BEING NEEDED, so it gets DELETED.

    An exemption whose path the oracle now accepts is dead weight that hides the
    next finding behind it.
    """
    for entry in census.EXEMPTIONS:
      path = ".".join(entry.segments)
      assert census._ask(path) is not None, (
        f"{path} is now a declared key — delete this exemption"
      )

  def test_every_exemption_states_a_reason(self, clean_census):
    for entry in census.EXEMPTIONS:
      assert len(entry.reason) > 40, f"{entry.segments}: a reason, not a label"

  def test_the_reserved_internal_node_is_exempt_as_a_subtree(self, clean_census):
    judged = _judge(("binding_derivations", "system", "seeded", "/home/agent"))
    assert judged.verdict == census.Verdict.EXEMPT
    assert judged.key_len == 1
    assert census.render(
      ("binding_derivations", "system", "seeded", "/home/agent"), judged.key_len,
    ) == "binding_derivations ⟨system | seeded | /home/agent⟩"

  def test_a_scope_local_binding_derivations_table_is_NOT_exempt(self, clean_census):
    """The exemption is the ROOT node, not the spelling: ``box.binding_derivations``
    is an ordinary undeclared key."""
    assert _judge(
      ("box", "binding_derivations"),
    ).verdict == census.Verdict.UNDECLARED

  def test_an_exact_exemption_does_not_cover_its_children(self, clean_census):
    assert _judge(
      ("agent", "default", "cache_probe"),
    ).verdict == census.Verdict.EXEMPT
    assert _judge(
      ("agent", "default", "cache_probe", "deeper"),
    ).verdict == census.Verdict.UNDECLARED


class TestOriginSeam:
  """ORIGIN decides WHO chose the key, which is the whole enforcement question."""

  def test_the_file_partial_parser_is_pinned_by_code_object(self):
    """⚑ By CODE OBJECT, not by name: a rename must break the IMPORT, loudly.

    Matched on a string, a rename would silently re-class every file-sourced key as
    a fabrication and red the armed run for the wrong reason.
    """
    assert settings_assemble._parse_node.__code__ in census._FILE_PARTIAL_CODE
    assert settings_assemble.parse_bind_map.__code__ in census._FILE_PARTIAL_CODE

  def test_a_write_from_this_test_file_is_TEST_origin(self, clean_census):
    assert census._write_sites().origin == "TEST"


@pytest.mark.skipif(
  census._enabled(),
  reason="the plugin is ARMED in this session and has ALREADY patched the class; "
         "installing the patch a second time here recurses. These cases cover the "
         "interposition in the ordinary (disarmed) gate, which is where it is unused "
         "and therefore most in need of a test.",
)
class TestEnforcement:
  """The interposition records real writes, names the violation, and comes off."""

  def test_a_fabricated_key_written_through_the_funnel_fails_the_run(
    self, clean_census, monkeypatch,
  ):
    original = KeyStore.__setitem__
    census._original_setitem = original
    monkeypatch.setattr(
      KeyStore, "__setitem__", census._patched_setitem, raising=False,
    )
    store: KeyStore = KeyStore()
    store["box"] = {"image": "img", "fabricated_by_code": "x"}
    monkeypatch.undo()
    census._original_setitem = None
    # The write path is TEST-origin here, so force the CODE reading the enforcement
    # actually acts on — the interposition's recording is what is under test.
    for slot in census._pending.values():
      slot[2] = slot[2]._replace(origin="CODE")
    census.drain()
    census._apply_container_rule()
    bad = {row["path"] for row in census.violations()}
    assert "box.fabricated_by_code" in bad
    assert "box.image" not in bad
    assert "box" not in bad, "the intermediate node is scaffolding, not a key"
    assert KeyStore.__setitem__ is original

  def test_the_patch_is_removed_even_if_the_report_is_never_drawn(self, clean_census):
    original = KeyStore.__setitem__
    census._original_setitem = original
    KeyStore.__setitem__ = census._patched_setitem  # type: ignore[method-assign]
    census._uninstall()
    assert KeyStore.__setitem__ is original
    assert census._original_setitem is None
