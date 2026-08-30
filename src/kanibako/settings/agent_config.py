"""Agent identity: the ``AgentConfig`` record, the agent store's paths and category roots.

⚑ THE FILE'S SHAPE IS NOT HERE — :mod:`kanibako.settings.agent_file` is the one module that
spells the file's root table [spec:15-21, "self"].  Design notes:
``llm-docs/kanibako/settings/agent_config.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping

from kanibako.agent_ref import display_agent_ref
from kanibako.settings.config import AGENT_META_FILE
from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES, DECLARATION_ROOT_REF
from kanibako.settings.settings_resolve import SettingsError, match_var

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


def store_dirname(node: str) -> str:
    """The on-disk store DIRNAME for a *node* — the ``+`` spelling, never ``℘``."""
    # ⚑ THE single place a node becomes a DIRECTORY, so every store path is spelled
    # once.  ``℘`` is a key-path device (``agent_ref.SEPARATORS`` says why) and a
    # store dir is not a key: a user lists it and cd's into it, so it wears the ``+``
    # they typed.  DELEGATES rather than repeating the substitution.
    return display_agent_ref(node)


def agent_settings_path(agents_root: Path, agent_id: str) -> Path:
    """Return ``@meta.agent.<agent>.settings`` for *agent_id*."""
    # ⚑ INSIDE the store dir as ``agent.yaml``, not the old sibling
    # ``agents/<agent>.yaml`` (D-2026-06-22).  Callers pass the CANONICAL node; the
    # ``+`` dirname is applied here, so no caller has to know the two differ.
    return agents_root / store_dirname(agent_id) / AGENT_META_FILE


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


# ⚑ ``agent_category_root(agents_root, agent, category)`` USED TO LIVE HERE and is
# GONE: the RESOLVED twin of :func:`agent_category_root_ref`, composing
# ``agents/<store_dirname>/<category>``.  Its ONE consumer was the persona symlink
# shim, which stopped asking "where does this CATEGORY store?" when the re-root went
# generic — it now carries a WHOLE store-relative path (``common/plugins``,
# ``seedsrc``) that names no category at all, so a per-category composer could not
# express the question.
# 🛑 DO NOT RESURRECT IT AS A CONVENIENCE.  A resolved store path is what
# ``@meta.agent.<a>.path`` ALREADY resolves to (``settings_launch.meta_agent_path_floor``
# defines that anchor as ``@config.agents/<store_dirname>``); a second composer beside
# it is a second answer to one layout question, free to drift, and the drift is silent
# because every path here is create-if-absent.  A caller needing a real Path composes
# ``agents_root / store_dirname(node) / <rel>`` — the ``store_dirname`` call IS the
# shared fact, and it is the one that must not be re-spelled.


def category_root_ref(scope: str, category: str, *, agent: str | None = None) -> str:
    """The self-resolving ``@``-ref an abstract *category*'s sources root at, per SCOPE.

    ⚑ What a loader STORES: it must resolve on its own, with no layer prepending
    anything later (spec §2a).  Every row comes from the single copy of the
    DECLARATION-ROOT table, never a local literal — the four scopes differ ONLY in
    which row is read, which is why there is one function and not four.  *agent* is
    REQUIRED for the agent row and is the only placeholder any row carries; an
    undeclared *scope* is refused rather than given a bare root (spec §0).
    """
    try:
        root = DECLARATION_ROOT_REF[scope]
    except KeyError:
        raise ValueError(
            f"{scope!r} is not a DECLARATION-ROOT scope "
            f"(declared: {', '.join(sorted(DECLARATION_ROOT_REF))}; spec §2a)"
        ) from None
    if "{agent}" in root:
        if agent is None:
            raise ValueError(
                f"the {scope!r} DECLARATION ROOT is DISCRIMINATED "
                f"({root!r}); an agent name is required to build it (spec §2d)"
            )
        root = root.format(agent=agent)
    return f"{root}/{agent_category_dirname(category)}"


def agent_category_root_ref(agent: str, category: str) -> str:
    """The self-resolving ``@``-ref an abstract *category*'s sources root at."""
    # ⚑ The AGENT row of :func:`category_root_ref`, kept as a named entry point for
    # the per-agent loaders that never see another scope.
    return category_root_ref("agent", category, agent=agent)


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


def is_unambiguous_path_value(value: str) -> bool:
    r"""Whether a STORED PATH-KEY value says ON ITS OWN where it points ([R147]).

    Legal: absolute, ``~``-rooted, ``$XDG_*`` or an ``@``-ref.  Anything else — a
    bare leaf, ``./x``, ``../x`` — is AMBIGUOUS and is refused rather than anchored
    (:func:`ambiguous_path_value_error` says why and names both readings).

    ⚑ NARROWER THAN :func:`is_self_resolving`, ON PURPOSE, AND THE DIFFERENCE IS
    ``$VAR``.  That predicate rules on a BIND SOURCE, where a declaration may name
    any variable the launch namespace supplies; this one rules on a path a USER
    typed, and the keyspace's non-XDG variables (``$AGENT``, ``$WORKSET``) expand to
    a bare NAME — so ``$AGENT/logs`` is exactly as relative as ``logs``.
    ⚑ The leading-escape cases fall the same way they do there: ``\/foo`` unescapes
    to an absolute path, ``\~foo`` to a plain relative dir.
    """
    if value[:1] in ("~", "@"):
        return True
    if value[:1] == "$":
        try:
            name, _ = match_var(value, 0)
        except SettingsError:
            return False
        return name.startswith("XDG_")
    return value[:1] == "/" or value[:2] == "\\/"


#: How the other reading is INTRODUCED when the anchor is the root the key's own
#: declared default sits under — every Layer-1/Layer-2 path key and every workset dir key.
DEFAULT_ROOT_LABEL = "this key's default root"

#: The same, for a path key that declares NO root of its own: ``box.images_store``
#: (runtime-probed from podman) and the whole ``secret_path.<VAR>`` family.  The other
#: reading is then the key's spec §2a DECLARATION ROOT, and it is introduced as one.
#: ⚑ THE LABEL IS NOT DECORATION.  Calling a declaration root "this key's default root"
#: would tell a reader a default exists to fall back to, which is the single thing a
#: message about an unset, ambiguous value must not invent.
DECLARATION_ROOT_LABEL = "this key's scope root"


def ambiguous_path_value_error(
    key: str, value: str, *, anchor: str, anchor_ref: str | None = None,
    where: str | None = None, anchor_label: str = DEFAULT_ROOT_LABEL,
) -> str:
    """The [R147] refusal for a bare-relative *value* stored at *key* — BOTH readings named.

    *anchor* is the RESOLVED directory of the other candidate reading: the root this
    key's own default sits under, which is the only anchor besides the cwd that the
    user could plausibly have meant.  *anchor_ref* is that root's legal spelling
    (``@meta.workset.path``, ``$XDG_DATA_HOME``), offered so the user can paste the
    fix.  *where* names the file the value was read from, when the seam has one.
    *anchor_label* introduces the reading — see :data:`DECLARATION_ROOT_LABEL` for the
    one case where the default wording would be a lie.

    ⚑ NAMING BOTH READINGS IS THE POINT, not decoration.  The user had two plausible
    meanings that land in different directories; a message that only said "be
    absolute" would move the guess onto the user instead of removing it.
    ⚑ AN UNRESOLVABLE ANCHOR IS PASSED AS ITS OWN REF SPELLING and *anchor_ref* is then
    left unset — the reading is still named, and in a form the user can paste, without
    the line saying the same thing twice.
    """
    in_file = f" in {where}" if where else ""
    spelled = f", spelled '{anchor_ref}/{value}'" if anchor_ref else ""
    return (
        f"{key} is set to {value!r}{in_file}, which is a BARE RELATIVE path. "
        f"kanibako will not guess what it is relative to — both of these readings "
        f"are plausible and they are DIFFERENT directories:\n"
        f"    {Path(anchor) / value}   ({anchor_label}{spelled})\n"
        f"    {Path.cwd() / value}   (the directory kanibako was run from)\n"
        f"Set the one you mean, spelled so it resolves on its own: an absolute "
        f"path, '~/...', '$XDG_*/...', or an '@'-ref."
    )


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
