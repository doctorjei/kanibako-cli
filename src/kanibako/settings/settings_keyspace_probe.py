"""REPORT-ONLY probe: which UNDECLARED keys reach a resolved snapshot.

Spec §0 (``settings-keyspace-1.8.0.md`` :245): *"reading, setting, or RESOLVING an
undeclared key is an ERROR that NAMES the offending key — never a silent accept,
never a fabricated default, never a free-form passthrough."* A ``box.yaml`` carrying
``box: {zippity: wibble}`` used to go through ``assemble_levels`` and ``merge`` and
resolve to ``'wibble'`` with no error and no warning, because ``settings_assemble``
never asks :func:`~kanibako.settings.settings_keyspace.key_class` anything.

⚑⚑ THIS MODULE STILL DOES NOT CLOSE THAT GAP, AND MUST NOT BE MADE TO.
:func:`~kanibako.settings.settings_launch.build_launch_snapshot` sits behind
``load_merged_config``, whose callers are ``diagnose`` · ``commands/box/_parser`` ·
``baseline_cmd`` · ``image`` · ``code_cmd`` · ``setup_cmd`` · ``start`` ·
``config_interface`` — so a raise at the resolve seam refuses nearly every kanibako
command, not just ``start``. That was a decision with an owner, it was taken on the
number this module produced, and what enforces it is
``settings_launch._refuse_undeclared_snapshot``: a SIBLING call, one line after
:func:`observe`, never a mode of this module. ⚑ THE ORDER IS LOAD-BEARING — the
instrument records the observation and the refusal fires after it, because a raise
placed first would blind the probe to precisely the resolves that matter.

What the refusal arms is EXACTLY what was measured, by reading the oracle below
(:func:`keyspace_verdict`) rather than building a second one. That is why this
module is on the production path with the probe DISARMED, and why a change to the
oracle is a change to what kanibako refuses.

THREE PROPERTIES, and all three are load-bearing:

1. **OFF by default.** Unset :data:`ENV_FLAG` and :func:`observe` returns before it
   touches the store.
2. **INCAPABLE of failing a run.** Every path through :func:`observe` is wrapped: an
   oracle fault, a full disk or a malformed store is COUNTED in :data:`probe_errors`
   and swallowed. An instrument that can break the thing it measures produces no
   measurement.
3. **It reports PATHS, never a verdict about them.** The classification is
   ``settings_keyspace``'s (one carrier); this module supplies the oracle, the seam
   and the file.

THE ORACLE IS DELIBERATELY PERMISSIVE ABOUT AGENT NAMES. ``valid_agents`` is
injected into ``key_class`` for purity, and both production and the test
environment invent agent names freely (personas, ``myagent``, ``testagent``). A
narrow set would manufacture rows saying "'x' is not a valid agent", which is a
finding about a DISCRIMINATOR, not about the keyspace.

⚑⚑ AND THE CONCESSION REACHES THAT AGENT'S LEAVES, because the two are a DEPENDENT
PAIR. An agent's leaf vocabulary is its PLUGIN's (§0: *"Agent specifics are
PLUGIN-declared"*), so where the plugin is not installed there is no vocabulary to
judge against — and ``agent.goose.provider``, a real goose ``setting_descriptor``
leaf, was refused as "not a declared agent key" on a claude-only machine. Conceding
the name while judging the leaves is conceding half a pair, and it produces a false
positive on a key that IS declared. :data:`KNOWN_LEAF_AGENTS` draws the line: an
agent this machine CAN see is judged exactly as before, a persona is judged by its
HARNESS, and ``agent.default`` is judged always — the all-agents tier is core's, not
a plugin's, and ``key_class`` holds that rule rather than any supplier.

⚑⚑ THE CONCESSION IS ASKED IN TWO STEPS AND THE FIRST ONE IS THE KEYSPACE'S. Before
``KNOWN_LEAF_AGENTS`` is consulted at all, ``key_class`` asks whether the segment
COULD name an agent (``_could_name_an_agent``): a §2a category token and ``default``
could not, so neither is ever conceded. Asking only "is it installed" made
``agent.common.plugins`` — the undiscriminated relic MIGRATION.md §2.11 tells users
to grep for — resolve as a KEY, because no plugin declares an agent named ``common``.
🛑 THE RESIDUAL COST IS STATED, NOT HIDDEN, and it is IRREDUCIBLE: an agent NAME
cannot be enumerated, so ``agent.goose.zippity`` resolves on a machine without goose
(the price of not refusing ``agent.goose.provider`` there), and so does a typo'd
``agent.clade.zippity`` — ``clade`` is exactly the shape an uninstalled harness has.
Where goose IS installed, ``zippity`` refuses like anything else.
``meta.agent.<agent>.*`` is untouched: its vocabulary is core-declared, so it is
knowable either way.

⚑ The pytest write census (``tests/_keystore_census.py``) reads its oracle FROM HERE
rather than keeping its own — same question, one answer.
"""

from __future__ import annotations

import json
import os
from typing import Any, Collection, Container, Final, Iterator, NamedTuple

from kanibako.agent_ref import harness_of
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import (
  KeyClass,
  KeyJudgement,
  key_class,
  render_store_path,
  undeclared_store_paths,
)

#: Names the probe. UNSET (or an :data:`_OFF_TOKENS` member) means OFF — the inverse
#: of the write census, which is default-ON because it judges test code only. This
#: one runs inside production launches.
ENV_FLAG: Final[str] = "KANI_KEYSPACE_PROBE"

#: The values that leave it off. Anything else — including ``1`` — arms it.
_OFF_TOKENS: Final[frozenset[str]] = frozenset({"", "0", "off", "false", "no"})

#: Where the JSONL rows are appended. ⚑ APPEND, never truncate: the chunked test
#: runner is one process PER FILE, so a whole-suite probe is the union of many
#: processes' rows.
ENV_FILE: Final[str] = "KANI_KEYSPACE_PROBE_FILE"
DEFAULT_PROBE_FILE: Final[str] = "/tmp/kanibako-keyspace-probe.jsonl"

#: Faults the probe swallowed, kept so a run can say the instrument misbehaved
#: rather than silently reporting a clean sheet it never measured. ⚑ An ORACLE
#: fault lands here even with the probe disarmed, because the refusal reads the same
#: oracle — the fault is still swallowed there, but it is swallowed INTO a refusal
#: naming the exception, never into a pass.
probe_errors: list[str] = []


### The oracle ###

class _AnyAgent(Collection[str]):
  """A ``valid_agents`` that accepts every discriminator (see the module doc).

  ``__iter__`` / ``__len__`` exist only because ``_bad_agent_reason`` renders the
  set — a path this can never reach.
  """

  def __contains__(self, item: object) -> bool:
    return True

  def __iter__(self) -> Iterator[str]:
    return iter(("claude", "codex", "goose"))

  def __len__(self) -> int:
    return 3


ANY_AGENT: Final[_AnyAgent] = _AnyAgent()


class _Plugins(NamedTuple):
  """What ONE discovery pass tells the oracle: the leaves, and whose they are."""

  leaves: frozenset[str]
  agents: frozenset[str]


_PLUGINS: _Plugins | None = None


def _discover() -> _Plugins:
  """ONE discovery pass, memoised — the leaves the plugins declare AND their names.

  ⚑ ONE pass for both, because they are a DEPENDENT PAIR: the leaf set is only
  meaningful for the agents it was read from, and two passes could disagree about
  which those are. The single memo is also a single priming point — the pytest
  census calls :func:`plugin_agent_leaves` before any test patches discovery, and
  that one call fixes both halves.

  ⚑ Discovered here rather than through ``settings_prefs.default_valid_agents`` on
  purpose: that supplier MEMOIZES into a process-wide cache the production code
  reads, so priming it from a probe would hand every later caller a discovery result
  computed before its own patches were in place.
  ⚑ Every failure is conceded — to an empty leaf set AND an empty agent set, which
  together mean "no agent's vocabulary is known here", the safe direction. A plugin
  that will not import is a fact about the environment; refusing to measure because
  of it is not an option an instrument has.
  """
  global _PLUGINS
  if _PLUGINS is not None:
    return _PLUGINS
  leaves: set[str] = set()
  agents: set[str] = set()
  try:
    from kanibako.targets import discover_targets

    for name, target_cls in discover_targets().items():
      agents.add(name)
      try:
        leaves.update(d.key for d in target_cls().setting_descriptors())
      except Exception:
        continue
  except Exception:
    pass
  _PLUGINS = _Plugins(frozenset(leaves), frozenset(agents))
  return _PLUGINS


def plugin_agent_leaves() -> frozenset[str]:
  """PLUGIN-declared agent keys, to union over the core §2d set (spec §0)."""
  return _discover().leaves


class _KnownLeafAgents(Container[str]):
  """The agents whose LEAF VOCABULARY this machine can actually answer for.

  ⚑ ANSWERED BY HARNESS, not by node name: ``persona℘claude`` takes its leaves from
  ``claude``, so asking about the node would concede every persona on the machine.
  Membership is asked and never enumerated — which is why this is a ``Container`` —
  because the persona set is open.
  ⚑ ``default`` is NOT special-cased here. ``key_class`` never asks about it: the
  all-agents tier is core's, so its standing belongs to the keyspace and not to
  whoever supplies this.
  """

  def __contains__(self, item: object) -> bool:
    name = item if isinstance(item, str) else str(item)
    return harness_of(name) in _discover().agents


KNOWN_LEAF_AGENTS: Final[_KnownLeafAgents] = _KnownLeafAgents()


def declared_keyspace_oracle(path: str) -> KeyJudgement:
  """*path*'s ``KeyClass`` — KEY, declared NAMESPACE, or UNDECLARED.

  ⚑ ALL THREE, never the key-or-not view: the classifier's only other way to tell a
  declared interior from a fabrication is to count segments, and that reports every
  namespace below depth 1 as a violation.

  ⚑ THE DISCRIMINATOR AND ITS LEAVES ARE CONCEDED TOGETHER (see the module doc).
  ``ANY_AGENT`` concedes the agent NAME because this machine's plugin set is not the
  keyspace; :data:`KNOWN_LEAF_AGENTS` then concedes that agent's LEAVES for the same
  reason, since the vocabulary a leaf is judged against is the very plugin that is
  missing.
  """
  return key_class(
    path,
    valid_agents=ANY_AGENT,
    agent_leaves=plugin_agent_leaves(),
    agents_with_known_leaves=KNOWN_LEAF_AGENTS,
  )


#: Verdict per distinct dotted prefix. The prefix walk asks about every proper prefix
#: of every path, and prefixes repeat heavily across one store.
_verdicts: dict[str, KeyJudgement] = {}


def keyspace_verdict(path: str) -> KeyJudgement:
  """THE oracle, memoised: what the closed keyspace says *path* is.

  PUBLIC because two consumers must not answer this question twice.
  :func:`observe` REPORTS on it and ``settings_launch._refuse_undeclared_snapshot``
  REFUSES on it, and a refusal armed on a second oracle would refuse something
  other than what was measured. ⚑ The memo is process-wide on purpose: the prefix
  walk asks about every proper prefix of every path, and a launch resolves many.
  """
  if path not in _verdicts:
    try:
      _verdicts[path] = declared_keyspace_oracle(path)
    except Exception as exc:  # pragma: no cover - an oracle fault is not a failure
      # ⚑ UNDECLARED, not a silent pass: a classifier that cannot judge a path must
      # SAY so — in the row, and in the refusal, which prints this note. Conceding
      # the path instead would let a broken oracle green-light §0.
      _verdicts[path] = KeyJudgement(
        KeyClass.UNDECLARED, f"<oracle raised {type(exc).__name__}: {exc}>",
      )
      _note_error(exc)
  return _verdicts[path]


### The seam ###

def probe_enabled() -> bool:
  """Is the probe armed? OFF unless :data:`ENV_FLAG` says otherwise."""
  return os.environ.get(ENV_FLAG, "").strip().lower() not in _OFF_TOKENS


def _note_error(exc: BaseException) -> None:
  if len(probe_errors) < 50:
    probe_errors.append(f"{type(exc).__name__}: {exc}")


def _probe_file() -> str:
  return os.environ.get(ENV_FILE) or DEFAULT_PROBE_FILE


def observe(store: KeyStore[Any], *, origin: str) -> None:
  """Record the undeclared paths in *store*. NO-OP when disarmed; never raises.

  *origin* names the seam that produced *store*, so a row can be attributed without
  a frame walk. Under pytest the row also carries ``PYTEST_CURRENT_TEST``, which is
  what makes "which test files surface them" answerable — and it is READ from the
  environment rather than derived, precisely because a measurement must not reach
  into its caller.

  ⚑ ONE ROW PER OBSERVATION, INCLUDING A CLEAN ONE. A probe that wrote only findings
  could not tell "the seam resolved a hundred clean snapshots" from "the seam was
  never reached", and those two produce the same number by two opposite routes. The
  row carries the count, so an empty run is visibly empty rather than silently good.
  """
  if not probe_enabled():
    return
  try:
    findings = undeclared_store_paths(store, oracle=keyspace_verdict)
    row = {
      "origin": origin,
      "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
      "count": len(findings),
      "undeclared": [
        {
          "path": render_store_path(segments, judgement.key_len),
          "note": judgement.note,
        }
        for segments, judgement in findings
      ],
    }
    with open(_probe_file(), "a", encoding="utf-8") as fh:
      fh.write(json.dumps(row) + "\n")
  except Exception as exc:  # pragma: no cover - the probe must never fail a run
    _note_error(exc)
