"""Agent-agnostic, declarative assembly of an agent launch from a PluginDescriptor.

This module is the data-driven core that replaces the per-plugin
``build_cli_args`` / ``binary_mounts`` hooks: given a plugin's
:class:`~kanibako.targets.base.PluginDescriptor` plus the resolved host
:class:`~kanibako.targets.base.AgentInstall` and a handful of per-launch knobs,
it assembles the agent argv, container env, and host->box mounts.

Everything here is pure and side-effect-free apart from filesystem *existence*
checks in :func:`descriptor_mounts` / :func:`resolve_binding_source` (which only
``Path.exists()`` — they never read, write, or mutate anything).  All functions
are agent-agnostic: no plugin names appear, and divergent LOGIC stays behind the
plugin ``Target`` hook methods.

LIVE: this module is wired into ``commands/start.py`` for every descriptor-bearing
target (all three first-party agents).  ``resolve_mode`` / ``assemble_argv`` /
``assemble_env`` build the launch argv + container env, and ``descriptor_mounts``
emits the delivery binds; the legacy ``build_cli_args`` / ``binary_mounts`` hooks
are bypassed when a target exposes a descriptor.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Collection

from kanibako.log import get_logger
from kanibako.targets.base import (
    BindScope,
    Channel,
    HostSrcOrigin,
    Mount,
)

if TYPE_CHECKING:
    from kanibako.targets.base import (
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

    *available_modes* is the set of mode keys the descriptor declares
    (``descriptor.mode.keys()``).  Resolution order:

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


def effective_safe_mode_off(
    *,
    secure: bool,
    autonomous: bool,
    persisted_access: str = "",
) -> bool:
    """Return True when the agent should run WITHOUT safety (safe-bypass ON).

    Resolution (matches kanibako's documented default of autonomous):

    * *secure* (``-S``) -> ``False`` (safe mode ON, bypass OFF) — wins over everything.
    * *autonomous* (``-A``) -> ``True``.
    * else the persisted default (``persisted_access``, the redeemed claude
      ``access`` setting; ``""`` for goose/codex): ``"permissive"`` -> ``True``,
      ``"restricted"`` -> ``False``, anything else (empty/unknown) -> ``True``
      (start.py: "Neither means autonomous (default)").
    """
    if secure:
        return False
    if autonomous:
        return True
    if persisted_access == "permissive":
        return True
    if persisted_access == "restricted":
        return False
    return True


def assemble_argv(
    descriptor: PluginDescriptor,
    *,
    mode_key: str | None,
    safe_mode_off: bool,
    setting_values: dict[str, str],
    op: str | None = None,
    extra_args: list[str],
) -> list[str]:
    """Assemble the agent argv that follows the entrypoint binary.

    The returned list is everything that should be passed to the container AFTER
    the ``--entrypoint`` program — i.e. it EXCLUDES ``descriptor.command[0]`` (the
    entrypoint, available via :func:`entrypoint`) and starts at
    ``descriptor.command[1:]``.  This mirrors how ``start.py`` uses ``cli_args``
    after setting ``--entrypoint`` separately, and corresponds to today's
    ``build_cli_args + state_args`` tail.

    Build order (after ``command[1:]``):

    1. If *op* is set: the standalone operation fragment
       (``descriptor.operations[op].fragment``); NO interactive mode is added.
    2. Else if *mode_key* is set: the interactive mode fragment
       (``descriptor.mode[mode_key]``).
    3. If the descriptor's ``safe_bypass`` is FLAG-channel: emit its ``flag``
       when *safe_mode_off*, else its ``secure_flag`` (the symmetric SECURE
       emission — empty ``secure_flag`` emits nothing on safe-ON, the
       claude/codex default-safe behavior).
    4. For each FLAG-channel :class:`SettingArg` whose value in *setting_values*
       is truthy: ``flag + [value]``.
    5. *extra_args*, appended last.

    ENV-channel safe-bypass / settings are NOT argv and are emitted by
    :func:`assemble_env` instead.
    """
    argv: list[str] = list(descriptor.command[1:])

    if op is not None:
        argv.extend(descriptor.operations[op].fragment)
    elif mode_key is not None:
        argv.extend(descriptor.mode[mode_key])

    sb = descriptor.safe_bypass
    if sb is not None and sb.channel is Channel.FLAG:
        if safe_mode_off:
            argv.extend(sb.flag)
        elif sb.secure_flag:
            argv.extend(sb.secure_flag)

    for s in descriptor.settings:
        if s.channel is Channel.FLAG:
            value = setting_values.get(s.setting_key)
            if value:
                argv.extend(s.flag)
                argv.append(value)

    argv.extend(extra_args)
    return argv


def assemble_env(
    descriptor: PluginDescriptor,
    *,
    safe_mode_off: bool,
    setting_values: dict[str, str],
) -> dict[str, str]:
    """Assemble the container environment overlay from the descriptor.

    Starts from ``descriptor.container_env``, then:

    * If *safe_mode_off* and the descriptor's ``safe_bypass`` is ENV-channel with
      an ``env_var``: set it to ``env_value`` (falling back to ``"auto"`` when the
      descriptor left ``env_value`` empty, e.g. goose ``GOOSE_MODE=auto``).
    * Else (NOT *safe_mode_off* — secure/``-S``) if the ENV-channel ``safe_bypass``
      declares a non-empty ``secure_env_value``: set ``env_var`` to it (the
      symmetric SECURE emission, e.g. goose ``GOOSE_MODE=approve``).  This is
      REQUIRED for agents whose unset env default is unsafe (goose's ``GOOSE_MODE``
      defaults to ``auto``); an empty ``secure_env_value`` emits nothing on
      safe-ON, preserving agents already safe-by-default (claude/codex).
    * For each ENV-channel :class:`SettingArg` with an ``env_var`` and a truthy
      value in *setting_values*: set ``env_var`` to that value.

    FLAG-channel safe-bypass / settings are argv and are emitted by
    :func:`assemble_argv` instead.
    """
    env: dict[str, str] = dict(descriptor.container_env)

    sb = descriptor.safe_bypass
    if sb is not None and sb.channel is Channel.ENV and sb.env_var:
        if safe_mode_off:
            env[sb.env_var] = sb.env_value or "auto"
        elif sb.secure_env_value:
            env[sb.env_var] = sb.secure_env_value

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
    shared_store_root: Path | None = None,
    override: str = "",
) -> Path | None:
    """Resolve a binding's host source path (no existence check here).

    A non-empty *override* (a user cascade value, ``agent.<name>.binding.<key>``)
    always wins and is returned as ``Path(override)``.  Otherwise the source is
    derived from ``binding.origin``:

    * ``LAUNCHER`` -> ``install.launcher`` (falling back to ``install.binary``).
    * ``INSTALL_DIR`` -> ``install.install_dir``.
    * ``BINARY`` -> ``install.binary``.
    * ``SHARED_STORE`` -> ``shared_store_root / binding.src_rel`` when a root is
      given, else ``None`` (unresolvable without a store root).
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
    if origin is HostSrcOrigin.SHARED_STORE:
        if shared_store_root is None:
            return None
        return shared_store_root / binding.src_rel
    if origin is HostSrcOrigin.LITERAL:
        return binding.literal_src

    return None


def descriptor_mounts(
    descriptor: PluginDescriptor,
    install: AgentInstall,
    *,
    shared_store_root: Path | None = None,
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
    * ``AGENT`` (e.g. plugins): best-effort.  A source that is unresolvable or
      missing is skipped (a missing/suppressed agent share is fine) with a debug
      log; otherwise it is appended.

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
            shared_store_root=shared_store_root,
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
