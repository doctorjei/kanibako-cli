"""Agent descriptor → KeyStore representation (block 7a — PURE, ALONGSIDE).

This module represents a plugin's per-agent DESCRIPTOR delivery binds as
agent-level category-default entries in the resolved-keyspace
:class:`~kanibako.settings.settings_store.KeyStore`, so agent binary/launcher/share
delivery flows through the ONE category keyspace (the single-route invariant),
NOT a parallel descriptor mount route.

It is item-0's hard half: each :class:`~kanibako.targets.base.Binding` in a
:class:`~kanibako.targets.base.PluginDescriptor` becomes an
``agent.<name>.bindings.{ro,rw}.<key> = Bind(host_src, box_dest, opts)`` leaf under
the descriptor's OWN agent name ``<name>`` (``install.name``; S27) — the §2d
``agent.<agent>.bindings.*`` key form, NOT a bare ``agent`` token (§0) —
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
  stored as ``str(host_src)`` (``Bind.host`` is ``str``, S1).
* **box_dest** = ``binding.box_dest`` VERBATIM (see above).
* **opts** = ``"ro"`` if ``binding.ro`` else ``None``. ``None`` (NOT ``""``) means
  "no per-entry mount-options override" — ``Bind.opts`` defaults to ``None`` (S1)
  and reconcile falls back to the category default for an rw bind. This is the
  ``Bind`` convention, distinct from ``descriptor_mounts``'s ``""`` (an argv-mount
  detail, not the stored shape).
* **key path** = the descriptor's OWN agent name ``<name>`` (``install.name``;
  S27): ``agent.<name>.bindings.ro.<key>`` or ``agent.<name>.bindings.rw.<key>``
  per ``binding.ro``. The agent NAME is IN the key path (the §2d
  ``agent.<agent>.*`` form, NOT a bare ``agent`` token, §0), so this partial
  merges BY NAME with 2a's discriminated ``agent.<active-name>.*`` level and any
  higher-scope ``agent.<name>.*`` override (S8 / block 2b).

None-origin rule (S27, recorded here)
-------------------------------------
When ``resolve_binding_source`` returns ``None`` (an unresolvable origin — e.g.
a ``LITERAL`` binding with no ``literal_src``, or a detection field the install
left unset), the entry is **OMITTED**. This mirrors the AGENT best-effort skip in
:func:`~kanibako.targets.assembly.descriptor_mounts` and keeps the tier-2 typed
accessor honest: ``bindings`` exposes ``Mapping[str, Bind]`` (NOT ``Bind | None``)
ONLY because build omits absent/None binds (design §5/§6e) — emitting a
``None``-host bind would be a lie a consumer crashes on.

Authority: spec ``settings-keyspace-1.8.0.md`` §2d
(``agent.<agent>.bindings.{ro,rw}.<key>`` — the ONLY agent key form; §0
forbids a bare ``agent.<key>``) / §2a (binding REPRESENTATION);
``~/vault/rw/keystore-design.md`` §2 (binds are ``Bind``) / §6a (raw refs). SEAMS
S1/S2/S3/S7/S8/S9 + S26/S27.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kanibako.settings.settings_store import Bind, KeyStore
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
    ``agent.<node>.bindings.{ro,rw}.<key> = Bind(host_src, box_dest, opts)`` leaf —
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

    for binding in descriptor.bindings:
        src = resolve_binding_source(binding, install, override="")
        if src is None:
            # None-origin → OMIT (S27); matches the AGENT best-effort skip and
            # keeps tier-2 `Mapping[str, Bind]` honest (design §5/§6e).
            continue
        opts = "ro" if binding.ro else None
        bind = Bind(str(src), binding.box_dest, opts)
        # `binding.key` is the stable override key; place it under ro/rw by `ro`.
        if binding.ro:
            ro_binds[binding.key] = bind
        else:
            rw_binds[binding.key] = bind

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
    name = node_name if node_name is not None else install.name
    agent_sub = KeyStore()
    if dict.__len__(bindings):
        agent_sub["bindings"] = bindings

    agent = KeyStore()
    agent[name] = agent_sub

    partial = KeyStore()
    partial["agent"] = agent
    return partial


def agent_default_bind_keys(node_name: str) -> "dict[str, tuple[str, ...]]":
    """The per-node DESCRIPTOR delivery-bind KEYS as a context-light set-time floor
    registry (item-0 — the descriptor sibling of :func:`core_defaults.core_default_bind_keys`).

    Given a *node_name* (the canonical ``℘``-form, e.g. ``claude`` or
    ``navigator℘claude``), emits the SAME ``agent.<node>.bindings.{ro,rw}.<key>`` KEYS
    :func:`agent_default_partial` delivers at launch, keyed under *node_name* (the
    slot the launch floor reads), each with the STATIC ``box_dest`` + ``options``
    straight from the plugin :class:`~kanibako.targets.base.PluginDescriptor`, but
    with a PLACEHOLDER host_src (:data:`~kanibako.settings.core_defaults.FLOOR_PLACEHOLDER_SRC`)
    in element 0.

    DETECT-FREE (the de-risk, Fork 3): it resolves the target purely by HARNESS
    (``resolve_target(harness_of(node), project_path=None)``) and reads only
    ``target.descriptor.bindings`` — the DECLARATIVE ``Binding.box_dest`` / ``.ro`` —
    so it NEVER calls ``detect()`` / ``resolve_binding_source`` and works even for an
    UNINSTALLED agent (no host binary needed). The only thing the set-time must-exist
    gate needs from the floor is ``base[1:]`` (box_dest + options), which are pure
    declarative literals (``settings_configset.repoint_host_src`` discards element 0).
    So this exposes EXACTLY the launch descriptor-floor keys to ``config set`` so a
    source-only repoint of a delivery bind (``system set
    agent.claude.bindings.ro.launcher /new``) is no longer refused / mis-routed.

    ``box_dest`` / ``options`` are byte-identical to :func:`agent_default_partial`
    (same descriptor, same fields): ``opts = "ro" if binding.ro else None`` (a non-ro
    bind emits a 2-element tuple, opts absent — the ``Bind`` convention). host_src is
    the discarded placeholder. An unknown harness / a descriptor-less (no-agent)
    target yields ``{}`` (the repoint is then refused as nowhere-in-the-cascade).
    """
    from kanibako.agent_ref import harness_of
    from kanibako.settings.core_defaults import FLOOR_PLACEHOLDER_SRC
    from kanibako.targets import resolve_target
    from kanibako.targets.base import HostSrcOrigin

    try:
        target = resolve_target(harness_of(node_name), project_path=None)
    except (KeyError, ValueError):
        return {}
    descriptor = target.descriptor
    if descriptor is None:
        return {}

    binds: "dict[str, tuple[str, ...]]" = {}
    for binding in descriptor.bindings:
        # Match `agent_default_partial`'s None-origin OMIT (S27) for the PURELY
        # DECLARATIVE case: a LITERAL binding with no literal_src resolves to None
        # at launch (detect-free — no `install` needed), so it must not be exposed
        # as a set-time key either. The install-DEPENDENT None-origin cases
        # (LAUNCHER/BINARY/INSTALL_DIR with the corresponding install.<field> unset)
        # are intentionally still emitted: they can't be evaluated detect-free, and
        # repointing such a key legitimately SUPPLIES the missing source.
        if binding.origin is HostSrcOrigin.LITERAL and binding.literal_src is None:
            continue
        sub = "ro" if binding.ro else "rw"
        key = f"agent.{node_name}.bindings.{sub}.{binding.key}"
        if binding.ro:
            binds[key] = (FLOOR_PLACEHOLDER_SRC, binding.box_dest, "ro")
        else:
            binds[key] = (FLOOR_PLACEHOLDER_SRC, binding.box_dest)
    return binds


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
    """
    from kanibako.settings.agent_config import agent_category_root_ref

    if not node_name or node_name == harness:
        return dict(table)
    harness_root = agent_category_root_ref(harness, "common") + "/"
    node_root = agent_category_root_ref(node_name, "common") + "/"
    prefix = f"agent.{harness}.common."
    out: "dict[str, tuple]" = {}
    for key, value in table.items():
        if not key.startswith(prefix):
            out[key] = value  # not this harness's common — leave untouched.
            continue
        new_key = f"agent.{node_name}.common.{key[len(prefix):]}"
        host_src = value[0]
        if isinstance(host_src, str) and host_src.startswith(harness_root):
            host_src = node_root + host_src[len(harness_root):]
        out[new_key] = (host_src, *value[1:])
    return out
