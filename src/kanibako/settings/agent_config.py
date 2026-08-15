"""Agent identity: the ``AgentConfig`` record, the agent store's paths and category roots.

⚑ THE FILE'S SHAPE IS NOT HERE.  Reading, writing and addressing the per-agent settings file
belong to :mod:`kanibako.settings.agent_file`, the one module that spells the file's root table
(rulings 49-52).  What stays here is what a caller holds INDEPENDENTLY of any file: the record
itself, where the store lives, and where a category's sources root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping

from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES, DECLARATION_ROOT_REF

# Keys that live directly in the [agent] section as agent identity (not state).
IDENTITY_KEYS = frozenset({"name", "run_args"})


@dataclass
class AgentConfig:
    """Per-agent configuration loaded from an agent YAML file.

    Sections:
      agent        — identity (name, run_args) plus agent-state knobs
                     (model, access, allow_helpers, endpoint, …), all FLAT under
                     the file's root, beside the category tables.
      env          — the ENV category (``agent.<node>.env.<VAR>``), stored FLAT
                     under ``self`` for the same reason *secret_path* below is:
                     ``self`` IS ``agent.<node>``.  ⚑ NOT A LAUNCH-INVOCATION INPUT
                     and not a delivery route: ``_agent_partial`` re-roots the table
                     into the cascade and the variable reaches the box through the
                     collapse's arbitrated env slots like every other scope's
                     (MBR-1 P3).  What the field is FOR is the READ side of the
                     ``agent`` verbs — ``agent info`` and ``agent show`` render it
                     (``agent_cmd`` ``:223`` / ``:531``) and ``agent get <node>
                     env.<VAR>`` returns it (``:489``).  ⚑ It is NOT needed to
                     preserve a user's ``agent set``: that verb writes through
                     ``write_nested_key`` and never builds an ``AgentConfig``, and
                     every ``agent_file.save`` caller persists a FRESHLY
                     GENERATED config (first-use only), so no read-modify-write
                     round-trip exists for a value to fall out of.
      secret_path  — the SECRET category (spec §2a, 2026-07-06; RENAMED from the
                     rc0-rc2 ``env_file``): VAR -> host PATH pointer to secret
                     material (e.g. a 0600 bearer-token file). Stored DISCRIMINATED
                     under ``agent.<node>.secret_path.<VAR>`` (the SAME first-class
                     category shape ``config set agent.<node>.secret_path.<VAR>``
                     writes and ``_agent_partial`` reads into the launch cascade),
                     so it resolves through ``system → workset → box → agent``
                     precedence. The value is a PATH only — at launch it is ro-bind-
                     mounted arm's-length + exported IN-BOX; kanibako NEVER reads the
                     secret VALUE (never in the snapshot/keystore/logs/argv).
      category_tables
                   — the CATEGORY tables this record does not model as fields of its
                     own: ``bindings`` (the ``{ro, rw}`` pair, whole), ``caches``,
                     ``seeded``, ``common``, ``synced``, ``masks``. All FLAT under
                     the file's root since the S2 flatten — ``self`` IS
                     ``agent.<node>``, so there is no per-node sub-table to hold
                     them. NOT modelled here (they ride ``_agent_partial`` into the
                     launch cascade, not the launch invocation) and carried OPAQUELY
                     through the load→write round trip.

                     ⚑ THAT ROUND TRIP HAS NO LIVE PRODUCER, MEASURED: all four
                     ``agent_file.save`` callers persist a FRESHLY GENERATED config
                     (both ``start.py`` sites gate on ``agent_cfg_dirty``, which is
                     first-use-only, and both ``cli.py`` sites build the config
                     inline), so nothing today loads this file, edits it and writes
                     it back. The carry protects a shape no caller currently
                     exercises — a guard, not a running guarantee.
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    secret_path: dict[str, str] = field(default_factory=dict)
    transform_settings: dict = field(default_factory=dict)
    category_tables: dict[str, dict] = field(default_factory=dict)


def agents_dir(data_path: Path, paths_agents: str = "agents") -> Path:
    """Return the agents directory under *data_path*."""
    return data_path / (paths_agents or "agents")


def agent_settings_path(agents_root: Path, agent_id: str) -> Path:
    """Return ``@meta.agent.<agent>.settings`` for *agent_id*.

    The per-agent SETTINGS cascade file lives INSIDE the per-agent store dir
    (``@meta.agent.<agent>.path`` = ``agents/<agent>/``) as ``settings.yaml``
    — NOT the old sibling ``agents/<agent>.yaml`` file (D-2026-06-22).  This
    parallels the per-agent template dir ``agents/<agent>/template`` and the
    per-category store dirs ``agents/<agent>/{common,caches,seeded}/`` (see
    :data:`AGENT_CATEGORY_DIRNAME`).
    """
    return agents_root / agent_id / "settings.yaml"


# --------------------------------------------------------------------------- #
# The per-agent HOST LAYOUT of the ABSTRACT categories (spec §2a)      #
# --------------------------------------------------------------------------- #
#
# THE single source of the agent-store category layout. Both consumers read it
# from here — the declaration-time ref builder (``agent_defaults.load_common``)
# and the persona symlink shim (``commands.start.ensure_persona_share_symlinks``)
# — so the dirname is spelled ONCE and the two cannot drift (design principle 2:
# no duplicated shared data).

#: category -> the FIXED sub-dirname under the per-agent store root.
#: "The root dirname is FIXED MACHINERY, not a key" (spec §2a): it is not
#: user-settable, because every reasonable want is served better by an absolute
#: ``bindings`` entry or by moving the scope root (``config.agents``), both of
#: which ARE keys.
#:
#: DERIVED from the category list rather than re-typed: the dirname IS the category
#: name, so spelling the three names twice would be two copies of one fact.
AGENT_CATEGORY_DIRNAME: Final[Mapping[str, str]] = {
    category: category for category in ABSTRACT_CATEGORIES
}


def agent_category_dirname(category: str) -> str:
    """The FIXED sub-dirname for an ABSTRACT *category* in a per-agent store.

    An undeclared category is NOT one of the abstract three and is REFUSED rather
    than silently taking a bare root (closed-keyspace rule, spec §0): the concrete
    ``bindings.{ro,rw}`` categories take NO root at any scope (§2a), so asking for
    their dirname is a caller bug.
    """
    try:
        return AGENT_CATEGORY_DIRNAME[category]
    except KeyError:
        raise ValueError(
            f"{category!r} is not an ABSTRACT category with a per-agent store dir "
            f"(declared: {', '.join(sorted(AGENT_CATEGORY_DIRNAME))}); "
            "bindings.{ro,rw} take no root at any scope (spec §2a)"
        ) from None


def agent_category_root(agents_root: Path, agent: str, category: str) -> Path:
    """The REAL host dir an abstract *category* stores under for *agent*.

    ``<agents_root>/<agent>/<dirname>`` — the resolved twin of
    :func:`agent_category_root_ref`. Used where a caller needs a real
    :class:`~pathlib.Path` (the persona shim), never to build a stored value.
    """
    return agents_root / agent / agent_category_dirname(category)


def agent_category_root_ref(agent: str, category: str) -> str:
    """The self-resolving ``@``-ref an abstract *category*'s sources root at.

    ``@meta.agent.<agent>.path/<dirname>`` — the AGENT row of the spec's
    DECLARATION-ROOT table (§2a), read from the single copy of that table
    in :data:`~kanibako.settings.settings_categories.DECLARATION_ROOT_REF`. This is what a
    loader STORES, so the stored value resolves on its own with no layer prepending
    anything later (§2a).
    """
    root = DECLARATION_ROOT_REF["agent"].format(agent=agent)
    return f"{root}/{agent_category_dirname(category)}"


#: The TOKEN prefixes that make a ``host_src`` resolve on its own when UNESCAPED
#: (spec §2a: ``~``, ``$var`` or an ``@``-ref). A leading ``/`` is handled
#: separately — see :func:`is_self_resolving`.
_SELF_RESOLVING_TOKENS: Final[tuple[str, ...]] = ("~", "$", "@")


def is_self_resolving(src: str) -> bool:
    r"""Whether *src* resolves on its own (spec §2a).

    True iff *src* is ABSOLUTE, or begins with an UNESCAPED ``~`` / ``$`` / ``@``.
    Anything else is a BARE RELATIVE leaf: meaningful only under a root, and a
    DEFECT wherever no root exists.

    ⚑ ESCAPES ARE READ THE WAY THE RESOLVER READS THEM, and the two leading-escape
    cases fall on OPPOSITE sides — which is why this cannot be a plain first-char
    test:

    * ``\/foo`` unescapes to ``/foo``, which is ABSOLUTE. The retired post-expand
      join never joined it (it tested the unescaped string for a leading ``/``), so
      calling it relative here would DIVERGE from the behaviour this phase
      preserves.
    * ``\~foo`` unescapes to the literal ``~foo`` — a plain relative dir that
      merely starts with a tilde, NOT a home reference. The retired join did not
      join it either, but only because ``~foo`` expands home-ward before the test;
      the answer (leave it alone / treat as relative) matches.

    So a leading ``/`` is tested AFTER unescaping, while the token prefixes count
    only when they are NOT escaped.
    """
    if src[:1] in _SELF_RESOLVING_TOKENS:
        return True
    # A leading ``/``, escaped or not, is absolute once the resolver unescapes it.
    return src[:1] == "/" or src[:2] == "\\/"


def root_relative_source(src: str, root_ref: str) -> str:
    """Root a BARE RELATIVE *src* under *root_ref*; return it unchanged otherwise.

    THE declaration-time rooting, implemented ONCE (spec §2a): a
    self-resolving source (:func:`is_self_resolving`) is emitted VERBATIM; a bare
    relative leaf becomes ``<root_ref>/<src>``.

    ⚑ An ABSOLUTE (or ``~`` / ``$var`` / ``@``-ref) source in an abstract category
    is LEGAL and is NOT root-joined — the root is a DEFAULT FOR RELATIVE SOURCES,
    not a universal law (spec §2a; the spec's own ``caches.transform``
    worked example is an ``@system.cache``-rooted identity mount).

    Applied ONLY by the ABSTRACT-category declaration loaders.
    ``bindings.{ro,rw}`` take no root at any scope, so a relative source there is a
    DEFECT, not a shorthand, and is refused where it is declared.
    """
    if is_self_resolving(src):
        return src
    return f"{root_ref}/{src}"


def agent_config_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Return the path to an agent's config (settings) file.

    Convenience wrapper for callers that hold a *data_path* rather than the
    resolved agents root; delegates to :func:`agent_settings_path`.
    """
    return agent_settings_path(agents_dir(data_path, paths_agents), agent_id)
