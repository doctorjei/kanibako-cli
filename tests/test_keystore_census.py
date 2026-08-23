"""The KeyStore write census & its ENFORCEMENT, driven directly.

⚑ These drive :mod:`tests._keystore_census`'s classification, its negative-marker
mechanism and its interposition, so the layer that fails a run is itself covered
rather than only exercised by the run that uses it.

⚑ The census is DEFAULT-ON, so in an ordinary session the plugin has already
patched ``KeyStore`` before these run.  The ``interposed`` fixture below adapts to
either state; nothing here may install a second patch over the live one.

⚑ Every case restores the module's globals; ``_keystore_census`` is a live pytest
plugin in this same process and a leaked ``_rows`` entry, a leaked marker path or a
leaked patch would follow the rest of the session.
"""

from __future__ import annotations

import pytest

from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE
from kanibako.settings.keystore import KeyStore
from tests import _keystore_census as census


@pytest.fixture(scope="module", autouse=True)
def _arming_survives_this_file():
  """⚑ REGRESSION GUARD. This file drives install/uninstall directly, and ONE
  careless restore disarmed the census for every test that ran after it — the
  whole-suite run then reported zero paths written and every marker stale, which
  reads as a census bug rather than as a leak from here."""
  before = KeyStore.__setitem__
  yield
  assert KeyStore.__setitem__ is before, (
    "this file left KeyStore.__setitem__ changed: the census is disarmed for "
    "everything that runs after it"
  )


@pytest.fixture
def clean_census():
  """Snapshot & restore every mutable global the plugin owns, AND the funnel.

  ⚑⚑ THE FUNNEL IS RESTORED TO WHAT IT WAS, not to ``_original_setitem``.  Those
  are different objects in an armed session — ``KeyStore.__setitem__`` is the
  PATCH and ``_original_setitem`` is the real funnel it wraps — and putting the
  real one back on the class silently DISARMS the census for every test that runs
  after this file.  (It did exactly that; the whole-suite census then reported zero
  paths written and every marker as stale.)
  """
  saved = (
    dict(census._rows), dict(census._pending), dict(census._verdicts),
    list(census._collector_errors), census._original_setitem, census._finalized,
    set(census._declared_negative), set(census._hit_negative),
  )
  saved_funnel = KeyStore.__setitem__
  census._rows.clear()
  census._pending.clear()
  yield census
  KeyStore.__setitem__ = saved_funnel  # type: ignore[method-assign]
  census._rows.clear()
  census._rows.update(saved[0])
  census._pending.clear()
  census._pending.update(saved[1])
  census._verdicts.clear()
  census._verdicts.update(saved[2])
  census._collector_errors[:] = saved[3]
  census._original_setitem = saved[4]
  census._finalized = saved[5]
  census._declared_negative.clear()
  census._declared_negative.update(saved[6])
  census._hit_negative.clear()
  census._hit_negative.update(saved[7])


@pytest.fixture
def interposed(clean_census):
  """Guarantee the interposition is live, however the session was configured.

  Armed (the default), the plugin already installed it and ``_original_setitem``
  holds the REAL funnel — re-reading ``KeyStore.__setitem__`` into that slot would
  make the patch call itself.  Disarmed, install and remove it here.
  """
  if clean_census._original_setitem is not None:
    yield clean_census
    return
  original = KeyStore.__setitem__
  clean_census._original_setitem = original
  KeyStore.__setitem__ = clean_census._patched_setitem  # type: ignore[method-assign]
  try:
    yield clean_census
  finally:
    KeyStore.__setitem__ = original  # type: ignore[method-assign]
    clean_census._original_setitem = None


def _judge(segments):
  return census.classify(tuple(segments))


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
    """⚑ A dest must not be forged into a key path."""
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

  def test_a_path_is_judged_by_WHAT_IT_IS_never_by_who_wrote_it(self, clean_census):
    """⚑ THE ANTI-REGRESSION FOR THE DELETED ORIGIN DISCRIMINATOR.

    An earlier revision excused a key by the FRAME that wrote it — a test tree, or
    the settings file-partial parser matched by code object.  That excused ~44
    synthetic fixture keys and ``meta.workset.{created,projects}``, every one of
    them a real violation.  :func:`census.classify` now takes the path and nothing
    else; there is no parameter left for a caller to plead with.
    """
    import inspect

    assert list(inspect.signature(census.classify).parameters) == ["segments"]
    assert not hasattr(census, "_FILE_PARTIAL_CODE")
    assert not hasattr(census.Verdict, "UNJUDGED")


class TestContainerRule:
  """A flagged NODE is judged by what landed underneath it, not by a list."""

  def _row(self, segments, *, verdict, is_node, negative=False):
    census._rows[tuple(segments)] = {
      "path": census.render(segments), "segments": list(segments),
      "verdict": verdict, "key": "", "note": "", "negative": negative, "count": 1,
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

  def test_an_empty_scope_root_is_a_NAMESPACE_not_a_fabrication(self, clean_census):
    """⚑ A settings file's ``box: {}`` writes an EMPTY scope table.  A root's
    key-hood is not a function of what a session happened to put under it — every
    root is a namespace by §0 — so the "carries something declared" test must not
    be applied to one.  (It was, and it reddened test_settings_prefs.py, whose
    fixtures are all empty box files.)"""
    self._row(("box",), verdict=census.Verdict.UNDECLARED, is_node=True)
    census._apply_container_rule()
    assert census._rows[("box",)]["verdict"] == census.Verdict.CONTAINER
    assert "namespace" in census._rows[("box",)]["note"]

  def test_a_SCALAR_at_a_scope_root_is_still_a_violation(self, clean_census):
    """The ``is_node`` guard: a namespace is a node.  ``box = "str"`` is an
    undeclared SHAPE and keeps reddening."""
    self._row(("box",), verdict=census.Verdict.UNDECLARED, is_node=False)
    census._apply_container_rule()
    assert census._rows[("box",)]["verdict"] == census.Verdict.UNDECLARED

  def test_a_leaf_is_never_rescued_by_the_rule(self, clean_census):
    self._row(("box", "bogus"), verdict=census.Verdict.UNDECLARED, is_node=False)
    self._row(("box", "bogus", "image"), verdict=census.Verdict.DECLARED,
              is_node=False)
    census._apply_container_rule()
    assert census._rows[("box", "bogus")]["verdict"] == census.Verdict.UNDECLARED


class TestReservedInternalNode:
  """The one non-key node the SPEC names — structural, not an exemption."""

  def test_the_reserved_node_is_its_own_class_for_its_whole_subtree(
    self, clean_census,
  ):
    judged = _judge(
      (BINDING_DERIVATIONS_NODE, "system", "seeded", "/home/agent"),
    )
    assert judged.verdict == census.Verdict.RESERVED
    assert judged.key_len == 1
    assert census.render(
      (BINDING_DERIVATIONS_NODE, "system", "seeded", "/home/agent"),
      judged.key_len,
    ) == "binding_derivations ⟨system | seeded | /home/agent⟩"

  def test_a_scope_local_derivations_table_is_NOT_reserved(self, clean_census):
    """The class is the ROOT node, not the spelling: ``box.binding_derivations``
    is an ordinary undeclared key."""
    assert _judge(
      ("box", BINDING_DERIVATIONS_NODE),
    ).verdict == census.Verdict.UNDECLARED

  def test_there_is_no_exemption_list_to_grow(self, clean_census):
    """⚑ Rule: a name-keyed exemption hides the next finding behind it.  The
    apparatus is GONE, not merely empty — an empty tuple invites an entry."""
    for gone in ("EXEMPTIONS", "Exemption", "exemption_for"):
      assert not hasattr(census, gone), f"{gone} is back"


class TestNegativeMarker:
  """A test that exercises a refusal DECLARES the undeclared paths it writes."""

  def test_a_declared_path_is_reclassed_and_does_not_fail_the_run(
    self, clean_census,
  ):
    census._rows[("box", "bogus")] = {
      "path": "box.bogus", "segments": ["box", "bogus"],
      "verdict": census.Verdict.UNDECLARED, "key": "", "note": "not a key",
      "negative": True, "count": 1, "site": "x:1", "outer_site": "y:2",
      "value_type": "str", "is_node": False,
    }
    census._apply_negative_rule()
    assert census._rows[("box", "bogus")]["verdict"] == census.Verdict.NEGATIVE
    assert census.violations() == []

  def test_the_same_path_written_from_anywhere_else_is_still_a_violation(
    self, clean_census,
  ):
    """⚑ The AND in :func:`census.drain`: a declaration covers ONE test's writes,
    never the path.  One write from outside the declaring test and it reds again."""
    census._rows[("box", "bogus")] = {
      "path": "box.bogus", "segments": ["box", "bogus"],
      "verdict": census.Verdict.UNDECLARED, "key": "", "note": "not a key",
      "negative": False, "count": 2, "site": "x:1", "outer_site": "y:2",
      "value_type": "str", "is_node": False,
    }
    census._apply_negative_rule()
    assert census._rows[("box", "bogus")]["verdict"] == census.Verdict.UNDECLARED

  def test_a_declared_path_that_is_never_written_fails_the_run(self, clean_census):
    """⚑ WHAT STOPS A MARKER OUTLIVING ITS REASON.  A declaration nothing exercises
    — because the test changed, or because the key became declared — is a stale
    blessing and reds in its own right."""
    census._declared_negative.add("box.gone_stale")
    assert census.unused_negatives() == ["box.gone_stale"]
    census._hit_negative.add("box.gone_stale")
    assert census.unused_negatives() == []

  @pytest.mark.writes_undeclared(
    "box.declared_by_this_very_test",
    reason="drives the marker end to end: this test writes the path it names, so "
           "the census must class it NEGATIVE instead of failing the run, and the "
           "path must count as EXERCISED rather than as a stale declaration.",
  )
  def test_the_marker_reaches_the_collector_at_write_time(self, interposed):
    store: KeyStore = KeyStore()
    store["box"] = {"image": "img", "declared_by_this_very_test": "x"}
    interposed.drain()
    interposed._apply_container_rule()
    interposed._apply_negative_rule()
    row = interposed._rows[("box", "declared_by_this_very_test")]
    assert row["verdict"] == census.Verdict.NEGATIVE
    assert "box.declared_by_this_very_test" in interposed._hit_negative
    assert interposed.violations() == []


class TestEnforcement:
  """The interposition records real writes and names the violation."""

  def test_a_fabricated_key_written_through_the_funnel_fails_the_run(
    self, interposed,
  ):
    store: KeyStore = KeyStore()
    store["box"] = {"image": "img", "fabricated_by_code": "x"}
    interposed.drain()
    interposed._apply_container_rule()
    interposed._apply_negative_rule()
    bad = {row["path"] for row in interposed.violations()}
    assert "box.fabricated_by_code" in bad
    assert "box.image" not in bad
    assert "box" not in bad, "the intermediate node is scaffolding, not a key"

  def test_the_patch_is_removed_even_if_the_report_is_never_drawn(self, clean_census):
    """``_uninstall`` restores the funnel whatever ran — the class is never left
    patched.  Driven against the REAL funnel, so it holds in either session mode."""
    real = census._original_setitem or KeyStore.__setitem__
    census._original_setitem = real
    KeyStore.__setitem__ = census._patched_setitem  # type: ignore[method-assign]
    census._uninstall()
    assert KeyStore.__setitem__ is real
    assert census._original_setitem is None


class TestArming:
  """DEFAULT-ON: the flag opts OUT, and only on a token that means "off"."""

  @pytest.mark.parametrize("value", ["0", "off", "false", "no", "", "  OFF "])
  def test_the_off_tokens_disarm_it(self, monkeypatch, value):
    monkeypatch.setenv(census.ENV_FLAG, value)
    assert census._enabled() is False

  @pytest.mark.parametrize("value", ["1", "yes", "on", "anything"])
  def test_anything_else_arms_it(self, monkeypatch, value):
    monkeypatch.setenv(census.ENV_FLAG, value)
    assert census._enabled() is True

  def test_unset_is_ARMED(self, monkeypatch):
    """⚑ The keyspace is closed whether or not anyone remembered a flag."""
    monkeypatch.delenv(census.ENV_FLAG, raising=False)
    assert census._enabled() is True
