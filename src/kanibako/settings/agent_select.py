"""Agent SELECTION — which agent a box runs (spec §1A / §2g / §2h).

The ONE seam every command uses to answer *"which agent is this box's?"*.

Selection order (spec §2h, least to most specific)::

    system.agent  <  workset pref  <  box pref  <  --agent (the §1A CLI level)

The cascade settles the first three through a narrow pre-pass retaining pref
dependencies. One facade call classifies its winner; the selected harness's
vocabulary then judges the captured prefs. Explicit ``--agent`` bypasses this
pre-pass. Identity and captured input stay with the same selection.

⚑ Whatever wins is INSTALLED at ``system.agent`` as the §1A top-most level,
unconditionally — separate readers dereference ``{system.agent}``, so the snapshot
MUST agree with the process that runs.

🛑 **IF NOTHING NAMES AN AGENT, NOTHING LAUNCHES — THE INSTALLED-AGENT COUNT IS NOT
CONSULTED** (spec §2b). Two states, two refusals, and they
are deliberately NOT the same sentence:

* ``system.agent`` **UNSET** — setup has never chosen one ⇒
  :class:`~kanibako.errors.AgentUnsetError`, which sends the user to ``kanibako setup``.
* ``system.agent`` **present-``None``** — a settings file declined to name a default ⇒
  :class:`~kanibako.errors.AgentNoDefaultError`, which asks for a name.

A third, unrelated failure is easy to confuse with them and must stay distinct: kanibako
reads ``'None'``/``none`` as STRINGS, so those spellings request an agent by that name
and fail as *not installed*.

The reference material lives in ``llm-docs/kanibako/settings/agent_select.py.md``:
the P7 retirement of ``box.agent_name``, the three ways the snapshot and the
process diverge without the install, P8's generalization to every key-shadowing
flag, and why the selection key is excluded from §2h's locator closure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from kanibako.agent_ref import AgentIdentity
from kanibako.settings.kb_store import __MISSING__

if TYPE_CHECKING:
  from kanibako.persona_store import RegisteredPersona

#: The key naming the agent a box runs (spec §2g). Re-exported so callers spell it once.
SELECTION_KEY = "system.agent"


@dataclass(frozen=True)
class AgentSelection:
  """One resolved identity and its choosing tier; node is a view, never reparsed."""

  identity: AgentIdentity
  source: str
  registered_persona: RegisteredPersona | None = field(default=None, repr=False, compare=False)

  def __post_init__(self) -> None:
    if self.registered_persona is not None and self.registered_persona.identity != self.identity:
      raise ValueError("registered input does not own the selected identity")

  @property
  def node(self) -> str:
    """The canonical node carried by the resolved identity."""
    return self.identity.node

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

    ``target = resolve_target(sel.identity.target_name) if sel.has_agent else None``

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
    """The selected node's §1A level; a node-less selection installs nothing."""
    return {SELECTION_KEY: self.node} if self.node else None


def launch_resolve_ctx(std, proj, agent_name: "str | None"):
  """Build the host-side :class:`~kanibako.settings.settings_resolve.ResolveCtx`.

  The ONE ctx builder for every snapshot resolve —
  ``settings_launch.resolve_inputs`` calls this too, so the selection pre-pass and
  the launch snapshot cannot drift in what ``{config.*}`` / ``{$XDG_*}`` / ``~`` mean.
  The llm-doc carries the §1A resolver SPLIT and the xdg map's shape.

  ⚑ *agent_name* is ``None`` for the SELECTION pass — no agent is known yet, so a
  ``{$AGENT}`` resolves to a recorded refusal rather than to a silent ``""``.
  """
  return host_resolve_ctx(std, proj.group, agent_name)


class _WorksetGroup(Protocol):
  """What :func:`host_resolve_ctx` reads off a working set (a ``ProjectGroup`` or a ``Workset``)."""

  @property
  def name(self) -> str: ...
  @property
  def is_default(self) -> bool: ...


def host_resolve_ctx(std, group: "_WorksetGroup | None", agent_name: "str | None"):
  """The :func:`launch_resolve_ctx` builder for a resolve with NO box.

  *group* is the working set the resolve is in, or ``None`` for a resolve in none
  (the system scope).  The primary working set is spelled like ``None``:
  ``ResolveCtx.workset_name`` names a NAMED working set only.
  """
  from kanibako.settings.paths import host_config_map
  from kanibako.utils.xdg import host_xdg_map
  from kanibako.settings.settings_resolve import ResolveCtx

  workset_name = group.name if (group is not None and not group.is_default) else None
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
) -> AgentSelection:
  """Resolve the agent for *proj* — the ONE seam (spec §1A / §2g / §2h).

  Raises :class:`~kanibako.errors.AgentNoDefaultError` for a present-``None``
  selection, the typed :class:`~kanibako.errors.AgentResolutionError` subclasses
  ``config.resolve_agent_identity`` raises (including
  :class:`~kanibako.errors.AgentUnsetError` when nothing set the key), a
  :class:`~kanibako.settings.settings_resolve.SettingsError` when the selection
  key itself does not resolve, the ref-grammar ``ConfigError`` for an
  *explicit_agent* that is malformed or blank, and the retired-key refusal
  (migration M-4).
  Informational callers that must degrade rather than fail keep their own
  ``try/except`` — see the llm-doc.
  """
  from kanibako.errors import AgentNoDefaultError
  from kanibako.settings.config import resolve_agent_identity, settings_base_path
  from kanibako.settings.paths import box_workset_settings_paths, project_cure_ref
  from kanibako.settings.settings_assemble import ReadPurpose, read_settings_files
  from kanibako.settings.settings_keyspace import is_valid_agent_segment
  from kanibako.settings.settings_launch import selection_from_overlays
  from kanibako.settings.settings_prefs import (
    apply_prefs,
    collect_prefs,
    pref_overlay,
    resolve_valid_agents,
  )

  box_path, workset_path = box_workset_settings_paths(proj)
  system_path = std.settings

  # RETIRED spellings: refuse by name first (P7 / M-4), BASE
  # included — a site admin's stale key defaults DOWN into every box. Judged on the
  # reader's view: a dropped table gets no cure.
  read_settings_files(
    (
      ("base", settings_base_path()),
      ("system", system_path),
      ("workset", workset_path),
      ("box", box_path),
    ),
    purpose=ReadPurpose.SELECT,
    # The cure's subject is the reference that reaches the box from ANY cwd.
    box_ref=project_cure_ref(proj) if proj.name else None,
  )

  requested: object = __MISSING__
  requests = None
  # Given means ``is not None``, never truthiness: a blank explicit ref must
  # reach the facade's grammar refusal, not the cascade.
  if explicit_agent is None:
    # Only the cascade can decline or supply; ``--agent`` short-circuits it.
    requests = collect_prefs(workset_path, box_path)
    valid_agents = resolve_valid_agents(req.target for req in requests)
    known_prefix = []
    for req in requests:
      parts = req.target.split(".", 2)
      if (
        len(parts) > 1
        and parts[0] == "agent"
        and not is_valid_agent_segment(parts[1], valid_agents)
      ):
        break
      known_prefix.append(req)
    if known_prefix:
      apply_prefs(known_prefix, valid_agents=valid_agents)
    requested = selection_from_overlays(
      ctx=launch_resolve_ctx(std, proj, None),
      system_path=system_path,
      workset_path=workset_path,
      box_path=box_path,
      workset_overlay=pref_overlay(req for req in requests if req.level == "workset"),
      box_overlay=pref_overlay(req for req in requests if req.level == "box"),
    )
    if requested is None or requested is __MISSING__:
      apply_prefs(requests)
    if requested is None:
      # PRESENT-None = NO DEFAULT IS SET (spec §2b): an agent must be named
      # explicitly, or the launch REFUSES saying so. ⚑ Keep it distinct from
      # ``__MISSING__`` — never collapse them with a falsiness test: the two
      # states print DIFFERENT refusals, which is the whole ruling.
      # 🛑 It used to return the NO-AGENT plain-shell box; under
      # [Jei:2026-09-19] `<None>` no longer reaches that box — the `shell`
      # pseudo-agent does, BY NAME.
      raise AgentNoDefaultError(
        "No default agent is set: system.agent is null (a blank value and "
        "'~' spell this too),\n"
        "so an agent must be named explicitly.\n"
        "Name one with '--agent <name>', or set this box's agent with:\n"
        "  kanibako box set pref.system.agent=<name>\n"
        "'kanibako shell' reaches the box's container without an agent."
      )

  registered: list[RegisteredPersona] = []
  identity = resolve_agent_identity(
    explicit_agent=explicit_agent,
    requested=None if requested is __MISSING__ else str(requested),
    registered_persona_out=registered,
  )
  if requests is not None:
    apply_prefs(
      requests,
      valid_agents=valid_agents.with_harnesses({identity.node: identity.target_name}),
    )
  # ⚑ TWO SOURCES, not four: ``resolve_agent_identity`` REFUSES an unset key rather than
  # picking for the user, so a returning call always has a name somebody wrote.
  source = "cli" if explicit_agent is not None else "settings"
  return AgentSelection(
    identity=identity, source=source, registered_persona=registered[0] if registered else None
  )
