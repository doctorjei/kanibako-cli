"""Agent-ref parsing: the ``persona+harness`` selection grammar (persona MVP).

**_Terminology_**
- _harness_: agent runtime/plugin; right of separator (e.g., ``claude``)
- _persona_: identity/"ghost"; left of separator (``gemma``). Some are built-in (claude, codex)
- _agent_: canonical combo form ``persona℘harness`` (``gemma℘claude``). (alias: **node-name**)
"""

from __future__ import annotations
import re
from kanibako.errors import ConfigError

# Persona/harness separators: one for typing [+], another internally to avoid collisions [℘]
SEPARATORS = (PLUS_SEP := "+", CANONICAL_SEP := "℘")

# "Safe" characters in agent names
_SAFE_EXTRA = frozenset("-_")
SEGMENT_CHAR_CLASS = r"\w" + "".join(re.escape(ch) for ch in sorted(_SAFE_EXTRA))

# Appended to rejection message when offending segment contains ``.`` - a mistake users actually
# make (``kimi.k3+claude``), & "only letters and digits" does not explain why dot is not one.
_DOT_HINT = "; '.' is reserved as settings key-path separator and cannot appear in an agent name"


def _is_segment_safe(segment: str) -> bool:
  """A non-empty segment of only letters/digits (any language) plus ``-``/``_``."""
  return all(ch.isalnum() or ch in _SAFE_EXTRA for ch in segment) if segment else False


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
  """
  if not isinstance(raw, str):
    raise ConfigError(f"agent ref must be a string, got {type(raw).__name__}: {raw!r}")
  ref = raw.strip()

  if not ref:
    raise ConfigError("agent ref is empty")
  idx = _first_sep_index(ref)

  if idx == -1:
    # Bare: node == harness == the whole (validated) name.
    if not _is_segment_safe(ref):
      raise ConfigError(f"invalid agent name '{ref}': names may contain only letters and "
                        f"digits (any language), '-', & '_'{_DOT_HINT if '.' in ref else ''}")
    return ref, ref

  if not _is_segment_safe(persona := ref[:idx]):
    raise ConfigError(f"invalid agent ref '{raw}': persona segment '{persona}' must be non-empty "
                      f"& contain only letters & digits (any language), '-', & '_' (no separator)"
                      f"{_DOT_HINT if '.' in persona else ''}")

  if not _is_segment_safe(harness := ref[idx+1:]):
    raise ConfigError(f"invalid agent ref '{raw}': harness segment '{harness}' must be non-empty "
                      f"& contain only letters & digits (any language), '-', & '_' (no separator)"
                      f"{_DOT_HINT if '.' in harness else ''}")

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
