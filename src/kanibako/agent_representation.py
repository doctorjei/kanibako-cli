"""Agent descriptor → KeyStore representation (block 7a — PURE, ALONGSIDE).

This module represents a plugin's per-agent DESCRIPTOR delivery binds as
agent-level category-default entries in the resolved-keyspace
:class:`~kanibako.settings_store.KeyStore`, so agent binary/launcher/share
delivery flows through the ONE category keyspace (the single-route invariant),
NOT a parallel descriptor mount route.

It is item-0's hard half: each :class:`~kanibako.targets.base.Binding` in a
:class:`~kanibako.targets.base.PluginDescriptor` becomes a
``agent.bindings.{ro,rw}.<key> = Bind(host_src, box_dest, opts)`` leaf under the
BARE ``agent`` scope token (S27) — mirroring
:func:`~kanibako.targets.assembly.resolve_binding_source`'s origin→host_src
resolution.

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
  (:func:`kanibako.agent_defaults._build_binding`) has ALREADY expanded the
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
* **key path** = the BARE ``agent`` token (S27): ``agent.bindings.ro.<key>`` or
  ``agent.bindings.rw.<key>`` per ``binding.ro``. The agent NAME selects WHICH
  descriptor (claude's descriptor for the claude agent) — it is NOT in the key
  path, so this partial merges BY NAME with 2a's ``agent.default`` /
  ``agent.<active>`` levels (S8).

None-origin rule (S27, recorded here)
-------------------------------------
When ``resolve_binding_source`` returns ``None`` (an unresolvable origin — e.g.
a ``LITERAL`` binding with no ``literal_src``, or a detection field the install
left unset), the entry is **OMITTED**. This mirrors the AGENT best-effort skip in
:func:`~kanibako.targets.assembly.descriptor_mounts` and keeps the tier-2 typed
accessor honest: ``bindings`` exposes ``Mapping[str, Bind]`` (NOT ``Bind | None``)
ONLY because build omits absent/None binds (design §5/§6e) — emitting a
``None``-host bind would be a lie a consumer crashes on.

Authority: ``~/vault/rw/keystore-design.md`` §2 (binds are ``Bind``) / §4 B1 (the
agent tier; bare ``agent`` token) / §6a (raw refs); spec
``settings-keyspace-1.6.0-target.md`` §2a (binding REPRESENTATION) / §2d
(``agent.<agent>.bindings.*``). SEAMS S1/S2/S3/S7/S8/S9 + S26/S27.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kanibako.settings_store import Bind, KeyStore
from kanibako.targets.assembly import resolve_binding_source

if TYPE_CHECKING:
    from kanibako.targets.base import AgentInstall, PluginDescriptor


def agent_default_partial(
    descriptor: PluginDescriptor,
    install: AgentInstall,
) -> KeyStore:
    """Represent a descriptor's delivery bindings as an agent-level KeyStore partial.

    Returns a :class:`~kanibako.settings_store.KeyStore` partial rooted at the BARE
    ``agent`` scope token (S27), holding each resolvable
    :class:`~kanibako.targets.base.Binding` as a
    ``agent.bindings.{ro,rw}.<key> = Bind(host_src, box_dest, opts)`` leaf —
    mirroring :func:`~kanibako.targets.assembly.resolve_binding_source` with NO
    override and NO existence check (S26). See the module docstring for the full
    rules and the None-origin OMIT contract.

    The partial nests ``agent.bindings`` with ``ro`` / ``rw`` sub-tables; a
    sub-table is present only if at least one binding lands in it, ``bindings`` is
    present only if at least one binding resolved, and the result is ALWAYS a
    ``KeyStore`` rooted at ``agent`` (empty when no binding resolves) so it merges
    uniformly with the 2a agent levels (S8). Descriptor order is preserved.

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

    agent = KeyStore()
    if dict.__len__(bindings):
        agent["bindings"] = bindings

    partial = KeyStore()
    partial["agent"] = agent
    return partial
