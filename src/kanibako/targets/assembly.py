"""Agent-agnostic, declarative assembly of an agent launch from a PluginDescriptor.

This module is the data-driven core of the descriptor-only plugin system: given
a plugin's :class:`~kanibako.targets.base.PluginDescriptor` plus the resolved
host :class:`~kanibako.targets.base.AgentInstall` and a handful of per-launch
knobs, it assembles the agent argv, container env, and host->box mounts.  (It
superseded the per-plugin ``build_cli_args`` / ``binary_mounts`` hooks, which
were removed for the public release.)

Everything here is pure and side-effect-free apart from filesystem *existence*
checks in :func:`descriptor_mounts` / :func:`resolve_binding_source` (which only
``Path.exists()`` — they never read, write, or mutate anything).  All functions
are agent-agnostic: no plugin names appear, and divergent LOGIC stays behind the
plugin ``Target`` hook methods.

LIVE: this module is wired into ``commands/start.py`` for every descriptor-bearing
target (all three first-party agents).  ``resolve_mode`` / ``assemble_argv`` /
``assemble_env`` build the launch argv + container env, and ``descriptor_mounts``
emits the delivery binds.  The only descriptor-less target is ``NoAgentTarget``
(the ``kanibako shell`` fallback), which launches a plain shell with no agent
argv and no delivery binds.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Collection, Sequence

from kanibako.errors import ConfigError
from kanibako.log import get_logger
from kanibako.settings.settings_keyspace import ACCESS_DEFAULT, ACCESS_TIERS
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
    """An AGENT_CRITICAL binding's resolved host source is missing/unresolvable.

    Raised by :func:`descriptor_mounts` so the caller can fail fast with a clean,
    actionable kanibako error instead of letting the runtime (crun) crash on a
    dangling bind source.  This is the declarative replacement for start.py's
    existing "mount source disappeared before launch" safe-fail.
    """


def entrypoint(descriptor: PluginDescriptor) -> str:
    """Return the container entrypoint: the first element of ``descriptor.command``.

    The descriptor's ``command`` is the full box argv prefix (e.g. ``("claude",)``);
    its first element is the program podman launches via ``--entrypoint``.  The
    remaining elements (``command[1:]``) are agent args and are produced by
    :func:`assemble_argv`.
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
    """Select the interactive launch mode key, lifting claude's ``build_cli_args`` logic.

    *available_modes* is the set of mode keys the agent's launch grammar declares —
    read off the snapshot's ``meta.agent.<a>.mode`` table (B5, spec §2d), where the
    descriptor materialized it.  Resolution order:

    1. ``-R`` resume picker: if *resume_mode* and ``"resume"`` is available -> ``"resume"``.
    2. ``skip_continue`` is true when a new session was forced (*new_session* /
       *is_new_project*) or the user passed ``--resume``/``-r`` in *extra_args*.
    3. If not *skip_continue* and ``"continue"`` is available -> ``"continue"``.
    4. Otherwise -> ``"start"``.

    A descriptor that declares only ``{"start", "continue"}`` (no picker) makes
    *resume_mode* fall through past step 1; with no new-session/``--resume``-in-extra
    forcing, step 3 then yields ``"continue"`` (continue-last) — the sane mapping
    for an agent that has no dedicated resume picker (e.g. goose/codex).
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

    ``None`` / unset ⇒ :data:`~kanibako.settings.settings_keyspace.ACCESS_DEFAULT`
    (``full`` — R-41's ruled default: today's behaviour preserved, the box is the
    containment boundary).  Anything else must be a member of
    :data:`~kanibako.settings.settings_keyspace.ACCESS_TIERS` EXACTLY.

    ⚑ An unknown value RAISES, naming the key and the legal values.  It is NEVER
    coerced and NEVER falls back to the default: on a permission axis a typo must
    not decide whether the agent prompts.  (The old boolean read did exactly that
    — ``coerce_bool("flase")`` → ``None`` → the permissive default — which is why
    the set-time guard existed; the guard stays, and this is now a second fence
    for a value that reached the file some other way, e.g. a hand edit.)

    ⚑ The EMPTY STRING is not unset — it is an INVALID VALUE, and it refuses like
    any other.  Only ``None`` (the key absent from the cascade) takes the default.
    Both set paths already refuse ``""``, so the only way it reaches here is the
    hand edit the paragraph above describes — i.e. exactly the case this second
    fence exists for.  Treating it as unset would make the ONE reachable route to
    this function's permissive arm a route the validators never approved (R-41: an
    unknown stored value is rejected, never treated as permissive).
    """
    if access is None:
        return ACCESS_DEFAULT
    if access not in ACCESS_TIERS:
        raise ConfigError(
            f"'access' must be one of {' | '.join(ACCESS_TIERS)} (spec §2d); "
            f"this box resolved {access!r}. Refusing rather than running: an "
            f"unrecognised permission tier is never treated as "
            f"'{ACCESS_DEFAULT}'."
        )
    return access


def effective_access(
    *,
    secure: bool,
    autonomous: bool,
    access: "str | None" = None,
) -> str:
    """Return the permission TIER this launch's ARGV/ENV should run at.

    Resolution (spec §2d + §1A; R-41 replaced the old boolean
    ``effective_safe_mode_off``):

    * *secure* (``-S``) -> ``"restricted"`` — wins over everything.
    * *autonomous* (``-A``) -> ``"full"`` — the per-launch override.
    * else the cascade-resolved *access* key, defaulting to ``full`` when unset
      (:func:`resolve_access_tier`, which REFUSES an unknown value).

    ⚑ The flags are EPHEMERAL and apply to the launch argv/env ONLY. The
    PROJECTED surfaces (claude ``settings.json`` / codex ``config.toml`` / goose
    ``config.yaml``) resolve the tier from the CASCADE alone — spec §1A's
    projected-surface exception, because a projection outlives the launch.  So
    both values exist at a launch and they are deliberately NOT the same read.
    """
    if secure:
        return "restricted"
    if autonomous:
        return "full"
    return resolve_access_tier(access)


def access_row(
    descriptor: PluginDescriptor, tier: str, *, agent: str = "",
) -> "AccessTierRow | None":
    """The descriptor's realization of *tier*, or ``None`` when it declares no
    ``access_realization`` at all (an agent with no permission surface).

    RAISES when the descriptor HAS an ``access_realization`` but cannot render
    *tier* — the un-rendered-tier rule: the launch stops and names the tiers this
    agent CAN render, rather than substituting a neighbouring one.  Never silently
    permissive, never silently stricter (goose's missing ``editing``: substituting
    ``auto`` would over-permit, substituting ``approve`` would deliver
    prompt-on-every-edit while reporting success — both are lies about what the
    user asked for).

    ⚑ A descriptor that declares an ``access_realization`` with ZERO rows is
    diagnosed SEPARATELY as PLUGIN VERSION SKEW, because that is what it actually
    is: a harness with a permission surface always realizes at least one tier, so
    no rows means the block was parsed from a plugin that predates the ``access``
    tiers — the retired PRE-TIER BODY (``flag``/``secure_flag`` with no ``tiers:``)
    loads to an empty :class:`AccessRealization`.  Reporting "this agent cannot
    render that tier" there would blame the agent for an install problem and send
    the user looking for a capability limit that does not exist.

    ⚑ SPELLING, old vs new: the block is named ``access_realization:`` (this
    release); it was named ``safe_bypass:`` before.  A descriptor still using the
    OLD KEY never reaches here — it is refused at descriptor load by
    :func:`~kanibako.settings.agent_defaults.load_descriptor`, because an unknown
    descriptor key would otherwise be ignored and leave the agent with NO
    permission surface at all.  So the only pre-tier shape that survives to this
    point is the old BODY under the NEW key, which is what the message below
    describes.
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


# ⚑ ``resolve_new_session`` was DELETED in P8 (v1.8.0). Its whole body was the fold
# *"the per-launch -N/-C/-R flags over the persisted ``continue_mode`` key"* — i.e.
# one hand-rolled precedence chain for one flag family. Spec §1A makes the COMMAND
# LINE its own LEVEL, the highest, so that fold now happens ONCE, declaratively, in
# :func:`kanibako.settings.settings_cli_level.build_cli_level` (``-N`` ⇒ ``continue_mode``
# False, ``-C``/``-R`` ⇒ True), and the launch simply reads the resolved key:
# ``effective_new_session = not continue_default``.
#
# Do NOT reintroduce it. Two places folding the same flags from two different inputs
# is the "two forms that mean the same thing" failure — and the second one would be
# the one nobody tested. ``resolve_mode`` still takes the raw ``resume_mode``,
# because ``-R`` selects a launch GRAMMAR (the resume mode fragment), which is not a
# key.


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

    The returned list is everything that should be passed to the container AFTER
    the ``--entrypoint`` program — i.e. it EXCLUDES ``descriptor.command[0]`` (the
    entrypoint, available via :func:`entrypoint`) and starts at
    ``descriptor.command[1:]``.  This mirrors how ``start.py`` uses ``cli_args``
    after setting ``--entrypoint`` separately, and corresponds to today's
    ``build_cli_args + state_args`` tail.

    ⚑ B5 (spec §2d): the launch-grammar fragments are PARAMETERS, not descriptor
    reads.  The live caller reads them off the ONE launch snapshot —
    ``meta.agent.<a>.mode[mode_key]`` (*mode_fragment*) and ``meta.agent.<a>.exec``
    (*op_fragment*) via :func:`kanibako.settings.settings_launch.meta_agent_grammar`
    — where the descriptor MATERIALIZED them
    (:func:`~kanibako.settings.settings_launch.meta_agent_grammar_floor`).  This
    function must NOT read ``descriptor.mode`` / ``descriptor.operations``: the
    descriptor feeds the KEYSPACE only, and a second (descriptor-direct) source
    for the same argv fragment is the drift shape B5 exists to kill.

    Build order (after ``command[1:]``):

    1. If *op_fragment* is set: the standalone operation fragment
       (``meta.agent.<a>.exec``); NO interactive mode is added — the two are
       MUTUALLY EXCLUSIVE at this argv slot (spec §2d).
    2. Else if *mode_fragment* is set: the interactive mode fragment
       (``meta.agent.<a>.mode[mode_key]`` for the resolved *mode_key*).
    3. If the descriptor's ``access_realization`` is FLAG-channel: emit the ``flag`` of
       its row for the *access* TIER (R-41).  An EMPTY row emits nothing (the
       claude/codex ``restricted`` realization — their own default already
       prompts); a MISSING row RAISES (:func:`access_row` — the un-rendered-tier
       rule), so no tier can ever fall through to a different tier's emission.
    4. For each FLAG-channel :class:`SettingArg` WITH A ``flag`` whose value in
       *setting_values* is truthy: ``flag + [value]``.
    5. *extra_args*, appended last.

    ⚑ The ``and s.flag`` in step 4 is the exact twin of :func:`assemble_env`'s
    ``and s.env_var``, and exists for a sharper reason.  Without it a flagless
    FLAG entry extends by ``()`` and then appends the value, so the value lands
    as a BARE POSITIONAL — for claude, the initial PROMPT.  A setting's value
    silently becoming the text the agent is asked to act on is far worse than
    the value going undelivered, so the emission is withheld.  The DECLARATION
    itself is refused one level up, at descriptor load
    (:func:`~kanibako.settings.agent_defaults._build_setting_arg`), which is
    where a plugin author gets told the file and the field; this guard is the
    containment for a :class:`PluginDescriptor` hand-built in code, which never
    passes through that loader.

    *access* is the tier this LAUNCH runs at (``-S``/``-A``-folded — see
    :func:`effective_access`); *agent* names the agent in the refusal message.
    ENV-channel access rows / settings are NOT argv and are emitted by
    :func:`assemble_env` instead.
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

    Starts from ``descriptor.container_env``, then:

    * If the descriptor's ``access_realization`` is ENV-channel with an ``env_var``: set
      it to the ``env_value`` of the row for the *access* TIER (R-41; goose
      ``GOOSE_MODE=auto`` at ``full``, ``approve`` at ``restricted``).  An EMPTY
      row emits nothing; a MISSING row RAISES (:func:`access_row`).  ⚑ For an
      agent whose UNSET env default is itself permissive (goose's ``GOOSE_MODE``
      defaults to ``auto``) every renderable tier must carry a value — emitting
      nothing there would BE the bypass, which is why the un-rendered tier
      refuses instead of falling through.
    * For each ENV-channel :class:`SettingArg` with an ``env_var`` and a truthy
      value in *setting_values*: set ``env_var`` to that value.

    FLAG-channel access rows / settings are argv and are emitted by
    :func:`assemble_argv` instead.
    """
    env: dict[str, str] = dict(descriptor.container_env)

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


def resolve_binding_source(
    binding: Binding,
    install: AgentInstall,
    *,
    override: str = "",
) -> Path | None:
    """Resolve a binding's host source path (no existence check here).

    A non-empty *override* (a caller-supplied host source repoint; the settable
    user cascade equivalent is ``agent.<name>.bindings.{ro,rw}.<key>``, resolved
    upstream in the launch snapshot — this param is now used by tests only)
    always wins and is returned as ``Path(override)``.  Otherwise the source is
    derived from ``binding.origin``:

    * ``LAUNCHER`` -> ``install.launcher`` (falling back to ``install.binary``).
    * ``INSTALL_DIR`` -> ``install.install_dir``.
    * ``BINARY`` -> ``install.binary``.
    * ``LITERAL`` -> ``binding.literal_src``.

    Returns ``None`` when the source cannot be resolved.
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

    The DECLARATION-level question: "does this plugin deliver something to that
    box-side path?"  Asked of the box_dest and not of a key NAME on purpose —
    the thing that collides in a box is the DESTINATION (spec §0's identical-dest
    table, which errors on two concrete bindings at one dest), and a plugin is
    free to name its key whatever it likes.  A key-name test would pass a
    third-party plugin's identically-destined binding straight into that error.

    Declaration-level, not resolution-level: it does not touch the filesystem and
    does not care whether the source resolves.  That matches
    :func:`~kanibako.settings.agent_representation.agent_default_partial`, which represents
    a binding in the launch snapshot with no existence check — so a caller gating
    on this predicate sees exactly what the snapshot will carry.

    *descriptor* may be ``None`` (the no-agent target has none), which answers
    False.  *box_dest* must be the ABSOLUTE guest path: descriptor box_dests are
    ``$GUEST_HOME``-expanded by the defaults loader, so a ``~``-spelled dest never
    matches and callers must expand first.

    Sole caller today: :func:`kanibako.settings.core_defaults.kickoff_default_categories`
    (the P-5 transition gate).
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

    For each :class:`Binding` (descriptor order preserved):

    * Its host source is resolved via :func:`resolve_binding_source`, with any
      per-key value in *overrides* taking precedence.
    * ``AGENT_CRITICAL`` (binary/launcher/share): the source MUST resolve and
      exist, else :class:`BindingSourceError` is raised — the clean safe-fail
      that replaces a crun crash on a dangling bind source.  It is then bound
      as-is (podman inode-pins it at mount time).
    * ``AGENT`` (best-effort): a source that is unresolvable or missing is
      skipped (a missing/suppressed agent share is fine) with a debug log;
      otherwise it is appended.  (No shipped plugin currently declares an AGENT
      binding — agent-scope shared dirs flow through the category resolver — but
      the branch is kept for the general binding contract.)

    Mount options are ``"ro"`` when ``binding.ro`` is set, else ``""`` (rw).

    NOTE: clearing any pre-existing dest symlink in the box is the caller's job
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
