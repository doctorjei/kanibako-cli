"""Target plugin discovery and resolution."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from importlib.metadata import entry_points

from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target, TargetSetting
from kanibako.targets.no_agent import NoAgentTarget

__all__ = [
    "AgentInstall", "Mount", "NoAgentTarget", "ResourceMapping", "ResourceScope",
    "Target", "TargetSetting",
    "discover_targets", "get_target", "resolve_target",
]

logger = logging.getLogger(__name__)


def _scan_plugin_modules(targets: dict[str, type[Target]]) -> None:
    """Scan ``kanibako.plugins.*`` for Target subclasses (bind-mount fallback).

    Entry points rely on dist-info metadata which doesn't travel via
    bind-mount.  This fallback imports all sub-packages of
    ``kanibako.plugins`` and collects any ``Target`` subclasses found,
    keyed by their ``name`` property.

    Already-discovered targets (from entry points) are not overwritten.
    """
    try:
        import kanibako.plugins as plugins_pkg
    except ImportError:
        return

    for finder, module_name, ispkg in pkgutil.walk_packages(
        plugins_pkg.__path__, prefix="kanibako.plugins."
    ):
        if module_name in ("kanibako.plugins",):
            continue
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            logger.debug("Failed to import plugin module %s", module_name, exc_info=True)
            continue

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name, None)
            if (
                isinstance(attr, type)
                and issubclass(attr, Target)
                and attr is not Target
                and attr is not NoAgentTarget
            ):
                try:
                    instance = attr()
                    name = instance.name
                except Exception:
                    continue
                if name not in targets:
                    targets[name] = attr


def discover_targets() -> dict[str, type[Target]]:
    """Scan entry points and plugin modules for targets.

    Primary: ``kanibako.targets`` entry point group (pip-installed plugins).
    Fallback: ``kanibako.plugins.*`` module scan (bind-mounted plugins).
    """
    targets: dict[str, type[Target]] = {}
    eps = entry_points(group="kanibako.targets")
    for ep in eps:
        cls = ep.load()
        targets[ep.name] = cls

    # Fallback: scan kanibako.plugins.* for bind-mounted plugins
    _scan_plugin_modules(targets)

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
