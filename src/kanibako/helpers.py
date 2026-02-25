"""Helper spawning: B-ary tree numbering and spawn budget management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib

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


# ---------------------------------------------------------------------------
# Spawn budget
# ---------------------------------------------------------------------------

DEFAULT_DEPTH = 4
DEFAULT_BREADTH = 4


@dataclass(frozen=True)
class SpawnBudget:
    """Spawn limits for an agent.  Immutable."""

    depth: int = DEFAULT_DEPTH
    breadth: int = DEFAULT_BREADTH


def check_spawn_allowed(budget: SpawnBudget, current_children: int) -> str | None:
    """Return an error message if spawning is not allowed, else ``None``."""
    if budget.depth == 0:
        return "spawn depth exhausted (depth=0)"
    if budget.breadth != -1 and current_children >= budget.breadth:
        return f"breadth limit reached ({current_children}/{budget.breadth})"
    return None


def child_budget(parent: SpawnBudget) -> SpawnBudget:
    """Compute the spawn budget for a child of *parent*.

    Depth is decremented by 1 (unless unlimited).  Breadth is inherited.
    """
    new_depth = parent.depth if parent.depth == -1 else parent.depth - 1
    return SpawnBudget(depth=new_depth, breadth=parent.breadth)


def resolve_spawn_budget(
    ro_config: SpawnBudget | None,
    host_config: SpawnBudget | None,
    cli_depth: int | None,
    cli_breadth: int | None,
) -> SpawnBudget:
    """Resolve the effective spawn budget using config precedence.

    Order: RO config > host config > CLI flags > built-in defaults.
    CLI flags only apply when neither RO nor host config exist.
    """
    if ro_config is not None:
        return ro_config
    if host_config is not None:
        return host_config
    depth = cli_depth if cli_depth is not None else DEFAULT_DEPTH
    breadth = cli_breadth if cli_breadth is not None else DEFAULT_BREADTH
    return SpawnBudget(depth=depth, breadth=breadth)


# ---------------------------------------------------------------------------
# Spawn config I/O
# ---------------------------------------------------------------------------


def read_spawn_config(path: Path) -> SpawnBudget | None:
    """Read spawn limits from a TOML file (kanibako.toml or RO spawn config).

    Looks for a ``[spawn]`` section with ``depth`` and ``breadth`` keys.
    Returns ``None`` if the file or section is absent.
    """
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = tomllib.load(f)
    spawn = data.get("spawn")
    if spawn is None:
        return None
    return SpawnBudget(
        depth=int(spawn.get("depth", DEFAULT_DEPTH)),
        breadth=int(spawn.get("breadth", DEFAULT_BREADTH)),
    )


def write_spawn_config(path: Path, budget: SpawnBudget) -> None:
    """Write spawn limits as a ``[spawn]`` section in a TOML file.

    For RO spawn configs this creates a standalone file.
    For kanibako.toml this preserves other sections.
    """
    existing: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            existing = tomllib.load(f)
    existing["spawn"] = {"depth": budget.depth, "breadth": budget.breadth}
    _write_spawn_toml(path, existing)


def _write_spawn_toml(path: Path, data: dict) -> None:
    """Write a dict as TOML.  Handles top-level scalars and one level of sections."""
    lines: list[str] = []
    # Top-level scalars first
    for k, v in data.items():
        if not isinstance(v, dict):
            lines.append(f"{k} = {_format_value(v)}")
    # Then sections
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {_format_value(v)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _format_value(v: object) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    return f'"{v}"'
