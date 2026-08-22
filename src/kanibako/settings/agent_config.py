"""Agent identity: the ``AgentConfig`` record, the agent store's paths and category roots.

⚑ THE FILE'S SHAPE IS NOT HERE — :mod:`kanibako.settings.agent_file` is the one module that
spells the file's root table [spec:15-21, "self"].  Design notes:
``llm-docs/kanibako/settings/agent_config.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping

from kanibako.settings.config import AGENT_META_FILE
from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES, DECLARATION_ROOT_REF

# Keys that live directly in the [agent] section as agent identity (not state).
IDENTITY_KEYS = frozenset({"name", "run_args"})


@dataclass
class AgentConfig:
    """Per-agent configuration loaded from an agent YAML file.

    ⚑ ``secret_path`` is THREE-STATE: a VAR is ABSENT, maps to ``None``
    (PRESENT-null — deliberately KEYLESS), or maps to a path ``str``.  Test
    membership (``var in cfg.secret_path``) before reading — ``.get(var)`` cannot
    tell the first two apart.  Field-by-field notes are in the llm-doc.
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str | None] = field(default_factory=dict)
    # ⚑ `env` is the READ side of the `agent` verbs, NOT a delivery route: env
    # reaches the box through the collapse's arbitrated slots (MBR-1 P3).
    env: dict[str, str] = field(default_factory=dict)
    secret_path: dict[str, str | None] = field(default_factory=dict)
    transform_settings: dict = field(default_factory=dict)
    # ⚑ Carried OPAQUELY through load→write; no live producer today.  A guard
    # against a shape change, not dead code.
    category_tables: dict[str, dict] = field(default_factory=dict)


def agents_dir(data_path: Path, paths_agents: str = "agents") -> Path:
    """Return the agents directory under *data_path*."""
    return data_path / (paths_agents or "agents")


def agent_settings_path(agents_root: Path, agent_id: str) -> Path:
    """Return ``@meta.agent.<agent>.settings`` for *agent_id*."""
    # ⚑ INSIDE the store dir as ``agent.yaml``, not the old sibling
    # ``agents/<agent>.yaml`` (D-2026-06-22).
    return agents_root / agent_id / AGENT_META_FILE


# --------------------------------------------------------------------------- #
# The per-agent HOST LAYOUT of the ABSTRACT categories (spec §2a)      #
# --------------------------------------------------------------------------- #

#: category -> the FIXED sub-dirname under the per-agent store root.  THE single
#: source of that layout; both consumers read it from here, so it cannot drift.
#: ⚑ DERIVED from the category list, never re-typed: the dirname IS the category
#: name, and the root dirname is fixed machinery rather than a key (spec §2a).
AGENT_CATEGORY_DIRNAME: Final[Mapping[str, str]] = {
    category: category for category in ABSTRACT_CATEGORIES
}


def agent_category_dirname(category: str) -> str:
    """The FIXED sub-dirname for an ABSTRACT *category* in a per-agent store."""
    # ⚑ An undeclared category is REFUSED, never given a bare root (spec §0).
    try:
        return AGENT_CATEGORY_DIRNAME[category]
    except KeyError:
        raise ValueError(
            f"{category!r} is not an ABSTRACT category with a per-agent store dir "
            f"(declared: {', '.join(sorted(AGENT_CATEGORY_DIRNAME))}); "
            "bindings.{ro,rw} take no root at any scope (spec §2a)"
        ) from None


def agent_category_root(agents_root: Path, agent: str, category: str) -> Path:
    """The REAL host dir an abstract *category* stores under for *agent*."""
    # ⚑ The resolved twin of :func:`agent_category_root_ref`; for callers needing a
    # real Path, never to build a STORED value.
    return agents_root / agent / agent_category_dirname(category)


def agent_category_root_ref(agent: str, category: str) -> str:
    """The self-resolving ``@``-ref an abstract *category*'s sources root at."""
    # ⚑ What a loader STORES: it must resolve on its own, with no layer prepending
    # anything later (spec §2a).  The AGENT row comes from the single copy of the
    # DECLARATION-ROOT table, never a local literal.
    root = DECLARATION_ROOT_REF["agent"].format(agent=agent)
    return f"{root}/{agent_category_dirname(category)}"


#: TOKEN prefixes that make a ``host_src`` resolve on its own when UNESCAPED
#: (spec §2a). A leading ``/`` is handled separately in :func:`is_self_resolving`.
_SELF_RESOLVING_TOKENS: Final[tuple[str, ...]] = ("~", "$", "@")


def is_self_resolving(src: str) -> bool:
    r"""Whether *src* resolves on its own — absolute, or an unescaped ``~``/``$``/``@``.

    ⚑ NOT a plain first-char test.  Escapes are read the way the RESOLVER reads
    them, and the two leading-escape cases fall on OPPOSITE sides: ``\/foo``
    unescapes to an ABSOLUTE path, ``\~foo`` to a plain relative dir.  So ``/`` is
    tested AFTER unescaping while the tokens count only UNESCAPED.  Both cases are
    worked through in the llm-doc.
    """
    if src[:1] in _SELF_RESOLVING_TOKENS:
        return True
    # A leading ``/``, escaped or not, is absolute once the resolver unescapes it.
    return src[:1] == "/" or src[:2] == "\\/"


def root_relative_source(src: str, root_ref: str) -> str:
    """Root a BARE RELATIVE *src* under *root_ref*; return it unchanged otherwise.

    THE declaration-time rooting, implemented ONCE (spec §2a).  ⚑ An ABSOLUTE (or
    ``~``/``$var``/``@``-ref) source is LEGAL and is NOT joined — the root is a
    DEFAULT FOR RELATIVE SOURCES, not a universal law.  Applied ONLY by the
    ABSTRACT-category declaration loaders; ``bindings.{ro,rw}`` take no root at any
    scope, so a relative source there is a DEFECT and is refused where declared.
    """
    if is_self_resolving(src):
        return src
    return f"{root_ref}/{src}"


def agent_config_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Convenience wrapper for callers holding a *data_path*, not an agents root."""
    return agent_settings_path(agents_dir(data_path, paths_agents), agent_id)
