"""Agent SELECTION — which agent a box runs (spec §1A / §2g / §2h).

The ONE seam every command uses to answer *"which agent is this box's?"*.

Selection order (spec §2h, least to most specific)::

    system.agent  <  workset pref  <  box pref  <  --agent (the §1A CLI level)

The first three are settled INSIDE the settings cascade — ``system.agent`` is the
stored default and a ``pref.system.agent`` request from the workset or box file
overrides it, box beating workset by assignment order. This module resolves that
much with a NARROW pre-pass (:func:`kanibako.settings.settings_launch.resolve_selected_agent`),
applies ``--agent`` on top, and hands the winner to
:func:`kanibako.settings.config.resolve_agent`, which owns everything that is NOT a key:
name validation against the installed set, persona-ref canonicalisation, and the
installed-count rule.

⮕ **P7 (v1.8.0) — what changed and why.** The box tier used to be the flat scalar
key ``box.agent_name``, read by a private loader
(``config.load_merged_config`` → ``KanibakoConfig.box_agent_name``) that had
nothing to do with the keyspace. Spec §2b RETIRED it: it made the L4.1 anchors
``meta.box.auth.workset_path`` and ``meta.box.agent.auth.share_support`` derive
from a SETTABLE key at their own level, which §0's bootstrap rule forbids. The
replacement is the §2h REQUEST ``pref.system.agent``, and the system default is
now the spec-named ``system.agent`` (§2g) living in the ``system:`` table of the
system settings file.

**The selection LEVEL, and why the resolved answer is installed unconditionally.**
Whatever wins, :func:`select_agent` reports it and the launch installs it at
``system.agent`` as the §1A top-most level (``build_launch_snapshot(
cli_level=…)``). That is not tidiness — three separate readers now
dereference ``@system.agent`` (both re-pointed §2c anchors today, the handbook's
agent chapter tomorrow), so the snapshot MUST agree with the process that runs.
Without the install there are three ways to disagree:

* ``--agent claude`` with ``pref.system.agent: goose`` — the snapshot says goose
  while claude runs;
* the INSTALLED-COUNT rule — the commonest host has one agent installed and
  nothing stored, so the box runs claude while ``system.agent`` is ABSENT and every
  dereference of it resolves to ``""``;
* a persona ref stored as ``nav+claude`` — the running node is the canonical
  ``nav℘claude``.

Suppressing the pref when ``--agent`` is given (the alternative considered) fixes
none of these: it only changes WHICH wrong value the snapshot reports. One rule —
*the resolved selection is installed at the top* — covers all three, is a no-op
when the cascade already said it, and is exactly the mechanism P8 generalised to
every key-shadowing flag.

⮕ **P8 (v1.8.0) landed that generalisation.** The level is now built by
:func:`kanibako.settings.settings_cli_level.build_cli_level`, which owns the flag→key table
and carries this selection alongside the launch's ephemeral flag values, and it is
validated by :func:`~kanibako.settings.settings_cli_level.guard_cli_level` inside
``build_launch_snapshot``. :attr:`AgentSelection.selection_level` keeps its name
because it really is only the selection — it is this module's INPUT to that
builder. Which resolves see the flags is a separate rule stated at
``build_launch_snapshot``: the selection rides EVERY resolve, the flags ride only
the launch resolve, and no resolve whose output is written to disk sees a flag.

⚑ ``system.agent`` DOES select a cascade-input file (``meta.agent.<agent>.settings``),
which normally puts a key in §2h's LOCATOR CLOSURE. It is deliberately excluded
there and the same argument covers the CLI level (§1A's locator caveat): an AGENT
file may not carry prefs, so a re-selected agent file cannot introduce new
requests, and the selection level is an initial-value overlay that triggers no
re-read. See ``settings_prefs.LOCATOR_CLOSURE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kanibako.settings.settings_store import _MISSING

#: The key naming the agent a box runs (spec §2g). Re-exported so callers
#: spell it once.
SELECTION_KEY = "system.agent"


@dataclass(frozen=True)
class AgentSelection:
    """The resolved agent + where it came from.

    *node* is the canonical agent NODE-name (``persona℘harness``; a bare agent is
    byte-identical to its harness), or ``""`` for a **NO-AGENT plain-shell box**
    (spec §2b, D-M6).

    *source* answers *"why did system.agent resolve to that?"* without reading
    files — ``"cli"`` (``--agent``), ``"settings"`` (the stored key or a pref),
    ``"autopick"`` (the installed-count rule) or ``"suppressed"``
    (``pref.system.agent: null``).
    """

    node: str
    source: str

    @property
    def has_agent(self) -> bool:
        """Does this box run an agent AT ALL? (spec §2b D-M6.)

        ⚑⚑ **USE THIS — never ``bool(selection.node)`` at a call site, and NEVER
        pass an empty node on to ``resolve_target``.** Two vocabularies meet at that
        seam and ``""`` means OPPOSITE things in them:

        * to SELECTION, ``node == ""`` means *this box runs NO agent* (a
          ``pref.system.agent: null`` suppression);
        * to :func:`kanibako.targets.resolve_target`, an empty/absent name means
          *no name was given — AUTO-DETECT one*, which is its documented contract
          for other callers and is not a bug there.

        Handing the first to the second LAUNDERS a deliberate suppression into
        auto-detection: bifrost measured a suppressed box launching claude, with
        claude's binary, commons and CREDENTIALS delivered. Route every
        selection→target conversion through :func:`resolve_selected_target`, which
        is the ONE place these two vocabularies are translated.

        **The idiom, at every seam that needs a target:**

        ``target = resolve_target(harness_of(sel.node), path) if sel.has_agent else None``

        ``None`` is the shipped PLAIN-SHELL shape — exactly what ``kanibako shell``
        produces — and every downstream gate already keys on ``target is None`` (no
        agent binds, no agent config, no credential delivery, no ``KANIBAKO_AGENT``
        stamp, ``agent_id`` = ``"general"``).

        ⚑ Deliberately NOT ``NoAgentTarget()``: that is a RESOLVED target, so it
        would earn a ``KANIBAKO_AGENT`` stamp — and the stamp is what drives the
        stop / creds-watch writeback, which would then run a credential lifecycle
        against a box that has none. ``NoAgentTarget`` stays the right answer for
        ``resolve_target``'s own auto-detect-found-nothing case; it is not the right
        answer for "the user asked for NO agent".

        Keyed on *source* (not on the string) so the distinction survives even if a
        future source ever produced an empty node for a different reason; the
        ``node`` test is the belt-and-braces half.
        """
        return self.source != "suppressed" and bool(self.node)

    @property
    def selection_level(self) -> "dict[str, object] | None":
        """The §1A top-most level to install, or ``None`` for a NO-AGENT box.

        A no-agent box installs NOTHING: ``system.agent`` must stay absent/``None``
        so the box skips the agent binds, credentials and the layer-2 template.
        (In particular it must NOT be pinned to the ``"general"`` slot the launch
        uses as the agent-scope discriminator for a shell box — that is a template
        fallback, not an agent.)
        """
        return {SELECTION_KEY: self.node} if self.node else None


def launch_resolve_ctx(std, proj, agent_name: "str | None"):
    """Build the host-side :class:`~kanibako.settings.settings_resolve.ResolveCtx`.

    The ONE ctx builder for every snapshot resolve — ``start.py``'s
    ``_launch_snapshot_inputs`` calls this too, so the selection pre-pass and the
    launch snapshot cannot drift in what ``@config.*`` / ``$XDG_*`` / ``~`` mean.

    Resolver SPLIT (spec §1A / JC-2): the Layer-1 ``config.*`` foundation goes into
    ``ctx.config`` (so ``@config.*`` category refs route THERE, not the snapshot);
    the Layer-2 ``system.*`` path settings stay folded into the snapshot floor so
    ``@system.*`` resolves from it. The xdg map is the canonical FULL host map (a
    data-home-only partial map raised on stored ``$XDG_CACHE_HOME/...`` values),
    anchored on the resolved ``std.data_home``.

    *agent_name* is ``None`` for the SELECTION pass — no agent is known yet, so a
    ``$AGENT`` anywhere resolves to a refusal rather than to a silent ``""``. That
    refusal is recorded (never raised) because selection expands LENIENTLY; see
    :func:`kanibako.settings.settings_launch.resolve_selected_agent`.
    """
    from kanibako.settings.paths import host_xdg_map
    from kanibako.settings.settings_resolve import ResolveCtx

    workset_name = (
        proj.group.name
        if (proj.group is not None and not proj.group.is_default)
        else None
    )
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=str(Path.home()),
        xdg=host_xdg_map(std.data_home),
        config={
            "config.data": str(std.data),
            "config.agents": str(std.agents),
            "config.registry": str(std.registry),
            "config.primary_workset": str(std.primary_workset),
            "config.settings": str(std.settings),
        },
    )


def select_agent(
    *,
    std,
    proj,
    explicit_agent: "str | None" = None,
    project_path: "Path | None" = None,
) -> AgentSelection:
    """Resolve the agent for *proj* — the ONE seam (spec §1A / §2g / §2h).

    Raises the typed :class:`~kanibako.errors.AgentResolutionError` subclasses
    ``config.resolve_agent`` raises (Gate-2a / Gate-2b / not-installed), a
    :class:`~kanibako.settings.settings_resolve.SettingsError` when the selection key itself
    does not resolve, and the retired-key refusal when a settings file still
    carries ``box.agent_name`` / ``system.default_agent`` (migration M-4).

    Informational callers that must degrade rather than fail (``box info``, the
    ``config --effective`` display) keep their existing ``try/except`` — that is
    what makes the launch loud and the read verbs quiet, deliberately.
    """
    from kanibako.settings.config import resolve_agent, settings_base_path
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.paths import box_workset_settings_paths
    from kanibako.settings.settings_assemble import refuse_retired_keys
    from kanibako.settings.settings_launch import resolve_selected_agent

    box_path, workset_path = box_workset_settings_paths(proj)
    system_path = std.settings

    # RETIRED spellings: refuse by name BEFORE resolving anything (P7 / M-4).
    # Checked here rather than inside ``assemble_levels`` because a raise there
    # would also break ``config set`` — the very command the message prescribes.
    #
    # ⚑ EVERY tier the cascade reads is checked, BASE included (M-4's sweep names
    # ``/etc/kanibako/settings_base.yaml``): a site admin's stale ``box.agent_name``
    # would default DOWN into every box on the machine, which is the worst version
    # of the silent-wrong-agent failure, not an exempt one. The cure is
    # LEVEL-APPROPRIATE — a pref is not writable at base/system, so those levels are
    # told to REMOVE the key rather than to run a `box set` that cannot fix them.
    for level, path in (
        ("base", settings_base_path()),
        ("system", system_path),
        ("workset", workset_path),
        ("box", box_path),
    ):
        if path is not None and Path(path).exists():
            refuse_retired_keys(load_doc(Path(path)), level=level, path=Path(path))

    requested: object = _MISSING
    if not explicit_agent:
        # Only the cascade can suppress or supply; ``--agent`` short-circuits it,
        # so a launch that names its agent pays for no extra resolve.
        requested = resolve_selected_agent(
            ctx=launch_resolve_ctx(std, proj, None),
            system_path=system_path,
            workset_path=workset_path,
            box_path=box_path,
        )
        if requested is None:
            # PRESENT-None = an explicit ``pref.system.agent: null`` SUPPRESSION ⇒
            # the NO-AGENT plain-shell box (D-M6). ⚑ This arm is the capability the
            # retired ``box.agent_name`` could NOT express (a stored system default
            # always re-supplied an agent), so it must be kept distinct from
            # ``_MISSING`` — never collapse them with a falsiness test.
            return AgentSelection(node="", source="suppressed")

    node = resolve_agent(
        explicit_agent=explicit_agent,
        requested=None if requested is _MISSING else str(requested),
        project_path=project_path if project_path is not None else proj.project_path,
    )
    if explicit_agent:
        source = "cli"
    elif requested is _MISSING:
        source = "autopick"
    else:
        source = "settings"
    return AgentSelection(node=node, source=source)
