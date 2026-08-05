"""Agent YAML configuration: load, write, and resolve per-agent settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES, DECLARATION_ROOT_REF

# Keys that live directly in the [agent] section as agent identity (not state).
IDENTITY_KEYS = frozenset({"name", "run_args"})

# Every schema-owned (MODELLED) key of the per-agent file. Anything else that is
# dict-valued under ``self`` is a discriminated ``self.<node>.*`` sub-table and
# rides ``AgentConfig.node_tables`` opaquely; the modelled keys must never be
# captured there (load) nor clobbered from there (write).
_MODELED_KEYS = IDENTITY_KEYS | frozenset({"env", "secret_path", "transform_settings"})


@dataclass
class AgentConfig:
    """Per-agent configuration loaded from an agent YAML file.

    Sections:
      agent        — identity (name, run_args) plus agent-state knobs
                     (model, access, allow_helpers, endpoint, …). The
                     per-node DISCRIMINATED sub-table ``agent.<node>.secret_path``
                     also lives here (see *secret_path* below).
      env          — raw env vars injected into container (VAR -> value)
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
      node_tables  — the DISCRIMINATED ``self.<node>.*`` sub-tables (bindings —
                     the shape ``agent_file_route`` still routes ``bindings.*`` to,
                     pending the tracked bindings flatten). NOT modelled here (they
                     ride ``_agent_partial`` into the launch cascade, not the launch
                     invocation) but carried OPAQUELY through the load→write
                     round-trip so a read-modify-write persist never drops a
                     user's node binds.
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    secret_path: dict[str, str] = field(default_factory=dict)
    transform_settings: dict = field(default_factory=dict)
    node_tables: dict[str, dict] = field(default_factory=dict)


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


def agent_file_route(tail: str, node: str) -> tuple[tuple[str, ...], str]:
    """Map a per-agent-file key TAIL to its ``(sections, leaf)`` inside the file.

    *tail* is the part of a canonical per-node agent key AFTER the ``agent.<node>.``
    prefix (e.g. ``model``, ``env.FOO``, ``secret_path.TOK``, ``bindings.ro.share``);
    *node* is the agent id (the file's own discriminator).

    SINGLE SOURCE OF TRUTH for the per-agent settings-file shape. The file's own
    top-level table is ``self`` (its self-reference — the renamed old bare
    ``agent`` values), and the category split is load-bearing:

    * flat state (``model`` / ``endpoint`` / ``access`` / …) and ``env.*`` live
      DIRECTLY under ``self`` (``self.<key>`` / ``self.env.<VAR>``) — the shape
      :func:`load_agent_config` reads into ``AgentConfig`` for the launch invocation;
    * ``secret_path.*`` lives DIRECTLY under ``self`` (``self.secret_path.<VAR>``) —
      ``self`` IS ``agent.<node>``, so there is NO second ``<node>`` embedding. It is
      read by :func:`load_agent_config` into ``AgentConfig`` for the launch mount.
    * ``bindings.{ro,rw}.*`` still live in the DISCRIMINATED ``self.<node>.*`` sub-table
      — the shape ``_agent_partial`` reads into the launch cascade (it reads
      ``self.<node>`` and re-roots to ``agent.<node>``). Flattening bindings the same
      way as secret_path is a tracked follow-up (its cascade/repoint machinery needs a
      separate, careful pass).

    Every reader/writer of the file (``agent set``/get/reset, the ``config_interface``
    generic engine's per-node resolvers, and the bind ``repoint_host_src`` write)
    routes through here, so ``self`` and the flat/nested split are defined ONCE — a
    future rename touches this function alone.
    """
    if tail.startswith("secret_path."):
        return ("self", "secret_path"), tail[len("secret_path."):]
    if tail.startswith("bindings."):
        segs = tail.split(".")  # bindings.<ro|rw>.<name>
        return ("self", node, *segs[:-1]), segs[-1]
    if tail.startswith("env."):
        return ("self", "env"), tail[len("env."):]
    return ("self",), tail


def agent_config_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Return the path to an agent's config (settings) file.

    Convenience wrapper for callers that hold a *data_path* rather than the
    resolved agents root; delegates to :func:`agent_settings_path`.
    """
    return agent_settings_path(agents_dir(data_path, paths_agents), agent_id)


def load_agent_config(path: Path) -> AgentConfig:
    """Read an agent config file and return an AgentConfig.

    Returns defaults if the file does not exist.
    """
    cfg = AgentConfig()
    if not path.exists():
        return cfg

    data = load_doc(path)

    agent_sec = data.get("self", {})
    if not isinstance(agent_sec, dict):
        agent_sec = {}
    cfg.name = str(agent_sec.get("name", ""))
    raw_args = agent_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    # Flat state = the SCALAR agent-state knobs. Exclude identity keys AND any
    # dict-valued entry: the discriminated ``<node>:`` sub-table (secret_path, node
    # binds) is a dict and is NOT flat state — it rides ``_agent_partial``, not the
    # ``_agent_state_partial`` state channel.
    cfg.state = {
        k: str(v)
        for k, v in agent_sec.items()
        if k not in IDENTITY_KEYS and not isinstance(v, dict)
    }
    cfg.env = {k: str(v) for k, v in agent_sec.get("env", {}).items()}
    # secret_path: VAR -> host PATH pointer, read DIRECTLY from ``self.secret_path``
    # (spec §2a SECRET category — ``self`` IS ``agent.<node>``, no second embedding).
    # Stored as a plain string path; the file's CONTENTS (the secret) are never
    # persisted here nor read — they are ro-mounted + exported IN-BOX only at launch.
    secret_sub = agent_sec.get("secret_path", {})
    cfg.secret_path = {
        k: str(v) for k, v in secret_sub.items()
    } if isinstance(secret_sub, dict) else {}
    cfg.transform_settings = dict(agent_sec.get("transform_settings", {}))
    # Any OTHER dict-valued entry under ``self`` is a discriminated
    # ``self.<node>.*`` sub-table (node binds).  Carry it OPAQUELY (see the
    # class docstring): without this, every load→write round-trip (the launch
    # persist at start.py, the persona import) would silently DROP it.
    cfg.node_tables = {
        k: dict(v)
        for k, v in agent_sec.items()
        if isinstance(v, dict) and k not in _MODELED_KEYS
    }

    return cfg


def write_agent_config(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a YAML file."""
    agent_sec: dict = {
        "name": cfg.name,
        "run_args": list(cfg.run_args),
    }
    for k, v in cfg.state.items():
        agent_sec[k] = v
    # secret_path (spec §2a SECRET category) is stored DIRECTLY under ``self``
    # (``self.secret_path.<VAR>`` — ``self`` IS ``agent.<node>``, no second ``<node>``
    # embedding) — the SAME first-class category location ``config set
    # agent.<node>.secret_path.<VAR>`` writes and ``_agent_partial`` reads into the
    # launch cascade. Only materialized when non-empty (sparse).
    if cfg.secret_path:
        agent_sec["secret_path"] = dict(cfg.secret_path)
    # Sparse write — an EMPTY category is not materialized (parity with
    # secret_path above; [[settings-must-map-to-keystore-key]]). A phantom
    # ``transform_settings: {}`` / ``env: {}`` would otherwise be counted as an
    # override by ``agent reset --all``. transform_settings is NOT a reset-all
    # exception — when set it is a normal override, wiped like any other.
    if cfg.transform_settings:
        agent_sec["transform_settings"] = dict(cfg.transform_settings)
    if cfg.env:
        agent_sec["env"] = dict(cfg.env)
    # Discriminated ``self.<node>.*`` sub-tables (node binds) re-emitted
    # opaquely — sparse (an empty table is dropped, parity with the categories
    # above); see :func:`load_agent_config` and the class docstring.  A
    # schema-owned key can never ride through here (guard both ends: load never
    # captures one, and a hand-built config cannot clobber a modelled table).
    for node_key, node_table in cfg.node_tables.items():
        if node_table and node_key not in _MODELED_KEYS:
            agent_sec[node_key] = dict(node_table)

    data: dict = {
        "self": agent_sec,
    }
    # The settings file lives inside the per-agent store dir
    # (agents/<agent>/settings.yaml); ensure that dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, data)
