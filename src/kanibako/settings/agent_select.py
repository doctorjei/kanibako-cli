"""Agent SELECTION — which agent a box runs (spec §1A / §2g / §2h).

The ONE seam every command uses to answer *"which agent is this box's?"*.

Selection order (spec §2h, least to most specific)::

    system.agent  <  workset pref  <  box pref  <  --agent (the §1A CLI level)

The first three are settled INSIDE the settings cascade; this module resolves that
much with a NARROW pre-pass
(:func:`kanibako.settings.settings_launch.resolve_selected_agent`), applies
``--agent`` on top, and hands the winner to
:func:`kanibako.settings.config.resolve_agent`, which owns everything that is NOT
a key.

⚑ Whatever wins is INSTALLED at ``system.agent`` as the §1A top-most level,
unconditionally — separate readers dereference ``@system.agent``, so the snapshot
MUST agree with the process that runs.

The reference material lives in ``llm-docs/kanibako/settings/agent_select.py.md``:
the P7 retirement of ``box.agent_name``, the three ways the snapshot and the
process diverge without the install, P8's generalisation to every key-shadowing
flag, and why the selection key is excluded from §2h's locator closure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kanibako.settings.kb_store import __MISSING__

#: The key naming the agent a box runs (spec §2g). Re-exported so callers spell it once.
SELECTION_KEY = "system.agent"


@dataclass(frozen=True)
class AgentSelection:
    """The resolved agent node, plus the *source* that chose it.

    *node* is the canonical agent NODE-name, or ``""`` for a NO-AGENT plain-shell
    box (spec §2b, D-M6); *source* is ``"cli"``, ``"settings"``, ``"autopick"`` or
    ``"suppressed"``. Both vocabularies are spelled out in the llm-doc.
    """

    node: str
    source: str

    @property
    def has_agent(self) -> bool:
        """Does this box run an agent AT ALL? (spec §2b D-M6.)

        ⚑⚑ **USE THIS — never ``bool(selection.node)`` at a call site, and NEVER
        pass an empty node on to ``resolve_target``.** ``""`` means OPPOSITE things
        on the two sides of that seam: *this box runs NO agent* here, *no name was
        given, AUTO-DETECT one* there. Handing the first to the second LAUNDERS a
        deliberate suppression into a live agent with credentials delivered — a
        measured incident, not a theory. ⚑ There is no helper; the guard IS the
        translation, spelled at each seam:

        ``target = resolve_target(harness_of(sel.node), path) if sel.has_agent else None``

        ⚑ ``None``, deliberately NOT ``NoAgentTarget()`` — the llm-doc says why,
        and carries the downstream gates that key on ``target is None``.
        """
        return self.source != "suppressed" and bool(self.node)

    @property
    def selection_level(self) -> "dict[str, object] | None":
        """The §1A top-most level to install, or ``None`` for a NO-AGENT box.

        ⚑ A no-agent box installs NOTHING, and in particular must NOT be pinned to
        the ``"general"`` slot — that is a template fallback, not an agent.
        """
        return {SELECTION_KEY: self.node} if self.node else None


def launch_resolve_ctx(std, proj, agent_name: "str | None"):
    """Build the host-side :class:`~kanibako.settings.settings_resolve.ResolveCtx`.

    The ONE ctx builder for every snapshot resolve — ``start.py``'s
    ``_launch_snapshot_inputs`` calls this too, so the selection pre-pass and the
    launch snapshot cannot drift in what ``@config.*`` / ``$XDG_*`` / ``~`` mean.
    The llm-doc carries the §1A resolver SPLIT and the xdg map's shape.

    ⚑ *agent_name* is ``None`` for the SELECTION pass — no agent is known yet, so a
    ``$AGENT`` resolves to a recorded refusal rather than to a silent ``""``.
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
    ``config.resolve_agent`` raises, a
    :class:`~kanibako.settings.settings_resolve.SettingsError` when the selection
    key itself does not resolve, and the retired-key refusal (migration M-4).
    Informational callers that must degrade rather than fail keep their own
    ``try/except`` — see the llm-doc.
    """
    from kanibako.settings.config import resolve_agent, settings_base_path
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.paths import box_workset_settings_paths
    from kanibako.settings.settings_assemble import cascade_view, refuse_retired_keys
    from kanibako.settings.settings_launch import resolve_selected_agent

    box_path, workset_path = box_workset_settings_paths(proj)
    system_path = std.settings

    # RETIRED spellings: refuse by name BEFORE resolving anything (P7 / M-4).
    # ⚑ EVERY tier is checked, BASE included — a site admin's stale key defaults
    # DOWN into every box on the machine. The cure is LEVEL-APPROPRIATE, which is
    # why ``proj.name`` is threaded only for ``level == "box"``. Reasoning, and why
    # this is not inside ``assemble_levels``: llm-doc.
    # ⚑⚑ IT JUDGES WHAT THE CASCADE SEES, NOT WHAT THE FILE SAYS (``cascade_view``),
    # the same rule the resolve seam's retirement scan follows. ``agent.default.
    # default_agent`` is the one retired spelling this reaches under a CONTAINING
    # scope's table, so in a box or workset file directional enforcement had already
    # dropped it: the cure sent a user to rewrite a line that was doing nothing, and
    # the two ``box.*`` spellings — which are NOT dropped at those tiers — still
    # refuse exactly as before.
    for level, path in (
        ("base", settings_base_path()),
        ("system", system_path),
        ("workset", workset_path),
        ("box", box_path),
    ):
        if path is not None and Path(path).exists():
            refuse_retired_keys(
                cascade_view(load_doc(Path(path)), level=level),
                level=level, path=Path(path),
                box_name=proj.name if level == "box" else None,
            )

    requested: object = __MISSING__
    if not explicit_agent:
        # Only the cascade can suppress or supply; ``--agent`` short-circuits it.
        requested = resolve_selected_agent(
            ctx=launch_resolve_ctx(std, proj, None),
            system_path=system_path,
            workset_path=workset_path,
            box_path=box_path,
        )
        if requested is None:
            # PRESENT-None = an explicit ``pref.system.agent: null`` SUPPRESSION ⇒
            # the NO-AGENT plain-shell box (D-M6). ⚑ Keep it distinct from
            # ``__MISSING__`` — never collapse them with a falsiness test.
            return AgentSelection(node="", source="suppressed")

    node = resolve_agent(
        explicit_agent=explicit_agent,
        requested=None if requested is __MISSING__ else str(requested),
        project_path=project_path if project_path is not None else proj.project_path,
    )
    if explicit_agent:
        source = "cli"
    elif requested is __MISSING__:
        source = "autopick"
    else:
        source = "settings"
    return AgentSelection(node=node, source=source)
