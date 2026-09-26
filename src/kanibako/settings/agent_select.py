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

🛑 **IF NOTHING NAMES AN AGENT, NOTHING LAUNCHES — THE INSTALLED-AGENT COUNT IS NOT
CONSULTED** (his ruling, 2026-09-19; spec §2b). Two states, two refusals, and they
are deliberately NOT the same sentence:

* ``system.agent`` **UNSET** — setup has never chosen one ⇒
  :class:`~kanibako.errors.AgentUnsetError`, which sends the user to ``kanibako setup``.
* ``system.agent`` **present-``None``** — a settings file declined to name a default ⇒
  :class:`~kanibako.errors.AgentNoDefaultError`, which asks for a name.

A third, unrelated failure is easy to confuse with them and must stay distinct: YAML
reads ``None``/``none`` as STRINGS, so those spellings request an agent by that name
and fail as *not installed*.

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
    box (spec §2b, D-M6); *source* is ``"cli"`` or ``"settings"``. Both
    vocabularies are spelled out in the llm-doc.

    🛑 **TWO SOURCES ARE RETIRED AND NEITHER IS PRODUCED ANY MORE.** ``"autopick"``
    was the installed-agent count rule, deleted 2026-09-19 — nothing auto-selects,
    not even with exactly one agent installed. ``"suppressed"`` was
    ``pref.system.agent: null`` yielding the plain-shell box; that state is now a
    REFUSAL (:class:`~kanibako.errors.AgentNoDefaultError`).

    ⚑ **The ``has_agent`` / ``target is None`` guards below are KEPT, and NOT
    because ``shell`` is coming.** Keyspec §2b makes the plain-shell box an
    effective ``@system.agent`` of ``shell`` — a NAMED node (``meta.agent.shell.name
    = "shell"``, §2d) — so a D2 selection is ``node="shell"`` and ``has_agent`` is
    TRUE for it. The guards stay because ``""`` still means opposite things on the
    two sides of the target seam, holding that shape costs one conjunct, and the
    incident that proved it is on record (bifrost E-NULL, 2026-07-31). **Do not
    read them as evidence that a null selection still launches anything.**
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

        ⚑ ``None``, deliberately NOT ``ShellTarget()`` — the llm-doc says why,
        and carries the downstream gates that key on ``target is None``.

        ⚑ **The NODE decides, and nothing else (P4).** This used to read
        ``self.source != "suppressed" and bool(self.node)``; ``"suppressed"`` is a
        retired source no production path emits, so the conjunct was dead weight in
        front of the only test that ever mattered. ``bool(self.node)`` is the same
        predicate for every value the class can hold.
        """
        return bool(self.node)

    @property
    def selection_level(self) -> "dict[str, object] | None":
        """The §1A top-most level to install, or ``None`` for a NO-AGENT box.

        ⚑ A no-agent box installs NOTHING, and in particular must NOT be pinned to
        the ``"shell"`` slot — that would name a default nobody set.
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
    from kanibako.settings.paths import host_config_map, host_xdg_map
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
        # ⚑⚑ BOTH MAPS ARE DERIVED BUILDERS, NEITHER IS A LITERAL HERE. The
        # ``config=`` half used to be five string literals, and ``CONFIG_PATH_DEFAULTS``
        # has declared SIX since ``config.journal`` landed the day after they were
        # written: a binding sourced at ``@config.journal`` was accepted by ``config
        # set`` and DROPPED at launch, silently, rc 0. ``paths.host_config_map`` derives
        # the tier from the declared table, so the two cannot part again.
        xdg=host_xdg_map(std.data_home),
        config=host_config_map(std),
    )


def select_agent(
    *,
    std,
    proj,
    explicit_agent: "str | None" = None,
    project_path: "Path | None" = None,
) -> AgentSelection:
    """Resolve the agent for *proj* — the ONE seam (spec §1A / §2g / §2h).

    Raises :class:`~kanibako.errors.AgentNoDefaultError` for a present-``None``
    selection, the typed :class:`~kanibako.errors.AgentResolutionError` subclasses
    ``config.resolve_agent`` raises (including
    :class:`~kanibako.errors.AgentUnsetError` when nothing set the key), a
    :class:`~kanibako.settings.settings_resolve.SettingsError` when the selection
    key itself does not resolve, the ref-grammar ``ConfigError`` for an
    *explicit_agent* that is malformed or blank, and the retired-key refusal
    (migration M-4).
    Informational callers that must degrade rather than fail keep their own
    ``try/except`` — see the llm-doc.
    """
    from kanibako.errors import AgentNoDefaultError
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
                cascade_view(load_doc(Path(path)), level=level, path=Path(path)),
                level=level, path=Path(path),
                box_name=proj.name if level == "box" else None,
            )

    requested: object = __MISSING__
    # ⚑⚑ "GIVEN" IS ``is not None``, NOT TRUTHINESS.  This gate can answer the
    # whole question by itself (the no-default REFUSAL below never reaches
    # ``resolve_agent``), so a truthy test let a GIVEN-but-blank ref be answered
    # by the cascade — ``--agent ""`` came back as a no-agent box, or as whatever
    # the files said, with nothing printed.  A blank ref is a value: it goes
    # through to ``resolve_agent``, which refuses it by the ref grammar's own
    # message.  ``None`` keeps its one meaning — no ref was given at all.
    if explicit_agent is None:
        # Only the cascade can decline or supply; ``--agent`` short-circuits it.
        requested = resolve_selected_agent(
            ctx=launch_resolve_ctx(std, proj, None),
            system_path=system_path,
            workset_path=workset_path,
            box_path=box_path,
        )
        if requested is None:
            # PRESENT-None = NO DEFAULT IS SET (spec §2b): an agent must be named
            # explicitly, or the launch REFUSES saying so. ⚑ Keep it distinct from
            # ``__MISSING__`` — never collapse them with a falsiness test: the two
            # states print DIFFERENT refusals, which is the whole ruling.
            # 🛑 It used to return the NO-AGENT plain-shell box; under the
            # 2026-09-19 ruling `<None>` no longer reaches that box — the `shell`
            # pseudo-agent does, BY NAME.
            raise AgentNoDefaultError(
                "No default agent is set: system.agent is null (a blank value and "
                "'~' spell this too),\n"
                "so an agent must be named explicitly.\n"
                "Name one with '--agent <name>', or set this box's agent with:\n"
                "  kanibako box set pref.system.agent=<name>\n"
                "'kanibako shell' reaches the box's container without an agent."
            )

    node = resolve_agent(
        explicit_agent=explicit_agent,
        requested=None if requested is __MISSING__ else str(requested),
        project_path=project_path if project_path is not None else proj.project_path,
    )
    # ⚑ TWO SOURCES, not four: ``resolve_agent`` REFUSES an unset key rather than
    # picking for the user, so a returning call always has a name somebody wrote.
    source = "cli" if explicit_agent is not None else "settings"
    return AgentSelection(node=node, source=source)
