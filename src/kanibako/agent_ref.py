"""Agent-ref parsing: the ``persona+harness`` selection grammar (persona MVP).

**_Terminology_**
- _harness_: agent runtime/plugin; right of separator (e.g., ``claude``)
- _persona_: identity/"ghost"; left of separator (``gemma``). Some are built-in (claude, codex)
- _agent_: canonical combo form ``persona℘harness`` (``gemma℘claude``). (alias: **node-name**)
"""

from __future__ import annotations
import re
from kanibako.errors import ConfigError
from kanibako.identifiers import find_identifier

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
# the CLI's ``--agent``, ``kanibako agent``, the persona store, the stored-agent readers.
# ⚑ THE RESERVATION REFUSES A CLAIMANT, NOT AN ADDRESS: a ref that ADDRESSES the built-in
# shell pseudo-agent reaches it through :func:`parse_agent_address`, which is this gate
# plus that one owner — the name still names nobody else.
# ⚑ FOLDS FOR COMPARISON, never a prefix test ([R172], keyspec §0): ``Shell``
# is the same identifier as ``shell`` & is refused like it, while ``shellx``
# stays an ordinary name, & widening a user-facing refusal past the names the
# spec reserves is its own defect.
# ⚑ NOT the same rule as ``settings.config_dest.check_agent_node``'s ``default`` arm — that
# one refuses a settings ROUTE & carries the any-agent tier's own cure.  It short-circuits on
# ``default`` before reaching this parser, so that message is unchanged.
# ⚑ WIDENING THIS SET obliges a matching entry in ``kinemata.toml``'s ``reserved-agent-names``.
PSEUDO_AGENT_NAMES = frozenset({"default", "shell"})

# The AGENT-SLOT name a launch wears when NO true agent is involved — a
# plain-shell box, & the agent-less resolves that stand in for one
# (``kanibako init``'s agent file, the workset previews, the effective-settings
# dumps).  It OCCUPIES the ``agents/<node>/`` dir & the ``agent.<node>.*``
# cascade POSITION as a real node-name does — which is why
# ``_materialize_box_agent_mirror``'s blank short-circuit does not fire & the
# ``agent.default`` backstop still reaches a no-agent launch — for every key the
# shell tier does not supply itself (why: ``core-defaults.yaml`` ``agent_shell:``).
# 🛑 IT IS A DECLARED PSEUDO-AGENT (keyspec §2d, "Pseudo-agent(s)"), & the
# difference from the old ``"general"`` slot is not cosmetic: ``agent.shell.*``
# is DECLARED, so a closed-keyspace resolve ACCEPTS it where it REFUSED
# ``agent.general.*``.  That is why ``settings_cli_level.build_cli_level`` is
# still given ``active_agent=None`` for a no-agent launch — spelling
# ``agent.shell.model`` there would install a flag value onto the shell tier no
# parser exposes flags for.
# ⚑ THIS COMMENT IS THE CONSTANT'S ONLY AUTHORITY.  The eleven literals it replaced each
# carried their own value; ``tests/test_agent_ref.py`` pins the VALUE, & the spelling is a
# fact about a user's store — rename it & the on-disk dir moves.
# ⚑ A PSEUDO-AGENT, NOT A TEMPLATE FALLBACK (keyspec ``agent.shell.*``).  Nothing
# SELECTS it implicitly —
# :attr:`kanibako.settings.agent_select.AgentSelection.selection_level` installs NOTHING for
# a node-less box rather than pinning ``system.agent`` here.  It reaches a slot only as the
# ``else`` arm where a resolved target would otherwise supply the name, or BY NAME
# (``--agent shell``, ``pref.system.agent``, ``system.agent: shell`` — spec §2b).
# ⚑ IN ``PSEUDO_AGENT_NAMES`` BY DESIGN, unlike its ``"general"`` predecessor:
# that set is a user-facing REFUSAL against agents/personas/harnesses CLAIMING the
# name, & the shell slot IS the reserved owner — the boundary working, not bent
# ([R175]: built-in is a category, not a carve-out).
GENERAL_SLOT = "shell"


def _is_segment_safe(segment: str) -> bool:
  """A non-empty segment of only letters/digits (any language) plus ``-``/``_``."""
  return all(ch.isalnum() or ch in _SAFE_EXTRA for ch in segment) if segment else False


def reserved_pseudo_agent_reason(name: str) -> str | None:
  """Why *name* may not be claimed by an agent, persona or harness — or ``None``.

  ONE sentence for every site that refuses a reserved name: the spec reserves the names
  against all three roles in a single breath, so three refusals must not drift apart.

  ⚑ A REASON, NOT A RAISE — :mod:`kanibako.targets` skips a badly-named plugin rather
  than raising, so the sentence has to be usable without an exception.

  ⚑ FOLDS FOR COMPARISON through :func:`kanibako.identifiers.find_identifier` —
  the one carrier of the rule, so no hand fold lives here for the pin to catch.
  """
  if find_identifier(name, PSEUDO_AGENT_NAMES) is None:
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


def parse_agent_address(raw: str) -> tuple[str, str]:
  """:func:`parse_agent_ref` for a ref that ADDRESSES an agent rather than NAMING one.

  A ref that selects or configures an agent — ``--agent``, ``system.agent``, the
  ``kanibako agent <agent>`` positional, a ``KANIBAKO_AGENT`` stamp read back — may name
  the built-in shell pseudo-agent: keyspec §2b invokes it *"as any other agent"*, and
  its §2d fence declares its own settings file.  Such a ref parses to
  ``(GENERAL_SLOT, GENERAL_SLOT)``; every other ref goes through
  :func:`parse_agent_ref` unchanged.

  ⚑ THE §2d RESERVATION STILL HOLDS for everything that would CLAIM the name — a plugin,
  a persona segment, a harness segment ([R175]: the built-in is the owner, so
  addressing it claims nothing).  ``shell`` inside a composite ref is refused as
  before, since a persona cannot ride a pseudo-agent ([R178]).
  ⚑ ONLY ``shell``, not every pseudo-agent: ``default`` is the any-agent fallback tier,
  not an agent a ref can select, and its §2d fence declares no settings file.
  ⚑ FOLDS FOR COMPARISON ([R172]): ``Shell`` addresses the same agent, and the node
  returned is the lowercase slot.
  """
  if isinstance(raw, str) and find_identifier(raw.strip(), {GENERAL_SLOT}) is not None:
    return GENERAL_SLOT, GENERAL_SLOT
  return parse_agent_ref(raw)


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
