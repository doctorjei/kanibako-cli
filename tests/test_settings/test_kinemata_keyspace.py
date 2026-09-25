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
* a leaf under a PLUGIN agent's node, whose vocabulary is plugin-declared, is
  CONCEDED (spec §0, *Universal vs agent-specific*) -- and the product, judging
  it against core's table alone, refuses it. A CORE-OWNED node (``default``, the
  ``shell`` pseudo-agent) is judged by both, and so is the node itself.

Every key is also tried as a ``pref.<key>`` request: both sides hold the family
to the §2h allowlist (spec §0), so ``pref.box.image`` is refused by both.

kinemata is not imported here: the adapter must construct without it.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from kanibako.agent_ref import GENERAL_SLOT, PSEUDO_AGENT_NAMES, parse_agent_address
from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.kinemata_keyspace import KeyspaceRegistry
from kanibako.settings.settings_keyspace import KeyClass, glob_match, key_class

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
#: The nodes a concrete ``keys:`` row names: core's own, since the core registry
#: never enumerates a plugin's values.
_CORE_NODES = frozenset(
  k.split(".")[1] for k in _KEYS if k.startswith(_TIER_HEAD + ".") and "<" not in k.split(".")[1]
)
_VALID_AGENTS = (_PLUGIN_AGENT, _TIER_NODE)
_AGENT_NODES = (_PLUGIN_AGENT, *sorted(_CORE_NODES))
#: A node no tree registers and core does not own: the plugin agent, two letters swapped.
_TYPO_NODE = "cluade"
#: The §2h allowlist, read from the manifest's own ``pref`` section.
_ALLOWLIST = tuple(_DOC["pref"]["allowlist"])

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


def _instantiate(template: str, nodes: tuple[str, ...] = _AGENT_NODES) -> list[str]:
  """Every sample spelling of one parametric ``keys:`` row."""
  samples = {
    "<agent>": nodes,
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


def _allowlisted(target: str) -> bool:
  return any(glob_match(entry, target) for entry in _ALLOWLIST)


#: Every corpus key a pref may name, requested: the declared ``pref`` members.
_PREF_CORPUS = sorted("pref." + k for k in _CORPUS if _allowlisted(k))
#: Every corpus key a pref may NOT name, requested: declared targets, not members.
_PREF_REFUSED = sorted("pref." + k for k in _CORPUS if not _allowlisted(k))


#: The manifest's spelling of the ACTIVE-agent placeholder (``agent.active`` in
#: ``categories.scopes``, the spec's ``agent.<active>``), read as a literal node. It is
#: no agent, so a key spelled through it is refused -- the reading the adapter once got
#: wrong by taking the scope token as a real segment.
_PLACEHOLDER_NODES = tuple(
  node for scope in _CATEGORIES["scopes"]
  for head, _, node in [scope.partition(".")]
  if head == _TIER_HEAD and node and node not in _CORE_NODES
)


def _node_typos() -> list[str]:
  """Every ``<agent>`` row, and every agent scope-by-category key, at a node that is no
  agent: a typo'd plugin name, and the placeholder token spelled literally."""
  out: set[str] = set()
  for node in (_TYPO_NODE, *_PLACEHOLDER_NODES):
    for key in _KEYS:
      if "<agent>" in key:
        out.update(_instantiate(key, (node,)))
    out.update(f"{_TIER_HEAD}.{node}.{tail}" for tail in _CATEGORY_TAILS)
  return sorted(out)


def _undiscriminated() -> list[str]:
  """Every category tail directly under the agent head, with NO node: ``agent.env.FOO``,
  ``agent.caches`` -- and, one segment deeper, the relic ``agent.common.plugins``
  spelling. The agent tier is discriminated (spec §2d), so none is a key."""
  return sorted(
    f"{_TIER_HEAD}.{tail}{extra}" for tail in _CATEGORY_TAILS for extra in ("", ".plugins")
  )


def _mutations() -> list[str]:
  """Every corpus key -- and every pref member -- with its leaf typo'd, and with a
  segment appended.

  A typo'd VAR under a VAR family is another VAR, so those keys take only the
  append.
  """
  out: set[str] = set()
  for key in [*_CORPUS, *_PREF_CORPUS]:
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

  The plugin namespace arm (``plugin_contributed.namespace``) names the node, and
  the ``<key>`` mirror row with no ``<agent>`` names a node that is a runtime fact.
  A CORE-OWNED node is not conceded: core owns it (spec §0). A ``pref`` request is
  conceded when its target is and the allowlist admits it.
  """
  head_, _, target = identifier.partition(".")
  if head_ == "pref":
    return _allowlisted(target) and _conceded(target)
  segs = identifier.split(".")
  head = _DOC["plugin_contributed"]["namespace"].split(".<")[0].split(".")
  if len(segs) == len(head) + 2 and segs[: len(head)] == head:
    if not (head == [_TIER_HEAD] and segs[len(head)] in _CORE_NODES):
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

  def test_the_pref_corpus_is_populated_both_ways(self) -> None:
    assert _PREF_CORPUS
    assert _PREF_REFUSED
    assert _node_typos()
    assert _PLACEHOLDER_NODES
    assert f"{_TIER_HEAD}.common.plugins" in _undiscriminated()

  def test_the_core_owned_nodes_are_the_pseudo_agents(self) -> None:
    """The nodes the manifest spells concretely are the ones ``agent_ref`` reserves."""
    assert _CORE_NODES == PSEUDO_AGENT_NAMES


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

  @pytest.mark.parametrize("key", _PREF_CORPUS)
  def test_every_allowlisted_request_is_declared_by_both(
    self, registry: KeyspaceRegistry, key: str
  ) -> None:
    assert _product_accepts(key), key_class(key, valid_agents=_VALID_AGENTS).reason
    assert registry.declared(key)
    assert registry.resolve(key) != ()

  @pytest.mark.parametrize("identifier", _PREF_REFUSED)
  def test_a_request_outside_the_allowlist_is_refused_by_both(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert registry.resolve(identifier) == ()

  @pytest.mark.parametrize("identifier", _node_typos())
  def test_an_unknown_agent_node_is_refused_by_both(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert not registry.declared("pref." + identifier)

  @pytest.mark.parametrize("identifier", _undiscriminated())
  def test_a_category_with_no_agent_node_is_refused_by_both(
    self, registry: KeyspaceRegistry, identifier: str
  ) -> None:
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert registry.resolve(identifier) == ()

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
  """The tiers core owns are never conceded; a two-segment family is a tail."""

  @pytest.mark.parametrize("node", sorted(_CORE_NODES))
  def test_an_undeclared_core_owned_leaf_is_refused(
    self, registry: KeyspaceRegistry, node: str
  ) -> None:
    identifier = f"{_TIER_HEAD}.{node}.bogus"
    assert not _product_accepts(identifier)
    assert not registry.declared(identifier)
    assert registry.resolve(identifier) == ()

  def test_the_addressable_shell_is_a_core_owned_node(self, registry: KeyspaceRegistry) -> None:
    """``agent_ref.parse_agent_address`` lets a ref select the shell pseudo-agent;
    the gate judges that node's leaves rather than conceding them."""
    node, _harness = parse_agent_address(GENERAL_SLOT)
    assert node in _CORE_NODES
    own = sorted(k for k in _KEYS if k.startswith(f"{_TIER_HEAD}.{node}."))
    assert own and all(registry.declared(k) for k in own)
    assert not registry.declared(f"{_TIER_HEAD}.{node}.bogus")

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
