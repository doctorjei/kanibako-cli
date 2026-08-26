"""The resolve-seam probe module, driven DIRECTLY — oracle, discovery, and seam.

⚑ WHY A FILE OF ITS OWN. ``settings_keyspace_probe`` stopped being a diagnostic:
``keyspace_verdict`` is read on EVERY settings resolve, because
``settings_launch._refuse_undeclared_snapshot`` refuses on it. Until now it was
exercised only transitively — through ``test_settings_keyspace.py`` (the keyspace
RULES) and ``test_settings_launch.py`` (the seam's REFUSAL) — so the module's own
mechanism had no direct driver.

⚑⚑ THE DIVISION OF LABOUR WITH ITS TWO NEIGHBOURS IS DELIBERATE, and a second
carrier of either of theirs would be the defect class this project keeps paying for:

* ``test_settings_keyspace.py`` owns the KEYSPACE RULES — what ``key_class`` says
  about a path, including the derived-corpus sweep over every §2a category token.
* ``test_settings_launch.py`` owns the SEAM'S POLICY — that a resolve REFUSES, what
  the message names, and that the probe flag is no bypass for it.
* THIS file owns what is left, and it is the part with no other driver: that
  ``keyspace_verdict`` — the single entry both live consumers read — delivers those
  rules through its MEMO, that discovery is one conceding pass, and that the
  instrument itself is off by default, appends one row per observation, and cannot
  fail a run.

⚑ Every case restores the module's process-wide globals. ``_PLUGINS``, ``_verdicts``
and ``probe_errors`` outlive a test, and the memo in particular is consulted by the
census plugin and by every later resolve in the session.
"""

from __future__ import annotations

import json

import pytest

from kanibako.settings import settings_keyspace_probe as probe
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import KeyClass

#: A claude-only install, which is the position a shared settings file lands in and
#: the one the leaf concession exists for. ⚑ PATCHED IN rather than measured: this
#: machine has all three plugins, so the concession is unreachable without it and
#: every case below would pass on the real vocabulary instead.
_CLAUDE_ONLY = ("claude",)


@pytest.fixture(scope="module", autouse=True)
def _globals_survive_this_file():
  """⚑ REGRESSION GUARD, and the sibling census file is why it is here.

  ``_PLUGINS`` and ``_verdicts`` are PROCESS-WIDE, and this file replaces both. A
  leaked fake plugin set would silently change what every later resolve in the
  session refuses — and a leaked memo would answer them off it — which reads as a
  keyspace bug rather than as a leak from here.
  """
  before = (probe._PLUGINS, probe._verdicts, probe.probe_errors)
  yield
  assert (probe._PLUGINS, probe._verdicts, probe.probe_errors) == before, (
    "this file leaked one of the probe's process-wide globals: every later resolve "
    "in the session now reads it"
  )


@pytest.fixture
def clean_probe(monkeypatch):
  """The probe module with its three process-wide globals restored afterwards.

  ⚑ ``_verdicts`` IS CLEARED, not merely restored. It is a memo of a PURE function
  under unpatched conditions, so clearing costs only recomputation — but a case that
  patches ``_PLUGINS`` and reads a verdict warmed under the REAL plugin set would
  measure the memo rather than the concession, and would pass whatever the oracle
  said.
  """
  monkeypatch.setattr(probe, "_verdicts", {})
  monkeypatch.setattr(probe, "probe_errors", [])
  monkeypatch.setattr(probe, "_PLUGINS", probe._PLUGINS)
  return probe


@pytest.fixture
def claude_only(clean_probe, monkeypatch):
  """As :func:`clean_probe`, with discovery answering "claude, and no leaves"."""
  monkeypatch.setattr(
    probe, "_PLUGINS", probe._Plugins(frozenset(), frozenset(_CLAUDE_ONLY)),
  )
  return clean_probe


def _armed(monkeypatch, tmp_path, name="probe.jsonl"):
  """Arm the probe onto a fresh row file and return that file."""
  rows = tmp_path / name
  monkeypatch.setenv(probe.ENV_FLAG, "1")
  monkeypatch.setenv(probe.ENV_FILE, str(rows))
  return rows


def _rows(path):
  return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# ONE oracle, and it answers all THREE classes                                #
# --------------------------------------------------------------------------- #


def test_both_live_consumers_read_THIS_oracle_object():
  """⚑⚑ A REFUSAL ARMED ON A SECOND ORACLE REFUSES SOMETHING OTHER THAN WHAT WAS
  MEASURED, which is the whole reason ``keyspace_verdict`` is public.

  ``observe`` REPORTS on it and ``settings_launch._refuse_undeclared_snapshot``
  REFUSES on it; the pytest write census reads ``declared_keyspace_oracle`` for the
  same question. Identity, not equivalence — a copy that happens to agree today is
  exactly the drift this forbids.

  MUTATION: give ``settings_launch`` its own ``key_class`` call and this reddens.
  """
  from kanibako.settings import settings_launch
  from tests import _keystore_census as census

  assert settings_launch.keyspace_verdict is probe.keyspace_verdict
  assert census._oracle is probe.declared_keyspace_oracle


def test_the_oracle_answers_all_THREE_classes_not_key_or_not():
  """🛑 NOT the key-or-not view. ``key_validity`` collapses NAMESPACE into "not a
  key", and a store-path classifier given that has only DEPTH left to tell a declared
  interior from a fabrication — which is the rule that reported ``meta.box.agent``
  and ``agent.<agent>`` as violations whenever their last child was dropped.

  MUTATION: point ``declared_keyspace_oracle`` at ``key_validity`` and the middle
  assertion cannot be satisfied at all.
  """
  assert probe.keyspace_verdict("box.image").cls is KeyClass.KEY
  assert probe.keyspace_verdict("box").cls is KeyClass.NAMESPACE
  assert probe.keyspace_verdict("box.zippity").cls is KeyClass.UNDECLARED


def test_a_non_key_verdict_carries_its_reason_and_a_KEY_carries_none():
  """Spec §2h: the error NAMES the key and says WHY — the row and the refusal both
  print this string, so an empty one would name a key with no cause."""
  assert probe.keyspace_verdict("box.image").reason == ""
  assert "not a declared box key" in probe.keyspace_verdict("box.zippity").reason


# --------------------------------------------------------------------------- #
# The concession, through the entry point the live consumers actually read     #
# --------------------------------------------------------------------------- #
#
# ⚑ THE KEYSPACE RULES BELOW ARE PINNED IN ``test_settings_keyspace.py``, against
# ``key_class`` and ``declared_keyspace_oracle``. What is pinned HERE is that
# ``keyspace_verdict`` — the memoised wrapper both live consumers call, and the only
# one either of them can reach — still delivers them. A memo is a place a correct
# oracle can be given a wrong answer.


def test_the_agent_NAME_is_conceded(claude_only):
  """⚑ LOAD-BEARING, AND NOT A DEFECT TO FIX.

  ``valid_agents`` is ``ANY_AGENT``: this machine's plugin set is not the keyspace,
  and both production and the test tree invent agent names freely (personas,
  ``myagent``, ``testagent``). A narrow set manufactures rows saying "'x' is not a
  valid agent" — a finding about a DISCRIMINATOR, not about the keyspace. Narrowing
  it was MEASURED to red 19 files / ~154 node IDs, persona-discriminated nodes
  (``navigator℘claude``) among them, and personas are a shipped feature.
  """
  assert "zzznotinstalled" in probe.ANY_AGENT
  assert probe.keyspace_verdict("agent.zzznotinstalled").cls is KeyClass.NAMESPACE
  assert probe.keyspace_verdict("agent.zzznotinstalled.model").cls is KeyClass.KEY
  assert probe.keyspace_verdict("agent.navigator℘zzznot.model").cls is KeyClass.KEY


def test_the_concession_reaches_that_agents_LEAVES_too(claude_only):
  """⚑⚑ THE NAME AND ITS LEAVES ARE A DEPENDENT PAIR (spec §0: agent specifics are
  PLUGIN-declared).

  Where the plugin is absent there is no vocabulary to judge a leaf against, so
  conceding the name while judging the leaves concedes HALF a pair — and it produced
  a false positive on a key that IS declared: ``agent.goose.provider``, a real goose
  ``setting_descriptor`` leaf, refused as "not a declared agent key" on a claude-only
  machine.

  MUTATION: drop ``agents_with_known_leaves`` from ``declared_keyspace_oracle`` and
  this reddens UNDECLARED, with a reason naming the wrong cause.
  """
  assert probe.keyspace_verdict("agent.goose.provider").cls is KeyClass.KEY


def test_an_agent_this_machine_CAN_see_is_judged_exactly_as_before(claude_only):
  """The other direction — what keeps it a concession rather than a hole."""
  judged = probe.keyspace_verdict("agent.claude.zippity")
  assert judged.cls is KeyClass.UNDECLARED
  assert "not a declared agent key" in judged.reason


def test_a_PERSONA_is_known_by_its_HARNESS(claude_only):
  """``KNOWN_LEAF_AGENTS`` answers by harness, so a persona of an INSTALLED harness
  is judged, not conceded. Asking about the node name instead would concede every
  persona on the machine — and the persona set is open, which is why membership is
  asked and never enumerated."""
  assert "navigator℘claude" in probe.KNOWN_LEAF_AGENTS
  assert "navigator℘goose" not in probe.KNOWN_LEAF_AGENTS
  assert probe.keyspace_verdict(
    "agent.navigator℘claude.zippity"
  ).cls is KeyClass.UNDECLARED


def test_the_concession_does_NOT_reach_the_relic_shapes(claude_only):
  """🛑 THE TEST IS "COULD THIS SEGMENT BE AN AGENT AT ALL", NOT "IS IT INSTALLED".

  The §2a category tokens are a KNOWN FINITE SET the keyspace declares itself, so
  there is nothing unknowable about them to concede. An earlier cut asked only the
  install question and classified all three shapes below as KEY — because no plugin
  declares an agent named ``common``, ``env`` or ``seeded`` — which un-armed §0 over
  exactly the undiscriminated relic MIGRATION.md §2.11 tells users to grep for.

  ⚑ THREE NAMED SHAPES, not a corpus. The exhaustive sweep over every token in
  ``CATEGORY_FAMILY_ROOTS`` is ``test_settings_keyspace.py``'s
  ``test_a_category_token_under_agent_is_never_conceded``; duplicating its derivation
  here would be a second carrier of one rule. These are the three the migration
  document names by hand, and the typo case beneath them is the residual.
  """
  for path in ("agent.common.plugins", "agent.env.FOO", "agent.seeded.x"):
    assert probe.keyspace_verdict(path).cls is KeyClass.UNDECLARED, path
  # ``default`` fails the same first question: the all-agents tier is CORE's, so its
  # vocabulary is knowable on every machine and conceding it would give away the
  # whole behaviour floor on any install missing any one plugin.
  assert probe.keyspace_verdict("agent.default.zippity").cls is KeyClass.UNDECLARED


def test_the_residual_is_a_TYPO_and_it_is_IRREDUCIBLE(claude_only):
  """🛑 STATED, NOT HIDDEN, AND NOT TO BE CLOSED.

  An agent NAME cannot be enumerated, so ``clade`` is indistinguishable from a real
  harness this machine has never heard of — which is the price of not refusing
  ``agent.goose.provider`` on a claude-only box. Anything that closed this would have
  to decide that an unknown name is not an agent, which is the measured red.
  """
  assert probe.keyspace_verdict("agent.clade.zippity").cls is KeyClass.KEY


def test_the_meta_agent_tier_is_NOT_conceded(claude_only):
  """``meta.agent.<agent>.*`` is core-declared and no plugin extends it, so its
  vocabulary is knowable either way — there is nothing to concede, and conceding it
  would give away a subtree for a reason that does not apply to it."""
  assert probe.keyspace_verdict("meta.agent.goose.zippity").cls is KeyClass.UNDECLARED


# --------------------------------------------------------------------------- #
# The MEMO                                                                     #
# --------------------------------------------------------------------------- #


def test_the_verdict_is_memoised_per_distinct_path(clean_probe, monkeypatch):
  """The prefix walk asks about every proper PREFIX of every path, and a launch
  resolves many — so the memo is what keeps the oracle off the hot path."""
  calls = []
  real = probe.declared_keyspace_oracle

  def counting(path):
    calls.append(path)
    return real(path)

  monkeypatch.setattr(probe, "declared_keyspace_oracle", counting)
  first = probe.keyspace_verdict("box.image")
  second = probe.keyspace_verdict("box.image")
  assert calls == ["box.image"]
  assert first is second


def test_the_memo_survives_a_change_of_DISCOVERY(clean_probe, monkeypatch):
  """⚑ A HAZARD, PINNED SO IT IS NOT REDISCOVERED IN A DEBUGGER.

  The memo is process-wide and nothing invalidates it, so a caller that changes what
  discovery reports gets the answer computed BEFORE the change. That is correct for
  production — discovery does not move within a process — and it is precisely why
  every case above patches ``_PLUGINS`` through a fixture that clears ``_verdicts``.
  """
  assert probe.keyspace_verdict("agent.goose.provider").cls is KeyClass.KEY
  monkeypatch.setattr(
    probe, "_PLUGINS", probe._Plugins(frozenset(), frozenset({"goose"})),
  )
  # Uncached, it would now refuse; cached, the earlier answer stands.
  assert probe.keyspace_verdict("agent.goose.provider").cls is KeyClass.KEY
  assert probe.declared_keyspace_oracle(
    "agent.goose.provider"
  ).cls is KeyClass.UNDECLARED


def test_an_oracle_FAULT_answers_UNDECLARED_and_names_itself(clean_probe, monkeypatch):
  """⚑ NOT a silent pass. A classifier that cannot judge a path must SAY so — in the
  probe's row AND in the refusal, which prints this note. Conceding the path instead
  would let a broken oracle green-light §0 across a whole snapshot.

  The fault is also COUNTED, so a run can say the instrument misbehaved rather than
  report a clean sheet it never measured.
  """
  def boom(path):
    raise RuntimeError("oracle is broken")

  monkeypatch.setattr(probe, "declared_keyspace_oracle", boom)
  judged = probe.keyspace_verdict("box.image")
  assert judged.cls is KeyClass.UNDECLARED
  assert judged.reason == "<oracle raised RuntimeError: oracle is broken>"
  assert probe.probe_errors == ["RuntimeError: oracle is broken"]


def test_the_error_list_is_CAPPED(clean_probe):
  """A pathological run must not grow the list without bound; the cap is what keeps
  the instrument's own failure mode bounded too."""
  for i in range(60):
    probe._note_error(ValueError(f"fault {i}"))
  assert len(probe.probe_errors) == 50
  assert probe.probe_errors[0] == "ValueError: fault 0"


# --------------------------------------------------------------------------- #
# DISCOVERY — one pass, and every failure conceded                             #
# --------------------------------------------------------------------------- #


class _Descriptor:
  def __init__(self, key):
    self.key = key


def _target(*keys, broken=False):
  """A minimal ``Target``-shaped class: one ``setting_descriptors`` and nothing else."""
  class _T:
    def setting_descriptors(self):
      if broken:
        raise RuntimeError("this plugin's descriptors do not load")
      return [_Descriptor(k) for k in keys]

  return _T


def _target_breaking_part_way(reached, unreached):
  """As :func:`_target`, but the descriptors are a GENERATOR that raises mid-sequence.

  ⚑ The shape matters: ``setting_descriptors`` is annotated ``list[TargetSetting]``,
  but the probe consumes whatever it is handed, so a plugin that builds its list
  lazily raises AFTER the reader has already seen some of it — ``set.update`` keeps
  what it took. That is the only way to tell "the contribution was merged as it
  arrived" from "it was merged once whole".
  """
  class _T:
    def setting_descriptors(self):
      yield _Descriptor(reached)
      raise RuntimeError(f"descriptors ran out before {unreached}")

  return _T


def test_discovery_is_ONE_pass_supplying_BOTH_halves(clean_probe, monkeypatch):
  """⚑ ONE pass because the leaves and the names are a DEPENDENT PAIR: the leaf set
  is only meaningful for the agents it was read FROM, and two passes could disagree
  about which those are.

  The single memo is also the single priming point — ``_keystore_census`` calls
  ``plugin_agent_leaves()`` at ``pytest_configure``, before any test patches
  discovery, and that one call fixes both halves.
  """
  calls = []

  def fake_discover(project_path=None):
    calls.append(project_path)
    return {"claude": _target("model"), "goose": _target("provider")}

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
  assert probe.plugin_agent_leaves() == frozenset({"model", "provider"})
  assert "claude" in probe.KNOWN_LEAF_AGENTS
  assert "goose" in probe.KNOWN_LEAF_AGENTS
  assert "zzznotinstalled" not in probe.KNOWN_LEAF_AGENTS
  assert len(calls) == 1, "both halves must come from the SAME discovery pass"


def test_discovery_that_will_not_import_concedes_BOTH_halves(clean_probe, monkeypatch):
  """⚑ THE SAFE DIRECTION, and the only one an instrument has.

  An empty leaf set AND an empty agent set together mean "no agent's vocabulary is
  known here", so every agent's leaves are conceded. A plugin that will not import is
  a fact about the environment; refusing to MEASURE because of it is not an option.

  MUTATION: let the exception escape ``_discover`` and every resolve in the process
  raises out of the refusal seam instead.
  """
  def explode(project_path=None):
    raise ImportError("no plugin machinery here")

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", explode)
  assert probe.plugin_agent_leaves() == frozenset()
  assert "claude" not in probe.KNOWN_LEAF_AGENTS
  # …so the whole agent tier is conceded rather than refused wholesale.
  assert probe.keyspace_verdict("agent.claude.zippity").cls is KeyClass.KEY


def test_one_plugins_descriptor_fault_does_not_abort_the_pass(clean_probe, monkeypatch):
  """The inner ``continue``: a plugin whose descriptors raise must not cost the OTHER
  plugins their vocabulary, which is what an un-caught fault would do — the outer
  handler concedes everything.

  ⚑ The faulting agent's OWN standing is the case below; this one is only that the
  blast radius stops at it.
  """
  def fake_discover(project_path=None):
    return {"claude": _target("model"), "goose": _target(broken=True)}

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
  assert probe.plugin_agent_leaves() == frozenset({"model"})


def test_a_plugin_whose_DESCRIPTORS_raise_is_conceded_like_an_ABSENT_one(
  clean_probe, monkeypatch,
):
  """⚑⚑ THE DEPENDENT PAIR IS PER AGENT, NOT MERELY PER PASS.

  A plugin that imports but cannot declare leaves no vocabulary for THAT agent, which
  is the same position an uninstalled plugin leaves it in — and the module's rule for
  a discovery failure is to concede BOTH halves. Recording the name FIRST kept the
  agent in ``KNOWN_LEAF_AGENTS`` with an EMPTY vocabulary, so every leaf it genuinely
  declares classified UNDECLARED: ``agent.goose.provider`` refused for having no
  declared leaves, which is the exact false positive the concession exists to prevent,
  one layer in. ⚑ Withholding the name IS conceding the leaves — the leaf concession
  is the only reader of the agent set.

  ⚑ THE SECOND HALF IS WHAT KEEPS IT A CONCESSION AND NOT A HOLE: the working plugin's
  agent is still judged, so one broken plugin does not disarm §0 for the rest.

  MUTATION: move ``agents.add(name)`` above the descriptor read in ``_discover`` — the
  pre-image — and the first two assertions redden, the goose one naming "not a declared
  agent key" as its cause.
  """
  def fake_discover(project_path=None):
    return {"claude": _target("model"), "goose": _target("provider", broken=True)}

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
  assert "goose" not in probe.KNOWN_LEAF_AGENTS
  assert probe.keyspace_verdict("agent.goose.provider").cls is KeyClass.KEY
  assert "claude" in probe.KNOWN_LEAF_AGENTS
  assert probe.keyspace_verdict("agent.claude.zippity").cls is KeyClass.UNDECLARED


def test_a_HALF_READ_descriptor_list_contributes_NOTHING(clean_probe, monkeypatch):
  """⚑ THE CONCESSION POINTS OUTWARD TOO, and this is the half that is easy to miss.

  The leaf set is a UNION with no per-agent partition, so a leaf salvaged from a plugin
  whose name is then conceded is counted as declared for every OTHER agent — the same
  half-a-pair fault, judging one agent's key against another's vocabulary. Merging the
  contribution only once it is whole is what makes the per-agent concession total.

  MUTATION: consume the descriptors straight into ``leaves`` (``leaves.update(d.key for
  d in ...)``, the pre-image) and both the salvaged leaf and the claude verdict redden.
  """
  def fake_discover(project_path=None):
    return {
      "claude": _target("model"),
      "goose": _target_breaking_part_way("provider", "endpoint"),
    }

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
  # ``provider`` WAS read before the fault; ``endpoint`` never was. Neither survives.
  assert probe.plugin_agent_leaves() == frozenset({"model"})
  assert "goose" not in probe.KNOWN_LEAF_AGENTS
  assert probe.keyspace_verdict("agent.claude.provider").cls is KeyClass.UNDECLARED


# --------------------------------------------------------------------------- #
# …and it is DEFERRED: a resolve pays for discovery only where it must           #
# --------------------------------------------------------------------------- #
#
# ⚑⚑ THIS ORACLE IS ON THE PRODUCTION PATH OF EVERY SETTINGS RESOLVE, disarmed probe
# and all — ``_refuse_undeclared_snapshot`` reads it. Discovery imports and
# instantiates every installed plugin, and those modules parse YAML in their module
# bodies. Passing ``plugin_agent_leaves()`` as a keyword ARGUMENT evaluated all of it
# before ``key_class`` had looked at the head, so the FIRST path judged paid, whatever
# its shape: a cold ``load_merged_config`` on this box judged ``box``, ``box.image``
# and ``box.share_images``, and the discovery pass was attributed to ``box`` — a
# NAMESPACE, which cannot carry an agent leaf at all. 223ms median -> 17ms.
#
# 🛑 THE COST IS INVISIBLE TO EVERY OTHER CASE IN THIS FILE. Re-materialising either
# argument changes no verdict, so nothing else here would red. That is what these
# cases are for.


@pytest.fixture
def counting_discovery(clean_probe, monkeypatch):
  """Discovery replaced by a counter, over a one-plugin machine, starting COLD.

  ⚑ It counts PASSES, not questions: ``_PLUGINS`` starts at ``None``, so the first
  path that asks pays and every later one reads the memo. What is pinned below is
  whether a path asks AT ALL.

  ⚑ ``model`` is a CORE §2d leaf and ``provider`` is not, so the same fake plugin
  serves both directions.
  """
  calls = []

  def fake_discover(project_path=None):
    calls.append(project_path)
    return {"claude": _target("model", "provider")}

  monkeypatch.setattr(probe, "_PLUGINS", None)
  monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
  return calls


@pytest.mark.parametrize("path", [
  "box", "box.image", "box.zippity", "system.template", "workset.boxes",
  "meta.box.path", "meta.agent.claude.zippity", "pref.box.image", "config.data",
  "agent", "agent.claude", "agent.claude.model", "agent.default.access",
])
def test_a_path_the_CORE_keyspace_answers_imports_NO_plugin(path, counting_discovery):
  """⚑ THE DEFERRAL, through the entry point the live consumers actually call.

  Every path here is answerable from constants ``settings_keyspace`` declares, so a
  plugin import to answer it is pure cost — and it was being paid on all of them.
  """
  probe.declared_keyspace_oracle(path)
  assert counting_discovery == [], f"{path} imported the plugins to be judged"


@pytest.mark.parametrize("path", ["agent.claude.provider", "agent.claude.zippity"])
def test_a_leaf_only_a_PLUGIN_can_answer_DOES_import_one(path, counting_discovery):
  """The other direction, and without it the case above passes on an oracle that had
  simply stopped consulting the plugins.

  ``provider`` is a real ``setting_descriptor`` leaf the core §2d table does not
  declare; ``zippity`` is declared by nothing, and refusing it needs BOTH the plugin
  vocabulary and the concession — one memoised pass answers both.
  """
  probe.declared_keyspace_oracle(path)
  assert len(counting_discovery) == 1


def test_plugin_agent_leaves_stays_EAGER_because_it_is_the_PRIMING_POINT(
  counting_discovery,
):
  """🛑 DO NOT MAKE THIS ONE LAZY. ``tests/_keystore_census`` calls it at
  ``pytest_configure``, before any test patches discovery, and that one call is what
  fixes both halves of the memo for the whole session. A deferred version would prime
  nothing and the census would measure whatever the running test had patched in.

  :data:`probe.PLUGIN_LEAVES` is the deferred VIEW, and it exists BESIDE this rather
  than in place of it — asking it anything discovers just the same.
  """
  probe.plugin_agent_leaves()
  assert len(counting_discovery) == 1
  assert "provider" in probe.PLUGIN_LEAVES
  assert len(counting_discovery) == 1, "the deferred view must read the same memo"


# --------------------------------------------------------------------------- #
# The SEAM — off by default, one row per observation, incapable of failing a run
# --------------------------------------------------------------------------- #


def test_the_OFF_tokens_are_exactly_these(clean_probe):
  """⚑ Written out rather than read from the module, which would make the corpus
  self-derived: a token silently added to the set has to be visible as a change
  HERE, since arming the probe in production is what the set decides."""
  assert probe._OFF_TOKENS == frozenset({"", "0", "off", "false", "no"})


@pytest.mark.parametrize("token", ["", "0", "off", "false", "no", "OFF", " no ", "FaLsE"])
def test_the_probe_is_OFF_for_an_off_token(clean_probe, monkeypatch, token):
  """Case-folded and stripped, so ``KANI_KEYSPACE_PROBE=' Off '`` does not arm it."""
  monkeypatch.setenv(probe.ENV_FLAG, token)
  assert probe.probe_enabled() is False


def test_the_probe_is_OFF_when_the_flag_is_UNSET(clean_probe, monkeypatch):
  """OFF BY DEFAULT — the inverse of the write census, which judges test code only.
  This one runs inside production launches."""
  monkeypatch.delenv(probe.ENV_FLAG, raising=False)
  assert probe.probe_enabled() is False


@pytest.mark.parametrize("token", ["1", "2", "on", "yes", "true", "anything"])
def test_anything_else_ARMS_it(clean_probe, monkeypatch, token):
  """⚑ INCLUDING ``1``: the set above is what leaves it off, and everything else —
  not a whitelist of on-tokens — arms it."""
  monkeypatch.setenv(probe.ENV_FLAG, token)
  assert probe.probe_enabled() is True


def test_a_disarmed_probe_never_touches_the_store_or_the_file(
  clean_probe, monkeypatch, tmp_path,
):
  """⚑ PROPERTY 1. Disarmed, ``observe`` returns before it reads anything — so a
  store that cannot be walked at all costs a production launch nothing.

  MUTATION: move the ``probe_enabled`` guard below the walk and this raises rather
  than reddening quietly, which is the point of asserting on the unwalkable store.
  """
  rows = tmp_path / "probe.jsonl"
  monkeypatch.delenv(probe.ENV_FLAG, raising=False)
  monkeypatch.setenv(probe.ENV_FILE, str(rows))
  probe.observe(object(), origin="test")
  assert not rows.exists()
  assert probe.probe_errors == []


def test_ONE_row_PER_OBSERVATION_including_a_clean_one(clean_probe, monkeypatch, tmp_path):
  """⚑ A CLEAN OBSERVATION IS STILL A ROW, and that is what makes an empty run
  legible: without it, "the seam resolved a hundred clean snapshots" and "the seam
  was never reached" produce the same number by two opposite routes.

  ⚑ APPEND, never truncate — the chunked runner is one process PER FILE, so a
  whole-suite probe is the union of many processes' rows.
  """
  rows = _armed(monkeypatch, tmp_path)
  store = KeyStore({"box": {"image": "x"}})
  probe.observe(store, origin="first")
  probe.observe(store, origin="second")
  written = _rows(rows)
  assert [row["origin"] for row in written] == ["first", "second"]
  assert [row["count"] for row in written] == [0, 0]
  assert [row["undeclared"] for row in written] == [[], []]


def test_a_row_names_its_ORIGIN_and_the_RUNNING_TEST(clean_probe, monkeypatch, tmp_path):
  """*origin* names the seam that produced the store, so a row is attributable
  without a frame walk — and the test name is READ FROM THE ENVIRONMENT rather than
  derived, precisely because a measurement must not reach into its caller.

  The sentinel proves the read: a derived value could not produce it.
  """
  rows = _armed(monkeypatch, tmp_path)
  monkeypatch.setenv("PYTEST_CURRENT_TEST", "a::sentinel::value (call)")
  probe.observe(KeyStore({"box": {"image": "x"}}), origin="build_launch_snapshot")
  row = _rows(rows)[-1]
  assert row["origin"] == "build_launch_snapshot"
  assert row["test"] == "a::sentinel::value (call)"


@pytest.mark.writes_undeclared(
  "box.zippity",
  reason="a FINDING row only exists where the store carries an undeclared path, so "
         "the case that pins the row's contents has to write one.",
)
def test_a_FINDING_row_carries_the_path_and_the_REASON(clean_probe, monkeypatch, tmp_path):
  """⚑ PROPERTY 3: it reports PATHS, never a verdict about them. The classification
  is ``settings_keyspace``'s — one carrier — and what this module supplies is the
  oracle, the seam and the file.

  ⚑ The scope NODE above the finding is NOT reported: ``box`` is a declared
  NAMESPACE carrying a node, which the container rule rescues. A row naming it would
  mean the rescue had stopped being applied.
  """
  rows = _armed(monkeypatch, tmp_path)
  probe.observe(KeyStore({"box": {"zippity": "wibble"}}), origin="test")
  row = _rows(rows)[-1]
  assert row["count"] == 1
  assert [finding["path"] for finding in row["undeclared"]] == ["box.zippity"]
  assert "not a declared box key" in row["undeclared"][0]["note"]


def test_an_unwritable_row_FILE_cannot_fail_a_run(clean_probe, monkeypatch, tmp_path):
  """⚑ PROPERTY 2. An instrument that can break the thing it measures produces no
  measurement — so a full disk, or in this case a path that is a DIRECTORY, is
  counted and swallowed."""
  monkeypatch.setenv(probe.ENV_FLAG, "1")
  monkeypatch.setenv(probe.ENV_FILE, str(tmp_path))
  probe.observe(KeyStore({"box": {"image": "x"}}), origin="test")
  assert len(probe.probe_errors) == 1
  assert "Error" in probe.probe_errors[0]


def test_a_store_the_walk_cannot_read_cannot_fail_a_run(clean_probe, monkeypatch, tmp_path):
  """The same property from the other side: a malformed store is counted, not raised.

  MUTATION: narrow ``observe``'s ``except`` to ``OSError`` and this raises.
  """
  rows = _armed(monkeypatch, tmp_path)
  probe.observe(object(), origin="test")
  assert len(probe.probe_errors) == 1
  assert not rows.exists(), "a failed observation writes no row"


def test_the_row_file_defaults_and_can_be_overridden(clean_probe, monkeypatch):
  """The default is under ``/tmp`` so an unconfigured run writes somewhere bounded."""
  monkeypatch.delenv(probe.ENV_FILE, raising=False)
  assert probe._probe_file() == probe.DEFAULT_PROBE_FILE
  assert probe.DEFAULT_PROBE_FILE.startswith("/tmp/")
  monkeypatch.setenv(probe.ENV_FILE, "")
  assert probe._probe_file() == probe.DEFAULT_PROBE_FILE, "an empty value is not a path"
  monkeypatch.setenv(probe.ENV_FILE, "/tmp/elsewhere.jsonl")
  assert probe._probe_file() == "/tmp/elsewhere.jsonl"
