"""Agent-ref parsing: the ``persona+harness`` selection grammar (persona MVP).

An ``--agent`` value is a *ref* that names an agent as ``persona+harness`` —
for example ``navigator+claude``.  This module is the single, dependency-light,
pure parser for that grammar; every ref source (explicit ``--agent``, box /
workset / system settings) is normalised through here on the way into the
resolver so the rest of the code deals only in *node-names*.

Terminology (design SOT ``plans/2026-06-24-agent-variant-DESIGN.md``):

* **harness** — the agent runtime/plugin, right of the separator (``claude``).
  Resolves the target/plugin (``resolve_target`` / ``discover_targets`` key).
* **persona** — the identity, left of the separator (``navigator``); a node
  whose persona name differs from its harness is itself called *a persona*.
* **node-name** — the canonical fused form ``persona℘harness`` (``navigator℘claude``).
  This is the on-disk ``agents/<node>/`` dir and the keyspace ``agent.<node>.*``
  slot.  It is what ``resolve_agent`` returns and what ``KANIBAKO_AGENT`` stamps.

Separators: the human-typable ``+`` (U+002B) and the canonical ``℘`` (U+2118,
SCRIPT CAPITAL P — "persona").  Both are accepted on input; the node-name always
canonicalises the separator to ``℘``.  Only the FIRST separator splits (a persona
segment may not itself contain a separator; the harness segment is whatever
follows, verbatim, and is likewise separator-checked).

Backward compatibility (LOAD-BEARING): a bare ref with no separator (``claude``)
parses to ``(raw, raw)`` — node == harness == the bare name — so every bare path
is byte-for-byte identical to pre-persona behaviour.
"""

from __future__ import annotations

from kanibako.errors import ConfigError

#: The canonical persona/harness separator (U+2118 SCRIPT CAPITAL P).
CANONICAL_SEP = "℘"  # ℘
#: The human-typable separator (U+002B PLUS SIGN).
PLUS_SEP = "+"
#: Both accepted separators.
SEPARATORS = (PLUS_SEP, CANONICAL_SEP)

#: Characters permitted in a persona OR harness segment (fs- and key-safe).
#: Alphanumeric plus ``-``, ``.``, ``_`` — NO separators, NO path/whitespace.
_SAFE_EXTRA = frozenset("-._")


def _is_segment_safe(segment: str) -> bool:
    """A non-empty segment of only ``[A-Za-z0-9]`` plus ``-`` / ``.`` / ``_``."""
    if not segment:
        return False
    return all(ch.isalnum() or ch in _SAFE_EXTRA for ch in segment)


def _first_sep_index(raw: str) -> int:
    """Index of the FIRST separator in *raw*, or ``-1`` if none."""
    indices = [raw.find(sep) for sep in SEPARATORS]
    present = [i for i in indices if i != -1]
    return min(present) if present else -1


def parse_agent_ref(raw: str) -> tuple[str, str]:
    """Parse an agent ref into ``(node, harness)``.

    * Bare (no separator): ``"claude"`` -> ``("claude", "claude")`` — node and
      harness are the same, preserving backward compatibility.
    * Composite: ``"navigator+claude"`` -> ``("navigator℘claude", "claude")`` —
      the persona (left) and harness (right) are validated and re-joined with
      the canonical ``℘`` separator to form the node-name.

    Splits on the FIRST separator only; a second separator in EITHER segment is
    rejected (a segment must be fs/key-safe: alnum plus ``-``/``.``/``_``).

    :raises ConfigError: on empty input, an empty persona/harness segment, or a
        segment containing an illegal character (incl. a stray separator).
    """
    if not isinstance(raw, str):
        raise ConfigError(
            f"agent ref must be a string, got {type(raw).__name__}: {raw!r}"
        )
    ref = raw.strip()
    if not ref:
        raise ConfigError("agent ref is empty")

    idx = _first_sep_index(ref)
    if idx == -1:
        # Bare: node == harness == the whole (validated) name.
        if not _is_segment_safe(ref):
            raise ConfigError(
                f"invalid agent name '{ref}': names may contain only letters, "
                f"digits, '-', '.', '_'"
            )
        return ref, ref

    persona = ref[:idx]
    harness = ref[idx + 1 :]

    if not _is_segment_safe(persona):
        raise ConfigError(
            f"invalid agent ref '{raw}': the persona segment '{persona}' must be "
            f"non-empty and contain only letters, digits, '-', '.', '_' "
            f"(no separator)"
        )
    if not _is_segment_safe(harness):
        raise ConfigError(
            f"invalid agent ref '{raw}': the harness segment '{harness}' must be "
            f"non-empty and contain only letters, digits, '-', '.', '_' "
            f"(no separator)"
        )

    node = f"{persona}{CANONICAL_SEP}{harness}"
    return node, harness


def harness_of(node: str) -> str:
    """Return the harness (part right of ``℘``) of a *node*-name.

    A bare node with no separator IS its own harness (``"claude" -> "claude"``).
    Only the canonical ``℘`` is recognised here — callers pass node-names, which
    are always canonicalised.  ``+`` is deliberately NOT split here so a literal
    ``+`` in an already-canonical node (there should be none) is not mistaken for
    a separator; use :func:`parse_agent_ref` / :func:`canonicalize_agent_ref` for
    raw, possibly-``+`` input.
    """
    _, _, harness = node.rpartition(CANONICAL_SEP)
    return harness if harness else node


def persona_of(node: str) -> str:
    """Return the persona segment (part LEFT of ``℘``) of a *node*-name.

    The inverse of :func:`harness_of`.  A persona node (``navigator℘claude``)
    yields its identity segment (``navigator``) — the name the class setup script
    uses for its host config dir ``~/.config/claude/<persona>/``.  A bare node with
    no separator has no distinct persona (``"claude" -> "claude"``); callers use
    this only for personas (``harness_of(node) != node``).

    Reuses the same canonical ``℘`` split as :func:`harness_of` / :func:`with_harness`
    (design law: never re-split a ref on the raw separator at the call site).
    """
    persona, sep, _ = node.rpartition(CANONICAL_SEP)
    return persona if sep else node


def with_harness(node: str, harness: str) -> str:
    """Return *node* with its harness segment REPLACED by *harness*.

    Preserves the persona name (left of ``℘``) while swapping the harness — used when
    the actually-RESOLVED target differs from the requested harness (e.g. the
    ``NoAgentTarget`` fallback when a named agent's binary is absent), so the
    on-disk ``agents/<node>/`` dir + keyspace slot follow the real target.

    * bare node (``"claude"``, harness ``"claude"``) -> ``"claude"`` (unchanged);
    * bare node, fallback harness (``"claude"``, ``"no_agent"``) -> ``"no_agent"``;
    * persona node (``"navigator℘claude"``, ``"claude"``) -> ``"navigator℘claude"``;
    * persona node, fallback (``"navigator℘claude"``, ``"no_agent"``) ->
      ``"navigator℘no_agent"`` (persona name kept, harness swapped).
    """
    persona, sep, _ = node.rpartition(CANONICAL_SEP)
    if not sep:
        # Bare node: it IS just a harness — replace wholesale.
        return harness
    return f"{persona}{CANONICAL_SEP}{harness}"


def display_agent_ref(node: str) -> str:
    """Return the USER-FACING form of a *node*-name (``℘`` -> ``+``).

    Presentation-only inverse of :func:`canonicalize_agent_ref`: internal storage,
    keyspace, on-disk paths, and ``KANIBAKO_AGENT`` all keep the canonical ``℘``
    form; whenever an agent ref is rendered to the user (error text, ``crab``/``box``
    listings, session labels, log lines) it is shown with the typable ``+``.

    Bare names contain no ``℘`` -> returned unchanged (existing output byte-identical).
    Does NOT validate: it is a pure display swap tolerant of any string.
    """
    return node.replace(CANONICAL_SEP, PLUS_SEP)


def canonicalize_agent_ref(raw: str) -> str:
    """Return the canonical *node*-name for a raw ref (``+`` -> ``℘``).

    Idempotent: a node that is already canonical (or bare) returns unchanged.
    Validates via :func:`parse_agent_ref`, so a malformed ref raises ``ConfigError``.
    """
    node, _ = parse_agent_ref(raw)
    return node
