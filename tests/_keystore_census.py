"""KeyStore write census + ENFORCEMENT: every key written to a store is judged, & a
fabricated one FAILS THE SESSION.

WHAT THIS IS FOR.  ``key_validity`` refuses an undeclared key at the BOUNDARIES
(``config set``, the file partials, the launch seam).  Nothing catches a key
FABRICATED INTERNALLY that reaches a resolved store without crossing one.  This
plugin comes between the store and the code that writes it: it patches the single
write funnel, records every path written during the session, judges each one
against a pluggable declared-keyspace oracle, and at session end REPORTS every
unapproved key with the line that set it and FAILS THE RUN.

DEFAULT-ON.  The keyspace is CLOSED (spec §0), so an undeclared key is a defect
whether or not anyone remembered to arm a flag.  Set ``KANI_KEYSTORE_CENSUS`` to
``0`` (or ``off``/``false``/``no``) to opt OUT -- the only reason to is bisecting
the census itself.  When on, the patch is installed at ``pytest_configure`` and
REMOVED again at the end of the session; the class is never left patched.
MEASURED COST, whole suite through the chunked runner, same tree, same 162 files:
**399.7 s on vs 325.0 s off** (+23%, ~0.46 s per file), with IDENTICAL outcomes --
``ok=162 fail=5 memkill=0``, 7892 passed, 7 skipped, both ways.  It is not free;
it is cheap, and a run that cannot be trusted about the keyspace is worth less
than 75 s.

--------------------------------------------------------------------------------
WHAT COUNTS AS A VIOLATION -- the half of this that is adjudication, not mechanism
--------------------------------------------------------------------------------

A resolved store legitimately holds a great deal that is NOT a key, and a collector
that flagged all of it would report noise.  Every recorded write lands in exactly
one class (:class:`Verdict`), and only ``UNDECLARED`` fails the run.  ⚑⚑ EVERY
CLASS BELOW IS STRUCTURAL -- decided by the path's SHAPE or by a constant the
SPEC sanctions.  There is no list of blessed key names here, and none may be
added: a name-keyed exemption is exactly the carve-out that hides the next
finding behind it.

``DECLARED``   the oracle accepts the whole path.  A key.
``VALUE``      a proper PREFIX is a declared key, so the rest addresses a VALUE
               INSIDE it -- ``box.caches``'s destinations, ``box.masks``'s entries,
               a ``bindings`` arm's map.  Structure, not a key of its own.
``DATA_SEGMENT`` a segment CONTAINS A DOT, so the path cannot be a key path at all
               (see the rendering note below).
``CONTAINER``  an intermediate NODE that carries declared keys underneath it
               (``box``, ``meta.box``, ``box.bindings``).  Scaffolding.
``UNROOTED``   the root segment is not a keyspace root, so this is a scope-LOCAL or
               fragment store whose key path the collector cannot know.  A fragment
               that ever reaches a real store is re-censused THERE, with its true
               path, so nothing is lost by declining to guess.
``RESERVED``   the RESERVED INTERNAL NODE the spec names in so many words: *"The
               derivation is NOT a key -- it is a RESERVED INTERNAL NODE ... rides
               inside the per-launch snapshot as an internal node named
               `binding_derivations`"* (spec §0, ABSTRACT declarations).  Keyed off
               ``kb_store.BINDING_DERIVATIONS_NODE``, the constant that declares it
               -- not off a copy of the name.
``NEGATIVE``   the RUNNING TEST DECLARED IT (see the marker below).  A test that
               exercises the refusal of an undeclared key has to write one; that is
               its point.
``UNDECLARED`` none of the above: something put a key into a store that the keyspace
               does not declare.  THE RUN FAILS.

⚑⚑ THE ``writes_undeclared`` MARKER -- intent declared AT THE TEST, never here.
A test that drives a refusal names the exact paths it will write::

    @pytest.mark.writes_undeclared(
      "box.meta", "box.meta.mode",
      reason="the nested <scope>.meta ride-through: _drop_upward_scopes looks at "
             "the TOP-LEVEL view only, so the nested node must survive un-dropped.",
    )

Three properties make this a declaration rather than a blessing, and all three
matter:
1. It is scoped to ONE test.  The same path written by anything else still reds.
2. It is EXACT.  Any OTHER undeclared path that test writes still reds.
3. It MUST CHANGE A VERDICT.  A declared path that never turns a violation into a
   ``NEGATIVE`` -- because the test stopped writing it, because the key became
   declared, or because it was scaffolding all along -- FAILS THE RUN, so a marker
   cannot quietly outlive its reason.

⚑ THERE IS NO ORIGIN DISCRIMINATOR.  An earlier revision excused a key by WHERE it
was written from (a test frame, the file-partial parser), matched by code object.
That excused two whole classes of real violation -- ~44 synthetic fixture keys and
``meta.workset.{created,projects}`` -- and its frame walk was the fragile part of
the machinery.  A key is judged by WHAT IT IS.  A test that means to write one says
so; a settings FILE carrying an undeclared key is a defect in the fixture that
wrote the file, and is now reported as one.

⚑⚑ THE COLLECTOR MUST NOT RENDER NON-KEY DATA AS A DOTTED KEY.  A destination is
DATA and NEVER appears in a dotted key: ``KeyStore.insert_segments`` takes each
segment verbatim and its own comment names this case.  So the prefix walk STOPS at
the first segment containing a dot -- the oracle is never handed ``box.caches.~/
.cache/uv`` -- and :func:`render` pipe-joins a path with such a segment instead of
dot-joining it, so nothing here can be misread as a key.  (⚑ The one live question
this leaves OPEN is whether an ``env.<VAR>`` NAME may contain a dot: ``ENV_KEY_RE``
forbids it, the persona path never checks, and ``test_settings_launch.py`` pins
``env.WEIRD.VAR`` surviving as ONE literal leaf.  Unruled -- so those rows land in
``DATA_SEGMENT``, are named in the report, and do NOT fail the run.)

--------------------------------------------------------------------------------
HOW A NESTED NODE LEARNS ITS PATH
--------------------------------------------------------------------------------

``KeyStore.__setattr__`` routes DUNDER names to ``object.__setattr__`` and
everything else to ``self[name]``, so an attribute spelled ``__kani_path__`` is a
real attribute that can never become a key.  That is the tag.  It holds a
one-element LIST (a "path box") whose content is a TUPLE OF SEGMENTS, not a dotted
string: a dest-keyed segment is routinely a filesystem path with dots in it, so a
dotted string loses the very segment boundaries the classification depends on.  The
box is a box, rather than the tuple itself, because a node's children are written
BEFORE the node is attached to its parent::

    KeyStore({"box": {"workspace": "/w"}})
    # inner node's `workspace` write happens inside __wrap, i.e. BEFORE the outer
    # `box` write that would tell the inner node it is `box`.

Recording the box rather than the resolved string lets a later attachment fix the
path of writes already recorded.  ``_tag`` rewrites a node's box and recurses, so
attaching a pre-built subtree corrects its whole descendant set retroactively.
(An identity-keyed side table is not an option: a ``KeyStore`` is a ``dict`` and
therefore unhashable.)

KNOWN BLIND SPOTS, stated rather than papered over:
- ``dict.update(store, ...)`` and other C-level ``dict`` mutators do NOT route
  through ``__setitem__`` and are invisible here.  So is ``dict.__setitem__``
  called unbound.
- A ``writes_undeclared`` marker is matched against the write's RESOLVED path, so
  a marker on a test whose write is re-tagged to a different path later in the
  session goes unused and reds -- deliberately.
- ``packages/*/tests`` do not load ``tests/conftest.py``, so plugin-side writes
  are not censused unless this plugin is registered there too.
- A node inserted at two paths keeps the tag of the LAST attachment.
- A fabricated key whose ROOT segment is itself invented lands in ``UNROOTED`` and
  is reported, not enforced -- the keyspace roots are a closed set and nothing in
  the tree writes a new one, but the collector cannot prove that from one write.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Collection, Iterator
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from kanibako.settings import keystore as _keystore_mod
from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, SCOPE_CONTAINMENT
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import key_validity

if TYPE_CHECKING:  # pragma: no cover - typing only
  import pytest

#: The census is ON unless this names one of :data:`_OFF_TOKENS`.
ENV_FLAG = "KANI_KEYSTORE_CENSUS"

#: The values that opt OUT.  Anything else -- including unset -- is ON.
_OFF_TOKENS = frozenset({"0", "off", "false", "no", ""})

#: The marker a test uses to DECLARE the undeclared paths it will write on purpose.
NEGATIVE_MARKER = "writes_undeclared"

#: Where the JSONL rows are appended.  Override with ``KANI_KEYSTORE_CENSUS_FILE``.
#: ⚑ APPEND, never truncate: the chunked runner is one pytest process PER FILE, so
#: a census of the whole suite is the union of many sessions' rows.
ENV_FILE = "KANI_KEYSTORE_CENSUS_FILE"
DEFAULT_CENSUS_FILE = "/tmp/kanibako-keystore-census.jsonl"

#: The dunder tag holding a node's path box.  Dunder => never storable as a key.
PATH_TAG = "__kani_path__"

#: Cap on ``_tag`` recursion; a self-referential store would otherwise not terminate.
_MAX_TAG_DEPTH = 64

#: The tokens a KEYSPACE path may start with: the four scopes (``kb_store``'s own
#: containment order), the three non-scope namespaces, and the reserved internal
#: node -- included so a ``binding_derivations`` write is recognised as the node
#: the SPEC names (``Verdict.RESERVED``) rather than dismissed as an unrooted
#: fragment the collector could not place.
KEYSPACE_ROOTS: frozenset[str] = (
  frozenset(SCOPE_CONTAINMENT) | {"config", "meta", "pref", BINDING_DERIVATIONS_NODE}
)


### The oracle — reached through a substitutable seam ###

class _AnyAgent(Collection[str]):
  """A ``valid_agents`` that accepts every discriminator.

  DELIBERATELY PERMISSIVE.  ``valid_agents`` is injected for purity, and the test
  environment invents agent names freely ("myagent", "testagent", persona refs).
  A narrow set would manufacture rows that say "'x' is not a valid agent" about
  fixtures, which is noise about the FIXTURE, not a finding about the keyspace.
  The census is here to find fabricated KEYS, so the discriminator is conceded and
  the LEAF still has to be declared.  ``__iter__``/``__len__`` exist only because
  ``_bad_agent_reason`` renders the set — a path this can never reach.
  """

  def __contains__(self, item: object) -> bool:
    return True

  def __iter__(self) -> Iterator[str]:
    return iter(("claude", "codex", "goose"))

  def __len__(self) -> int:
    return 3


ANY_AGENT = _AnyAgent()

#: Plugin-declared agent leaves, discovered ONCE at install (see ``_agent_leaves``).
_LEAVES: frozenset[str] | None = None


def _agent_leaves() -> frozenset[str]:
  """PLUGIN-declared agent keys, to union over the core §2d set.

  ⚑ Discovered here rather than through ``settings_prefs.default_valid_agents``
  on purpose: that supplier MEMOIZES into a process-wide cache the production code
  reads, so priming it from a test plugin would hand every later test a discovery
  result computed before its own patches were in place.
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


def declared_keyspace_oracle(path: str) -> str | None:
  """``None`` when *path* is a declared key, else the REASON it is not."""
  return key_validity(path, valid_agents=ANY_AGENT, agent_leaves=_agent_leaves())


#: THE ORACLE SEAM.  The collector below never names ``key_validity`` itself, so an
#: oracle can be substituted (:func:`set_oracle`) without reaching into the
#: classifier.  DECOUPLING is the whole of its value -- it is not a migration
#: waiting to happen.
#: 🛑🛑 A MANIFEST-SOURCED ORACLE IS **NO-GO**, DECIDED 2026-08-23 ON MEASUREMENT.
#: The swap would make the census MORE PERMISSIVE, and ⚑ THE FAILURE IS SILENT: a
#: too-permissive oracle reports "0 undeclared" instead of going red.  Two reasons
#: carry it.  (a) ``policy.parametric_expansion`` in
#: ``src/kanibako/data/keyspace-manifest.yaml`` is THREE ENGLISH SENTENCES in a YAML
#: list, so a manifest oracle is a hand-written interpreter of prose -- a paraphrase
#: of ``key_validity``, not an independent source.  (b) the generic
#: ``agent.<agent>.<key>`` row read as a literal wildcard accepts EVERY agent leaf,
#: retired ones included, silently disabling the arm where plugin code writes most.
#: Full reasoning, and the measurements behind it:
#: ``~/canon/workbook/plans/2026-08-23-part3-manifest-enforcer.md`` (its NO-GO
#: section).  What was built instead asserts code ← manifest without touching this
#: oracle: the family half of key-set conformance in
#: ``tests/test_settings/test_manifest_enforces.py``.
_oracle: Callable[[str], str | None] = declared_keyspace_oracle


def set_oracle(fn: Callable[[str], str | None]) -> None:
  """Substitute the declared-keyspace oracle (see THE ORACLE SEAM above)."""
  global _oracle
  _oracle = fn


### Classification ###

class Verdict:
  """The classes a recorded write can land in; only ``UNDECLARED`` fails a run."""
  DECLARED = "DECLARED"
  VALUE = "VALUE"
  DATA_SEGMENT = "DATA_SEGMENT"
  CONTAINER = "CONTAINER"
  UNROOTED = "UNROOTED"
  RESERVED = "RESERVED"
  NEGATIVE = "NEGATIVE"
  UNDECLARED = "UNDECLARED"


#: The one non-key node the SPEC names, quoted at :data:`Verdict.RESERVED`.  Sourced
#: from the declaring constant so a rename cannot leave a stale spelling behind.
_RESERVED_REASON = (
  f"{BINDING_DERIVATIONS_NODE!r} is the RESERVED INTERNAL NODE the spec names in "
  f"so many words (§0, ABSTRACT declarations): the materialised binding an abstract "
  f"declaration derives is machinery output, not a settable surface. Its interior "
  f"is declaration keys and box DESTINATIONS, which are data."
)


class Judgement(NamedTuple):
  """One write's class, the declared key it sits in, & where the DATA starts."""
  verdict: str
  key: str
  key_len: int  # how many leading segments are key path; the rest is DATA
  note: str


def render(segments: Collection[str], key_len: int | None = None) -> str:
  """A display path that NEVER renders non-key data as a dotted key.

  ⚑⚑ THE COLLECTOR'S JOB, not a cosmetic one. A destination is DATA and never
  appears in a dotted key, so the *key_len* leading segments are dot-joined and
  everything after them is PIPE-joined inside ``⟨⟩`` — visibly not key path. With
  no *key_len* the whole path is key path, and even then a segment carrying a dot
  forces the pipe form rather than forging a segment boundary that is not there.
  """
  parts = list(segments)
  if key_len is None or key_len >= len(parts):
    return " | ".join(parts) if any("." in s for s in parts) else ".".join(parts)
  head = ".".join(parts[:key_len])
  tail = " | ".join(parts[key_len:])
  return f"{head} ⟨{tail}⟩" if head else f"⟨{tail}⟩"


### Collector state ###

#: Un-drained writes, keyed by ``(id(box), key, file, line)`` so a hot write site
#: collapses to one entry instead of one per call.  The box is held in the VALUE,
#: which is what keeps its ``id`` from being recycled under us.
_pending: dict[tuple[int, str, str, str], list[Any]] = {}

#: The paths the RUNNING test declared it would write, captured AT WRITE TIME (not
#: at drain): a drain happens in teardown, by which point the item may have moved on.
_current_negative: frozenset[str] = frozenset()

#: Every path any ``writes_undeclared`` marker declared this session, and the subset
#: actually written.  The difference FAILS the run -- that is what stops a marker
#: outliving its reason.
_declared_negative: set[str] = set()
_hit_negative: set[str] = set()

#: Drained rows: segments -> row.  EVERY distinct path written, judged or not — the
#: clean ones are the census's denominator, and they are what tells the CONTAINER
#: rule whether a flagged NODE carries anything real underneath it.
_rows: dict[tuple[str, ...], dict[str, Any]] = {}

#: dotted prefix -> oracle verdict, so the oracle runs once per distinct prefix.
_verdicts: dict[str, str | None] = {}

#: Writes the collector itself failed on.  A census bug must not red the suite for
#: the WRONG reason, so the failure is COUNTED and reported rather than raised.
_collector_errors: list[str] = []

_original_setitem: Callable[..., None] | None = None
_finalized = False

_KEYSTORE_FILE = os.path.abspath(_keystore_mod.__file__)
_SETTINGS_DIR = os.path.dirname(_KEYSTORE_FILE)
_SELF_FILE = os.path.abspath(__file__)


class _Sites(NamedTuple):
  """Where a write came from: the line that set it, and the caller of settings."""
  inner: str
  outer: str


def _write_sites() -> _Sites:
  """The line that wrote the key, and the caller of the settings stack.

  TWO frames, because either one alone misleads.  The plan asks for the first
  frame outside ``kanibako/settings`` (*outer*) — but the settings stack writes
  most of the store, so for those writes *outer* names whoever called
  ``build_launch_snapshot``, never the line that chose the key.  *inner* is the
  first frame outside ``keystore.py`` itself (the mechanism), which IS that line.
  ⚑ REPORTING ONLY.  Neither frame decides a verdict: a key is judged by what it
  is, never by who wrote it.
  """
  inner = ""
  frame: Any = sys._getframe()
  while frame is not None:
    name = frame.f_code.co_filename
    if name != _SELF_FILE and name != _KEYSTORE_FILE:
      if not inner:
        inner = f"{name}:{frame.f_lineno}"
      if not name.startswith(_SETTINGS_DIR):
        return _Sites(inner, f"{name}:{frame.f_lineno}")
    frame = frame.f_back
  return _Sites(inner or "<keystore internal>", "<inside settings>")


def _box(node: KeyStore[Any]) -> list[tuple[str, ...]]:
  """This node's path box, created & attached if absent."""
  box = getattr(node, PATH_TAG, None)
  if box is None:
    box = [()]
    object.__setattr__(node, PATH_TAG, box)
  return box


def _tag(
  child: KeyStore[Any], parent_box: list[tuple[str, ...]], key: str, depth: int = 0,
) -> None:
  """Give *child* its path segments & push the correction down its subtree."""
  if depth > _MAX_TAG_DEPTH:
    return
  segments = parent_box[0] + (key,)
  box = _box(child)
  if box[0] == segments:
    return
  box[0] = segments
  for sub_key, value in dict.items(child):
    if isinstance(value, KeyStore):
      _tag(value, box, sub_key, depth + 1)


def _record(node: KeyStore[Any], key: str, value: Any) -> None:
  """Note one SUCCESSFUL write; the path resolves later, at drain."""
  box = _box(node)
  sites = _write_sites()
  ident = (id(box), key, sites.inner, sites.outer)
  slot = _pending.get(ident)
  if slot is None:
    # ⚑ ``type(value).__name__``, NOT ``repr(value)``: a node's repr is recursive,
    # so reprs at every write would be quadratic in store size.
    # ⚑ The declaring set is captured HERE, at the write, because drain runs in
    # teardown and the running item's markers are a write-time fact.
    _pending[ident] = [box, key, sites, type(value).__name__, 1, _current_negative]
  else:
    slot[4] += 1


def _patched_setitem(self: KeyStore[Any], key: str, value: Any) -> None:
  """The interposition.  Records only writes the real funnel ACCEPTED."""
  assert _original_setitem is not None
  # Original FIRST: a refused key (reserved name, dunder, non-str) is not a write,
  # and censusing one would invent a row out of a test that asserts a refusal.
  _original_setitem(self, key, value)
  try:
    # The STORED value, not the argument: ``__wrap`` turns a plain dict into a
    # node, and whether a write landed a NODE or a LEAF is what the CONTAINER rule
    # turns on.
    stored = dict.get(self, key, None)
    _record(self, key, stored)
    if isinstance(stored, KeyStore):
      _tag(stored, _box(self), key)
  except Exception as exc:  # pragma: no cover - the census must never fail a run
    if len(_collector_errors) < 20:
      _collector_errors.append(f"{type(exc).__name__}: {exc}")


def _ask(path: str) -> str | None:
  """The oracle's verdict on *path*, memoised per distinct dotted prefix."""
  if path not in _verdicts:
    try:
      _verdicts[path] = _oracle(path)
    except Exception as exc:  # pragma: no cover - an oracle fault is not a failure
      _verdicts[path] = f"<oracle raised {type(exc).__name__}: {exc}>"
  return _verdicts[path]


def classify(segments: tuple[str, ...]) -> Judgement:
  """One write's :class:`Judgement`, ignoring what landed UNDER it.

  The CONTAINER and NEGATIVE rules need more than the path — what landed
  underneath, and what the running test declared — and are applied later, in
  :func:`_apply_container_rule` / :func:`_apply_negative_rule`.  Everything else is
  decidable from the path alone.
  """
  if not segments:  # pragma: no cover - __setitem__ cannot produce this
    return Judgement(Verdict.UNROOTED, "", 0, "empty path")
  if segments[0] == BINDING_DERIVATIONS_NODE:
    return Judgement(Verdict.RESERVED, BINDING_DERIVATIONS_NODE, 1, _RESERVED_REASON)
  if segments[0] not in KEYSPACE_ROOTS:
    return Judgement(
      Verdict.UNROOTED, "", len(segments),
      f"{segments[0]!r} is not a keyspace root: a scope-LOCAL or fragment store, "
      f"whose key path this cannot know",
    )
  for cut in range(1, len(segments) + 1):
    if "." in segments[cut - 1]:
      # ⚑ STOP. A dotted segment is DATA; asking the oracle past it would forge a
      # key out of a destination (or an env VAR name — the open question).
      return Judgement(
        Verdict.DATA_SEGMENT, ".".join(segments[:cut - 1]), cut - 1,
        f"segment {segments[cut - 1]!r} contains a dot, so this is not a key path",
      )
    prefix = ".".join(segments[:cut])
    if _ask(prefix) is None:
      if cut == len(segments):
        return Judgement(Verdict.DECLARED, prefix, cut, "")
      return Judgement(
        Verdict.VALUE, prefix, cut,
        f"a value address inside the declared key {prefix!r}",
      )
  full = ".".join(segments)
  return Judgement(Verdict.UNDECLARED, "", len(segments), _ask(full) or "")


def drain() -> None:
  """Resolve pending writes to segment paths & fold them into the census."""
  for box, key, sites, kind, count, negatives in _pending.values():
    segments = box[0] + (key,)
    declared = ".".join(segments) in negatives
    row: dict[str, Any] | None = _rows.get(segments)
    if row is not None:
      row["count"] += count
      # ⚑ AND, never OR: one write of this path from outside a declaring test is
      # enough to make the path a violation again.
      row["negative"] = row["negative"] and declared
      # ⚑ OR, never "first write wins": the CONTAINER rule turns on whether a path
      # ever held a NODE. Latching on the first write would make the verdict depend
      # on the ORDER two tests happened to run in.
      row["is_node"] = row["is_node"] or kind == "KeyStore"
      continue
    judged = classify(segments)
    _rows[segments] = {
      "path": render(segments, judged.key_len), "segments": list(segments),
      "verdict": judged.verdict, "key": judged.key, "note": judged.note,
      "negative": declared, "count": count, "site": sites.inner,
      "outer_site": sites.outer, "value_type": kind, "is_node": kind == "KeyStore",
    }
  _pending.clear()


def _apply_container_rule() -> None:
  """Re-class a flagged NODE that is STRUCTURE rather than a key.  Two rules.

  ⚑ AN INTERMEDIATE node is judged by WHAT LANDED UNDER IT rather than by a list:
  ``meta.box`` and ``box.bindings`` are scaffolding the store needs, and neither is
  a key.  A node with NOTHING declared beneath it is not scaffolding — an empty
  fabricated subtree stays a violation, which is why this is a filter and not a
  blanket "nodes are exempt".

  ⚑⚑ A KEYSPACE ROOT is different, and the difference is the spec's, not a
  convenience: a root is a NAMESPACE, which ``key_validity`` says in its own words
  (*"'meta' is a namespace, not a key"*).  Its key-hood cannot depend on what a
  given session happened to write beneath it — an empty ``box: {}`` scope table in
  a settings file is a scope table, not a fabrication, and the "carries something
  declared" test would call it one in any run that only exercised empty files.
  A SCALAR at a root is still a violation: that is a genuinely undeclared shape,
  and the ``is_node`` guard is what keeps this rule from excusing it.
  """
  real = {Verdict.DECLARED, Verdict.VALUE}
  carriers: set[tuple[str, ...]] = set()
  for segments, row in _rows.items():
    if row["verdict"] in real:
      for cut in range(1, len(segments)):
        carriers.add(segments[:cut])
  for segments, row in _rows.items():
    if not row["is_node"] or row["verdict"] != Verdict.UNDECLARED:
      continue
    if len(segments) == 1:
      row["verdict"] = Verdict.CONTAINER
      row["note"] = "a keyspace ROOT: a namespace, not a key (spec §0)"
    elif segments in carriers:
      row["verdict"] = Verdict.CONTAINER
      row["note"] = "an intermediate node carrying declared keys underneath it"


def _apply_negative_rule() -> None:
  """Re-class a violation EVERY write of which came from a test that declared it.

  ⚑ Applied AFTER the container rule and only to what is still ``UNDECLARED``, so a
  marker cannot dress up a row some other rule already explains.
  ⚑⚑ A declaration counts as EXERCISED only where it actually changed a verdict —
  which is what :func:`unused_negatives` reads.  A marker naming a path that turns
  out to be scaffolding, or a declared key, or one that some OTHER test also writes
  undeclared, changed nothing, and saying so is the whole point of the check.
  """
  for segments, row in _rows.items():
    if row["verdict"] == Verdict.UNDECLARED and row["negative"]:
      row["verdict"] = Verdict.NEGATIVE
      row["note"] = (
        f"the test declared this write with @pytest.mark.{NEGATIVE_MARKER} — "
        f"{row['note']}"
      )
      _hit_negative.add(".".join(segments))


def unused_negatives() -> list[str]:
  """Declared marker paths that changed no verdict.  Each one FAILS the run."""
  return sorted(_declared_negative - _hit_negative)


### pytest hooks ###

def _enabled() -> bool:
  return os.environ.get(ENV_FLAG, "1").strip().lower() not in _OFF_TOKENS


def _census_file() -> str:
  return os.environ.get(ENV_FILE) or DEFAULT_CENSUS_FILE


def _marker_paths(item: "pytest.Item") -> frozenset[str]:
  """Every path the item's ``writes_undeclared`` markers name (closest wins nothing
  — a class marker and a function marker COMPOSE)."""
  paths: set[str] = set()
  for mark in item.iter_markers(name=NEGATIVE_MARKER):
    paths.update(str(a) for a in mark.args)
  return frozenset(paths)


def pytest_configure(config: "pytest.Config") -> None:
  global _original_setitem
  config.addinivalue_line(
    "markers",
    f"{NEGATIVE_MARKER}(*paths, reason=...): this test writes these UNDECLARED key "
    f"paths on purpose (it exercises a refusal). Every path must actually be "
    f"written or the run fails.",
  )
  if not _enabled() or _original_setitem is not None:
    return
  _agent_leaves()  # discover before any test patches discovery
  _original_setitem = KeyStore.__setitem__
  KeyStore.__setitem__ = _patched_setitem  # type: ignore[method-assign]


def pytest_runtest_setup(item: "pytest.Item") -> None:
  """Arm the item's declared paths BEFORE its fixtures run — a fixture write is
  as much the test's own input as a write from the test body."""
  global _current_negative
  _current_negative = _marker_paths(item)


def pytest_runtest_call(item: "pytest.Item") -> None:
  """Register the declared paths only once the test actually RUNS.

  ⚑ Not in setup: a skipped or setup-errored test never gets the chance to write
  what it declared, and reporting its paths as unused would red the run for a test
  that did not execute.
  """
  _declared_negative.update(_current_negative)


def pytest_runtest_teardown(item: "pytest.Item") -> None:
  """Drain per test so pending state (and the boxes it pins) stays bounded."""
  global _current_negative
  if _original_setitem is not None:
    drain()
  _current_negative = frozenset()


def _uninstall() -> None:
  global _original_setitem
  if _original_setitem is not None:
    KeyStore.__setitem__ = _original_setitem  # type: ignore[method-assign]
    _original_setitem = None


def _ordered() -> list[dict[str, Any]]:
  return sorted(_rows.values(), key=lambda r: (-r["count"], r["path"]))


def violations() -> list[dict[str, Any]]:
  """Every row that FAILS the run: a key the code fabricated."""
  return [r for r in _ordered() if r["verdict"] == Verdict.UNDECLARED]


def _write_census(ordered: list[dict[str, Any]]) -> None:
  """Append this session's rows as JSONL — the clean ones too, deliberately.

  A flagged NODE is adjudicable only against what was written UNDER it, so the
  accepted paths are evidence, not clutter.
  """
  session = " ".join(sys.argv[1:])[:200]
  try:
    with open(_census_file(), "a", encoding="utf-8") as fh:
      for row in ordered:
        fh.write(json.dumps({**row, "session": session}) + "\n")
  except OSError as exc:  # pragma: no cover - a full /tmp is not a test failure
    _collector_errors.append(f"census write failed: {exc}")


def _finalize() -> list[dict[str, Any]]:
  global _finalized
  if _finalized or _original_setitem is None:
    return _ordered()
  drain()
  _uninstall()
  _apply_container_rule()
  _apply_negative_rule()
  _finalized = True
  ordered = _ordered()
  _write_census(ordered)
  return ordered


def pytest_terminal_summary(terminalreporter: Any) -> None:
  if not _enabled():
    return
  ordered = _finalize()
  counts: dict[str, int] = {}
  for row in ordered:
    counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
  bad = violations()
  stale = unused_negatives()
  write = terminalreporter.write_line
  write("")
  write(
    f"KeyStore census: {len(ordered)} distinct paths written -> {_census_file()}"
  )
  write("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
  if _collector_errors:
    write(f"  collector errors ({len(_collector_errors)}): {_collector_errors[:3]}")
  if stale:
    write("")
    write(
      f"KEYSTORE CENSUS FAILURE — {len(stale)} @pytest.mark.{NEGATIVE_MARKER} "
      f"path(s) declared but never written:"
    )
    for path in stale:
      write(f"          {path}")
    write(
      "  A declaration that no longer describes what the test writes is a stale "
      "blessing. Delete it, or fix the path."
    )
  if not bad:
    return
  write("")
  write(f"KEYSTORE ENFORCEMENT FAILURE — {len(bad)} undeclared key(s) written:")
  for row in bad:
    write(f"  {row['count']:>6}x  {row['path']}")
    write(f"           set at {row['site']}  (via {row['outer_site']})")
    write(f"           {row['note']}")
  write(
    "  The keyspace is CLOSED (spec §0): an undeclared key is not a key. Either stop "
    f"writing it, or declare it in the SPEC. A test that means to write one says so "
    f"with @pytest.mark.{NEGATIVE_MARKER}; there is no exemption list."
  )


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
  """FAIL the session when the code fabricated a key. This is the enforcement."""
  if not _enabled():
    return
  _finalize()
  # ⚑ ESCALATE FROM CLEAN ONLY. ``session.exitstatus`` is read AFTER every
  # ``pytest_sessionfinish`` hook, so setting it here is what turns the report into a
  # failed run — but overwriting an INTERRUPTED / NO-TESTS-COLLECTED status would
  # relabel a run that never got far enough to have a census.
  if (violations() or unused_negatives()) and session.exitstatus == 0:
    session.exitstatus = 1


def pytest_unconfigure(config: "pytest.Config") -> None:
  """Belt & braces: the class must not be left patched, whatever ran."""
  _finalize()
  _uninstall()
