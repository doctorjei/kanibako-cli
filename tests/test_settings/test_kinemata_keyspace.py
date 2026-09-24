"""The kinemata keyspace adapter pinned against the product's own key check.

:class:`KeyspaceRegistry` answers membership for the ``kinemata undeclared`` gate
from the manifest; :func:`key_class` answers it for the product. Two answers to
one question drift unless something compares them, so every identifier below is
DERIVED from the manifest and judged by both. Where they may legitimately differ
the difference is a named predicate, asserted in both directions, never a skip:

* a RENAMED spelling (``not_keys.renamed``) is declared, so a mention of it is
  recognized in order to be refused with its cure -- and the product refuses
  it as a key. ``not_keys.code_residue`` is the opposite case: code-only names
  never spec-sanctioned, which both refuse, so a live use is a stray;
* a leaf under an agent node whose vocabulary is plugin-declared is CONCEDED
  (spec §0, *Universal vs agent-specific*) -- and the product, judging it
  against core's table alone, refuses it.

kinemata is not imported here: the adapter must construct without it.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.kinemata_keyspace import KeyspaceRegistry
from kanibako.settings.settings_keyspace import KeyClass, key_class

_DOC = manifest_doc()
_KEYS: dict[str, Any] = _DOC["keys"]
_CATEGORIES: dict[str, Any] = _DOC["categories"]

#: A plugin agent's node, standing in for any agent this machine cannot read.
_PLUGIN_AGENT = "claude"
_SAMPLE_VAR = "FOO"


def _tier_rule() -> tuple[str, str, str]:
  """The agent tier's head, default node and prefix, read from its own row."""
  for key, row in _KEYS.items():
    if "<agent>" in key and "<key>" in key:
      prefix = row["fallback"].split("<key>")[0]
      return key.split(".")[0], prefix.rstrip(".").rsplit(".", 1)[-1], prefix
  raise AssertionError("the manifest declares no agent.<agent>.<key> row")


_TIER_HEAD, _TIER_NODE, _TIER_PREFIX = _tier_rule()
_VALID_AGENTS = (_PLUGIN_AGENT, _TIER_NODE)
_AGENT_NODES = (_PLUGIN_AGENT, _TIER_NODE)

_FAMILIES = [f for f, block in _CATEGORIES.items() if isinstance(block, dict)]
_VAR_FAMILIES = {f for f in _FAMILIES if "VAR" in map(str, _CATEGORIES[f].get("parametric", ()))}
#: Every category tail a scope may carry, VAR families instantiated.
_CATEGORY_TAILS = [f + "." + _SAMPLE_VAR if f in _VAR_FAMILIES else f for f in _FAMILIES]
#: The default tier's own leaves: the vocabulary ``<key>`` falls back to.
_TIER_LEAVES = sorted(
  k[len(_TIER_PREFIX) :] for k in _KEYS if k.startswith(_TIER_PREFIX) and "<" not in k
)


def _scopes() -> list[str]:
  """Category scopes, the manifest's agent-node spelling instantiated per node."""
  out = []
  for scope in _CATEGORIES["scopes"]:
    if scope.split(".")[0] == _TIER_HEAD:
      out += [_TIER_HEAD + "." + node for node in _AGENT_NODES]
    else:
      out.append(scope)
  return out


def _instantiate(template: str) -> list[str]:
  """Every sample spelling of one parametric ``keys:`` row."""
  samples = {
    "<agent>": _AGENT_NODES,
    "<key>": _TIER_LEAVES + _CATEGORY_TAILS,
    "<VAR>": (_SAMPLE_VAR,),
  }
  out = [template]
  for token, values in samples.items():
    out = [o.replace(token, v, 1) for o in out for v in values] if token in template else out
  assert all("<" not in o for o in out), f"{template!r} has a placeholder with no sample"
  return out


def _declared_keys() -> list[str]:
  """The manifest's keys: concrete rows, parametric rows, scope-by-category."""
  corpus: set[str] = set()
  for key in _KEYS:
    corpus.update(_instantiate(key) if "<" in key else [key])
  corpus.update(scope + "." + tail for scope in _scopes() for tail in _CATEGORY_TAILS)
  return sorted(corpus)


_CORPUS = _declared_keys()


def _mutations() -> list[str]:
  """Every corpus key with its leaf typo'd, and with a segment appended.

  A typo'd VAR under a VAR family is another VAR, so those keys take only the
  append.
  """
  out: set[str] = set()
  for key in _CORPUS:
    head, _, leaf = key.rpartition(".")
    if head.rpartition(".")[2] not in _VAR_FAMILIES:
      out.add(head + "." + leaf + "_typo")
    out.add(key + ".extra")
  return sorted(out)


def _not_key_members(section: str) -> list[str]:
  """The exact (non-template) members of one ``not_keys`` section."""
  block = _DOC["not_keys"].get(section) or {}
  return sorted(k for k in block if "<" not in k and "*" not in k)


_RENAMED = _not_key_members("renamed")
_CODE_RESIDUE = _not_key_members("code_residue")


def _conceded(identifier: str) -> bool:
  """A one-segment leaf under a node whose vocabulary a plugin declares.

  The plugin namespace arms (``plugin_contributed.namespace``) name the nodes, and
  the ``<key>`` mirror row with no ``<agent>`` names a node that is a runtime fact.
  The agent tier's own default node is not conceded: core owns it (spec §0).
  ⚑ This reaches the ``shell`` pseudo-agent too. The product judges shell against
  core's table, but the manifest carries no pseudo-agent set to derive that from.
  """
  segs = identifier.split(".")
  for arm in _DOC["plugin_contributed"]["namespace"].split(" and "):
    head = arm.strip().split(".<")[0].split(".")
    if len(segs) == len(head) + 2 and segs[: len(head)] == head:
      if (head, segs[len(head)]) != ([_TIER_HEAD], _TIER_NODE):
        return True
  for key in _KEYS:
    if key.endswith(".<key>") and "<agent>" not in key:
      mirror = key[: -len("<key>")]
      if identifier.startswith(mirror) and "." not in identifier[len(mirror) :]:
        return True
  return False


def _product_accepts(identifier: str) -> bool:
  return key_class(identifier, valid_agents=_VALID_AGENTS).cls is KeyClass.KEY


@pytest.fixture(scope="module")
def registry() -> KeyspaceRegistry:
  return KeyspaceRegistry(closed=True)


class TestTheCorpusIsDerived:
  """Anti-vacuity: the corpus spans every row, and each exemption has members."""

  def test_the_corpus_covers_every_keys_row(self) -> None:
    concrete = [k for k in _KEYS if "<" not in k]
    templates = [k for k in _KEYS if "<" in k]
    assert len(_CORPUS) >= len(concrete) + len(templates) * len(_AGENT_NODES)
    assert set(concrete) <= set(_CORPUS)

  def test_both_exemptions_are_populated(self) -> None:
    assert [m for m in _mutations() if _conceded(m)]
    assert _RENAMED
    assert _CODE_RESIDUE


class TestDeclaredAgreesWithTheProduct:
  """``declared()`` against :func:`key_class` over the manifest-derived corpus."""

  @pytest.mark.parametrize("key", _CORPUS)
  def test_every_manifest_key_is_declared_by_both(
    self, registry: KeyspaceRegistry, key: str
  ) -> None:
    assert _product_accepts(key), key_class(key, valid_agents=_VALID_AGENTS).reason
    assert registry.declared(key)

  @pytest.mark.parametrize("identifier", [m for m in _mutations() if not _conceded(m)])
  def test_a_mutated_key_is_refused_by_both(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert registry.resolve(identifier) == ()

  @pytest.mark.parametrize("identifier", [m for m in _mutations() if _conceded(m)])
  def test_a_plugin_vocabulary_leaf_is_conceded_not_judged(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert registry.declared(identifier)

  @pytest.mark.parametrize("identifier", _RENAMED)
  def test_a_renamed_spelling_is_declared_and_still_not_a_key(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert registry.declared(identifier)

  @pytest.mark.parametrize("identifier", _CODE_RESIDUE)
  def test_a_code_residue_name_is_a_stray_to_both(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)

  def test_the_default_tier_provider_is_code_residue(self, registry: KeyspaceRegistry) -> None:
    """Not a default-tier leaf (spec §0), and the manifest records it only as residue."""
    identifier = _TIER_PREFIX + "provider"
    assert identifier in _CODE_RESIDUE
    assert not registry.declared(identifier)


class TestTheDefaultTierIsJudged:
  """The tier core owns is never conceded; a two-segment family is a tail."""

  def test_an_undeclared_default_leaf_is_refused(self, registry: KeyspaceRegistry) -> None:
    identifier = _TIER_PREFIX + "bogus"
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert registry.resolve(identifier) == ()

  @pytest.mark.parametrize("family", [f for f in _FAMILIES if "." in f])
  def test_a_two_segment_family_is_declared_under_a_plugin_agent(
    self, registry: KeyspaceRegistry, family: str
  ) -> None:
    identifier = f"{_TIER_HEAD}.{_PLUGIN_AGENT}.{family}"
    assert _product_accepts(identifier)
    assert registry.declared(identifier)
    assert registry.resolve(identifier) != ()


_CONCRETE = sorted(k for k in _KEYS if "<" not in k)

#: Literal contents that once produced stray candidates: a key-shaped run inside
#: a longer string, abutting an identifier character or buried in prose.
_ONCE_STRAYS = [
  ("legacy.config.key", "config.key"),
  ("new.config.key", "config.key"),
  ("*.meta.json", "meta.json"),
  (".meta.json", "meta.json"),
  ("SUBAGENTS, BY ROLE (agentType from sibling .meta.json)", "meta.json"),
  (" section-<id>.meta.json per attempt: status, finish_reason, timing, byte count.\n", "meta.json"),
]


class TestCandidatesAreKeyLiteralsAndRefs:
  """One string literal's content is a key mention whole, or through an ``@``-ref.

  kinemata's ``strings`` mode hands the adapter one evaluated literal per call
  and never a comment, a docstring or an f-string; the pinned input contract is
  asserted first, because every other case here is only true under it.
  """

  def test_the_input_contract_is_one_python_literal(self) -> None:
    assert KeyspaceRegistry.match_mode == "strings"
    assert KeyspaceRegistry.suffixes == (".py",)

  @pytest.mark.parametrize("key", _CONCRETE)
  def test_a_wholly_key_shaped_literal_is_a_candidate(
    self, registry: KeyspaceRegistry, key: str
  ) -> None:
    assert registry.candidates(key) == [key]

  @pytest.mark.parametrize("key", _CONCRETE)
  def test_the_same_key_in_prose_is_not(self, registry: KeyspaceRegistry, key: str) -> None:
    assert registry.candidates(f"Set {key} in the box file.") == []
    assert registry.candidates(f"# {key} is read here") == []
    assert registry.candidates(f"Read {key} and return it.\n\n  Longer docstring text.") == []

  @pytest.mark.parametrize("key", _CONCRETE)
  def test_an_at_ref_in_prose_is(self, registry: KeyspaceRegistry, key: str) -> None:
    assert registry.candidates(f"defaults to @{key}/common") == [key]
    assert registry.candidates(f"defaults to @{{{key}}}.bak") == [key]

  def test_the_ref_name_is_the_ref_parsers_whole_extent(self, registry: KeyspaceRegistry) -> None:
    key = _CONCRETE[0]
    assert registry.candidates(f"@{key}.jsonl") == [f"{key}.jsonl"]

  def test_an_at_abutting_an_identifier_opens_no_ref(self, registry: KeyspaceRegistry) -> None:
    assert registry.candidates(f"user@{_CONCRETE[0]}") == []

  @pytest.mark.parametrize(("literal", "stray"), _ONCE_STRAYS)
  def test_a_key_shaped_run_inside_a_longer_literal_is_not(
    self, registry: KeyspaceRegistry, literal: str, stray: str
  ) -> None:
    assert stray not in registry.candidates(literal)

  def test_a_whole_filename_literal_still_is(self, registry: KeyspaceRegistry) -> None:
    """A filename is declared as a constant, never exempted by its shape."""
    assert registry.candidates("meta.json") == ["meta.json"]


class TestDetectKeepsTheIdentifierEdge:
  """``detect()`` reads whole files for ``unused``, so it matches runs, edged."""

  @pytest.mark.parametrize("key", _CONCRETE)
  def test_a_quoted_key_is_seen_and_an_embedded_one_is_not(
    self, registry: KeyspaceRegistry, key: str
  ) -> None:
    assert key in registry.detect(f'x = "{key}"')
    assert key not in registry.detect(f"x.{key}")


def test_it_constructs_without_kinemata(monkeypatch: pytest.MonkeyPatch) -> None:
  """kinemata is an optional dependency: only ``entries()`` may need it."""
  monkeypatch.setitem(sys.modules, "kinemata", None)
  monkeypatch.setitem(sys.modules, "kinemata.contract", None)
  assert KeyspaceRegistry(closed=True).declared(_CORPUS[0])
