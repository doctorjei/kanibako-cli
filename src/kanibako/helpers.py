"""Helper spawning: B-ary tree numbering for globally unique agent IDs."""

from __future__ import annotations

# When breadth is unlimited (-1), use 2^16 for numbering purposes.
# Large enough to never collide; small enough for human-readable numbers.
UNLIMITED_BREADTH = 2**16


def effective_breadth(breadth: int) -> int:
    """Return the breadth used for numbering.

    Maps -1 (unlimited) to ``UNLIMITED_BREADTH``.  Positive values pass
    through unchanged.
    """
    if breadth == -1:
        return UNLIMITED_BREADTH
    if breadth < 1:
        msg = f"breadth must be positive or -1, got {breadth}"
        raise ValueError(msg)
    return breadth


def children_of(agent: int, breadth: int) -> tuple[int, int]:
    """Return the (first_child, last_child) global numbers for *agent*.

    Both bounds are inclusive.  The range always contains exactly
    ``effective_breadth(breadth)`` slots, regardless of how many children
    are actually spawned.
    """
    b = effective_breadth(breadth)
    first = agent * b + 1
    last = agent * b + b
    return first, last


def parent_of(agent: int, breadth: int) -> int | None:
    """Return the global number of *agent*'s parent.

    Returns ``None`` if *agent* is the director (agent 0).
    """
    if agent == 0:
        return None
    b = effective_breadth(breadth)
    return (agent - 1) // b


def agent_depth(agent: int, breadth: int) -> int:
    """Return the depth of *agent* in the tree (director = 0)."""
    depth = 0
    current = agent
    while current != 0:
        current = parent_of(current, breadth)  # type: ignore[assignment]
        depth += 1
    return depth


def nth_child(agent: int, n: int, breadth: int) -> int:
    """Return the global number of *agent*'s *n*-th child (0-indexed).

    Raises ``ValueError`` if *n* is out of range for the given breadth.
    """
    b = effective_breadth(breadth)
    if n < 0 or n >= b:
        msg = f"child index {n} out of range for breadth {b}"
        raise ValueError(msg)
    return agent * b + 1 + n


def sibling_index(agent: int, breadth: int) -> int:
    """Return the 0-based index of *agent* among its parent's children.

    The director (agent 0) has no siblings; returns 0 by convention.
    """
    if agent == 0:
        return 0
    b = effective_breadth(breadth)
    return (agent - 1) % b
