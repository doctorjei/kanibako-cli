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
finding about a DISCRIMINATOR, not about the keyspace. The discriminator is conceded;
the LEAF still has to be declared. ⚑ The pytest write census
(``tests/_keystore_census.py``) reads its oracle FROM HERE rather than keeping its
own — same question, one answer.
"""

from __future__ import annotations

import json
import os
from typing import Any, Collection, Final, Iterator

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

_LEAVES: frozenset[str] | None = None


def plugin_agent_leaves() -> frozenset[str]:
  """PLUGIN-declared agent keys, to union over the core §2d set (spec §0).

  ⚑ Discovered here rather than through ``settings_prefs.default_valid_agents`` on
  purpose: that supplier MEMOIZES into a process-wide cache the production code
  reads, so priming it from a probe would hand every later caller a discovery result
  computed before its own patches were in place.
  ⚑ Every failure is conceded to an empty set. A plugin that will not import is a
  fact about the environment; refusing to measure because of it is not an option an
  instrument has.
  """
  global _LEAVES
  if _LEAVES is not None:
    return _LEAVES
  leaves: set[str] = set()
  try:
    from kanibako.targets import discover_targets

    for target_cls in discover_targets().values():
      try:
        leaves.update(d.key for d in target_cls().setting_descriptors())
      except Exception:
        continue
  except Exception:
    pass
  _LEAVES = frozenset(leaves)
  return _LEAVES


def declared_keyspace_oracle(path: str) -> KeyJudgement:
  """*path*'s ``KeyClass`` — KEY, declared NAMESPACE, or UNDECLARED.

  ⚑ ALL THREE, never the key-or-not view: the classifier's only other way to tell a
  declared interior from a fabrication is to count segments, and that reports every
  namespace below depth 1 as a violation.
  """
  return key_class(
    path, valid_agents=ANY_AGENT, agent_leaves=plugin_agent_leaves(),
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
