"""Target plugin discovery and resolution."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
from importlib.metadata import entry_points
from pathlib import Path

from kanibako.agent_ref import reserved_pseudo_agent_reason
from kanibako.identifiers import agent_node_case, find_identifier
from kanibako.settings.bootstrap import STANDALONE_META_DIR
from kanibako.targets.base import AgentInstall, Mount, Target, TargetSetting
from kanibako.targets.shell import ShellTarget

__all__ = [
    "AgentInstall", "Mount", "ShellTarget",
    "Target", "TargetSetting",
    "discover_targets", "get_target", "resolve_target",
]

logger = logging.getLogger(__name__)

# Entry points whose load() already failed, so the stderr warning is emitted ONCE
# per process.  ``discover_targets`` is called many times per command (selection,
# setup's agent menu, the target resolve), and repeating the same paragraph on
# every call would bury the rest of the output.
_EP_LOAD_FAILED: set[str] = set()

# Harness names already refused as RESERVED, so the stderr warning is emitted ONCE per
# process — the same reason ``_EP_LOAD_FAILED`` above exists.
_RESERVED_NAME_WARNED: set[str] = set()

# Declared names already refused as CASE-COLLIDING, warned once per process, identically.
_COLLIDING_NAME_WARNED: set[str] = set()


def _register(
    targets: dict[str, type[Target]],
    declared: dict[str, tuple[str, str]],
    name: str,
    cls: type[Target],
    source: str,
    *,
    tier: str,
    override: bool,
) -> None:
    """Enter *cls* in *targets* under the NODE its declared *name* derives.

    THE ONE REGISTRATION GATE — all three discovery tiers assign through it, so the
    pseudo-agent reservation (keyspec §2d) cannot hold at one tier and not another.
    *override* carries each tier's own precedence rule: entry points and the two
    file-drop scans replace an earlier answer, the ``kanibako.plugins`` module fallback
    keeps the first one.

    ⚑⚑ **THE KEY IS THE NODE, NOT THE DECLARED NAME** (``[R173]``, keyspec §0): a
    plugin calling itself ``Shell`` keeps that spelling in its ``name`` property — the
    canonical NAME — while everything derived from the node is lowercase, the
    ``agents/<node>/`` store dir included.  Deriving the node HERE is what closes the
    macOS ``agents/Shell/`` vs ``agents/shell/`` collision; ``agent_config.store_dirname``
    is correct as it stands and must not fold.
    🛑 **The RESERVATION is therefore tested against the NODE too.** What a pseudo-agent
    owns is a store dir and an ``agent.<node>.*`` slot, and those follow the node — so
    ``Shell`` claims exactly what ``shell`` does.

    *declared* maps node → ``(declared name, tier)`` for what already holds it, which is
    how a CASE COLLISION is told from an ordinary override.  Two plugins in ONE tier
    declaring ``Claude`` and ``claude`` collapse to one node, and discovery order within
    a tier is arbitrary — so the second is REFUSED rather than silently winning.  Across
    tiers the precedence rule above is a documented answer, not an accident, and it is
    left alone: a file-drop plugin still replaces an installed one.

    ⚑ SKIP-AND-WARN, NEVER RAISE, for the reason the ``ep.load()`` guard in
    :func:`discover_targets` states at length: discovery runs on every command, so one
    third-party plugin's bad name must not take the CLI down. The refusal costs that ONE
    plugin its registration and nothing else.
    """
    node = agent_node_case(name)
    why = reserved_pseudo_agent_reason(node)
    if why is not None:
        if name not in _RESERVED_NAME_WARNED:
            _RESERVED_NAME_WARNED.add(name)
            spelling = (
                "" if node == name
                else f" The plugin declares '{name}', whose node is '{node}'."
            )
            print(
                f"Warning: {why}.{spelling} The agent plugin registering it ({source}) "
                f"is being SKIPPED; every other agent, and 'kanibako setup', still "
                f"work. The plugin's author must give it a name of its own.",
                file=sys.stderr,
            )
        return
    held = declared.get(node)
    if held is not None and held[0] != name and held[1] == tier:
        if name not in _COLLIDING_NAME_WARNED:
            _COLLIDING_NAME_WARNED.add(name)
            print(
                f"Warning: the agent plugin '{name}' ({source}) collides with '{held[0]}', "
                f"which is already registered: an agent's node is its name in lowercase "
                f"(keyspec §0), so both claim '{node}' and its 'agents/{node}/' store. "
                f"'{name}' is being SKIPPED; every other agent, and 'kanibako setup', "
                f"still work. One of the two must be renamed to more than its case.",
                file=sys.stderr,
            )
        return
    if override or node not in targets:
        targets[node] = cls
        declared[node] = (name, tier)


def _scan_plugin_modules(
    targets: dict[str, type[Target]], declared: dict[str, tuple[str, str]],
) -> None:
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
                and attr is not ShellTarget
            ):
                try:
                    instance = attr()
                    name = instance.name
                except Exception:
                    continue
                _register(
                    targets, declared, name, attr, f"module '{module_name}'",
                    tier="kanibako.plugins", override=False,
                )


def _scan_directory_plugins(
    directory: Path,
    targets: dict[str, type[Target]],
    declared: dict[str, tuple[str, str]],
) -> None:
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
                and attr is not ShellTarget
            ):
                try:
                    instance = attr()
                    name = instance.name
                except Exception:
                    continue
                # Later directories in the chain override earlier ones, so each
                # DIRECTORY is its own tier for the case-collision test.
                _register(
                    targets, declared, name, attr, f"file '{py_file}'",
                    tier=str(directory), override=True,
                )


def discover_targets(project_path: Path | None = None) -> dict[str, type[Target]]:
    """Scan entry points, plugin modules, and directories for targets.

    ⚑ **Keyed by NODE — the declared name in lowercase** (``[R173]``, keyspec §0).
    The declared spelling is the plugin's ``name`` property and stays there; these
    keys are what the ``agents/<node>/`` store dir and the ``agent.<node>.*`` cascade
    slot are spelled from, so they carry the node's case, not the name's.

    Discovery order (later overrides earlier):

    1. Entry points (pip-installed packages)
    2. ``kanibako.plugins.*`` module scan (bind-mount fallback)
    3. User directory (``<config.data>/plugins/``, by default
       ``~/.local/share/kanibako/plugins/``)
    4. Project directory (``{project}/box_data/plugins/``)
    """
    targets: dict[str, type[Target]] = {}
    # node -> (declared name, tier), so ``_register`` can tell a CASE COLLISION from
    # a tier's documented override.  Per-call and thrown away with the scan: it says
    # nothing about what is stored, only about what this scan has already seen.
    declared: dict[str, tuple[str, str]] = {}
    # ⚑ THE BUILT-IN IS SEEDED, NOT DISCOVERED ([R175] — built-in is a CATEGORY,
    # not a carve-out).  The plain-shell target owns the ``shell`` slot the way a
    # plugin owns its name, so it enters through no plugin door: no entry point
    # (``pyproject.toml`` carries none for it), no module scan, no file drop —
    # both scanners below skip it by identity.  The D6 reservation in
    # ``_register`` / ``_require_meta_name`` therefore never sees it, and a
    # third-party plugin declaring ``shell`` is still refused there — that
    # refusal protects exactly this slot.
    targets["shell"] = ShellTarget
    declared["shell"] = ("shell", "builtin")
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
        _register(
            targets, declared, ep.name, cls, "an installed entry point",
            tier="entry-points", override=True,
        )

    # Fallback: scan kanibako.plugins.* for bind-mounted plugins
    _scan_plugin_modules(targets, declared)

    # User-level file-drop plugins, under the ``config.data`` directory the user actually
    # configured — never the XDG data base plus a hardcoded "kanibako" leaf ([R155]).  The
    # store a user repoints ``config.data`` to was not scanned at all before, so a plugin
    # dropped there simply never appeared, and one left in the default store was loaded
    # instead without a word.
    # ⚑ ``resolve_data_path`` is PURE and TOTAL (creates nothing, never raises, degrades to
    # the default): discovery runs on every command, including before a config file exists,
    # so it must not acquire a failure mode here.
    from kanibako.settings.paths import resolve_data_path

    _scan_directory_plugins(resolve_data_path() / "plugins", targets, declared)

    # Project-level file-drop plugins.  Absence is not an error.
    if project_path is not None:
        _scan_directory_plugins(
            project_path / STANDALONE_META_DIR / "plugins", targets, declared,
        )

    return targets


def get_target(name: str, project_path: Path | None = None) -> type[Target]:
    """Look up a target class by name, compared WITHOUT REGARD TO CASE (keyspec §0).

    *name* is whatever a user typed or a settings value carried, so it arrives in any
    case; the registry is keyed by NODE.  ``--agent Claude`` and ``--agent claude``
    therefore reach one plugin instead of one working and one reporting an agent that
    is not installed.

    Raises ``KeyError`` if no target with that name is registered.
    """
    targets = discover_targets(project_path)
    node = find_identifier(name, targets)
    if node is None:
        available = ", ".join(sorted(targets)) or "(none)"
        raise KeyError(f"Unknown target '{name}'. Available: {available}")
    return targets[node]


def _require_meta_name(target: Target) -> Target:
    """Enforce that a resolved target declares a non-empty, non-RESERVED ``name``.

    The plugin's ``name`` is the HARNESS name — it is REQUIRED (D-2026-06-22):
    it identifies the harness's own store dir (``agents/<name>/``) and its
    ``agent.<name>.*`` cascade slot.  An agent with no resolvable name has
    neither, so fail loudly rather than silently writing to
    ``agents//agent.yaml``.

    ⚑ IT IS NOT THE VALUE OF ``meta.agent.<agent>.name``, and this docstring
    used to say it was.  That key is materialized by
    ``settings.settings_launch.meta_identity_floor`` from the ACTIVE NODE, which
    for a persona is not the plugin's name at all; the two coincide only for a
    bare agent.  Nothing here feeds this value into that key — this function
    VALIDATES a name, it never publishes one.

    ⚑ AND A RESERVATION FLOOR (keyspec §2d): the store dir and cascade slot the name
    identifies are exactly what a PSEUDO-AGENT name already owns, so a harness may not
    claim one. :func:`_register` normally keeps such a plugin out of discovery
    altogether; this is the floor under a ``Target`` that reaches a caller some other
    way, and it RAISES because by here the target is the one being launched.
    ⚑ It tests the NODE, exactly as ``_register`` does and for the same reason: what
    is owned follows the node, so ``Shell`` claims what ``shell`` claims.
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
    node = agent_node_case(meta_name)
    # ⚑ THE BUILT-IN OCCUPIES ITS OWN SLOT ([R175]).  The reservation below
    # refuses a HARNESS claiming a pseudo-agent name, because what is owned is a
    # store dir and a cascade slot — and this target IS that owner, claiming
    # nothing.  Scoping the refusal to non-built-ins states the rule (a plugin
    # may not claim the slot), it does not except anyone from it.
    if not isinstance(target, ShellTarget):
        why = reserved_pseudo_agent_reason(node)
        if why is not None:
            cls = type(target)
            spelling = "" if node == meta_name else f" (declared as '{meta_name}')"
            raise ValueError(
                f"{why}. Agent plugin {cls.__module__}.{cls.__qualname__} declares it as "
                f"its harness name{spelling}; rename the plugin's 'name' property."
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

    return _require_meta_name(ShellTarget())
