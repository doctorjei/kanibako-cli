"""Agent-agnostic, declarative assembly of an agent launch from a PluginDescriptor.

Given a plugin's :class:`~kanibako.targets.base.PluginDescriptor` plus the resolved
host :class:`~kanibako.targets.base.AgentInstall` and a handful of per-launch knobs,
this assembles the agent argv, container env, and host->box mounts.  Everything here
is pure apart from ``Path.exists()`` checks in :func:`descriptor_mounts` /
:func:`resolve_binding_source`, and agent-agnostic: no plugin names appear, and
divergent LOGIC stays behind the plugin ``Target`` hook methods.

LIVE in ``commands/start.py`` for every descriptor-bearing target; the only
descriptor-less one is ``NoAgentTarget`` (the ``kanibako shell`` fallback), which
launches a plain shell with no agent argv and no delivery binds.

Design, history and the full case tables: ``llm-docs/kanibako/targets/assembly.py.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Collection, Sequence

from kanibako.errors import ConfigError
from kanibako.log import get_logger
from kanibako.settings.settings_keyspace import ACCESS_TIERS, access_default
from kanibako.targets.base import (
    BindScope,
    Channel,
    HostSrcOrigin,
    Mount,
)

if TYPE_CHECKING:
    from kanibako.targets.base import (
        AccessTierRow,
        AgentInstall,
        Binding,
        PluginDescriptor,
    )

_logger = get_logger("targets.assembly")


class BindingSourceError(Exception):
    """An AGENT_CRITICAL binding's resolved host source is missing/unresolvable."""


def entrypoint(descriptor: PluginDescriptor) -> str:
    """Return the container entrypoint: the first element of ``descriptor.command``.

    ``command`` is the full box argv prefix; the rest (``command[1:]``) is agent args,
    produced by :func:`assemble_argv`.
    """
    return descriptor.command[0]


def resolve_mode(
    *,
    resume_mode: bool,
    new_session: bool,
    is_new_project: bool,
    extra_args: list[str],
    available_modes: Collection[str],
) -> str:
    """Select the interactive launch mode key from *available_modes*.

    *available_modes* is the set of mode keys the agent's launch grammar declares,
    read off the snapshot's ``meta.agent.<a>.mode`` table (B5, spec §2d).  Order:
    ``-R`` resume picker if declared; else ``continue`` unless a new session was
    forced (*new_session* / *is_new_project* / ``--resume``/``-r`` in *extra_args*);
    else ``start``.  An agent with no picker falls through to continue-last.
    """
    if resume_mode and "resume" in available_modes:
        return "resume"

    skip_continue = (
        new_session
        or is_new_project
        or any(a in ("--resume", "-r") for a in extra_args)
    )
    if not skip_continue and "continue" in available_modes:
        return "continue"

    return "start"


def resolve_access_tier(access: "str | None") -> str:
    """Validate a CASCADE-resolved ``access`` value into a permission TIER.

    ``None`` (unset) ⇒ :func:`~kanibako.settings.settings_keyspace.access_default`,
    DECLARED in ``core-defaults.yaml`` and not spelled here; anything else must be a
    member of :data:`~kanibako.settings.settings_keyspace.ACCESS_TIERS` EXACTLY.

    ⚑ An unknown value RAISES, naming the key and the legal values.  It is NEVER
    coerced and NEVER falls back to the default: on a permission axis a typo must
    not decide whether the agent prompts.  This is the SECOND FENCE behind the
    set-time guard, for a value that reached the file some other way (a hand edit).

    ⚑ The EMPTY STRING is not unset — it is an INVALID VALUE and refuses like any
    other; only ``None`` takes the default.  Treating it as unset would make this
    function's permissive arm reachable by a route the validators never approved.
    """
    if access is None:
        return access_default()
    if access not in ACCESS_TIERS:
        raise ConfigError(
            f"'access' must be one of {' | '.join(ACCESS_TIERS)} (spec §2d); "
            f"this box resolved {access!r}. Refusing rather than running: an "
            f"unrecognised permission tier is never treated as "
            f"'{access_default()}'."
        )
    return access


def effective_access(
    *,
    secure: bool,
    autonomous: bool,
    access: "str | None" = None,
) -> str:
    """Return the permission TIER this launch's ARGV/ENV should run at.

    *secure* (``-S``) ⇒ ``restricted``, winning over everything; *autonomous*
    (``-A``) ⇒ ``full``; else the cascade-resolved *access* key via
    :func:`resolve_access_tier` (spec §2d + §1A).

    ⚑ The flags are EPHEMERAL and apply to the launch argv/env ONLY.  The PROJECTED
    surfaces resolve the tier from the CASCADE alone (spec §1A's projected-surface
    exception), so both values exist at a launch and are NOT the same read.
    """
    if secure:
        return "restricted"
    if autonomous:
        return "full"
    return resolve_access_tier(access)


def access_row(
    descriptor: PluginDescriptor, tier: str, *, agent: str = "",
) -> "AccessTierRow | None":
    """The descriptor's realization of *tier*, or ``None`` when it declares none.

    RAISES when the descriptor HAS an ``access_realization`` but cannot render
    *tier* — the un-rendered-tier rule: name the tiers this agent CAN render rather
    than substitute a neighbouring one, which would be either silently permissive
    or silently stricter.  Both are lies about what the user asked for.

    ⚑ ZERO rows is diagnosed SEPARATELY as PLUGIN VERSION SKEW, not as a capability
    limit: the retired PRE-TIER BODY (``flag``/``secure_flag`` with no ``tiers:``)
    loads to an empty :class:`AccessRealization`, and blaming the agent would send
    the user looking for a limit that does not exist.

    ⚑ A descriptor spelling the OLD KEY (``safe_bypass:``) never reaches here — it
    is refused at descriptor load by
    :func:`~kanibako.settings.agent_defaults.load_descriptor`.  Only the old BODY
    under the NEW key survives this far, which is what the message below describes.
    """
    ar = descriptor.access_realization
    if ar is None:
        return None
    row = ar.row(tier)
    if row is None:
        who = f"'{agent}'" if agent else "this agent"
        rendered = ar.rendered_tiers()
        if not rendered:
            raise ConfigError(
                f"access tier '{tier}' cannot be delivered for {who}: its "
                f"plugin declares a permission surface with NO tier rows at "
                f"all. That is PLUGIN VERSION SKEW, not a limit of the agent — "
                f"a kanibako-agent-* package published before the 'access' "
                f"tiers declares the retired PRE-TIER body (a realization block "
                f"with no 'tiers:', formerly spelled 'safe_bypass:'), which "
                f"carries no tiers. Upgrade the kanibako-agent-* packages to "
                f"match the base (they are released together)."
            )
        raise ConfigError(
            f"access tier '{tier}' cannot be rendered by {who}: its harness has "
            f"no realization for it. Legal tiers for this agent: "
            f"{' | '.join(rendered)}. Refusing rather than running at a "
            f"DIFFERENT tier than you asked for (spec §2d)."
        )
    return row


# ⚑ ``resolve_new_session`` was DELETED in P8 (v1.8.0): spec §1A makes the COMMAND LINE
# its own LEVEL, so the ``-N``/``-C``/``-R`` fold over ``continue_mode`` now happens ONCE,
# declaratively, in :func:`kanibako.settings.settings_cli_level.build_cli_level`, and the
# launch reads ``effective_new_session = not continue_default``.
#
# Do NOT reintroduce it. Two places folding the same flags from two different inputs is
# the "two forms that mean the same thing" failure, and the second one would be the one
# nobody tested. ``resolve_mode`` still takes the raw ``resume_mode``, because ``-R``
# selects a launch GRAMMAR, not a key.


def assemble_argv(
    descriptor: PluginDescriptor,
    *,
    mode_fragment: "Sequence[str] | None",
    access: str,
    setting_values: dict[str, str],
    op_fragment: "Sequence[str] | None" = None,
    extra_args: list[str],
    agent: str = "",
) -> list[str]:
    """Assemble the agent argv that follows the entrypoint binary.

    The returned list starts at ``descriptor.command[1:]`` — it EXCLUDES the
    entrypoint program (:func:`entrypoint`), which ``start.py`` sets separately.

    ⚑ B5 (spec §2d): the launch-grammar fragments are PARAMETERS, not descriptor
    reads — the caller reads them off the ONE launch snapshot.  This function must
    NOT read ``descriptor.mode`` / ``descriptor.operations``: the descriptor feeds
    the KEYSPACE only, and a second, descriptor-direct source for the same argv
    fragment is the drift shape B5 exists to kill.

    Order after ``command[1:]``: *op_fragment* if set, which SUPPRESSES the
    interactive mode (the two are MUTUALLY EXCLUSIVE at this slot, spec §2d), else
    *mode_fragment*; then the FLAG-channel ``access_realization`` row for the
    *access* tier — an EMPTY row emits nothing, a MISSING row RAISES
    (:func:`access_row`), so no tier falls through to another tier's emission; then
    each FLAG-channel :class:`SettingArg` with a truthy value; then *extra_args*.

    ⚑ The ``and s.flag`` guard (twin of :func:`assemble_env`'s ``and s.env_var``):
    without it a flagless FLAG entry extends by ``()`` and the value lands as a BARE
    POSITIONAL — for claude, the initial PROMPT.  The DECLARATION is refused one
    level up at descriptor load; this is containment for a :class:`PluginDescriptor`
    hand-built in code, which never passes through that loader.

    *access* is the tier this LAUNCH runs at (see :func:`effective_access`); *agent*
    names the agent in the refusal message.  ENV-channel access rows / settings are
    NOT argv and are emitted by :func:`assemble_env` instead.
    """
    argv: list[str] = list(descriptor.command[1:])

    if op_fragment is not None:
        argv.extend(op_fragment)
    elif mode_fragment is not None:
        argv.extend(mode_fragment)

    ar = descriptor.access_realization
    if ar is not None and ar.channel is Channel.FLAG:
        row = access_row(descriptor, access, agent=agent)
        if row is not None:
            argv.extend(row.flag)

    for s in descriptor.settings:
        if s.channel is Channel.FLAG and s.flag:
            value = setting_values.get(s.setting_key)
            if value:
                argv.extend(s.flag)
                argv.append(value)

    argv.extend(extra_args)
    return argv


def assemble_env(
    descriptor: PluginDescriptor,
    *,
    access: str,
    setting_values: dict[str, str],
    agent: str = "",
) -> dict[str, str]:
    """Assemble the container environment overlay from the descriptor.

    ⚑ REALIZATIONS ONLY.  A plugin's STATIC variables are settings keys
    (``agent.<agent>.env.<VAR>``, ``Target.default_envs``) and never come through
    here; this is the per-launch translation of RESOLVED values onto the ENV
    channel — the ENV-channel ``access_realization`` row for the *access* tier, then
    each ENV-channel :class:`SettingArg` with a truthy value.  An EMPTY row emits
    nothing; a MISSING row RAISES (:func:`access_row`).

    ⚑ Where an agent's UNSET env default is itself permissive (goose's
    ``GOOSE_MODE`` defaults to ``auto``), every renderable tier must carry a value:
    emitting nothing there would BE the bypass.

    ⚑⚑ THE RETURN VALUE IS NOT APPLIED TO ANYTHING (MBR-1 P4c-2) — the launch
    installs it as ``agent.<node>.env.<VAR>`` KEYS before the collapse, so these
    variables are arbitrated, overridable and refusable like every other one.  This
    function stayed PURE through that move and must stay pure.  See
    ``commands/start._install_realized_env``.

    FLAG-channel access rows / settings are argv (:func:`assemble_argv`).
    """
    env: dict[str, str] = {}

    ar = descriptor.access_realization
    if ar is not None and ar.channel is Channel.ENV and ar.env_var:
        row = access_row(descriptor, access, agent=agent)
        if row is not None and row.env_value:
            env[ar.env_var] = row.env_value

    for s in descriptor.settings:
        if s.channel is Channel.ENV and s.env_var:
            value = setting_values.get(s.setting_key)
            if value:
                env[s.env_var] = value

    return env


def env_realization_drivers(descriptor: PluginDescriptor) -> dict[str, str]:
    """Every variable this descriptor can REALIZE -> the setting key that DRIVES it.

    The DECLARATION map, unconditional where :func:`assemble_env` is conditional:
    an emitted ``{var: value}`` map cannot carry provenance, and a caller that has
    to say WHY a variable is set needs the key that produces it, not the value.

    ⚑⚑ IT IS THE TWIN WALK OF :func:`assemble_env` AND MUST STAY BESIDE IT — the
    same two declaration sites in the same order.  A variable one of them knows
    about and the other does not either reaches the box unexplained or is explained
    but never set.

    A row's setting key may be EMPTY (a realization driven only by ``-S``/``-A``).
    It is carried through rather than dropped: the variable IS realized, and there
    is no key to point the user at.
    """
    drivers: dict[str, str] = {}

    ar = descriptor.access_realization
    if ar is not None and ar.channel is Channel.ENV and ar.env_var:
        drivers[ar.env_var] = ar.setting_key

    for s in descriptor.settings:
        if s.channel is Channel.ENV and s.env_var:
            drivers[s.env_var] = s.setting_key

    return drivers


def resolve_binding_source(
    binding: Binding,
    install: AgentInstall,
    *,
    override: str = "",
) -> Path | None:
    """Resolve a binding's host source from ``binding.origin`` (no existence check).

    A non-empty *override* wins and is returned as ``Path(override)``; R-9 retired
    its CLI set route, so the parameter is used by tests only.  Returns ``None``
    when the source cannot be resolved.
    """
    if override:
        return Path(override)

    origin = binding.origin
    if origin is HostSrcOrigin.LAUNCHER:
        return install.launcher or install.binary
    if origin is HostSrcOrigin.INSTALL_DIR:
        return install.install_dir
    if origin is HostSrcOrigin.BINARY:
        return install.binary
    if origin is HostSrcOrigin.LITERAL:
        return binding.literal_src

    return None


def declares_box_dest(
    descriptor: "PluginDescriptor | None", box_dest: str,
) -> bool:
    """True when *descriptor* declares a delivery binding at *box_dest*.

    ⚑ Asked of the box_dest and NOT of a key NAME on purpose: the thing that
    collides in a box is the DESTINATION (spec §0's identical-dest table), and a
    plugin names its keys freely — a key-name test would pass a third-party
    plugin's identically-destined binding straight into that error.

    Declaration-level, not resolution-level: no filesystem, no source resolution,
    matching what the launch snapshot carries
    (:func:`~kanibako.settings.agent_representation.agent_default_partial`).

    *descriptor* may be ``None`` (the no-agent target has none), which answers
    False.  *box_dest* must be the ABSOLUTE guest path; callers expand ``~`` first.

    Sole caller today: :func:`kanibako.settings.core_defaults.kickoff_default_categories`.
    """
    if descriptor is None:
        return False
    return any(b.box_dest == box_dest for b in descriptor.bindings)


def descriptor_mounts(
    descriptor: PluginDescriptor,
    install: AgentInstall,
    *,
    overrides: dict[str, str] | None = None,
) -> list[Mount]:
    """Build the ordered host->box mounts for a descriptor's bindings.

    Descriptor order is preserved; each source resolves via
    :func:`resolve_binding_source`, with any per-key *overrides* value winning.

    * ``AGENT_CRITICAL`` (binary/launcher/share): the source MUST resolve and
      exist, else :class:`BindingSourceError` — the clean safe-fail that replaces
      a crun crash on a dangling bind source.
    * ``AGENT`` (best-effort): unresolvable or missing is skipped with a debug log.
      No shipped plugin declares one today, but the branch is kept for the general
      binding contract.

    Mount options are ``"ro"`` when ``binding.ro`` is set, else ``""`` (rw).

    ⚑ Clearing any pre-existing dest symlink in the box is the CALLER's job
    (``_precreate_mount_stubs``), NOT this function's.
    """
    override_map = overrides or {}
    mounts: list[Mount] = []

    for binding in descriptor.bindings:
        src = resolve_binding_source(
            binding,
            install,
            override=override_map.get(binding.key, ""),
        )
        options = "ro" if binding.ro else ""

        if binding.scope is BindScope.AGENT_CRITICAL:
            if src is None or not src.exists():
                raise BindingSourceError(
                    f"binding {binding.key!r} source missing: {src}"
                )
            mounts.append(Mount(src, binding.box_dest, options))
        else:  # BindScope.AGENT — best-effort
            if src is None or not src.exists():
                _logger.debug(
                    "skipping agent binding %r: source missing (%s)",
                    binding.key,
                    src,
                )
                continue
            mounts.append(Mount(src, binding.box_dest, options))

    return mounts
