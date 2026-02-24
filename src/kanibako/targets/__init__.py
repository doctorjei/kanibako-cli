"""Target plugin discovery and resolution."""

from __future__ import annotations

from importlib.metadata import entry_points

from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target
from kanibako.targets.no_agent import NoAgentTarget

__all__ = [
    "AgentInstall", "Mount", "NoAgentTarget", "ResourceMapping", "ResourceScope", "Target",
    "discover_targets", "get_target", "resolve_target",
]


def discover_targets() -> dict[str, type[Target]]:
    """Scan the ``kanibako.targets`` entry point group and return a mapping of name → class."""
    targets: dict[str, type[Target]] = {}
    eps = entry_points(group="kanibako.targets")
    for ep in eps:
        cls = ep.load()
        targets[ep.name] = cls
    return targets


def get_target(name: str) -> type[Target]:
    """Look up a target class by name.

    Raises ``KeyError`` if no target with that name is registered.
    """
    targets = discover_targets()
    if name not in targets:
        available = ", ".join(sorted(targets)) or "(none)"
        raise KeyError(f"Unknown target '{name}'. Available: {available}")
    return targets[name]


def resolve_target(name: str | None = None) -> Target:
    """Instantiate a target by name, or auto-detect.

    If *name* is given, looks it up via entry points.
    If *name* is None, iterates all discovered targets and returns the first
    one whose ``detect()`` succeeds.

    Raises ``KeyError`` if no matching target is found.
    """
    if name:
        cls = get_target(name)
        return cls()

    # Auto-detect: try each target's detect() and return the first match.
    targets = discover_targets()
    for target_name, cls in targets.items():
        instance = cls()
        if instance.detect() is not None:
            return instance

    return NoAgentTarget()
