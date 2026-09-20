"""Agent-ref parsing: the ``persona+harness`` selection grammar (persona MVP).

**_Terminology_**
- _harness_: agent runtime/plugin; right of separator (e.g., ``claude``)
- _persona_: identity/"ghost"; left of separator (``gemma``). Some are built-in (claude, codex)
- _agent_: canonical combo form ``persona℘harness`` (``gemma℘claude``). (alias: **node-name**)
"""

from __future__ import annotations
import re
from kanibako.errors import ConfigError

# Persona/harness separators.  ``+`` is the spelling wherever a human looks, the on-disk store
# dirname included (:func:`kanibako.settings.agent_config.store_dirname`).  ``℘`` exists for ONE
# reason: a key path is split on ``.`` into ``SEGMENT_CHAR_CLASS`` segments (below), which admit
# no ``+`` — so ``agent.nav+claude.model`` would match only ``agent.nav`` and silently resolve a
# different key.  A node wears ``℘`` INSIDE a key and nowhere else.
SEPARATORS = (PLUS_SEP := "+", CANONICAL_SEP := "℘")

# "Safe" characters in agent names
_SAFE_EXTRA = frozenset("-_")
SEGMENT_CHAR_CLASS = r"\w" + "".join(re.escape(ch) for ch in sorted(_SAFE_EXTRA))

# Appended to rejection message when offending segment contains ``.`` - a mistake users actually
# make (``kimi.k3+claude``), & "only letters and digits" does not explain why dot is not one.
_DOT_HINT = "; '.' is reserved as settings key-path separator and cannot appear in an agent name"

# The PSEUDO-AGENT names (keyspec §2d, "Pseudo-agent(s)").  A pseudo-agent is not a true
# agent but serves the AGENT ROLE, so its name is reserved: it already owns an
# ``agent.<name>.*`` cascade slot & a store dir, & a true agent claiming one would own them
# too.  The refusal sits at THIS gate because every user-supplied ref passes through it —
# the CLI's ``-A``, ``kanibako agent``, the persona store, the stored-agent readers.
# ⚑ EXACT SPELLING, never a case fold or a prefix test: ``Shell`` & ``shellx`` are ordinary
# names, & widening a user-facing refusal past the names the spec reserves is its own defect.
# ⚑ NOT the same rule as ``settings.config_dest.check_agent_node``'s ``default`` arm — that
# one refuses a settings ROUTE & carries the any-agent tier's own cure.  It short-circuits on
# ``default`` before reaching this parser, so that message is unchanged.
# ⚑ WIDENING THIS SET obliges a matching entry in ``kinemata.toml``'s ``reserved-agent-names``.
PSEUDO_AGENT_NAMES = frozenset({"default", "shell"})

# The AGENT-SLOT name a launch wears when NO agent is involved — a no-agent/plain-shell
# box, & the agent-less resolves that stand in for one (``kanibako init``'s agent file,
# the workset previews, the effective-settings dumps).  It OCCUPIES the ``agents/<node>/``
# dir & the ``agent.<node>.*`` cascade POSITION as a real node-name does — which is why
# ``_materialize_box_agent_mirror``'s blank short-circuit does not fire & the
# ``agent.default`` backstop still reaches a no-agent launch.
# 🛑 IT IS NOT A DECLARED AGENT, & the difference is not cosmetic: ``agent.general.*`` is
# UNDECLARED, so a closed-keyspace resolve REFUSES it.  That is why
# ``settings_cli_level.build_cli_level`` is given ``active_agent=None`` for a no-agent
# launch — spelling ``agent.general.model`` there would fabricate a key.
# ⚑ THIS COMMENT IS THE CONSTANT'S ONLY AUTHORITY.  The eleven literals it replaced each
# carried their own value; ``tests/test_agent_ref.py`` pins the VALUE, & the spelling is a
# fact about a user's store — rename it & the on-disk dir moves.
# ⚑ A TEMPLATE/CHAPTER FALLBACK SLOT, NOT AN AGENT (keyspec ``templates/general/standard``,
# & the ``general`` canon chapter).  Nothing SELECTS it —
# :attr:`kanibako.settings.agent_select.AgentSelection.selection_level` installs NOTHING for
# a no-agent box rather than pinning ``system.agent`` here.  It reaches a slot only as the
# ``else`` arm where a resolved target would otherwise supply the name.
# ⚑ DELIBERATELY NOT in ``PSEUDO_AGENT_NAMES``: that set is a user-facing REFUSAL, & this
# name is not reserved against a user's agent, persona or harness.
GENERAL_SLOT = "general"


def _is_segment_safe(segment: str) -> bool:
  """A non-empty segment of only letters/digits (any language) plus ``-``/``_``."""
  return all(ch.isalnum() or ch in _SAFE_EXTRA for ch in segment) if segment else False


def reserved_pseudo_agent_reason(name: str) -> str | None:
  """Why *name* may not be claimed by an agent, persona or harness — or ``None``.

  ONE sentence for every site that refuses a reserved name: the spec reserves the names
  against all three roles in a single breath, so three refusals must not drift apart.

  ⚑ A REASON, NOT A RAISE — :mod:`kanibako.targets` skips a badly-named plugin rather
  than raising, so the sentence has to be usable without an exception.
  """
  if name not in PSEUDO_AGENT_NAMES:
    return None
  return (f"'{name}' is a RESERVED pseudo-agent name (spec §2d, 'Pseudo-agent(s)'); it may "
          f"not name an agent, a persona, or a harness")


def _first_sep_index(raw: str) -> int:
  """Index of the FIRST separator in *raw*, or ``-1`` if none."""
  return min([i for i in [raw.find(sep) for sep in SEPARATORS] if i != -1], default=-1)


def display_agent_ref(node: str) -> str:
  """Return the USER-FACING form of a *node*-name (``℘`` -> ``+``)."""
  return node.replace(CANONICAL_SEP, PLUS_SEP)


def canonicalize_agent_ref(raw: str) -> str:
  """Return the canonical *node*-name for a raw ref (``+`` -> ``℘``)."""
  return parse_agent_ref(raw)[0] # Just the node


def parse_agent_ref(raw: str) -> tuple[str, str]:
  """Parse an agent ref into ``(node, harness)``.
  - One-word refs: (``claude``) parse to ``(raw, raw)`` (persona == harness).
  - Composite refs: rejoined, parsed to ``("persona℘harness", "harness")``

  ⚑ TWO RULES, NOT ONE: the segment CHARSET (:func:`_is_segment_safe`) & the pseudo-agent
  RESERVATION (:data:`PSEUDO_AGENT_NAMES`).  They are checked apart because they are
  different facts — the charset is about spelling, the reservation about who already owns
  the name — & only the charset one is a property of a character.
  """
  if not isinstance(raw, str):
    raise ConfigError(f"agent ref must be a string, got {type(raw).__name__}: {raw!r}")
  ref = raw.strip()

  if not ref:
    raise ConfigError("agent ref is empty")
  idx = _first_sep_index(ref)

  if idx == -1:
    # Bare: node == harness == the whole (validated) name.  A bare ref names the persona
    # AND the harness, so ONE reservation check covers both roles here.
    if not _is_segment_safe(ref):
      raise ConfigError(f"invalid agent name '{ref}': names may contain only letters and "
                        f"digits (any language), '-', & '_'{_DOT_HINT if '.' in ref else ''}")
    if (why := reserved_pseudo_agent_reason(ref)) is not None:
      raise ConfigError(why)
    return ref, ref

  if not _is_segment_safe(persona := ref[:idx]):
    raise ConfigError(f"invalid agent ref '{raw}': persona segment '{persona}' must be non-empty "
                      f"& contain only letters & digits (any language), '-', & '_' (no separator)"
                      f"{_DOT_HINT if '.' in persona else ''}")

  if (why := reserved_pseudo_agent_reason(persona)) is not None:
    raise ConfigError(f"invalid agent ref '{raw}': persona segment {why}")

  if not _is_segment_safe(harness := ref[idx+1:]):
    raise ConfigError(f"invalid agent ref '{raw}': harness segment '{harness}' must be non-empty "
                      f"& contain only letters & digits (any language), '-', & '_' (no separator)"
                      f"{_DOT_HINT if '.' in harness else ''}")

  if (why := reserved_pseudo_agent_reason(harness)) is not None:
    raise ConfigError(f"invalid agent ref '{raw}': harness segment {why}")

  node = f"{persona}{CANONICAL_SEP}{harness}"
  return node, harness


def harness_of(node: str) -> str:
  """Return the harness (part right of ``℘``) of a *node*-name."""
  _, _, harness = node.rpartition(CANONICAL_SEP)
  return harness if harness else node


def persona_of(node: str) -> str:
  """Return the persona segment (part LEFT of ``℘``) of a *node*-name."""
  persona, sep, _ = node.rpartition(CANONICAL_SEP)
  return persona if sep else node


def with_harness(node: str, harness: str) -> str:
  """Return *node* with its harness segment REPLACED by *harness*."""
  persona, sep, _ = node.rpartition(CANONICAL_SEP) # bare node if sep is ""
  return f"{persona}{CANONICAL_SEP}{harness}" if sep else harness
