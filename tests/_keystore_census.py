"""Opt-in KeyStore write census: records every key written to a store & flags undeclared ones.

WHAT THIS IS FOR.  ``key_validity`` refuses an undeclared key at the BOUNDARIES
(``config set``, the file partials, the launch seam).  Nothing catches a key
FABRICATED INTERNALLY that reaches a resolved store without crossing one.  This
plugin comes between the store and the code that writes it: it patches the single
write funnel, records every dotted path written during the session, runs each one
through a pluggable declared-keyspace oracle, and reports what the oracle refuses.

PHASE 2A COLLECTS ONLY.  It does not fail the run, it does not raise, and it
exempts nothing -- a resolved store legitimately holds nodes that are not keys
(``kb_store.BINDING_DERIVATIONS_NODE`` is one), and deciding which flagged rows
are those nodes is an adjudication over the census, not a thing to guess at here.
Exempting early would bury the very rows the census exists to surface.

OPT-IN / ZERO-IMPACT, on the model of ``tests/_timing.py``: the plugin is
COMPLETELY INERT unless ``KANI_KEYSTORE_CENSUS`` is set, so normal runs, CI and
the gates pay nothing and no file is written.  When it IS set, the patch is
installed at ``pytest_configure`` and REMOVED again at the end of the session --
the class is never left patched.

HOW A NESTED NODE LEARNS ITS PATH.  ``KeyStore.__setattr__`` routes DUNDER names
to ``object.__setattr__`` and everything else to ``self[name]``, so an attribute
spelled ``__kani_path__`` is a real attribute that can never become a key.  That
is the tag.  It holds a one-element LIST (a "path box") whose content is a TUPLE
OF SEGMENTS, not a dotted string: a dest-keyed segment is routinely a filesystem
path with dots in it (``~/.cache/uv``), so a dotted string loses the very segment
boundaries the ancestor column below depends on.  The box is a box, rather than
the tuple itself, because a node's children are written BEFORE the node is
attached to its parent::

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
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Collection, Iterator
from typing import TYPE_CHECKING, Any, Callable

from kanibako.settings import keystore as _keystore_mod
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import key_validity

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pytest

#: Set to anything non-empty to arm the census.  Unset = the plugin is inert.
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


### Collector state ###

#: Un-drained writes, keyed by ``(id(box), key, file, line)`` so a hot write site
#: collapses to one entry instead of one per call.  The box is held in the VALUE,
#: which is what keeps its ``id`` from being recycled under us.
_pending: dict[tuple[int, str, str, int], list[Any]] = {}

#: Drained, path-resolved rows: path -> row.  EVERY distinct path written, flagged
#: or not — the clean ones are the census's denominator, and they are what tells a
#: later reader whether a flagged NODE is a prefix of anything real.
_rows: dict[str, dict[str, Any]] = {}

#: path -> oracle verdict, so the oracle runs once per distinct path.
_verdicts: dict[str, str | None] = {}

#: Writes the collector itself failed on.  A census bug must not red the suite,
#: so the failure is COUNTED and reported rather than raised.
_collector_errors: list[str] = []

_original_setitem: Callable[..., None] | None = None
_finalized = False

_KEYSTORE_FILE = os.path.abspath(_keystore_mod.__file__)
_SETTINGS_DIR = os.path.dirname(_KEYSTORE_FILE)
_SELF_FILE = os.path.abspath(__file__)


def _write_sites() -> tuple[str, str]:
  """``(inner, outer)`` — the line that wrote the key, & the caller of settings.

  TWO frames, because either one alone misleads.  The plan asks for the first
  frame outside ``kanibako/settings`` (*outer*) — but the settings stack writes
  most of the store, so for those writes *outer* names whoever called
  ``build_launch_snapshot``, never the line that chose the key.  *inner* is the
  first frame outside ``keystore.py`` itself (the mechanism), which IS that line.
  """
  inner = ""
  frame: Any = sys._getframe()
  while frame is not None:
    name = frame.f_code.co_filename
    if name != _SELF_FILE and name != _KEYSTORE_FILE:
      if not inner:
        inner = f"{name}:{frame.f_lineno}"
      if not name.startswith(_SETTINGS_DIR):
        return inner, f"{name}:{frame.f_lineno}"
    frame = frame.f_back
  return inner or "<keystore internal>", "<inside settings>"


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
  inner, outer = _write_sites()
  ident = (id(box), key, inner, outer)
  slot = _pending.get(ident)
  if slot is None:
    # ⚑ ``type(value).__name__``, NOT ``repr(value)``: a node's repr is recursive,
    # so reprs at every write would be quadratic in store size.
    _pending[ident] = [box, key, inner, outer, type(value).__name__, 1]
  else:
    slot[5] += 1


def _patched_setitem(self: KeyStore[Any], key: str, value: Any) -> None:
  """The interposition.  Records only writes the real funnel ACCEPTED."""
  assert _original_setitem is not None
  # Original FIRST: a refused key (reserved name, dunder, non-str) is not a write,
  # and censusing one would invent a row out of a test that asserts a refusal.
  _original_setitem(self, key, value)
  try:
    # The STORED value, not the argument: ``__wrap`` turns a plain dict into a
    # node, and whether a write landed a NODE or a LEAF is the census's most
    # useful discriminator between an internal container and a fabricated key.
    stored = dict.get(self, key, None)
    _record(self, key, stored)
    if isinstance(stored, KeyStore):
      _tag(stored, _box(self), key)
  except Exception as exc:  # pragma: no cover - the census must never fail a run
    if len(_collector_errors) < 20:
      _collector_errors.append(f"{type(exc).__name__}: {exc}")


def _ask(path: str) -> str | None:
  """The oracle's verdict on *path*, memoised per distinct path."""
  if path not in _verdicts:
    try:
      _verdicts[path] = _oracle(path)
    except Exception as exc:  # pragma: no cover - an oracle fault is not a failure
      _verdicts[path] = f"<oracle raised {type(exc).__name__}: {exc}>"
  return _verdicts[path]


def _declared_ancestor(segments: tuple[str, ...]) -> str | None:
  """The LONGEST proper ancestor of *segments* the oracle accepts as a key.

  ⚑ NOT an exemption — a COLUMN.  A write under a declared TERMINAL container
  (``box.env.<VAR>``, ``box.masks.<dest>``, ``box.bindings.ro.<dest>``) addresses
  a VALUE inside a real key, not a key of its own, and the oracle is right to
  refuse it as a key.  Naming the declared ancestor is what separates that class
  from a path with no declared ancestor at all, which is the class the census is
  hunting.  Computed over SEGMENTS so a dest containing dots stays one segment.
  """
  for cut in range(len(segments) - 1, 0, -1):
    ancestor = ".".join(segments[:cut])
    if _ask(ancestor) is None:
      return ancestor
  return None


def drain() -> None:
  """Resolve pending writes to dotted paths & fold them into the census."""
  for box, key, inner, outer, kind, count in _pending.values():
    segments = box[0] + (key,)
    path = ".".join(segments)
    reason = _ask(path)
    row: dict[str, Any] | None = _rows.get(path)
    if row is None:
      _rows[path] = {
        "path": path, "segments": list(segments), "reason": reason, "count": count,
        "site": inner, "outer_site": outer, "value_type": kind,
        "is_node": kind == "KeyStore",
        "declared_ancestor": None if reason is None else _declared_ancestor(segments),
      }
    else:
      row["count"] += count
  _pending.clear()


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


def _write_census(ordered: list[dict[str, Any]]) -> None:
  """Append this session's rows as JSONL — the CLEAN paths too, deliberately.

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
  _finalized = True
  ordered = _ordered()
  _write_census(ordered)
  return ordered


def pytest_terminal_summary(terminalreporter: Any) -> None:
  if not _enabled():
    return
  ordered = _finalize()
  flagged = [r for r in ordered if r["reason"] is not None]
  write = terminalreporter.write_line
  write("")
  write(
    f"KeyStore census: {len(ordered)} distinct paths written, "
    f"{len(flagged)} flagged by the oracle -> {_census_file()}"
  )
  for row in flagged[:10]:
    under = f" under {row['declared_ancestor']}" if row["declared_ancestor"] else ""
    write(f"  {row['count']:>6}x  {row['path']}{under}  [{row['site']}]")
  if len(flagged) > 10:
    write(f"  … {len(flagged) - 10} more flagged paths in the census file")
  if _collector_errors:
    write(f"  collector errors ({len(_collector_errors)}): {_collector_errors[:3]}")


def pytest_unconfigure(config: "pytest.Config") -> None:
  """Belt & braces: the class must not be left patched, whatever ran."""
  _finalize()
  _uninstall()
