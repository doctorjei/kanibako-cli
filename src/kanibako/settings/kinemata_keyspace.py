"""The keyspace as a kinemata ``kind = "import"`` registry.

A ``declared()`` override for a closed-but-not-flat keyspace: membership is
answered by several manifest sections, which no ``yaml-mapping`` block can
express. Everything derives from :func:`manifest_doc`; a key name or root
spelled here as a literal would be a third carrier of the keyspace, so there is
none. It re-implements membership rather than calling
:func:`~kanibako.settings.settings_keyspace.key_class`, because a gate built on
the product would certify the product against itself, and that answer is
three-valued where ``declared()`` is yes or no;
``tests/test_settings/test_kinemata_keyspace.py`` compares the two.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import TYPE_CHECKING, Any, Iterator

from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.settings_resolve import SettingsError, match_ref

if TYPE_CHECKING:
  from kinemata.contract import Entry  # type: ignore[import-not-found]

#: One identifier segment, shared by the candidate syntax and the VAR matchers.
_SEG = r"[A-Za-z_][A-Za-z0-9_]*"

#: What may not abut a ``detect()`` match. Upstream's contract names this rule
#: ``identifier`` (dotted keys own their dots); inlined so ``detect()`` works
#: with kinemata absent. It is upstream's matcher vocabulary, not keyspace data.
_IDENTIFIER_EDGE = r"[A-Za-z0-9_.\-]"

#: The ``not_keys`` sections whose members are DECLARED as spellings, so a
#: mention of one is recognized in order to be refused with its cure (spec §0).
#: ``code_residue`` is deliberately absent: those names were never
#: spec-sanctioned, so a live use of one is a stray, not a declaration.
_RECOGNIZED_NOT_KEYS = ("renamed",)

#: Manifest schema vocabulary (column names), not keyspace data.
_SPEC_FIELD = "spec"
_PARAMETRIC_FIELD = "parametric"
_PARAMETRIC_KEYS_FIELD = "parametric_keys"
_FALLBACK_FIELD = "fallback"


def _clauses(row: Any) -> tuple[str, ...]:
  """A manifest row's governing clauses, in the mapping adapter's shape."""
  if not isinstance(row, dict):
    return ()
  raw = row.get(_SPEC_FIELD)
  if raw is None:
    return ()
  if isinstance(raw, str):
    return (raw,)
  if isinstance(raw, (list, tuple)):
    return tuple(str(c) for c in raw)
  return (str(raw),)


def _static_head(template: str) -> str:
  """The literal segments before the first placeholder or glob."""
  head = re.split(r"<|\*", template, maxsplit=1)[0]
  return head[:-1] if head.endswith(".") else head


class _Shape:
  """One compiled parametric template plus the row's spec clauses."""

  def __init__(self, pattern: re.Pattern[str], spec: tuple[str, ...]) -> None:
    self.pattern = pattern
    self.spec = spec

  def match(self, identifier: str) -> re.Match[str] | None:
    """A full-template match of one identifier, else nothing."""
    return self.pattern.fullmatch(identifier)


class KeyspaceRegistry:
  """The keyspace, enumerated from the manifest and recognized from it too."""

  #: The input contract ``candidates()`` rests on, pinned rather than inherited
  #: from the project: under ``strings`` over ``.py``, kinemata hands over ONE
  #: evaluated string literal per call -- never a comment, a docstring or an
  #: f-string, which it drops whole. A wider scope would feed it raw lines.
  match_mode = "strings"
  suffixes: tuple[str, ...] | None = (".py",)
  machinery: tuple[str, ...] = ()

  #: Entries are meant to be routed through: an unmentioned one is a finding.
  #: Upstream's default (``contract.BaseRegistry``); restated here so the class
  #: satisfies the contract structurally without inheriting it -- kinemata stays
  #: an optional dependency, so this module must import without it (all kinemata
  #: imports are function-scope or ``TYPE_CHECKING``-only).
  mentions_are_uses = True

  def __init__(self, *, closed: bool = False, name: str = "keyspace", **options: Any) -> None:
    """Build every matcher from one ``manifest_doc()`` read."""
    self.name = name
    self.closed = closed
    self.boundary = _IDENTIFIER_EDGE
    self.options = dict(options)
    try:
      from kinemata.contract import (  # type: ignore[import-not-found]
        DEFAULT_BUDGET as _budget,
        DEFAULT_LINE_BUDGET as _line_budget,
      )
    except ImportError:
      # kinemata absent: construction still works (imports, unit tests); the
      # budgets only matter when kinemata itself projects this registry, in
      # which case the import above succeeds and these literals never run.
      # Values are upstream's ``contract.py`` defaults, cited not derived.
      _budget = 16 * 1024
      _line_budget = 160
    self.budget = _budget
    self.line_budget = _line_budget
    doc = manifest_doc()
    keys: dict[str, Any] = doc["keys"]
    self._rows = dict(keys)
    self._concrete = set(keys)
    self._roots = sorted({key.split(".", 1)[0] for key in keys})
    roots, edge = "|".join(self._roots), self.boundary
    self._candidate_re = re.compile(
      r"(?<!" + edge + r")(" + roots + r")(?:\." + _SEG + r")+(?!" + edge + r")"
    )
    self._interiors = self._build_interiors(keys, doc["not_keys"])
    scopes, families, var_families = self._read_categories(doc["categories"])
    self._scopes = scopes
    self._one_seg_scopes = {s for s in scopes if "." not in s}
    self._families = families
    self._var_families = var_families
    self._tier_prefix, self._tier_head = self._read_tier_rule(keys)
    self._tier_node = self._tier_prefix.rstrip(".").rsplit(".", 1)[-1] if self._tier_prefix else ""
    self._shapes: list[_Shape] = []
    self._build_key_shapes(keys)
    self._ns_shapes = self._build_ns_shapes(keys)
    self._known: set[str] = set()
    self._build_section_shapes(doc["not_keys"], doc["category_default_entries"], doc["plugin_contributed"])
    self._interiors |= self._cross_prefixes()

  @staticmethod
  def _build_interiors(keys: dict[str, Any], not_keys: dict[str, Any]) -> set[str]:
    """Proper prefixes of concrete ids plus static heads of templates."""
    interiors = set()
    templates: list[str] = [k for k in keys if "<" in k or "*" in k]
    for section in _RECOGNIZED_NOT_KEYS:
      block = not_keys.get(section)
      if isinstance(block, dict):
        templates += [k for k in block if "<" in k or "*" in k]
    for key in keys:
      parts = key.split(".")
      for i in range(1, len(parts)):
        interiors.add(".".join(parts[:i]))
    for template in templates:
      head = _static_head(template)
      if head:
        interiors.add(head)
    return interiors

  @staticmethod
  def _read_categories(categories: dict[str, Any]) -> tuple[list[str], set[str], set[str]]:
    """Scopes, families, and VAR-tailed families from the categories table."""
    scopes: list[str] = []
    families: set[str] = set()
    var_families: set[str] = set()
    for field, block in categories.items():
      if field.endswith("_spec"):
        continue
      if isinstance(block, list):
        scopes = [str(s) for s in block]
      elif isinstance(block, dict):
        families.add(field)
        params = block.get(_PARAMETRIC_FIELD)
        if isinstance(params, list) and "VAR" in [str(p) for p in params]:
          var_families.add(field)
    return scopes, families, var_families

  @staticmethod
  def _read_tier_rule(keys: dict[str, Any]) -> tuple[str, str]:
    """The default-tier prefix and node head from the universal row itself."""
    for key, row in keys.items():
      if not ("<agent>" in key and "<key>" in key):
        continue
      head = _static_head(key).split(".")[0]
      fallback = row.get(_FALLBACK_FIELD) if isinstance(row, dict) else None
      prefix = (
        fallback.split("<key>")[0] if isinstance(fallback, str) and "<key>" in fallback else ""
      )
      return prefix, head
    return "", ""

  def _shape_admits(self, match: re.Match[str]) -> bool:
    """Whether a template match names a key, judging its ``<key>`` tail if any."""
    groups = match.groupdict()
    tail = groups.get("tail")
    if tail is None:
      return True
    return self._tail_ok(str(tail), at_tier=groups.get("agent") == self._tier_node)

  def _tail_ok(self, tail: str, *, at_tier: bool) -> bool:
    """A ``<key>`` tail names a default-tier leaf or a category, or is conceded.

    The default tier's vocabulary is the manifest's own, so a tail there is
    judged. A one-segment tail under any other node is CONCEDED (spec §0): that
    node's leaf vocabulary is plugin-declared and unreadable here.
    """
    if self._tier_prefix + tail in self._concrete:
      return True
    if self._category_tail(tail.split(".")):
      return True
    return not at_tier and "." not in tail

  def _category_tail(self, segs: list[str]) -> bool:
    """A tail under an agent node matches the categories cross-product."""
    if len(segs) == 1:
      return segs[0] in self._families
    if len(segs) == 2:
      return ".".join(segs) in self._families or segs[0] in self._var_families
    return False

  def _compile(self, template: str, scope_alt: str, tier: bool) -> re.Pattern[str]:
    """A template into a full-match pattern; ``tier`` names ``<agent>`` and ``<key>``."""
    out: list[str] = []
    pos = 0
    for part in re.finditer(r"<[A-Za-z_][A-Za-z0-9_]*>|\*\*?", template):
      out.append(re.escape(template[pos : part.start()]))
      token = part.group(0)
      if token == "<scope>":
        out.append("(" + scope_alt + ")")
      elif token == "<agent>" and tier:
        out.append("(?P<agent>" + _SEG + ")")
      elif token in ("<agent>", "<VAR>", "<name>"):
        out.append("(" + _SEG + ")")
      elif token == "<key>":
        tail = _SEG + r"(?:\." + _SEG + r")*"
        out.append("(?P<tail>" + tail + ")" if tier else "(" + tail + ")")
      elif token == "**":
        out.append(_SEG + r"(?:\." + _SEG + r")*")
      elif token == "*":
        out.append("(" + _SEG + ")")
      else:
        raise ValueError(f"template {template!r} names placeholder {token!r} with no matcher")
      pos = part.end()
    out.append(re.escape(template[pos:]))
    return re.compile("".join(out))

  @staticmethod
  def _build_ns_shapes(keys: dict[str, Any]) -> list[re.Pattern[str]]:
    """Namespace shapes for heads taking ``<agent>`` next: node namespaces."""
    heads: dict[str, None] = {}
    for key in keys:
      segs = key.split(".")
      if "<agent>" in segs:
        heads[".".join(segs[: segs.index("<agent>")])] = None
    return [re.compile(re.escape(head) + r"\." + _SEG) for head in heads if head]

  def _cross_prefixes(self) -> set[str]:
    """Proper prefixes of the finite scope-by-family terminal spellings."""
    out = set()
    for scope in self._scopes:
      for family in self._families:
        parts = (scope + "." + family).split(".")
        for i in range(1, len(parts)):
          out.add(".".join(parts[:i]))
    return out

  def _scope_alt(self) -> str:
    """The scope alternation, longest first so two-segment scopes win."""
    return (
      "(?:" + "|".join(re.escape(s) for s in sorted(self._scopes, key=len, reverse=True)) + ")"
    )

  def _build_key_shapes(self, keys: dict[str, Any]) -> None:
    """One shape per parametric ``keys:`` row; ``<key>`` tails stay bounded."""
    scope_alt = self._scope_alt()
    for key, row in keys.items():
      if "<" not in key and "*" not in key:
        continue
      self._shapes.append(_Shape(self._compile(key, scope_alt, tier=True), _clauses(row)))

  def _build_section_shapes(
    self, not_keys: dict[str, Any], category_defaults: dict[str, Any], plugin: dict[str, Any]
  ) -> None:
    """Exact members and loose shapes from the sections outside ``keys:``."""
    scope_alt = self._scope_alt()
    for section in _RECOGNIZED_NOT_KEYS:
      block = not_keys.get(section)
      if not isinstance(block, dict):
        continue
      for key in block:
        if "<" in key or "*" in key:
          self._shapes.append(_Shape(self._compile(key, scope_alt, tier=False), ()))
        else:
          self._known.add(key)
    if isinstance(category_defaults, dict):
      for key in category_defaults:
        if key == _PARAMETRIC_KEYS_FIELD:
          continue
        if "<" in key or "*" in key:
          self._shapes.append(_Shape(self._compile(key, scope_alt, tier=False), ()))
        else:
          self._known.add(key)
    plugin_cde = plugin.get("category_default_entries") if isinstance(plugin, dict) else None
    if isinstance(plugin_cde, dict):
      self._known.update(plugin_cde)
    self._namespace_shapes = self._read_namespace_shapes(plugin)

  def _read_namespace_shapes(self, plugin: dict[str, Any]) -> list[_Shape]:
    """The plugin namespace's shape answer; the census is never consulted.

    On the agent tier's own head the node and leaf are named, so the default
    node is judged by :meth:`_shape_admits` like any ``<key>`` tail: a plugin
    contributes nothing at the tier core owns (spec §0).
    """
    namespace = plugin.get("namespace")
    spec = _clauses({_SPEC_FIELD: plugin.get("shape_spec")})
    shapes = []
    if isinstance(namespace, str):
      for arm in namespace.split(" and "):
        head = _static_head(arm.strip())
        if not head:
          continue
        if head == self._tier_head:
          node, leaf = "(?P<agent>" + _SEG + ")", "(?P<tail>" + _SEG + ")"
        else:
          node, leaf = _SEG, _SEG
        shapes.append(_Shape(re.compile(re.escape(head) + r"\." + node + r"\." + leaf), spec))
    return shapes

  def _split_scope(self, segs: list[str]) -> int:
    """How many leading segments name the scope: two, one, or none."""
    if len(segs) >= 3 and ".".join(segs[:2]) in self._scopes:
      return 2
    if len(segs) >= 2 and segs[0] in self._one_seg_scopes:
      return 1
    if self._tier_head and len(segs) >= 2 and segs[0] == self._tier_head:
      return 1
    return 0

  def _cross_declared(self, identifier: str) -> bool:
    """The categories cross-product: terminal keys plus VAR-tailed families."""
    segs = identifier.split(".")
    take = self._split_scope(segs)
    if not take:
      return False
    rest = segs[take:]
    if not rest:
      return False
    family = ""
    if len(rest) >= 2 and ".".join(rest[:2]) in self._families:
      family = ".".join(rest[:2])
      tail = rest[2:]
    elif rest[0] in self._families:
      family = rest[0]
      tail = rest[1:]
    else:
      return False
    if not tail:
      return True
    return len(tail) == 1 and family in self._var_families

  def entries(self) -> Iterator[Entry]:
    """Exactly the ``keys:`` rows: same ids, same extras, same spec clauses."""
    from kinemata.contract import Entry  # type: ignore[import-not-found]

    for key, row in self._rows.items():
      yield Entry(
        id=key, clauses=_clauses(row), extra=row if isinstance(row, dict) else {"value": row}
      )

  def line(self, entry: Entry) -> str:
    """One projected line: the bare identifier, upstream's default."""
    return entry.id

  def declared(self, identifier: str) -> bool:
    """Whether any manifest section this adapter reads declares one identifier."""
    if not isinstance(identifier, str) or not identifier:
      return False
    if identifier in self._concrete or identifier in self._known:
      return True
    if identifier in self._interiors:
      return True
    for ns in self._ns_shapes:
      if ns.fullmatch(identifier):
        return True
    for shape in self._shapes:
      match = shape.match(identifier)
      if match is not None and self._shape_admits(match):
        return True
    for shape in self._namespace_shapes:
      match = shape.match(identifier)
      if match is not None and self._shape_admits(match):
        return True
    return self._cross_declared(identifier)

  def resolve(self, identifier: str) -> tuple[str, ...]:
    """A declared identifier's governing spec clauses, else empty."""
    row = self._rows.get(identifier)
    if row is not None:
      return _clauses(row)
    for shape in self._shapes:
      match = shape.match(identifier)
      if match is not None and self._shape_admits(match):
        return shape.spec
    for shape in self._namespace_shapes:
      match = shape.match(identifier)
      if match is not None and self._shape_admits(match):
        return shape.spec
    return ()

  @cached_property
  def _detector(self) -> re.Pattern[str]:
    """The enumerated ids, longest first, under the identifier boundary."""
    ids = sorted(self._concrete, key=len, reverse=True)
    edge = self.boundary
    return re.compile(
      r"(?<!" + edge + r")(?:" + "|".join(re.escape(i) for i in ids) + r")(?!" + edge + r")"
    )

  def detect(self, text: str) -> list[str]:
    """Declared ids mentioned in one span of source, longest match wins."""
    seen: dict[str, None] = {}
    for match in self._detector.finditer(text):
      seen.setdefault(match.group(0), None)
    return list(seen)

  def candidates(self, text: str) -> list[str]:
    """Key mentions in one string literal: the whole of it, or an ``@``-ref in it.

    A literal that is not wholly key-shaped is PROSE -- help text, a message --
    and a key it names is ``kinemata claims``' business, not a use. An ``@``-ref
    is a use wherever it sits, so it is taken from prose too.
    """
    seen: dict[str, None] = {}
    if self._candidate_re.fullmatch(text):
      seen[text] = None
    for name in self._ref_names(text):
      seen.setdefault(name, None)
    return list(seen)

  def _ref_names(self, text: str) -> Iterator[str]:
    """Names of the ``@``-refs in *text*, read by the product's own ref parser.

    An ``@`` abutting an identifier character (``user@box.host``) opens no ref,
    and a name must pass the same candidate syntax as a whole literal. That
    syntax's segments admit no ``℘``, so a persona node's ref is not seen.
    """
    for at in re.finditer("@", text):
      i = at.start()
      if i and re.match(self.boundary, text[i - 1]):
        continue
      try:
        name, _end = match_ref(text, i)
      except SettingsError:
        continue
      if self._candidate_re.fullmatch(name):
        yield name

  def __post_init_check__(self) -> None:
    """A closed registry must recognize its own identifiers; ask it once."""
    if self.closed:
      self.candidates("")
