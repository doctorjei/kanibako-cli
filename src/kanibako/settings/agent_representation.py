"""Agent descriptor → KeyStore representation (block 7a — PURE, ALONGSIDE).

This module represents a plugin's per-agent DESCRIPTOR delivery binds as
agent-level category-default entries in the resolved-keyspace
:class:`~kanibako.settings.settings_store.KeyStore`, so agent binary/launcher/share
delivery flows through the ONE category keyspace (the single-route invariant),
NOT a parallel descriptor mount route.

It is item-0's hard half: each :class:`~kanibako.targets.base.Binding` in a
:class:`~kanibako.targets.base.PluginDescriptor` becomes an
``agent.<name>.bindings.{ro,rw}`` DEST-KEYED entry ``box_dest -> BindEntry(src,
opts)`` under the descriptor's OWN agent name ``<name>`` (``install.name``; S27) —
the §2d ``agent.<agent>.bindings.{ro,rw}`` key form, which is TERMINAL (R-5: the
arm is the WHOLE key and the destinations inside it are NOT key segments), and
never a bare ``agent`` token (§0) —
mirroring :func:`~kanibako.targets.assembly.resolve_binding_source`'s
origin→host_src resolution.

PURE + ALONGSIDE (the block-7a boundaries)
------------------------------------------
* **Imported, single-source resolution.** host_src is resolved by IMPORTING
  :func:`~kanibako.targets.assembly.resolve_binding_source` — the origin→path
  logic is NOT re-implemented here.
* **No override application (S26).** ``resolve_binding_source`` is called with
  ``override=""`` so this emits the origin DEFAULT only. A user repoint now comes
  from a HIGHER cascade level (``box`` / ``workset`` / ``agent.<active>``), merged
  by block 2b — NOT baked into the agent default here.
* **No existence check (S26).** This function NEVER touches the filesystem (no
  ``Path.exists()``); the ``AGENT_CRITICAL`` must-exist safe-fail is a CONSUMER
  concern (the block-7b mount/reconcile step), not representation. Feed it a
  fixture install whose paths do not exist and it still represents them.
* **No expansion (§6a / spec §0).** ``@``-refs / ``$XDG`` / ``~`` are left RAW —
  expansion is block 3. ``box_dest`` is carried VERBATIM: the descriptor loader
  (:func:`kanibako.settings.agent_defaults._build_binding`) has ALREADY expanded the
  ``$GUEST_HOME`` box constant at load, so the :class:`Binding` this function
  receives is post-expansion; re-expanding would be wrong. A LITERAL-origin raw
  ``@``/``$XDG``/``~`` ``box_dest`` therefore stays raw for free (§6a).
* **Build ALONGSIDE.** Nothing here is wired into the launch path; block 7b swaps
  ``descriptor_mounts`` onto this representation. ``assembly.py`` /
  ``agent_defaults.py`` / ``start.py`` are UNTOUCHED.

Representation rules (the heart of the block — brief §3)
--------------------------------------------------------
For each :class:`Binding` in ``descriptor.bindings`` (order preserved):

* **host_src** = ``resolve_binding_source(binding, install, override="")`` (the
  origin-resolved ``LAUNCHER`` / ``INSTALL_DIR`` / ``BINARY`` / ``LITERAL`` path),
  stored as ``str(host_src)`` (``BindEntry.src`` is ``str``, S1) and — ⚑ R-11 —
  NEVER canonicalized: a source is a HOST path and resolves on its own.
* **box_dest** = the MAP KEY, ``binding.box_dest`` normalized by
  :func:`~kanibako.settings.settings_resolve.normalize_bind_dest` (R-11: a dest is
  a GUEST path, so ``~`` expands to the fixed guest home and ``~``/``~/`` are one
  destination). It is no longer part of the value.
* **opts** = ``"ro"`` if ``binding.ro`` else ``None``. ``None`` (NOT ``""``) means
  "no per-entry mount-options override" — ``BindEntry.opts`` defaults to ``None``
  (S1) and reconcile falls back to the category default for an rw bind. This is the
  bind convention, distinct from ``descriptor_mounts``'s ``""`` (an argv-mount
  detail, not the stored shape).
* **key path** = the descriptor's OWN agent name ``<name>`` (``install.name``;
  S27): ``agent.<name>.bindings.ro`` or ``agent.<name>.bindings.rw`` per
  ``binding.ro``, with ``box_dest`` as the entry key INSIDE that arm.
  ⚑ ``binding.key`` is NOT used here any more (R-10 dropped the entry name from
  the keyspace); it remains the descriptor's own stable identifier and what
  ``critical`` names. The agent NAME is IN the key path (the §2d
  ``agent.<agent>.*`` form, NOT a bare ``agent`` token, §0), so this partial
  merges BY NAME with 2a's discriminated ``agent.<active-name>.*`` level and any
  higher-scope ``agent.<name>.*`` override (S8 / block 2b).

None-origin rule (S27, recorded here)
-------------------------------------
When ``resolve_binding_source`` returns ``None`` (an unresolvable origin — e.g.
a ``LITERAL`` binding with no ``literal_src``, or a detection field the install
left unset), the entry is **OMITTED**. This mirrors the AGENT best-effort skip in
:func:`~kanibako.targets.assembly.descriptor_mounts` and keeps the tier-2 typed
accessor honest: ``bindings`` exposes ``Mapping[str, BindEntry]`` (NOT
``BindEntry | None``)
ONLY because build omits absent/None binds (design §5/§6e) — emitting a
``None``-host bind would be a lie a consumer crashes on.

Authority: spec ``settings-keyspace-1.8.0.md`` §2d
(``agent.<agent>.bindings.{ro,rw}.<key>`` — the ONLY agent key form; §0
forbids a bare ``agent.<key>``) / §2a (binding REPRESENTATION);
``~/vault/rw/keystore-design.md`` §2 (binds are structured) / §6a (raw refs). SEAMS
S1/S2/S3/S7/S8/S9 + S26/S27.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.settings.settings_store import BindEntry, KeyStore
from kanibako.targets.assembly import resolve_binding_source

if TYPE_CHECKING:
    from kanibako.targets.base import AgentInstall, PluginDescriptor


def agent_default_partial(
    descriptor: PluginDescriptor,
    install: AgentInstall,
    node_name: str | None = None,
) -> KeyStore:
    """Represent a descriptor's delivery bindings as an agent-level KeyStore partial.

    Returns a :class:`~kanibako.settings.settings_store.KeyStore` partial rooted at
    ``agent.<node>`` where ``<node>`` is the ACTIVE node-name (*node_name*), holding
    each resolvable :class:`~kanibako.targets.base.Binding` as a
    ``agent.<node>.bindings.{ro,rw}`` entry ``box_dest -> BindEntry(src, opts)`` —
    mirroring :func:`~kanibako.targets.assembly.resolve_binding_source` with NO
    override and NO existence check (S26). See the module docstring for the full
    rules and the None-origin OMIT contract.

    PERSONA threading (Block E fix 2a): the read side (``_agent_pick_node``) walks
    ``agent.default`` ∪ ``agent.<active_agent>``, where ``<active_agent>`` is the
    resolved NODE-name (``navigator℘claude`` for a persona). The descriptor's
    ``install.name`` is the HARNESS (``"claude"``, hardcoded in claude's ``detect()``),
    so rooting the binds under ``install.name`` ORPHANS a persona's AGENT_CRITICAL
    delivery binds at ``agent.claude.*`` (never read → the ``claude`` binary is never
    mounted → the container exits immediately). So the partial roots under the ACTIVE
    node-name (*node_name*). For a BARE agent node==harness=="claude", so the binds
    still land at ``agent.claude.*`` — byte-identical. *node_name* falls back to
    ``install.name`` only when a caller omits it (legacy / test convenience).

    The partial nests ``agent.<name>.bindings`` with ``ro`` / ``rw`` sub-tables; a
    sub-table is present only if at least one binding lands in it, ``bindings`` is
    present only if at least one binding resolved, and the result is ALWAYS a
    ``KeyStore`` rooted at ``agent.<name>`` (empty ``agent.<name>`` node when no
    binding resolves) so it merges by NAME with the 2a agent levels (S8).
    Descriptor order is preserved.

    PURE: no filesystem access, no mutation of *descriptor* / *install*.
    """
    # Build the ro/rw sub-tables locally, then attach only the non-empty ones so
    # the partial shape stays minimal (an empty `ro`/`rw`/`bindings` node would be
    # an absent-vs-present-empty distinction the merge need not carry).
    ro_binds = KeyStore()
    rw_binds = KeyStore()
    # The ACTIVE NODE name — computed here rather than after the loop only so the
    # act-once refusal below can name the agent it is talking about.
    name = node_name if node_name is not None else install.name

    for binding in descriptor.bindings:
        src = resolve_binding_source(binding, install, override="")
        if src is None:
            # None-origin → OMIT (S27); matches the AGENT best-effort skip and
            # keeps tier-2 `Mapping[str, BindEntry]` honest (design §5/§6e).
            continue
        opts = "ro" if binding.ro else None
        # ⚑ DEST-KEYED (R-3/R-6/R-10/R-11): the arm's map key is the NORMALIZED box
        # DESTINATION and `binding.key` is NOT part of it. The descriptor keeps its
        # `key` — it is still the plugin's stable identifier and still what
        # `critical` names (targets/base.py) — it simply stopped being a settings
        # key segment. ⚑ Only `box_dest` is normalized; `src` is a HOST path and is
        # carried exactly as resolved.
        arm = ro_binds if binding.ro else rw_binds
        dest = normalize_bind_dest(binding.box_dest)
        if dict.__contains__(arm, dest):
            # Two descriptor bindings at ONE destination in ONE arm: act-once, so
            # this cannot be an overlay. Under dest-keying the second would just
            # replace the first with nothing downstream able to see the loss, so it
            # is named here instead — the descriptor is the plugin's, and the
            # plugin author is who has to fix it.
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

    # Root under the ACTIVE node-name (§2d ``agent.<agent>.*``): for a BARE agent the
    # node-name IS the harness (``agent.claude.bindings.*``); for a PERSONA it is the
    # composite node (``agent.navigator℘claude.bindings.*``) the read side actually
    # walks (fix 2a). The agent NAME is part of the KEY PATH (NOT a bare ``agent``
    # token, §0) — so this partial merges BY NAME with 2a's discriminated
    # ``agent.<active>.*`` level and any higher-scope ``agent.<node>.*`` override
    # (block 2b), including a user-set ``agent.<node>.bindings.*`` repoint on a
    # scope file.
    agent_sub = KeyStore()
    if dict.__len__(bindings):
        agent_sub["bindings"] = bindings

    agent = KeyStore()
    agent[name] = agent_sub

    partial = KeyStore()
    partial["agent"] = agent
    return partial


# ⚑ ``agent_default_bind_keys(node_name)`` USED TO LIVE HERE and is GONE (R-9).
# It emitted the same ``agent.<node>.bindings.{ro,rw}.<key>`` keys as
# :func:`agent_default_partial`, detect-free and with a placeholder host_src, as a
# context-light SET-TIME floor registry — its only purpose was to let ``config set``
# repoint a descriptor bind's source without the must-exist gate refusing it as
# "nowhere in the cascade". That CLI write route is retired (disk-store rework step 1,
# an accepted loss tracked as DS-BL1), so the registry had no consumer left.
#
# ⚑ NOTHING ABOUT LAUNCH CHANGED. :func:`agent_default_partial` above is the LAUNCH
# representation and is untouched; a user override authored by hand in
# ``agents/<node>/settings.yaml`` still beats it by cascade merge. Do not resurrect
# this function to "restore" a delivery path — it never was one.


def agent_common_for_node(
    table: "dict[str, tuple]", *, node_name: str, harness: str,
) -> "dict[str, tuple]":
    """Re-key a plugin's ``default_common()`` table from the HARNESS to the NODE.

    A plugin declares its agent-scope commons against its OWN name — the HARNESS
    (``load_common(pkg, file, self.name)`` → ``agent.claude.common.plugins``) — but
    the §2d read pick overlays ``agent.default`` ∪ ``agent.<ACTIVE NODE>``. For a
    PERSONA the active node is ``navigator℘claude``, so every harness-keyed common
    was invisible: a persona box mounted NEITHER ``~/.claude/plugins`` NOR
    ``~/.claude/cache``, and ``ensure_persona_share_symlinks`` maintained links
    nothing consumed. This is the P7 fix for that live bug (found while planning
    P3; deliberately deferred to here, where the agent-key semantics were open).

    **A persona INHERITS its harness's commons** — that is the documented intent of
    the symlink shim, which points ``agents/<node>/common/<leaf>`` at
    ``agents/<harness>/common/<leaf>`` and explicitly steps aside when the persona
    has a real dir of its own. Both halves are re-keyed so that intent holds:

    * the KEY ``agent.<harness>.common.<leaf>`` → ``agent.<node>.common.<leaf>``,
      so the pick actually sees it;
    * the SOURCE ``@meta.agent.<harness>.path/common/<leaf>`` →
      ``@meta.agent.<node>.path/common/<leaf>``, so the bind resolves through the
      NODE path — the symlink (shared with the harness) by default, or the persona's
      OWN directory when it has one. Re-keying WITHOUT re-rooting would bind the
      harness dir directly and make the shim's own-dir branch unreachable.

    ⚑ The re-root rule is deliberately NARROW: only a source that is exactly the
    harness's declaration root for this category is moved. An absolute / ``~`` /
    ``$var`` / unrelated ``@``-ref source is carried VERBATIM — those are
    self-resolving by the plugin's own choice (spec §2a) and are not the
    plugin saying "my store dir".

    A BARE agent (``node_name == harness``) gets the IDENTITY back — byte-identical
    to the plugin's table, so nothing about a non-persona launch changes.

    ⚑⚑ **THE TABLE IS DEST-KEYED (2026-08-08c), AND THE KEY TEST MOVED WITH IT.**
    ``common`` is a TERMINAL key, so the table holds ONE entry
    ``agent.<harness>.common -> {box_dest: (host_src[, opts])}`` and the re-key is
    an EXACT match on that key, not a PREFIX match on ``agent.<harness>.common.``.
    Getting that backwards is not a cosmetic slip: a trailing-dot prefix test can
    never match the terminal key, so the whole function would silently no-op and a
    persona box would lose ``~/.claude/plugins`` and ``~/.claude/cache`` again —
    the exact bug this function exists to fix.
    The re-root then walks the map's VALUES; the destinations (its keys) are
    untouched, because a persona and its harness deliver to the SAME in-box path.
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
    """The store-dir LEAF *host_src* names under *harness*'s ``common`` root.

    ``@meta.agent.claude.path/common/plugins`` → ``"plugins"`` for harness
    ``claude``; anything else → ``None``.

    ⚑ THE ONE PLACE THIS RULE IS WRITTEN, and it has two consumers that would
    otherwise each invent it: :func:`agent_common_for_node`, which re-roots a
    persona's inherited source, and ``commands.start.ensure_persona_share_symlinks``,
    which needs the same dirname to lay the symlink shim. Before 2026-08-08c both
    read it off the KEY (``agent.<a>.common.<leaf>``); dest-keying removed the
    entry name, so the rooted ``host_src`` is the only remaining carrier.

    ⚑ DELIBERATELY NARROW, and the narrowness IS the contract: only a source that
    is EXACTLY the harness's declaration root for this category yields a leaf. An
    absolute / ``~`` / ``$var`` / unrelated ``@``-ref source is the plugin saying
    "this specific path", not "my store dir" (spec §2a — such a source is
    self-resolving by the plugin's own choice), so it has no store leaf and gets
    ``None``. A caller must treat ``None`` as "nothing to re-root / nothing to
    shim", never as a parse failure.
    """
    root = harness_common_root(harness) + "/"
    if not isinstance(host_src, str) or not host_src.startswith(root):
        return None
    leaf = host_src[len(root):]
    return leaf or None
