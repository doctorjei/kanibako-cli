"""KeyStore write census + ENFORCEMENT: every key written to a store is judged, & a
fabricated one FAILS THE SESSION.

WHAT THIS IS FOR.  ``key_validity`` refuses an undeclared key at the BOUNDARIES
(``config set``, the file partials, the launch seam).  Nothing catches a key
FABRICATED INTERNALLY that reaches a resolved store without crossing one.  This
plugin comes between the store and the code that writes it: it patches the single
write funnel, records every path written during the session, judges each one
against a pluggable declared-keyspace oracle, and at session end REPORTS every
unapproved key with the line that set it and FAILS THE RUN.  A collector that only
warns is not enforcement.

OPT-IN / ZERO-IMPACT, on the model of ``tests/_timing.py``: the plugin is
COMPLETELY INERT unless ``KANI_KEYSTORE_CENSUS`` is set, so normal runs, CI and
the gates pay nothing, no file is written and no run can be failed by it.  When it
IS set, the patch is installed at ``pytest_configure`` and REMOVED again at the end
of the session -- the class is never left patched.

--------------------------------------------------------------------------------
WHAT COUNTS AS A VIOLATION -- the half of this that is adjudication, not mechanism
--------------------------------------------------------------------------------

A resolved store legitimately holds a great deal that is NOT a key, and a collector
that flagged all of it would report noise.  Every recorded write lands in exactly
one class (:class:`Verdict`), and only ``UNDECLARED`` fails the run:

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
``UNJUDGED``   the key did not come from the code (see ORIGIN below).
``EXEMPT``     adjudicated, with the reason recorded at the exemption
               (:data:`EXEMPTIONS`).
``UNDECLARED`` none of the above: the CODE put a key into a store that the keyspace
               does not declare.  THE RUN FAILS.

⚑ ORIGIN -- WHO CHOSE THE KEY, which is the whole question.  The gap this closes is
a key "fabricated INTERNALLY ... without crossing a boundary", so a key that DID
cross one is a different question and is reported rather than enforced:
- ``TEST``  the innermost or outermost frame is in a test tree.  A test that builds
  a store by hand, or calls the settings stack directly with a synthetic partial, is
  authoring its own input; enforcing there reds on FIXTURES, not on the code.
- ``FILE``  the writing frame is the settings FILE-partial parser, so the key is a
  settings file's CONTENT.  A file IS a boundary.
- ``CODE``  everything else -- the production call path -- and the only class judged.

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
from kanibako.settings import settings_assemble as _assemble_mod
from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, SCOPE_CONTAINMENT
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import key_validity

if TYPE_CHECKING:  # pragma: no cover - typing only
  import pytest

#: Set to anything non-empty to arm the census AND its enforcement.  Unset = inert.
ENV_FLAG = "KANI_KEYSTORE_CENSUS"

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
#: node -- included so it is EXEMPTED BY NAME below rather than dismissed as an
#: unrooted fragment.
KEYSPACE_ROOTS: frozenset[str] = (
  frozenset(SCOPE_CONTAINMENT) | {"config", "meta", "pref", BINDING_DERIVATIONS_NODE}
)


### The oracle — pluggable, so Part 3 can swap the manifest in ###

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


#: THE ORACLE SEAM.  Part 3 replaces this with a manifest-sourced callable; the
#: collector below never names ``key_validity`` itself.
_oracle: Callable[[str], str | None] = declared_keyspace_oracle


def set_oracle(fn: Callable[[str], str | None]) -> None:
  """Substitute the declared-keyspace oracle (Part 3's seam)."""
  global _oracle
  _oracle = fn


### The exemption set — the deliverable's other half ###

class Exemption(NamedTuple):
  """One adjudicated non-key, with the reason recorded AT the exemption."""
  segments: tuple[str, ...]
  subtree: bool
  reason: str

  def covers(self, path: tuple[str, ...]) -> bool:
    n = len(self.segments)
    if self.subtree:
      return path[:n] == self.segments
    return path == self.segments


#: ⚑⚑ EVERY MEMBER IS MEASURED AND CARRIES ITS REASON.  An entry added merely to
#: make a run green is the exact failure this phase exists to prevent — the classes
#: that are STRUCTURE (value addresses, containers, dest data) are handled by the
#: classification above and must never appear here.
EXEMPTIONS: tuple[Exemption, ...] = (
  Exemption(
    (BINDING_DERIVATIONS_NODE,), True,
    "the RESERVED INTERNAL NODE, not a key — kb_store.BINDING_DERIVATIONS_NODE's "
    "own comment says so. commands/start.py installs the collapse's per-declaration "
    "derivation map beside the keyspace in the same store; its interior is "
    "declaration keys and box DESTINATIONS, which are data.",
  ),
  Exemption(
    ("agent", "default", "cache_probe"), False,
    "INSTRUMENTATION LIMIT, not a fabrication: 'cache_probe' is a legal "
    "PLUGIN-declared agent leaf, registered by a TargetSetting that "
    "tests/test_resource_scoping.py injects. The oracle's plugin-leaf set is "
    "discovered ONCE at install (see _agent_leaves — discovering it later would "
    "prime a cache the production code reads), so a test-registered descriptor is "
    "invisible to it.",
  ),
  Exemption(
    ("meta", "box", "agent", "cache_probe"), False,
    "the meta.box.agent RO MIRROR of the same test-registered 'cache_probe' leaf; "
    "see the agent.default.cache_probe exemption.",
  ),
)


def exemption_for(segments: tuple[str, ...]) -> Exemption | None:
  """The exemption covering *segments*, or ``None``."""
  for entry in EXEMPTIONS:
    if entry.covers(segments):
      return entry
  return None


### Classification ###

class Verdict:
  """The classes a recorded write can land in; only ``UNDECLARED`` fails a run."""
  DECLARED = "DECLARED"
  VALUE = "VALUE"
  DATA_SEGMENT = "DATA_SEGMENT"
  CONTAINER = "CONTAINER"
  UNROOTED = "UNROOTED"
  UNJUDGED = "UNJUDGED"
  EXEMPT = "EXEMPT"
  UNDECLARED = "UNDECLARED"


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
_pending: dict[tuple[int, str, str, int], list[Any]] = {}

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
_TEST_DIR_MARKER = os.sep + "tests" + os.sep

#: ⚑ THE FILE-PARTIAL PARSER, BY CODE OBJECT, not by name or line.  These two build
#: a level partial out of a loaded settings DOCUMENT, so a key written from one of
#: their frames came out of a FILE — a boundary — not out of the code.  Imported
#: rather than string-matched so a rename breaks the census LOUDLY at install
#: instead of silently re-classifying every file-sourced key as a fabrication.
_FILE_PARTIAL_CODE = frozenset({
  _assemble_mod._parse_node.__code__, _assemble_mod.parse_bind_map.__code__,
})


class _Sites(NamedTuple):
  """Where a write came from: two frames, plus what they say about its ORIGIN."""
  inner: str
  outer: str
  origin: str


def _write_sites() -> _Sites:
  """The line that wrote the key, the caller of settings, & the write's ORIGIN.

  TWO frames, because either one alone misleads.  The plan asks for the first
  frame outside ``kanibako/settings`` (*outer*) — but the settings stack writes
  most of the store, so for those writes *outer* names whoever called
  ``build_launch_snapshot``, never the line that chose the key.  *inner* is the
  first frame outside ``keystore.py`` itself (the mechanism), which IS that line.
  """
  inner = ""
  origin = "CODE"
  frame: Any = sys._getframe()
  while frame is not None:
    name = frame.f_code.co_filename
    if name != _SELF_FILE and name != _KEYSTORE_FILE:
      if not inner:
        inner = f"{name}:{frame.f_lineno}"
        if frame.f_code in _FILE_PARTIAL_CODE:
          origin = "FILE"
        elif _TEST_DIR_MARKER in name:
          origin = "TEST"
      if not name.startswith(_SETTINGS_DIR):
        # ⚑ The OUTERMOST frame decides TEST even when the innermost was production:
        # a test calling assemble_levels with its own floor/partial authored that key.
        if _TEST_DIR_MARKER in name:
          origin = "TEST"
        return _Sites(inner, f"{name}:{frame.f_lineno}", origin)
    frame = frame.f_back
  return _Sites(inner or "<keystore internal>", "<inside settings>", origin)


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
    _pending[ident] = [box, key, sites, type(value).__name__, 1]
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


def classify(segments: tuple[str, ...], *, origin: str) -> Judgement:
  """One write's :class:`Judgement`, ignoring what landed UNDER it.

  The CONTAINER rule needs the whole census and is applied later, in
  :func:`_apply_container_rule`; everything else is decidable from the path alone.
  """
  if not segments:  # pragma: no cover - __setitem__ cannot produce this
    return Judgement(Verdict.UNROOTED, "", 0, "empty path")
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
  exemption = exemption_for(segments)
  if exemption is not None:
    return Judgement(
      Verdict.EXEMPT, ".".join(exemption.segments), len(exemption.segments),
      exemption.reason,
    )
  full = ".".join(segments)
  if origin != "CODE":
    what = "a settings FILE" if origin == "FILE" else "a test's own input"
    return Judgement(
      Verdict.UNJUDGED, "", len(segments),
      f"came from {what}, not from the code — {_ask(full)}",
    )
  return Judgement(Verdict.UNDECLARED, "", len(segments), _ask(full) or "")


def drain() -> None:
  """Resolve pending writes to segment paths & fold them into the census."""
  for box, key, sites, kind, count in _pending.values():
    segments = box[0] + (key,)
    row: dict[str, Any] | None = _rows.get(segments)
    if row is not None:
      row["count"] += count
      continue
    judged = classify(segments, origin=sites.origin)
    _rows[segments] = {
      "path": render(segments, judged.key_len), "segments": list(segments),
      "verdict": judged.verdict, "key": judged.key, "note": judged.note,
      "origin": sites.origin, "count": count, "site": sites.inner,
      "outer_site": sites.outer, "value_type": kind, "is_node": kind == "KeyStore",
    }
  _pending.clear()


def _apply_container_rule() -> None:
  """Re-class a flagged NODE that carries declared keys underneath it.

  ⚑ Judged by WHAT LANDED UNDER IT rather than by a list: ``box``, ``meta.box`` and
  ``box.bindings`` are scaffolding the store needs, and none of them is a key.  A
  node with NOTHING declared beneath it is not scaffolding — an empty fabricated
  subtree stays a violation, which is why this rule is a filter and not a blanket
  "nodes are exempt".
  """
  real = {Verdict.DECLARED, Verdict.VALUE}
  carriers: set[tuple[str, ...]] = set()
  for segments, row in _rows.items():
    if row["verdict"] in real:
      for cut in range(1, len(segments)):
        carriers.add(segments[:cut])
  for segments, row in _rows.items():
    # ⚑ ``EXEMPT`` is NOT in this set: a row somebody adjudicated by hand keeps the
    # class that carries its reason, or the exemption stops being visible to audit.
    if (
      row["is_node"]
      and row["verdict"] in (Verdict.UNDECLARED, Verdict.UNJUDGED)
      and segments in carriers
    ):
      row["verdict"] = Verdict.CONTAINER
      row["note"] = "an intermediate node carrying declared keys underneath it"


### pytest hooks ###

def _enabled() -> bool:
  return bool(os.environ.get(ENV_FLAG))


def _census_file() -> str:
  return os.environ.get(ENV_FILE) or DEFAULT_CENSUS_FILE


def pytest_configure(config: "pytest.Config") -> None:
  global _original_setitem
  if not _enabled() or _original_setitem is not None:
    return
  _agent_leaves()  # discover before any test patches discovery
  _original_setitem = KeyStore.__setitem__
  KeyStore.__setitem__ = _patched_setitem  # type: ignore[method-assign]


def pytest_runtest_teardown(item: "pytest.Item") -> None:
  """Drain per test so pending state (and the boxes it pins) stays bounded."""
  if _original_setitem is not None:
    drain()


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
  write = terminalreporter.write_line
  write("")
  write(
    f"KeyStore census: {len(ordered)} distinct paths written -> {_census_file()}"
  )
  write("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
  # ⚑ ONE LINE PER EXEMPTION, not per row: the exemption SET is the thing a reader
  # has to audit, and a subtree exemption that printed its every dest would bury it.
  for entry in EXEMPTIONS:
    hit = [r for r in ordered if r["verdict"] == Verdict.EXEMPT
           and entry.covers(tuple(r["segments"]))]
    if hit:
      scope = "subtree" if entry.subtree else "exact"
      write(
        f"  exempt ({scope})  {'.'.join(entry.segments)}  "
        f"— {len(hit)} path(s), {sum(r['count'] for r in hit)} write(s)"
      )
      write(f"           {entry.reason}")
  if _collector_errors:
    write(f"  collector errors ({len(_collector_errors)}): {_collector_errors[:3]}")
  if not bad:
    return
  write("")
  write(f"KEYSTORE ENFORCEMENT FAILURE — {len(bad)} undeclared key(s) written by code:")
  for row in bad:
    write(f"  {row['count']:>6}x  {row['path']}")
    write(f"           set at {row['site']}  (via {row['outer_site']})")
    write(f"           {row['note']}")
  write(
    "  The keyspace is CLOSED (spec §0): an undeclared key is not a key. Either stop "
    "writing it, or declare it — an exemption needs a stated reason and an adjudication."
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
  if violations() and session.exitstatus == 0:
    session.exitstatus = 1


def pytest_unconfigure(config: "pytest.Config") -> None:
  """Belt & braces: the class must not be left patched, whatever ran."""
  _finalize()
  _uninstall()
