"""Target plugin discovery and resolution."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
from importlib.metadata import entry_points
from pathlib import Path

from kanibako.targets.base import AgentInstall, Mount, Target, TargetSetting
from kanibako.targets.no_agent import NoAgentTarget

__all__ = [
    "AgentInstall", "Mount", "NoAgentTarget",
    "Target", "TargetSetting",
    "discover_targets", "get_target", "resolve_target",
]

logger = logging.getLogger(__name__)

# Entry points whose load() already failed, so the stderr warning is emitted ONCE
# per process.  ``discover_targets`` is called many times per command (selection,
# setup's agent menu, the target resolve), and repeating the same paragraph on
# every call would bury the rest of the output.
_EP_LOAD_FAILED: set[str] = set()


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
    4. Project directory (``{project}/box_data/plugins/``)
    """
    targets: dict[str, type[Target]] = {}
    # Group is agent-domain (a registry of agent adapters) → "kanibako.agents".
    # NB: distinct from the `kanibako.settings.agent_config` module (per-agent tool
    # config object); the module was named `agent_config` (not `agents`) to
    # avoid clashing with this entry-point registry. Do not "unify".
    eps = entry_points(group="kanibako.agents")
    for ep in eps:
        # ⚑⚑ ONE BROKEN ADAPTER MUST NOT TAKE THE WHOLE CLI DOWN.  ``ep.load()``
        # imports third-party code, and an adapter built against a different
        # kanibako-cli raises ImportError from its own module body.  Unguarded,
        # that propagated out of discovery and killed whatever command called it
        # — as a raw traceback, from a plugin the user was not even using, since
        # agent SELECTION enumerates every entry point.  Worse, ``setup_cmd``
        # calls this too, so the documented cure (`kanibako setup`) died the same
        # way and hand-editing site-packages was the only way back in.  MEASURED
        # 2026-08-17 with a stale kanibako-agent-goose: three sequential
        # dead-ends, three manual edits.
        #
        # The two FALLBACK scanners below have always tolerated a failing plugin
        # (``logger.debug`` + continue); this loop is the primary path and was the
        # only one that did not.  Skip-and-warn brings it in line.
        #
        # WARN, never swallow: a pip-installed adapter that cannot load is a
        # broken install the user must know about, so this goes to stderr with
        # the cure — unlike the fallbacks' debug-level note, which covers
        # optional bind-mount/file-drop paths where absence is routine.
        #
        # ⚑ AND IT IS NOW THE ONLY CARRIER OF THE CURE.  v1.8.0 DELETED the four
        # flat re-export shims outright (clean break — a shim is a deprecation
        # window), so an old plugin arrives here as a bare
        # ``ModuleNotFoundError``.  That exception names the missing MODULE but
        # not the PACKAGE that reached for it, and the user never wrote the
        # import — so this is the one place that can name the distribution, and
        # it hands over 'MIGRATION.md' as the term that finds the affected
        # versions.  Pinned by ``tests/test_plugin_import_compat.py``.
        try:
            cls = ep.load()
        except Exception as exc:
            if ep.name not in _EP_LOAD_FAILED:
                _EP_LOAD_FAILED.add(ep.name)
                dist = getattr(getattr(ep, "dist", None), "name", None)
                who = f"'{dist}'" if dist else f"the '{ep.name}' agent plugin"
                print(
                    f"Warning: {who} failed to load and is being SKIPPED: "
                    f"{type(exc).__name__}: {exc}\n"
                    f"  The '{ep.name}' agent is unavailable; every other agent, "
                    f"and 'kanibako setup', still work. This usually means the "
                    f"package was built against a different kanibako-cli — "
                    f"upgrade it, or uninstall it if you do not use it. Installing "
                    f"the 'kanibako' meta package pins a compatible set; see "
                    f"MIGRATION.md for the plugin versions this release breaks.",
                    file=sys.stderr,
                )
            logger.debug(
                "entry point %s failed to load", ep.name, exc_info=True,
            )
            continue
        targets[ep.name] = cls

    # Fallback: scan kanibako.plugins.* for bind-mounted plugins
    _scan_plugin_modules(targets)

    # User-level file-drop plugins
    from kanibako.settings.paths import xdg

    data_home = xdg("XDG_DATA_HOME", ".local/share")
    _scan_directory_plugins(data_home / "kanibako" / "plugins", targets)

    # Project-level file-drop plugins
    if project_path is not None:
        _scan_directory_plugins(project_path / "box_data" / "plugins", targets)

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


def _require_meta_name(target: Target) -> Target:
    """Enforce that a resolved target declares ``meta.agent.<agent>.name``.

    The plugin's ``name`` property IS ``meta.agent.<agent>.name`` — it is
    REQUIRED (D-2026-06-22): it identifies the per-agent store dir
    (``agents/<name>/``) and the agent cascade key.  An agent with no
    resolvable name has no store dir and no cascade slot, so fail loudly
    rather than silently writing to ``agents//agent.yaml``.
    """
    meta_name = getattr(target, "name", None)
    if not (isinstance(meta_name, str) and meta_name.strip()):
        cls = type(target)
        raise ValueError(
            f"Agent plugin {cls.__module__}.{cls.__qualname__} does not "
            f"declare meta.agent.<agent>.name (its 'name' property is empty); "
            f"a plugin MUST provide a non-empty name to identify its store dir "
            f"and cascade key."
        )
    return target


def resolve_target(
    name: str | None = None, project_path: Path | None = None,
) -> Target:
    """Instantiate a target by name, or auto-detect.

    If *name* is given, looks it up via entry points.
    If *name* is None, iterates all discovered targets and returns the first
    one whose ``detect()`` succeeds.

    Raises ``KeyError`` if no matching target is found.  Raises ``ValueError``
    if the resolved target does not declare ``meta.agent.<agent>.name``.
    """
    if name:
        cls = get_target(name, project_path)
        return _require_meta_name(cls())

    # Auto-detect: try each target's detect() and return the first match.
    targets = discover_targets(project_path)
    for target_name, cls in targets.items():
        instance = cls()
        if instance.detect() is not None:
            return _require_meta_name(instance)

    return _require_meta_name(NoAgentTarget())
