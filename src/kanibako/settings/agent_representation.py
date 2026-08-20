"""Agent descriptor → KeyStore representation: descriptor binds as agent-level keys.

A plugin's per-agent DESCRIPTOR delivery binds become ``agent.<node>.bindings.{ro,rw}``
DEST-KEYED entries ``box_dest -> BindEntry(src, opts)``, so agent binary/launcher/share
delivery flows through the ONE category keyspace (the single-route invariant), NOT a
parallel descriptor mount route.

⚑ PURE, and each half is load-bearing: no filesystem access (no existence check —
the ``AGENT_CRITICAL`` must-exist safe-fail is a CONSUMER concern, S26), no override
application (``override=""``, S26), no ``@``/``$XDG``/``~`` expansion (§6a), and no
canonicalization of a host source (R-11). Only ``box_dest`` is normalized.

Authority: spec ``settings-keyspace-1.8.0.md`` §2d (the ONLY agent key form; §0 forbids a
bare ``agent.<key>``) / §2a. Representation rules, the None-origin OMIT contract (S27) and
the block-7a boundaries: ``llm-docs/kanibako/settings/agent_representation.py.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kanibako.settings.kb_store import BindEntry
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.targets.assembly import resolve_binding_source

if TYPE_CHECKING:
    from kanibako.targets.base import AgentInstall, PluginDescriptor


def agent_default_partial(
    descriptor: PluginDescriptor,
    install: AgentInstall,
    node_name: str | None = None,
) -> KeyStore:
    """Represent a descriptor's delivery bindings as an agent-level KeyStore partial.

    Rooted at ``agent.<node>``, the ACTIVE node-name (*node_name*), holding each resolvable
    ``Binding`` as a ``agent.<node>.bindings.{ro,rw}`` entry in descriptor order.
    *node_name* falls back to ``install.name`` only when a caller omits it.

    ⚑ ROOT UNDER THE ACTIVE NODE, NEVER THE HARNESS (Block E fix 2a). The read side
    (``_agent_pick_node``) walks ``agent.default`` ∪ ``agent.<active node>``, and
    ``install.name`` is the HARNESS (``"claude"``), so rooting a persona's binds there
    ORPHANS its AGENT_CRITICAL delivery at ``agent.claude.*``: never read → the ``claude``
    binary is never mounted → the container exits immediately. A BARE agent, node ==
    harness, is byte-identical either way.

    PURE: also no mutation of *descriptor* / *install*. Partial shape and the None-origin
    OMIT contract: the llm-doc.
    """
    ro_binds = KeyStore()
    rw_binds = KeyStore()
    # Computed before the loop so the act-once refusal can name the agent.
    name = node_name if node_name is not None else install.name

    for binding in descriptor.bindings:
        src = resolve_binding_source(binding, install, override="")
        if src is None:
            continue  # None-origin → OMIT (S27); keeps tier-2 `Mapping[str, BindEntry]` honest.
        opts = "ro" if binding.ro else None  # `None`, NOT `""`: no per-entry override (S1).
        # ⚑ DEST-KEYED (R-3/R-6/R-10/R-11): the arm's map key is the NORMALIZED box
        # DESTINATION and `binding.key` is NOT part of it (it stays the plugin's stable
        # identifier and what `critical` names, targets/base.py). ⚑ Only `box_dest` is
        # normalized; `src` is a HOST path, carried exactly as resolved.
        arm = ro_binds if binding.ro else rw_binds
        dest = normalize_bind_dest(binding.box_dest)
        if dict.__contains__(arm, dest):
            # Act-once, so this cannot be an overlay: under dest-keying the second entry
            # would silently REPLACE the first, so the plugin author is told instead.
            raise SettingsError(
                f"agent {name}: descriptor bindings {binding.key!r} and an "
                f"earlier binding both target {dest!r} in the "
                f"{'ro' if binding.ro else 'rw'} arm; bindings are act-once and a "
                f"dest-keyed arm admits one entry per destination."
            )
        arm[dest] = BindEntry(str(src), opts)

    bindings = KeyStore()
    if dict.__len__(ro_binds):
        bindings["ro"] = ro_binds
    if dict.__len__(rw_binds):
        bindings["rw"] = rw_binds

    # The agent NAME is part of the KEY PATH (§2d), so this merges BY NAME with 2a's
    # ``agent.<active>.*`` level and any higher-scope override (block 2b).
    agent_sub = KeyStore()
    if dict.__len__(bindings):
        agent_sub["bindings"] = bindings

    agent = KeyStore()
    agent[name] = agent_sub

    partial = KeyStore()
    partial["agent"] = agent
    return partial


# ⚑ ``agent_default_bind_keys(node_name)`` USED TO LIVE HERE and is GONE (R-9): a SET-TIME
# floor registry whose only consumer, the ``config set`` repoint route, is retired (DS-BL1). ⚑ NOTHING ABOUT LAUNCH CHANGED — :func:`agent_default_partial`
# above is the LAUNCH representation and is untouched. Do not resurrect this function to
# "restore" a delivery path; it never was one.


def agent_env_for_node(
    table: "dict[str, str]", *, node_name: str, harness: str,
) -> "dict[str, str]":
    """Re-key a plugin's ``default_envs()`` table from the HARNESS to the NODE.

    The env twin of :func:`agent_common_for_node`, for the same reason: the §2d read pick
    overlays ``agent.default`` ∪ ``agent.<ACTIVE NODE>``, so a HARNESS-keyed declaration is
    invisible to a PERSONA node — a box launching without the variables its harness
    requires. NO re-root half: an env value is a scalar, so the KEY swap is the whole job.

    ⚑ The key is DATA: only the leading ``agent.<harness>.`` PREFIX is replaced and
    the rest is carried through untouched — never split into segments and rejoined.
    """
    if not node_name or node_name == harness:
        return dict(table)
    prefix = f"agent.{harness}."
    out: "dict[str, str]" = {}
    for key, value in table.items():
        if key.startswith(prefix):
            key = f"agent.{node_name}." + key[len(prefix):]
        out[key] = value
    return out


def agent_common_for_node(
    table: "dict[str, tuple]", *, node_name: str, harness: str,
) -> "dict[str, tuple]":
    """Re-key a plugin's ``default_common()`` table from the HARNESS to the NODE, and re-root it.

    A plugin declares its agent-scope commons against its OWN name, the HARNESS
    (``agent.claude.common.plugins``), which the §2d read pick never sees for a PERSONA —
    the live bug where a persona box mounted NEITHER ``~/.claude/plugins`` NOR
    ``~/.claude/cache``. BOTH halves move: the KEY, so the pick sees it, and the SOURCE
    root, so the bind resolves through the NODE path — re-keying alone would bind the
    harness dir and make the symlink shim's own-dir branch unreachable.

    ⚑⚑ EXACT-MATCH the TERMINAL key ``agent.<harness>.common`` — NOT a prefix match on
    ``agent.<harness>.common.``. The table is DEST-KEYED (2026-08-08c), so a trailing-dot
    prefix test can never match, the function silently no-ops, and the bug above is back.

    ⚑ The re-root is deliberately NARROW: only a source that is exactly the harness's
    declaration root moves. An absolute / ``~`` / ``$var`` / unrelated
    ``@``-ref source is carried VERBATIM (spec §2a). Full reasoning: the llm-doc.
    """
    if not node_name or node_name == harness:
        return dict(table)
    node_root = harness_common_root(node_name) + "/"
    category_key = f"agent.{harness}.common"
    new_category_key = f"agent.{node_name}.common"
    out: "dict[str, tuple]" = {}
    for key, value in table.items():
        if key != category_key or not isinstance(value, dict):
            out[key] = value  # not this harness's common — leave untouched.
            continue
        rekeyed = {}
        for dest, entry in value.items():
            host_src = entry[0]
            leaf = harness_common_leaf(host_src, harness)
            if leaf is not None:
                host_src = node_root + leaf
            rekeyed[dest] = (host_src, *entry[1:])
        out[new_category_key] = rekeyed
    return out


def harness_common_root(node: str) -> str:
    """The ``@``-ref declaration root of *node*'s agent-scope ``common`` store."""
    from kanibako.settings.agent_config import agent_category_root_ref

    return agent_category_root_ref(node, "common")


def harness_common_leaf(host_src: object, harness: str) -> str | None:
    """The store-dir LEAF *host_src* names under *harness*'s ``common`` root, else ``None``.

    ``@meta.agent.claude.path/common/plugins`` → ``"plugins"`` for harness ``claude``.

    ⚑ THE ONE PLACE THIS RULE IS WRITTEN, and it has two consumers that would otherwise
    each invent it: :func:`agent_common_for_node` and
    ``commands.start.ensure_persona_share_symlinks``.

    ⚑ DELIBERATELY NARROW, and the narrowness IS the contract: only a source that is
    EXACTLY the harness's declaration root yields a leaf. A caller must treat ``None`` as
    "nothing to re-root / nothing to shim", never as a parse failure.
    """
    root = harness_common_root(harness) + "/"
    if not isinstance(host_src, str) or not host_src.startswith(root):
        return None
    leaf = host_src[len(root):]
    return leaf or None
