"""Target plugin discovery and resolution."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from importlib.metadata import entry_points
from pathlib import Path

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


def _scan_directory_plugins(directory: Path, targets: dict[str, type[Target]]) -> None:
    """Scan a directory for .py files containing Target subclasses.

    Files starting with ``_`` are skipped.  Later directories in the
    discovery chain override earlier ones (same target name replaces).
    """
    if not directory.is_dir():
        return
    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"kanibako_plugin_{py_file.stem}", py_file,
            )
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            logger.debug("Failed to load plugin %s", py_file, exc_info=True)
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
                targets[name] = attr  # later overrides earlier


def discover_targets(project_path: Path | None = None) -> dict[str, type[Target]]:
    """Scan entry points, plugin modules, and directories for targets.

    Discovery order (later overrides earlier):

    1. Entry points (pip-installed packages)
    2. ``kanibako.plugins.*`` module scan (bind-mount fallback)
    3. User directory (``~/.local/share/kanibako/plugins/``)
    4. Project directory (``{project}/.kanibako/plugins/``)
    """
    targets: dict[str, type[Target]] = {}
    eps = entry_points(group="kanibako.targets")
    for ep in eps:
        cls = ep.load()
        targets[ep.name] = cls

    # Fallback: scan kanibako.plugins.* for bind-mounted plugins
    _scan_plugin_modules(targets)

    # User-level file-drop plugins
    from kanibako.paths import xdg

    data_home = xdg("XDG_DATA_HOME", ".local/share")
    _scan_directory_plugins(data_home / "kanibako" / "plugins", targets)

    # Project-level file-drop plugins
    if project_path is not None:
        _scan_directory_plugins(project_path / ".kanibako" / "plugins", targets)

    return targets


def get_target(name: str, project_path: Path | None = None) -> type[Target]:
    """Look up a target class by name.

    Raises ``KeyError`` if no target with that name is registered.
    """
    targets = discover_targets(project_path)
    if name not in targets:
        available = ", ".join(sorted(targets)) or "(none)"
        raise KeyError(f"Unknown target '{name}'. Available: {available}")
    return targets[name]


def resolve_target(
    name: str | None = None, project_path: Path | None = None,
) -> Target:
    """Instantiate a target by name, or auto-detect.

    If *name* is given, looks it up via entry points.
    If *name* is None, iterates all discovered targets and returns the first
    one whose ``detect()`` succeeds.

    Raises ``KeyError`` if no matching target is found.
    """
    if name:
        cls = get_target(name, project_path)
        return cls()

    # Auto-detect: try each target's detect() and return the first match.
    targets = discover_targets(project_path)
    for target_name, cls in targets.items():
        instance = cls()
        if instance.detect() is not None:
            return instance

    return NoAgentTarget()
