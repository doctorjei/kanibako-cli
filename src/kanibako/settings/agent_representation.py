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

from typing import TYPE_CHECKING, TypeVar

from kanibako.settings.kb_store import BindEntry
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_categories import DECLARATION_ROOT_REF
from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.targets.assembly import resolve_binding_source

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kanibako.targets.base import AgentInstall, PluginDescriptor

#: The value of a re-keyed table entry, carried through the key swap untouched.
_V = TypeVar("_V")


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


def _rekey_agent_keys(
    table: "Mapping[str, _V]", *, node_name: str, harness: str,
) -> "dict[str, _V]":
    """Swap the leading ``agent.<harness>.`` key prefix for ``agent.<node>.``.

    THE ONE key half of the node adaptation, shared by :func:`agent_env_for_node` and
    :func:`agent_categories_for_node` so the two cannot disagree about what a
    HARNESS-keyed declaration becomes.

    ⚑ The key is DATA: only the leading PREFIX is replaced and the rest is carried
    through untouched — never split into segments and rejoined. A key without the
    prefix (``agent.default.*``, another agent's) is left exactly as it is.
    """
    prefix = f"agent.{harness}."
    out: "dict[str, _V]" = {}
    for key, value in table.items():
        if key.startswith(prefix):
            key = f"agent.{node_name}." + key[len(prefix):]
        out[key] = value
    return out


def agent_env_for_node(
    table: "dict[str, str]", *, node_name: str, harness: str,
) -> "dict[str, str]":
    """Re-key a plugin's ``default_envs()`` table from the HARNESS to the NODE.

    The env twin of :func:`agent_categories_for_node`, for the same reason: the §2d read
    pick overlays ``agent.default`` ∪ ``agent.<ACTIVE NODE>``, so a HARNESS-keyed
    declaration is invisible to a PERSONA node — a box launching without the variables
    its harness requires. NO re-root half: an env value is a scalar, so the KEY swap is
    the whole job.
    """
    if not node_name or node_name == harness:
        return dict(table)
    return _rekey_agent_keys(table, node_name=node_name, harness=harness)


def agent_categories_for_node(
    table: "dict[str, object]", *, node_name: str, harness: str,
) -> "dict[str, object]":
    """Re-key a plugin's declared CATEGORY table to the NODE, and re-root its sources.

    THE ONE adapter for every agent-scope category a plugin declares —
    ``default_common()``, ``default_seeds()`` and ``default_category_binds()`` alike.
    A plugin declares against its OWN name, the HARNESS (``agent.claude.common``),
    which the §2d read pick never sees for a PERSONA: the live bug where a persona box
    mounted NEITHER ``~/.claude/plugins`` NOR ``~/.claude/cache``, and — until this
    function reached the other two hooks — took a declared seed or category bind with
    NO mount, NO copy, no error and no warning.

    BOTH halves move, and re-keying alone is not the fix (ruled 2026-08-27):

    * the KEY, so the §2d pick sees it;
    * the SOURCE root, so the bind resolves through ``@meta.agent.<NODE>.path`` — a
      SYMLINK that ``commands.start.ensure_persona_share_symlinks`` lays at the
      harness's real store. Sharing happens THROUGH that link, and *"the user can
      change the symlink to a directory or real target"*: replacing it is how a
      persona diverges. Pointing the source straight at the harness store would look
      identical in every test and silently destroy that escape hatch.

    ⚑⚑ THE KEY MATCH IS A PREFIX ON ``agent.<harness>.``, NEVER ON THE WHOLE TERMINAL
    KEY. The tables are DEST-KEYED (2026-08-08c), so the terminal key IS
    ``agent.<harness>.<category>`` and a match written against
    ``agent.<harness>.common.`` can never fire — the function silently no-ops and the
    bug above is back.

    ⚑ The re-root is deliberately NARROW: only a source rooted at the harness's own
    STORE (spec §2a's agent DECLARATION ROOT) moves. An absolute / ``~`` / ``$var`` /
    unrelated ``@``-ref source is carried VERBATIM (spec §2a) — a plugin naming the
    host's real ``~/.claude`` means that dir for every node.

    ⚑ A value that is not a dest-keyed map (a scalar source key, a LIST-valued
    ``masks``) is re-keyed and carried through: there is no source to re-root.
    """
    if not node_name or node_name == harness:
        return dict(table)
    return {
        key: (
            _reroot_arm(value, node_name=node_name, harness=harness)
            if isinstance(value, dict) else value
        )
        for key, value in _rekey_agent_keys(
            table, node_name=node_name, harness=harness,
        ).items()
    }


def _reroot_arm(
    arm: "dict[str, tuple]", *, node_name: str, harness: str,
) -> "dict[str, tuple]":
    """Re-root every HARNESS-store source in one dest-keyed arm onto *node_name*'s store."""
    node_root = harness_store_root(node_name) + "/"
    rerooted: "dict[str, tuple]" = {}
    for dest, entry in arm.items():
        host_src = entry[0]
        leaf = harness_store_leaf(host_src, harness)
        if leaf is not None:
            host_src = node_root + leaf
        rerooted[dest] = (host_src, *entry[1:])
    return rerooted


def harness_store_root(node: str) -> str:
    """The ``@``-ref DECLARATION ROOT of *node*'s whole agent store (spec §2a)."""
    # ⚑ Read from the ONE copy of the spec's table rather than spelled here, and it is
    # the WHOLE store root, not a category's: ``common`` is one entry under it and the
    # rule below has to hold for every other entry a plugin may name.
    return DECLARATION_ROOT_REF["agent"].format(agent=node)


def harness_store_leaf(host_src: object, harness: str) -> str | None:
    """The store-relative path *host_src* names under *harness*'s store root, else ``None``.

    ``@meta.agent.claude.path/common/plugins`` → ``"common/plugins"`` for harness
    ``claude``; ``@meta.agent.claude.path/seedsrc`` → ``"seedsrc"``.

    ⚑ THE ONE PLACE THIS RULE IS WRITTEN, and it has two consumers that would otherwise
    each invent it: :func:`_reroot_arm`, which re-roots a persona's inherited source,
    and ``commands.start.ensure_persona_share_symlinks``, which lays the link that
    source then resolves through. They must agree entry for entry — a re-root with no
    link behind it is an ABSENT source, which is the very symptom this closes, moved
    one hop.

    ⚑ DELIBERATELY NARROW, and the narrowness IS the contract: only a source rooted at
    the harness's store yields a leaf. A caller must treat ``None`` as "nothing to
    re-root / nothing to shim", never as a parse failure.
    """
    root = harness_store_root(harness) + "/"
    if not isinstance(host_src, str) or not host_src.startswith(root):
        return None
    leaf = host_src[len(root):]
    return leaf or None
