"""kanibako start / shell: container launch with credential flow."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig
    from kanibako.settings.config import KanibakoConfig
    from kanibako.settings.paths import ProjectPaths, StandardPaths
    from kanibako.settings.settings_launch import AuthSource
    from kanibako.targets.base import PersonaSpec
    from kanibako.vscode.vscode_config import CodexModelProvider

from kanibako.settings.agent_config import (
    agent_category_root,
    agent_settings_path,
    load_agent_config,
    write_agent_config,
)
from kanibako.box_supervisor import CONTINUE_MARKER, KANIBAKO_PKG_MOUNT_ROOT
from kanibako.commands.diagnose import probe_missing_executables
from kanibako.settings.config import (
    coerce_bool,
    config_file_path,
    load_config,
    load_merged_config,
    persist_creation_flags,
    read_system_agent,
)
from kanibako.settings import core_defaults
from kanibako.runtime.container import (
    ContainerRuntime,
    _guest_dest_to_host,
    detect_shadowed_mounts,
)
from kanibako.errors import ConfigError, ContainerError, KanibakoError, ProjectError
from kanibako.log import get_logger
from kanibako.runtime.rig_registry import load_registry, registry_path
from kanibako.runtime.rig_resolve import resolve_rig
from kanibako.settings.settings_categories import SECRET_MOUNT_DIR, SECRET_VAR_RE
from kanibako.settings.settings_cli_level import build_cli_level
from kanibako.settings.paths import (
    _upgrade_shell,
    box_state_home,
    box_workset_settings_paths,
    xdg,
    load_std_paths,
    resolve_box_target,
    workset_env_path,
)
from kanibako.agent_ref import (
    canonicalize_agent_ref,
    display_agent_ref,
    harness_of,
    parse_agent_ref,
    persona_of,
    with_harness,
)
from kanibako.targets import assembly, credsync, resolve_target
from kanibako.targets.assembly import BindingSourceError
from kanibako.utils import container_name_for, project_hash, short_hash
from kanibako.vscode.vscode_config import AGENT_MARKERS_DIR


def _agent_critical_dests() -> list[tuple[str, str]]:
    """Enumerate AGENT_CRITICAL bind mountpoints across ALL installed plugins.

    Each installed plugin's descriptor declares AGENT_CRITICAL delivery binds
    whose ``box_dest`` is an in-box absolute path (a ``$GUEST_HOME`` expression
    expanded to the ``/home/agent`` literal at load).  Map each to a
    shell_dir-relative path (strip the guest-home prefix) plus its kind
    ("file"/"dir"), for the per-launch hygiene sweep to reap stale empty stubs
    (the 0-byte ``.local/bin/<agent>`` decoy).  Enumerated across EVERY plugin,
    not just this launch's agent: a claude box later ``shell``-launched (no
    agent) must still have claude's stubs reaped.  Returns [] if nothing
    matches, which makes the reaper a no-op.
    """
    from kanibako.settings.settings_resolve import GUEST_HOME
    from kanibako.targets import discover_targets
    from kanibako.targets.base import BindKind, BindScope

    prefix = GUEST_HOME + "/"
    dests: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for cls in discover_targets().values():
        try:
            desc = cls().descriptor
        except Exception as exc:
            # A plugin that fails to instantiate/describe cannot contribute
            # dests; skip it (delivery would fail loudly elsewhere).  Debug-log
            # so a legit plugin whose stubs then go un-reaped is diagnosable.
            get_logger("start").debug(
                "mountpoint enumeration skipped %s: %s", cls, exc
            )
            continue
        if desc is None:
            continue
        for b in desc.bindings:
            if b.scope is not BindScope.AGENT_CRITICAL:
                continue
            box_dest = b.box_dest
            if not box_dest.startswith(prefix):
                continue
            rel = box_dest[len(prefix):]
            if not rel:
                continue
            kind = "dir" if b.kind is BindKind.DIR else "file"
            pair = (rel, kind)
            if pair in seen:
                continue
            seen.add(pair)
            dests.append(pair)
    return dests


def ensure_persona_share_symlinks(std, agent_id, target) -> None:
    """Point a persona's agent-scope common dirs at the harness's (symlink shim).

    A persona is a distinct agent NODE (``navigator℘claude``) whose ``agents/<node>/``
    dir is its own store, but whose plugins/cache SHOULD be shared with the bare
    harness (``agents/claude/``) rather than starting empty.  Rather than re-root
    the resolver, we lay a SYMLINK shim: for every agent-scope common the target
    declares (``target.default_common()`` — claude's ``plugins`` / ``cache``),
    ``agents/<node>/common/<leaf>`` becomes a symlink ->
    ``agents/<harness>/common/<leaf>``.
    The resolver + spec are UNCHANGED; the L7 guarantee-create later (``mkdir
    parents=True, exist_ok=True`` on the rw source) is a no-op on the symlink-to-
    existing-dir, so the harness dir is the real writeback target.

    ⚑ THE LEAF COMES FROM THE KEY, NOT THE VALUE, and both sides are built from
    :func:`~kanibako.settings.agent_config.agent_category_root` — the SAME layout helper the
    declaration-time ref builder uses.  The ``host_src`` VALUE is now the fully
    self-resolving ``@meta.agent.<a>.path/common/<leaf>`` (spec §2a), so reading a
    path component off it would produce the literal directory
    ``agents/<node>/@meta.agent.<a>.path/common/<leaf>``.  Single-sourcing the
    layout is also what stops this shim and the resolver from drifting apart —
    they are two consumers of ONE layout fact.

    Driven by the descriptor's declared ``common`` entries (generic over harnesses; NO
    per-plugin code).  Call at persona-dir MATERIALIZATION, BEFORE mount assembly /
    category source resolution, so the symlink pre-dates any real-dir guarantee-create.

    Edge cases (idempotent + fail-safe, NEVER clobber):
      * BARE (``agent_id == harness``) -> return immediately, do nothing.  Every
        existing (non-persona) agent path is byte-for-byte unchanged: no symlink,
        no new dir.
      * harness dir made FIRST (``mkdir parents``) so the link never dangles.
      * node path already the CORRECT symlink -> no-op.
      * node path ABSENT -> create the symlink.
      * node path EXISTS as a real dir OR a WRONG-target symlink -> LEAVE it +
        debug-log (a persona that legitimately has its own dir wins).
    """
    harness = harness_of(agent_id)
    if agent_id == harness:
        # Bare agent (node == harness): nothing to shim.  Backward-compatible.
        return
    if target is None:
        return

    logger = get_logger("start")
    agents_root = Path(std.agents)
    for key in target.default_common():
        # ``agent.<a>.common.<leaf>`` -> ``<leaf>`` (the keyspace name, which IS the
        # store's dir name; the host_src value is an @-ref, not a path component).
        leaf = key.rsplit(".", 1)[-1]
        harness_dir = agent_category_root(agents_root, harness, "common") / leaf
        node_link = agent_category_root(agents_root, agent_id, "common") / leaf

        # Create the harness (real) dir FIRST so the symlink never dangles.
        harness_dir.mkdir(parents=True, exist_ok=True)
        node_link.parent.mkdir(parents=True, exist_ok=True)

        if node_link.is_symlink():
            # An existing symlink: no-op if it already points at the harness dir,
            # otherwise LEAVE it (a persona that repointed its own common dir wins).
            try:
                correct = node_link.readlink() == harness_dir
            except OSError:
                correct = False
            if correct:
                continue
            logger.debug(
                "persona common %s: node link %s -> %s (not the harness dir %s); "
                "leaving as-is",
                leaf, node_link, os.readlink(node_link), harness_dir,
            )
            continue
        if node_link.exists():
            # A real dir/file already at the node path: the persona owns it; leave.
            logger.debug(
                "persona common %s: node path %s is a real dir; leaving as-is "
                "(not sharing the harness dir)",
                leaf, node_link,
            )
            continue
        node_link.symlink_to(harness_dir)
        logger.debug(
            "persona common %s: linked %s -> %s", leaf, node_link, harness_dir,
        )


def add_start_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "start",
        help="Start or continue an agent session (default)",
        description="Start or continue an agent session in a container.",
    )

    # Start mode: -N/-C/-R mutually exclusive
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "-N", "--new", action="store_true", dest="new_session",
        help="Start a new conversation (overrides continue_mode)",
    )
    mode_group.add_argument(
        "-C", "--continue", action="store_true", dest="continue_session",
        help="Continue the most recent conversation (overrides continue_mode)",
    )
    mode_group.add_argument(
        "-R", "--resume", action="store_true", dest="resume_session",
        help="Resume with conversation picker",
    )

    # Agent mode: -A/-S mutually exclusive
    agent_group = p.add_mutually_exclusive_group()
    agent_group.add_argument(
        "-A", "--autonomous", action="store_true",
        help="Run with full permissions (--dangerously-skip-permissions)",
    )
    agent_group.add_argument(
        "-S", "--secure", action="store_true",
        help="Run without --dangerously-skip-permissions",
    )

    p.add_argument(
        "-M", "--model", default=None,
        help="Override the agent model for this run",
    )
    p.add_argument(
        "-e", "--env", action="append", default=None, metavar="KEY=VALUE",
        help="Set per-run environment variable (repeatable)",
    )
    p.add_argument(
        "--image", default=None,
        help="Use IMAGE as the container image for this run (--rig is the preferred spelling)",
    )
    p.add_argument(
        "--rig", dest="image", default=None,
        help="Rig (image) to use; synonym for --image",
    )
    p.add_argument(
        "--entrypoint", default=None,
        help="Use CMD as the container entrypoint",
    )

    # Session persistence mode
    persist_group = p.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persistent", action="store_true",
        help="Run in a persistent tmux session (reattach on subsequent start)",
    )
    persist_group.add_argument(
        "--ephemeral", action="store_true",
        help="Run in foreground without tmux (single-use session)",
    )

    # Attach mode: --detach/--background (start a keep-alive box in the
    # background, do NOT attach this terminal) vs --attach (the default: attach
    # the terminal into the session).  Detach implies a persistent/tmux session:
    # the box's PID-1 is a BARE keep-alive shell (not the agent), so the box
    # stays Up for a later reattach / `kanibako code` / VS Code exec terminal.
    attach_group = p.add_mutually_exclusive_group()
    attach_group.add_argument(
        "--detach", "--background", action="store_const", const=True, dest="detach",
        help="Start a keep-alive box in the background without attaching the "
             "terminal (box stays running; reattach later with 'kanibako start')",
    )
    attach_group.add_argument(
        "--attach", action="store_const", const=False, dest="detach",
        help="Attach the terminal into the session (default)",
    )
    # Tri-state default None = neither --detach nor --attach given (the attach
    # default).  run_start normalises None -> attach and distinguishes an EXPLICIT
    # --attach (False) from the unset default so it can reject a contradictory
    # --warm-only --attach.
    p.set_defaults(detach=None)

    p.add_argument(
        "--warm-only", action="store_true", dest="warm_only",
        help="Warm the box AGENT-INDEPENDENT in the background (panel-watch "
             "supervisor, no CLI agent); implies --detach. Used by "
             "'kanibako code' so the VS Code panel is the sole agent (no "
             "two-agent split-brain on one ~/.claude).",
    )

    p.add_argument(
        "--print-container", action="store_true", dest="print_container",
        help="After the box is up, print its container name as the final "
             "stdout line (machine-readable; use with --detach)",
    )
    p.add_argument(
        "--no-helpers", action="store_true",
        help="Disable helper spawning (no hub socket mounted)",
    )
    p.add_argument(
        "--no-auto-auth", action="store_true",
        help="Disable automated browser-based OAuth refresh",
    )
    p.add_argument(
        "--browser", action="store_true",
        help="Launch a headless browser sidecar (BROWSER_WS_ENDPOINT injected)",
    )
    p.add_argument(
        "--share-images", action="store_true",
        help="Share host container image storage with child (read-only, experimental)",
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project directory or registered name (omit for current dir)",
    )
    p.set_defaults(func=run_start)


def add_shell_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shell",
        help="Open a bash shell in the container",
        description="Open a bash shell in the container (no agent).",
    )
    p.add_argument(
        "-e", "--env", action="append", default=None, metavar="KEY=VALUE",
        help="Set per-run environment variable (repeatable)",
    )
    p.add_argument(
        "--image", default=None,
        help="Use IMAGE as the container image for this run (--rig is the preferred spelling)",
    )
    p.add_argument(
        "--rig", dest="image", default=None,
        help="Rig (image) to use; synonym for --image",
    )
    p.add_argument(
        "--entrypoint", default=None,
        help="Use CMD as the container entrypoint",
    )

    # Session persistence mode
    persist_group = p.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persistent", action="store_true",
        help="Run in a persistent tmux session (reattach on subsequent start)",
    )
    persist_group.add_argument(
        "--ephemeral", action="store_true",
        help="Run in foreground without tmux (single-use session)",
    )

    p.add_argument(
        "--no-helpers", action="store_true",
        help="Disable helper spawning (no hub socket mounted)",
    )
    p.add_argument(
        "--share-images", action="store_true",
        help="Share host container image storage with child (read-only, experimental)",
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project directory or registered name (omit for current dir)",
    )
    p.set_defaults(func=run_shell)


def run_start(args: argparse.Namespace) -> int:
    entrypoint = getattr(args, "entrypoint", None)
    image_override = getattr(args, "image", None)
    new_session = getattr(args, "new_session", False)
    continue_session = getattr(args, "continue_session", False)
    resume_session = getattr(args, "resume_session", False)
    secure = getattr(args, "secure", False)
    model_override = getattr(args, "model", None)
    no_helpers = getattr(args, "no_helpers", False)
    no_auto_auth = getattr(args, "no_auto_auth", False)
    browser = getattr(args, "browser", False)
    share_images = getattr(args, "share_images", False)
    print_container = getattr(args, "print_container", False)
    explicit_persistent = getattr(args, "persistent", False)
    explicit_ephemeral = getattr(args, "ephemeral", False)
    warm_only = getattr(args, "warm_only", False)
    # --detach/--attach tri-state: True (--detach/--background), False (an EXPLICIT
    # --attach), or None (neither given -> the attach default).  Distinguishing an
    # explicit --attach lets --warm-only reject a contradictory foreground/attach
    # request; None and False both mean "do not detach" for every other path.
    detach_raw = getattr(args, "detach", None)
    detach = detach_raw is True
    # Reconcile the positional subject with the blanket --box flag (same → warn,
    # differ → error).  Computed HERE (ahead of the persistence-mode heuristic)
    # because ``bootstrap`` is now an AGENT-scope key (spec §2d): resolving its
    # value needs the box + its resolved agent, so ``_resolve_bootstrap_program``
    # takes the subject + any explicit ``--agent`` (Phase D seam).
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    explicit_agent = getattr(args, "agent", None)  # Phase D seam (--agent flag)
    bootstrap_program = _resolve_bootstrap_program(project_dir, explicit_agent)
    no_bootstrap = _is_no_bootstrap(bootstrap_program)
    if warm_only:
        # --warm-only warms the box AGENT-INDEPENDENT (panel-watch supervisor, no
        # CLI agent) — the same launch shape as start_detached(warm_only=True),
        # exposed as a first-class `start` flag for the `code --remote` leg (and
        # direct users).  There is no CLI agent to attach to, so it is inherently a
        # DETACHED/background persistent launch: force --detach and reject a
        # contradictory foreground/attach request, mirroring the --detach +
        # --ephemeral conflict handled below.  (A no-agent / shell box with
        # --warm-only is a harmless no-op: warm_only is inert in _run_container when
        # there is no agent to keep independent; it still detaches as a keep-alive.)
        if explicit_ephemeral or detach_raw is False:
            other = "--ephemeral" if explicit_ephemeral else "--attach"
            print(
                f"Error: --warm-only cannot be combined with {other} "
                "(warm-only starts a persistent background box with no CLI "
                "agent to attach to).",
                file=sys.stderr,
            )
            return 1
        detach = True
    if detach:
        # Detach is inherently a persistent/tmux mode (the box must survive as a
        # keep-alive with no attached terminal).  --ephemeral is a direct
        # contradiction (foreground single-use vs. background keep-alive).
        #
        # NOTE (Finding 4): unlike --persistent, detach does NOT require the
        # bootstrap program on the HOST.  --persistent checks the host because it
        # RE-ATTACHES this terminal into an in-box tmux and, historically, gates
        # on the host program being present.  Detach never attaches — the
        # keep-alive tmux runs entirely IN-BOX as PID-1, so the only thing that
        # must have the bootstrap program is the IMAGE, which _run_container's
        # tier-1 baseline probe already verifies for every persistent launch.
        # This also unifies the two detach entry points: `kanibako code`'s
        # auto-start goes through start_detached -> _run_container with no host
        # check, so --detach must match.  The `agent.default.bootstrap=none`
        # config contradiction (no in-box bootstrap to keep alive) is still a
        # genuine error, surfaced early here for a clean message and re-guarded
        # by _run_container.
        if explicit_ephemeral:
            print(
                "Error: --detach cannot be combined with --ephemeral "
                "(detach starts a persistent background box).",
                file=sys.stderr,
            )
            return 1
        if no_bootstrap:
            print(
                "Error: --detach requires a bootstrap program, but "
                "agent.default.bootstrap=none (foreground opt-out). Unset it or "
                "set an installed program (e.g. tmux) for background sessions.",
                file=sys.stderr,
            )
            return 1
        persistent = True
    elif explicit_persistent:
        # An explicit reattach request needs a working bootstrap program on the
        # HOST (reattach shells out to it).  Two distinct failures, both clean
        # here rather than a downstream crash:
        #   * `none` = opt-out — persistent is a contradiction.
        #   * a real program absent on the host (host-missing, NOT image-missing:
        #     don't conflate with the tier-1 in-image hard-stop).
        if no_bootstrap:
            print(
                "Error: --persistent requires a bootstrap program, but "
                "agent.default.bootstrap=none (foreground opt-out). Unset it or "
                "set an installed program (e.g. tmux) for persistent sessions.",
                file=sys.stderr,
            )
            return 1
        if not _bootstrap_available(bootstrap_program):
            print(
                f"Error: --persistent needs '{bootstrap_program}' on this host "
                f"(reattach runs it), but it is not installed. Install "
                f"'{bootstrap_program}', or run without --persistent for "
                f"foreground single-use mode.",
                file=sys.stderr,
            )
            return 1
        persistent = True
    elif explicit_ephemeral:
        persistent = False
    elif no_bootstrap:
        # Explicit `none` opt-out: foreground single-use, no note, and no
        # host probe (the user chose this on purpose).
        persistent = False
    elif _bootstrap_available(bootstrap_program):
        # Default: persistent when the configured bootstrap program is present.
        persistent = True
    else:
        # Configured program absent on the host: fall back to foreground
        # single-use (today's silent behavior) but CLUE THE USER IN once — name
        # the program, the consequence, and both remedies (install it, or make
        # foreground explicit with agent.default.bootstrap=none to silence this).
        persistent = False
        print(
            f"Note: '{bootstrap_program}' not found on this host; running in "
            f"the foreground (single-use, no reattach). Install "
            f"'{bootstrap_program}' for persistent sessions, or set "
            f"agent.default.bootstrap=none to make foreground mode explicit.",
            file=sys.stderr,
        )
    env_vars = getattr(args, "env", None) or []
    # ``project_dir`` + ``explicit_agent`` were resolved above (the persistence-mode
    # heuristic needs them for the agent-scope ``bootstrap`` lookup).  Agent
    # resolution proper happens UP FRONT inside _run_container via the unified
    # agent_select.select_agent seam (--agent > box pref > workset pref >
    # system.agent → the installed-count rule); a Gate-2a/2b there surfaces verbatim
    # with a non-zero exit — NEVER a silent drop to shell.  `kanibako shell`
    # (run_shell) bypasses it.
    agent_args = getattr(args, "agent_args", [])

    # The two ephemeral permission flags, carried RAW to the launch: ``-S``
    # (secure) selects the ``restricted`` access tier, ``-A`` (autonomous)
    # selects ``full``, and NEITHER means "use the box's resolved ``access``
    # key" (R-41; the fold lives in ``assembly.effective_access``, one place).
    safe_mode = secure
    autonomous = getattr(args, "autonomous", False)

    return _run_container(
        project_dir=project_dir,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=new_session,
        continue_override=continue_session,
        safe_mode=safe_mode,
        autonomous=autonomous,
        resume_mode=resume_session,
        extra_args=agent_args,
        no_helpers=no_helpers,
        no_auto_auth=no_auto_auth,
        browser=browser,
        share_images=share_images,
        persistent=persistent,
        detach=detach,
        warm_only=warm_only,
        model_override=model_override,
        cli_env=env_vars,
        explicit_agent=explicit_agent,
        print_container=print_container,
    )


def run_shell(args: argparse.Namespace) -> int:
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    shell_args = getattr(args, "shell_args", [])

    entrypoint = getattr(args, "entrypoint", None)
    box_shell_mode = False
    if not entrypoint:
        if shell_args:
            # One-off command exec: /bin/sh -c "<cmd>" (not the interactive shell).
            entrypoint = "/bin/sh"
        else:
            # Interactive shell: defer to _run_container's image-aware box.shell
            # resolution.  We leave entrypoint=None and flag box_shell_mode so
            # _run_container resolves the shell *with* the runtime/image handle
            # (box.shell -> $KANIBAKO_SHELL -> stored image login shell -> sh)
            # without engaging an agent.
            box_shell_mode = True
    # Wrap shell_args as -c "cmd" so /bin/sh executes them as a command
    if shell_args and not getattr(args, "entrypoint", None):
        shell_args = ["-c", " ".join(shell_args)]

    image_override = getattr(args, "image", None)
    no_helpers = getattr(args, "no_helpers", False)
    share_images = getattr(args, "share_images", False)
    env_vars = getattr(args, "env", None) or []

    explicit_persistent = getattr(args, "persistent", False)
    explicit_ephemeral = getattr(args, "ephemeral", False)
    if explicit_persistent:
        persistent = True
    elif explicit_ephemeral:
        persistent = False
    else:
        persistent = False  # shell defaults to ephemeral

    return _run_container(
        project_dir=project_dir,
        entrypoint=entrypoint,
        image_override=image_override,
        new_session=False,
        safe_mode=False,
        autonomous=False,
        resume_mode=False,
        extra_args=shell_args,
        no_helpers=no_helpers,
        share_images=share_images,
        persistent=persistent,
        cli_env=env_vars,
        box_shell_mode=box_shell_mode,
    )


def start_detached(
    project_dir: str | None, *, explicit_agent: str | None = None,
    warm_only: bool = True,
) -> int:
    """Start a box DETACHED and AGENT-INDEPENDENT — no terminal attach, no CLI agent.

    Public entry reused by ``kanibako code`` auto-start.  Runs the FULL persistent
    box assembly (mounts / credentials / agent delivery binds / env / the
    ``KANIBAKO_AGENT`` stamp) exactly as a normal persistent launch, and the
    caller's terminal is NOT attached.

    *warm_only* (default ``True``) selects the E2f AGENT-INDEPENDENT warm-up: PID-1
    is the box_supervisor in PANEL-WATCH mode — it starts NO CLI agent (the VS Code
    panel is the sole agent, avoiding a two-agent split-brain on one ``~/.claude``),
    watches the panel-agent liveness marker + the vscode_server surface, and
    self-heals a CLI agent only if the panel agent dies with the panel still
    connected.  A caller that instead wants the E2b supervised-agent keep-alive can
    pass ``warm_only=False`` (then a background CLI agent runs, self-healed).

    The box stays Up for a later VS Code attach, a ``tmux attach``, or a subsequent
    ``kanibako start``.  Returns 0 once the box is confirmed running in the
    background, non-zero on a launch failure (with the container logs surfaced).
    """
    return _run_container(
        project_dir=project_dir,
        entrypoint=None,
        image_override=None,
        new_session=False,
        safe_mode=False,
        autonomous=False,
        resume_mode=False,
        extra_args=[],
        persistent=True,
        detach=True,
        explicit_agent=explicit_agent,
        warm_only=warm_only,
    )


# Exact-string sentinel for the agent-scope ``bootstrap`` behavior key meaning "no
# bootstrap wrapper, on purpose": launch runs foreground single-use (today's
# absent-tmux fallback), with NO host-absent note and NO image baseline probe for a
# bootstrap exe.  It is a CONSUMER-side interpretation only (start.py) — the
# resolver/keyspace treat it as a plain agent-scope string value; nothing here
# changes the key's semantics.
_BOOTSTRAP_NONE = "none"

# The consumer default for the agent-scope ``bootstrap`` behavior key.  The spec
# lists ``agent.default.bootstrap | tmux`` (§2d), but — exactly like the old
# ``box.bootstrap_program or "tmux"`` coercion this replaced — the ``tmux`` default
# is applied HERE at the consumer (start.py), NOT baked into a descriptor floor, so
# an unset value (no scope sets ``bootstrap``) resolves to ``tmux`` and every shipped
# agent (which declares NO bootstrap override, spec §2d) inherits it.
_BOOTSTRAP_DEFAULT = "tmux"

# E2g / increment 4a — the box-local AGENT LIVENESS MARKERS directory (per-PID).
# The canonical constant lives in :mod:`kanibako.vscode.vscode_config` (the low-level module
# that owns the marker write-side hook command); it is imported here (see the module
# imports) so this file's ``--agent-markers-dir`` value (read side) and the
# ``KANIBAKO_AGENT_MARKERS_DIR`` env it seeds are byte-identical to the seeded hook's
# default dir — the two ends of the contract can never desync.  Re-exported from
# this module for existing importers (``from kanibako.commands.start import
# AGENT_MARKERS_DIR``).


def _is_no_bootstrap(program: str | None) -> bool:
    """True when *program* is the explicit ``none`` opt-out sentinel.

    Exact lowercase match only — an image, path, or program literally named
    ``none`` would collide, but that is not a real bootstrap program, so the
    sentinel wins deliberately.
    """
    return program == _BOOTSTRAP_NONE


def _effective_bootstrap(
    proj,
    system_settings_path: "Path | None",
    agent_id: str,
    *,
    agent_path: "Path | None" = None,
) -> str:
    """Resolve the effective AGENT-scope ``bootstrap`` behavior value for a box.

    ``bootstrap`` is an agent-scope behavior key (spec §2d
    ``agent.default.bootstrap | tmux``), resolved off the SAME KeyStore snapshot
    pipeline the launch reads for the other agent behavior scalars (``model`` /
    ``access`` / ``allow_helpers``): a focused ``build_launch_snapshot`` over
    the scope settings FILES (system / workset / box) + the per-agent file's flat
    state, then :func:`~kanibako.settings.settings_launch.effective_behavior`'s §2d
    active-over-default pick.  There is NO derived-on-disk value — the keystore is
    the sole intermediary ([[settings-must-map-to-keystore-key]]).

    *agent_id* is the launch-resolved active node-name (``"general"`` for a
    no-agent / shell box, so the ``agent.default`` backstop still applies).
    *agent_path* is the active agent's OWN settings file (``agents/<node>/
    settings.yaml``) so a per-agent ``config set agent.<agent>.bootstrap`` override
    is honored; ``None`` skips it (the scope-file cascade still resolves).

    Returns the resolved program name, or the consumer default ``tmux``
    (:data:`_BOOTSTRAP_DEFAULT`) when no scope sets ``bootstrap`` — byte-identical
    to the retired ``box.bootstrap_program or "tmux"`` coercion for the default case.
    """
    from kanibako.settings import settings_launch
    from kanibako.settings.paths import host_xdg_map
    from kanibako.settings.settings_resolve import ResolveCtx

    ctx = ResolveCtx(
        agent_name=agent_id,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=host_xdg_map(),
    )
    # The per-agent file's FLAT behavior state (agent.<active>.* slot) — the shape
    # ``effective_behavior`` reads for a per-agent override.  Absent file → empty.
    agent_state: "dict[str, str] | None" = None
    if agent_path is not None and Path(agent_path).exists():
        try:
            agent_state = dict(load_agent_config(agent_path).state)
        except Exception:
            agent_state = None
    _bootstrap_box_path, _bootstrap_ws_path = box_workset_settings_paths(proj)
    snapshot = settings_launch.build_launch_snapshot(
        agent_name=agent_id,
        ctx=ctx,
        system_path=system_settings_path,
        agent_path=None,
        workset_path=_bootstrap_ws_path,
        box_path=_bootstrap_box_path,
        # Seed the behavior FLOOR with just ``bootstrap`` (→ agent.default.bootstrap
        # = tmux) so the snapshot's ``agent`` node ALWAYS exists.  Without it, a box
        # whose SOLE agent-scope setting is the ``box.agent.bootstrap`` mirror (e.g.
        # ``=none`` for a one-off ephemeral box) has NO ``agent`` node, so
        # ``effective_behavior`` early-returns ``{}`` BEFORE consulting the box.agent
        # mirror — silently dropping the override (the regression the retired
        # ``box.bootstrap_program`` did not have).  Unlike ``model``'s read, this
        # focused snapshot has no descriptor floor, so it must floor ``bootstrap``
        # itself.  ``keys=["bootstrap"]`` below extracts ONLY bootstrap, so flooring
        # it has no effect on any other behavior key.
        behavior_floor={"bootstrap": _BOOTSTRAP_DEFAULT},
        agent_state=agent_state,
    )
    value = settings_launch.effective_behavior(
        snapshot, active_agent=agent_id, keys=["bootstrap"],
    ).get("bootstrap")
    return value if value else _BOOTSTRAP_DEFAULT


def _resolve_bootstrap_program(
    project_dir: str | None = None, explicit_agent: str | None = None,
) -> str:
    """Resolve the AGENT-scope ``bootstrap`` program for the host-side default-mode
    persistence heuristic in ``run_start``.

    ``bootstrap`` relocated from the retired box-scope ``box.bootstrap_program`` to
    the agent scope (spec §2d), so the persistence-mode default decision now
    needs the box's RESOLVED agent + its agent-scope ``bootstrap`` value.  Resolves
    them here WITHOUT side effects (``resolve_box_target(initialize=False)``) and
    reads the effective value off the settings snapshot via :func:`_effective_bootstrap`.

    FAIL-SOFT: any resolution failure (unresolvable box, ambiguous/uninstalled agent
    — those raise their own typed errors from ``_run_container`` moments later) falls
    back to the ``tmux`` default, so this cheap pre-flight never itself aborts the
    launch.  ``_run_container`` re-resolves the authoritative value the same way.
    """
    try:
        config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        config = load_config(config_file)
        std = load_std_paths(config)
        system_settings_path = std.settings
        # No side effects: resolve the box's PATHS only (no mkdir / register).
        proj = resolve_box_target(
            std, config, project_dir,
            initialize=False, register=False, warn=False,
        )
        from kanibako.settings.agent_select import select_agent

        _sel = select_agent(
            std=std, proj=proj, explicit_agent=explicit_agent,
        )
        agent_name = _sel.node
        # NODE-name (persona identity); the harness keys the target/plugin. The
        # translator is REQUIRED here too (see the seam note in _run_container): a
        # SUPPRESSED box has no node, and handing "" to resolve_target would
        # auto-detect an agent and read ITS bootstrap for a plain-shell box.
        target = (
            resolve_target(harness_of(agent_name), proj.project_path)
            if _sel.has_agent
            else None
        )
        agent_id = with_harness(agent_name, target.name) if target else "general"
        return _effective_bootstrap(
            proj, system_settings_path, agent_id,
            agent_path=agent_settings_path(std.agents, agent_id),
        )
    except Exception:
        return _BOOTSTRAP_DEFAULT


def _bootstrap_available(program: str = "tmux") -> bool:
    """Check if the host-side bootstrap program is installed.

    Used to decide the default persistence mode (persistent only when the
    bootstrap program is present on the host, since reattach shells out to it).
    """
    return shutil.which(program) is not None


def _check_box_components(proj) -> str | None:
    """D5 CRITICAL integrity gate: verify a resolved box's REQUIRED host-side
    components exist before launch.  Returns an error message when one is
    missing (the caller aborts with a non-zero exit), else ``None``.

    Two components are checked here, at LAUNCH:

    * **workspace** (``proj.project_path``) — the load-bearing check.  For a
      registered / externally-connected box the workspace is a dir recorded in a
      registry, so it can go stale independently of the box tree (the box was
      moved) and MUST be verified to exist.  (For a PRIMARY box the resolver's
      own create-time guard already rejects a missing workspace before this
      point; this is the uniform launch backstop that also catches the external
      case the resolver does not.)
    * **home** (``proj.shell_path``) — the ``/home/agent`` mount source; a box
      cannot run without it.

    This is a LAUNCH-time gate (NOT the shared ``resolve_box_target``
    chokepoint) so a ``box list`` / ``archive`` / ``diagnose`` of a MOVED box
    never hard-crashes — only an actual launch, which is about to mount these
    dirs, refuses.

    The **settings-file marker** (the third CRITICAL component per D5) is NOT
    re-checked here: its absence is already handled at resolution/detection time
    (``box_resolve.standalone_settings_present`` requires the box
    ``settings.yaml`` for a standalone to be recognised as a box at all; the
    read-side ``box_resolve`` returns ``None`` = "not a box").  A launch resolve
    (``initialize=True``) would recreate a fresh marker, so a marker check here
    would be dead.  The **vault** (NON-CRITICAL) only WARNS, at resolve time,
    via ``paths._flag_missing_vault`` — never here.
    """
    if not proj.project_path.is_dir():
        return (
            f"Error: the workspace for box '{proj.name or proj.project_path}' is "
            f"missing ({proj.project_path}).\n"
            "  Kanibako will not launch a box with no workspace.\n"
            "  If you moved it, re-create or remap the box."
        )
    if not proj.shell_path.is_dir():
        return (
            f"Error: the home directory for box "
            f"'{proj.name or proj.project_path}' is missing "
            f"({proj.shell_path}).\n"
            "  Kanibako cannot launch the box without it."
        )
    return None


def _resolve_existing_box(
    std: StandardPaths, config: KanibakoConfig, project_dir: str | None,
) -> ProjectPaths | None:
    """Non-materialising probe: the :class:`ProjectPaths` of an EXISTING
    registered box for *project_dir*, or ``None`` when no box exists there.

    Explicit-create gate (Jei 2026-07-11g): a launch (``start`` / bare
    ``kanibako`` / ``code`` / ``shell``) NEVER auto-creates a box — the box must
    already have been made by ``kanibako create``.  We resolve through the SAME
    ``resolve_box_target`` the launch uses, but:

    * ``initialize=False`` — a NON-materialising resolve (no mkdir / no name
      assignment / no seed): probing must never leave a box dir behind.
    * ``register=True`` — the register=False journal-recovery arm is NOT
      consulted, so a half-created box that crashed BEFORE its registry write
      reads as "no box" here (its dir/journal-entry do not resurrect it on a
      launch).  Forward-recovery of an interrupted create belongs to ``create``
      alone (re-running ``create`` completes it); the launch path must treat a
      not-fully-registered box as "no box" → error.  ⚑ This holds for PRIMARY and
      NAMED (registration IS the signal); STANDALONE is the EXCEPTION — its
      existence is a disk-marker + ``detect_project_mode``'s ``import_standalone``
      self-heal, which re-registers a half-created standalone box (and clobbers its
      create-journal entry) during the resolve, so a half-created STANDALONE box
      reads as "exists" here and launches.  That is a PRE-EXISTING import-reconcile
      interaction (not introduced by this gate) — see the explicit-create follow-up.
    * ``warn=False`` — a pure probe never doubles the non-conforming-name flag.

    REGISTRATION is the existence signal: a resolved box carries a non-empty
    ``name`` exactly when it is registered (PRIMARY membership / STANDALONE
    registry / NAMED workset membership), which is precisely "fully created".
    A bare token naming no registered box makes ``resolve_box_target`` refuse to
    path-ify it under ``initialize=False`` (it raises :class:`ProjectError`),
    which we also read as "no box".
    """
    try:
        proj = resolve_box_target(
            std, config, project_dir,
            initialize=False, register=True, warn=False,
        )
    except ProjectError:
        # A bare token that names no registered box (initialize=False refuses to
        # invent a cwd-relative path for it) or a path that does not exist → no
        # box to launch.  (WorksetError — a bare-workset / in-workset-but-not-a-
        # project spec — is intentionally NOT caught here: it carries its own
        # actionable message and must surface unchanged.)
        return None
    return proj if proj.name else None


def _no_box_error(project_dir: str | None) -> str:
    """The launch-time "no box; run create" error for an ABSENT box target.

    ``<path>`` is the resolved target we looked for a box at; the suggested
    ``create`` carries the user's own spec so it is copy-pasteable — an explicit
    ``kanibako create <spec>`` when they named a project, a bare ``kanibako
    create`` from inside the project dir (no spec).
    """
    if project_dir:
        # A path that resolves on disk shows its resolved dir; a bare NAME (no
        # such path) shows the spec verbatim — either way copy-pasteable.
        candidate = Path(project_dir)
        target = str(candidate.resolve()) if candidate.exists() else project_dir
        suggest = f"kanibako create {project_dir}"
    else:
        target = os.getcwd()
        suggest = "kanibako create"
    return f"Error: no box at {target}. To create a new box, run '{suggest}'"


# Sentinel returned by _check_launch_baseline when the launch-critical bootstrap
# program is missing from the image (tier-1 hard-stop).
_BOOTSTRAP_MISSING = object()


def _launch_issues_path(std, container_name: str) -> Path:
    """State-file path for a box's tier-2 launch warnings.

    Uses XDG_STATE (``$XDG_STATE_HOME/kanibako/launch-issues.<box>``) so the
    warnings survive the bootstrap session and can be reprinted on exit.
    """
    state_home = xdg("XDG_STATE_HOME", ".local/state")
    return state_home / "kanibako" / f"launch-issues.{container_name}"


def _check_launch_baseline(runtime, image, bootstrap_program, container_name, std):
    """Run the two-tier baseline probe against *image* before launch.

    Performs ONE ephemeral probe covering the bootstrap program (tier 1) plus
    every baseline executable (tier 2).

    * Tier 1 — if the bootstrap program is missing, prints a hard-stop message
      (noting a shell is still reachable to investigate) and returns
      :data:`_BOOTSTRAP_MISSING`.  SKIPPED entirely when *bootstrap_program* is
      the ``none`` opt-out sentinel: no bootstrap exe is probed and no hard stop
      can fire (there is no bootstrap program to be missing).
    * Tier 2 — returns the list of ``(package, executable)`` pairs whose
      executable is missing.  These are WARN-only: they are persisted to the
      box's launch-issues state file and surfaced after the session closes.
      Tier 2 runs regardless of the ``none`` sentinel.
    """
    from kanibako.runtime import baseline as baseline_mod

    pairs = baseline_mod.executables()  # [(pkg, exe), ...]
    baseline_exes = [exe for _pkg, exe in pairs]
    exe_to_pkg = {exe: pkg for pkg, exe in pairs}

    # `none` opt-out: no bootstrap exe to probe, no tier-1 hard stop — only the
    # tier-2 baseline sweep runs.
    probe_bootstrap = not _is_no_bootstrap(bootstrap_program)

    # One probe for bootstrap (unless `none`) + all baseline exes (dedup,
    # bootstrap first).
    probe_exes: list[str] = [bootstrap_program] if probe_bootstrap else []
    for exe in baseline_exes:
        if exe not in probe_exes:
            probe_exes.append(exe)
    missing = set(probe_missing_executables(runtime, image, probe_exes))

    # TIER 1: bootstrap program (skipped for the `none` opt-out).
    if probe_bootstrap and bootstrap_program in missing:
        print(
            f"Error: the bootstrap program '{bootstrap_program}' is not "
            f"installed in image '{image}'.\n"
            f"  Kanibako cannot start the interactive session without it.\n"
            f"  A shell IS still available to investigate, e.g.:\n"
            f"      {runtime.cmd} run --rm -it {image} bash\n"
            f"  or, once a box exists:  kanibako shell\n"
            f"  Install it in the image or set 'agent.default.bootstrap' to an "
            f"installed program.",
            file=sys.stderr,
        )
        return _BOOTSTRAP_MISSING

    # TIER 2: rest of the baseline (warn only).
    tier2 = [
        (exe_to_pkg.get(exe, "?"), exe)
        for exe in baseline_exes
        if exe in missing
    ]

    issues_path = _launch_issues_path(std, container_name)
    try:
        if tier2:
            issues_path.parent.mkdir(parents=True, exist_ok=True)
            issues_path.write_text(
                "\n".join(f"{pkg}: {exe}" for pkg, exe in tier2) + "\n"
            )
            # The warning is surfaced exactly once, after the session closes, by
            # _print_launch_issues (which also covers the reattach path); no
            # pre-launch print (the alt-screen wipes it and it would multiply on
            # the first-launch retry).
        else:
            # Clear any stale issues from a previous launch.
            issues_path.unlink(missing_ok=True)
    except OSError:
        pass  # best-effort; never block launch on the state file.

    return tier2


def _print_launch_issues(std, container_name: str) -> None:
    """Reprint a box's persisted tier-2 launch warnings (post-session)."""
    issues_path = _launch_issues_path(std, container_name)
    try:
        text = issues_path.read_text().strip()
    except OSError:
        return
    if not text:
        return
    print(
        "\nNote: this box's image is missing baseline tools "
        f"(from {issues_path}):",
        file=sys.stderr,
    )
    for line in text.splitlines():
        print(f"  - {line}", file=sys.stderr)
    print(
        "  Run 'kanibako rig diagnose' to recheck, or rebuild/update the image.",
        file=sys.stderr,
    )


def _shadow_issues_path(std, container_name: str) -> Path:
    """State-file path for a box's bind-shadow warnings.

    Mirrors :func:`_launch_issues_path` (same XDG_STATE call) so the warnings
    survive the bootstrap session and can be reprinted on exit.
    """
    state_home = xdg("XDG_STATE_HOME", ".local/state")
    return state_home / "kanibako" / f"launch-shadows.{container_name}"


def _persist_shadow_issues(std, container_name: str, shadowed: list[str]) -> None:
    """Persist bind-shadow warnings for *container_name*.

    When *shadowed* is non-empty the dests are written one-per-line to the
    state file; the warning itself is surfaced exactly once, after the session
    closes, by :func:`_print_shadow_issues` (which also covers the reattach
    path).  An empty list clears any stale state from a prior launch.  All file
    ops are best-effort.
    """
    issues_path = _shadow_issues_path(std, container_name)
    try:
        if shadowed:
            issues_path.parent.mkdir(parents=True, exist_ok=True)
            issues_path.write_text("\n".join(shadowed) + "\n")
        else:
            issues_path.unlink(missing_ok=True)
    except OSError:
        pass  # best-effort; never block launch on the state file.


def _print_shadow_issues(std, container_name: str) -> None:
    """Reprint a box's persisted bind-shadow warnings (post-session)."""
    issues_path = _shadow_issues_path(std, container_name)
    try:
        text = issues_path.read_text().strip()
    except OSError:
        return
    if not text:
        return
    print(
        "\nNote: some mounts shadow pre-existing files in this box's home "
        f"(from {issues_path}):",
        file=sys.stderr,
    )
    for line in text.splitlines():
        print(f"  - {line}", file=sys.stderr)


def _bootstrap_wrap(program: str, inner_cmd: str, cli_args: list[str]) -> tuple[str, list[str]]:
    """Build the (entrypoint, args) that launches *inner_cmd* under *program*.

    For tmux (the default) this preserves the persistent-session shape
    ``tmux new-session -s kanibako -- <inner_cmd> <cli_args...>``.  For any other
    bootstrap program the contract is intentionally MINIMAL/best-effort: the
    program is exec'd with the inner command and its args appended
    (``<program> <inner_cmd> <cli_args...>``); a non-tmux program is responsible
    for whatever session/multiplexing semantics it wants.
    """
    if program == "tmux":
        args = ["new-session", "-s", "kanibako", "--", inner_cmd, *cli_args]
        return "tmux", args
    return program, [inner_cmd, *cli_args]


def _env_flag_enabled(value: str | None) -> bool:
    """True iff *value* is a recognised truthy env-flag string (case-insensitive).

    Accepts ``1`` / ``true`` / ``yes`` / ``on`` (trimmed, any case) as enabled;
    everything else — including ``None``, empty, ``0``, ``false`` — is disabled.  Used
    to gate the experimental ``KANIBAKO_SESSION_TAKEOVER`` (4b) so the default (unset)
    stays OFF.
    """
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_supervisor_pid1(
    supervisor_argv: list[str], fallback_argv: list[str],
) -> tuple[str, list[str]]:
    """Compose the PID-1 ``(entrypoint, args)`` for a DETACHED agent box (E2b).

    The always-on supervisor (:mod:`kanibako.box_supervisor`) runs the agent in a
    detached tmux session and self-heals it, so a detached AGENT box makes the
    supervisor PID-1 instead of a bare-shell keep-alive.  Forward-compat (design
    §221-225): an OLD box image may ship a kanibako WITHOUT the supervisor module,
    so PID-1 is an import-GATED ``sh -c`` — it ``exec``s the supervisor only when
    ``import kanibako.box_supervisor`` succeeds, else ``exec``s the *fallback*
    bare-shell keep-alive (today's exact behavior), degrading gracefully.

    *supervisor_argv* and *fallback_argv* are FULL argv lists (the program at index
    0); both are ``shlex``-quoted into the single ``sh -c`` script so agent args
    carrying spaces / quotes survive intact through the nested shell.  Returns
    ``("sh", ["-c", <script>])``.

    Both the import PROBE and the supervisor ``exec`` run with the HOST kanibako
    (bind-mounted read-only at :data:`KANIBAKO_PKG_MOUNT_ROOT` by
    :func:`_kanibako_mounts`) FIRST on ``sys.path`` via an injected ``PYTHONPATH`` —
    so the supervisor that runs is the FRESH host version (== the host CLI), never
    the image's baked copy.  Published images ship an OLD kanibako WITHOUT the
    supervisor module, so probing/exec'ing the baked copy would silently degrade
    every real launch to the bare-shell fallback (design §221-225).  ``PYTHONPATH``
    is PREPENDED (``${PYTHONPATH:+:$PYTHONPATH}``), never clobbered, so any value the
    image sets survives after our entry; the supervisor scrubs its OWN mount-root
    entry back out before spawning the agent/tmux children
    (:func:`kanibako.box_supervisor.scrub_bootstrap_pythonpath`).  The FALLBACK
    ``exec`` is left UNCHANGED (no PYTHONPATH): it is a bare-shell keep-alive that
    never imports kanibako, and runs only when even the host supervisor import fails.
    The whole ``PYTHONPATH=…`` assignment is DOUBLE-QUOTED so the shell still expands
    ``${PYTHONPATH:+…}`` but the RESULT is never word-split or glob-expanded — an image
    whose own PYTHONPATH held a space or ``*`` would otherwise split the env assignment
    into extra ``env`` operands (``env`` treats the tail as a command and fails),
    silently degrading the probe to the bare-shell fallback.  The
    ``KANIBAKO_PKG_MOUNT_ROOT`` literal is itself a fixed, shell-safe path; the argv
    lists stay ``shlex``-quoted verbatim after each ``exec``.
    """
    pythonpath = f'"PYTHONPATH={KANIBAKO_PKG_MOUNT_ROOT}${{PYTHONPATH:+:$PYTHONPATH}}"'
    probe = shlex.join(["python3", "-c", "import kanibako.box_supervisor"])
    script = (
        f"env {pythonpath} {probe} 2>/dev/null "
        f"&& exec env {pythonpath} {shlex.join(supervisor_argv)} "
        f"|| exec {shlex.join(fallback_argv)}"
    )
    return "sh", ["-c", script]


def _bootstrap_attach(program: str) -> list[str]:
    """Build the in-container command that re-attaches to the bootstrap session.

    tmux uses ``tmux attach -t kanibako``; for a non-tmux bootstrap (which has
    no kanibako-named session contract) we fall back to exec'ing the program
    bare (best-effort) — non-tmux reattach is not a guaranteed feature.
    """
    if program == "tmux":
        return ["tmux", "attach", "-t", "kanibako"]
    return [program]


def _apply_tweakcc(install, agent_cfg, cache_path, image, runtime_cmd, logger):
    """Apply tweakcc patching if enabled in agent config.

    Patching runs inside a throwaway container (``podman run --rm``) using
    the same image that will be used for the agent.  The patched binary is
    cached on disk with flock-based reference counting.

    Returns ``(patched_install, cache_entry, cache)`` on success, or
    *None* if tweakcc is disabled or patching fails (graceful fallback).
    """
    from kanibako.bun_sea import BunSEAError, cli_js_hash
    from kanibako.targets.base import AgentInstall
    from kanibako.tweakcc import build_merged_config, resolve_tweakcc_config, write_merged_config
    from kanibako.tweakcc_cache import TweakccCache, TweakccCacheError, config_hash

    tweakcc_cfg = resolve_tweakcc_config(agent_cfg.transform_settings)
    if not tweakcc_cfg.enabled:
        return None

    try:
        merged_config = build_merged_config(tweakcc_cfg)
        bin_hash = cli_js_hash(install.binary)
        cfg_hash = config_hash(merged_config)

        cache_dir = cache_path / "tweakcc"
        cache = TweakccCache(cache_dir)
        key = cache.cache_key(bin_hash, cfg_hash)

        entry = cache.get(key)
        if entry is None:
            # Write merged config to cache dir (will be mounted into container)
            config_file = cache_dir / f".config-{key}.json"
            write_merged_config(merged_config, config_file)

            def patch_fn(staging_dir, staging_binary):
                """Run tweakcc --apply inside a throwaway container."""
                cmd = [
                    runtime_cmd, "run", "--rm", "--network=none",
                    "-v", f"{staging_dir}:/work:rw",
                    "-v", f"{config_file}:/root/.tweakcc/config.json:ro",
                    "-e", f"TWEAKCC_CC_INSTALLATION_PATH=/work/{staging_binary.name}",
                    image,
                    "tweakcc", "--apply",
                ]
                logger.debug("Running tweakcc via container: %s", cmd)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    raise TweakccCacheError(
                        f"tweakcc container failed (exit {result.returncode}): "
                        f"{result.stderr.strip()}"
                    )

            entry = cache.put(key, install.binary, patch_fn)
            logger.info("Patched binary cached: %s", key)
        else:
            logger.info("Using cached patched binary: %s", key)

        patched_install = AgentInstall(
            name=install.name,
            binary=entry.path,
            install_dir=install.install_dir,
        )
        return patched_install, entry, cache

    except (BunSEAError, TweakccCacheError) as exc:
        logger.warning(
            "tweakcc patching failed, using unpatched binary: %s", exc,
        )
        return None


def _parse_cli_env(cli_env: list[str] | None) -> dict[str, str]:
    """Parse ``-e/--env KEY=VALUE`` items into a dict (ignores malformed ones)."""
    env: dict[str, str] = {}
    for item in cli_env or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def _refuse_retired_behavior(
    *, proj, agent_id, system_settings_path, agent_cfg_path,
) -> None:
    """Refuse a RETIRED behavior spelling found in ANY cascade file (R-41/RQ-2).

    The BEHAVIOR-tier twin of the selection-seam refusal in
    :func:`kanibako.settings.agent_select.select_agent`: same shape, same
    "refuse rather than run" rule, different key set
    (:data:`~kanibako.settings.settings_assemble.RETIRED_BEHAVIOR_KEYS` —
    today just ``auto_approve`` → ``access``).

    EVERY tier the cascade reads is checked, in cascade order so the OUTERMOST
    stale value is reported first (fix-one-then-see-the-next, like every other
    config error here):

    * ``base`` / ``system`` / ``workset`` / ``box`` — the scope settings files,
      where the key sits under ``agent.<sub>`` or a ``pref.agent.<node>``
      request;
    * ``agent`` — the ACTIVE agent's own file, where it sits FLAT under
      ``self``. This tier is NOT in the selection refusal's list (agent
      SELECTION cannot live in an agent file) and it is exactly where
      ``crab set <agent> auto_approve=…`` used to write, so omitting it would
      leave the commonest stale spelling silent.

    Raises :class:`~kanibako.settings.settings_resolve.SettingsError`; the
    launch stops.
    """
    from kanibako.settings.config import settings_base_path
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.paths import box_workset_settings_paths
    from kanibako.settings.settings_assemble import refuse_retired_behavior_keys

    box_path, workset_path = box_workset_settings_paths(proj)
    subject = agent_id if agent_id and agent_id != "general" else None
    for level, path in (
        ("base", settings_base_path()),
        ("system", system_settings_path),
        ("agent", agent_cfg_path),
        ("workset", workset_path),
        ("box", box_path),
    ):
        if path is not None and Path(path).exists():
            refuse_retired_behavior_keys(
                load_doc(Path(path)), level=level, path=Path(path),
                subject=subject,
            )


def _deliver_panel_permissions(
    *,
    target,
    proj,
    access,
    provider,
    logger,
):
    """Best-effort panel / SessionStart-directive permission delivery.

    Pure side-effects.  The two ``Target`` delivery seams are called
    UNCONDITIONALLY for every agent — no name-branching in core (T1.1/T1.2);
    an agent without a behavior inherits the base no-op.  Each seam call is
    independently best-effort and never blocks the launch.

    Both deliveries key on the box's CASCADE-resolved ``access`` TIER (resolved
    just above at the call site), NOT the per-launch ``-S``/``-A`` flags: the VS
    Code panels spawn their OWN in-box agents without kanibako's launch
    env/flags, so they see only what is persisted onto each agent's native
    config surface — the panel reflects the box's configured tier, not a
    transient launch flag (spec §1A's projected-surface exception).

    ⚑ *access* is one of ``restricted`` / ``editing`` / ``full`` and has ALREADY
    been checked against this agent's descriptor rows by the caller: the
    per-delivery ``except`` below is BEST-EFFORT and would SWALLOW an
    unrenderable-tier refusal, so that refusal must happen (and does) BEFORE this
    function is reached. Nothing here may fall back to a permissive default.

    What lands where is each plugin's knowledge (see the
    ``Target.deliver_panel_permissions`` / ``deliver_directive_hook``
    docstrings and the plugin implementations); ``proj.shell_path`` is the box
    home as seen from the host — the one root every surface lives under.

    ORDER is load-bearing: panel permissions FIRST, directive hook SECOND
    (claude's two writes hit the same ``settings.json``; codex's approval keys
    land before the managed region write — the byte-order the golden fixtures
    freeze).  *provider* is the launch's resolved persona model-provider
    (``None`` for bare / non-persona ⇒ byte-identical write); this call site is
    reached whenever a box is (re)started — first launch after create and start
    of a stopped box — so the provider block re-materialises to the current
    persona config.  (A reattach to an ALREADY-running box early-returns above
    and does not re-deliver — a config change made while the box ran is NOT
    picked up until restart, so that path prints ``reattach_config_notice``
    instead of rewriting the live file; reconciled on the next start.)
    """
    if target is None:
        return
    try:
        target.deliver_panel_permissions(
            config_root=proj.shell_path,
            access=access,
        )
    except Exception:
        # ⚑ WARNING, not debug (R-41): this delivery is the PERMISSION axis. A
        # failure means the panel keeps whatever was persisted before — which for
        # an agent whose own default is permissive (goose's unset GOOSE_MODE is
        # ``auto``) is MORE permissive than the box asked for. Still best-effort
        # (the launch is never blocked, per the seam contract), but never silent.
        logger.warning(
            "could not deliver the '%s' permission tier to %s's panel config "
            "surface; the panel keeps its previous permissions (run with -v for "
            "the traceback)",
            access, target.name,
        )
        logger.debug(
            "failed to deliver %s panel permissions",
            target.name,
            exc_info=True,
        )
    try:
        target.deliver_directive_hook(
            config_root=proj.shell_path,
            access=access,
            model_provider=provider,
        )
    except Exception:
        logger.debug(
            "failed to seed %s SessionStart directive hook",
            target.name,
            exc_info=True,
        )


def _assemble_image_sharing_mounts(
    *,
    merged,
    proj,
    runtime,
    std,
    agent_id,
    system_settings_path,
    agent_cfg_path,
    auth_src,
    extra_mounts,
    logger,
):
    """Append host image-storage share mounts to ``extra_mounts`` (conditional).

    Extracted verbatim from ``_run_container``; extends the passed
    ``extra_mounts`` list in place exactly as the inline block did.

    ⮕ B6: the gate reads ``merged.box_share_images`` ALONE — the ``--share-images``
    flag already rides the §1A CLI level inside the merged-config resolve, so a
    separate flag disjunct here would be a second mechanism for the same value.

    ⮕ 11a (2026-08-02): the PROBE feeds ONLY the DEFAULT of ``box.images_store``;
    the RESOLVED key (any tier) feeds the bind AND the storage.conf delivery.  A
    failed probe therefore no longer skips sharing on its own — a SET
    ``box.images_store`` still shares; probe-fail + no set value skips WITH a
    warning naming both the cause and the cure.
    """
    if merged.box_share_images:
        from kanibako.runtime.image_sharing import (
            detect_graph_root,
            write_storage_conf,
        )
        from kanibako.settings.settings_store import KeyStore

        graph_root = detect_graph_root(runtime.cmd)
        staging = proj.metadata_path / ".image-sharing"
        # The GENERATED storage.conf (spec D-M8): written up front so the
        # reconcile's skip-if-missing sees a real source; its box DELIVERY
        # follows the resolved store below (no store → nothing emitted).
        storage_conf_path = write_storage_conf(staging)
        # Late, CONDITIONAL resolve through the same snapshot pipeline,
        # carrying ONLY the image table (include_base_families=False) — its
        # box_dests are disjoint from the main reconcile, so a separate
        # reconcile is byte-for-byte equivalent.
        _img_snap, _img_rec = _resolve_launch_snapshot(
            std=std,
            proj=proj,
            agent_name=agent_id,
            system_settings_path=system_settings_path,
            agent_cfg_path=agent_cfg_path,
            desc=None,
            install=None,
            target=None,
            graph_root=graph_root,
            storage_conf_path=storage_conf_path,
            deliver_creds=auth_src.creds_shared,
            include_base_families=False,
        )
        # The RESOLVED store (probed default OR a set tier value) gates the
        # emit — read off the snapshot, the PRIMARY SOURCE, with the UNBOUND
        # dict.get protocol (S3; the config._resolve_box_scalars walk).
        node: object = _img_snap
        for seg in ("box", "images_store"):
            node = dict.get(node, seg) if isinstance(node, KeyStore) else None
        resolved_store = node if isinstance(node, str) and node else None
        if resolved_store is not None:
            img_mounts = _emit_category_mounts(_img_rec, label="images")
            extra_mounts.extend(img_mounts)
            logger.info("Image sharing enabled: %d mounts added", len(img_mounts))
        else:
            print(
                "Warning: image sharing is enabled (box.share_images) but "
                "the host image store probe failed and no box.images_store "
                "is set. Continuing without image sharing. To share images, "
                "set box.images_store to the host store path or fix the "
                "podman storage probe.",
                file=sys.stderr,
            )


def _assemble_launch_env(
    *,
    std,
    proj,
    agent_cfg,
    reconciled,
    state_env,
    cli_env,
    target,
    agent_id,
    extra_mounts,
    logger,
):
    """Assemble the container environment + secret export list for the launch.

    Extracted verbatim from ``_run_container``.  Extends ``extra_mounts`` with
    the secret mounts in place (exactly where the inline block did) and returns
    the assembled ``container_env`` plus the ``secret_export_vars`` list.
    """
    # Read environment variables, accumulating across config levels with
    # the settings-framework precedence (low->high): system < agent <
    # workset < box.  Target-derived state env and per-run CLI -e env stay
    # above all config levels.
    global_env_path = std.data_path / "env"
    project_env_path = proj.metadata_path / "env"
    # Workset-level env for named AND primary worksets (F9): the primary's
    # tier file lives under @config.primary_workset, distinct from the
    # system tier's @config.data/env (pre-F4 the two aliased, so the
    # default group was skipped here).
    ws_env_path = workset_env_path(proj.group)
    container_env = _build_config_env(
        global_env_path, agent_cfg.env, ws_env_path, project_env_path,
    )
    # Settings-framework env (the `<scope>.env.<VAR>` category) supersedes
    # the retired `.env` files (Phase 2 decision E).  reconcile (the single
    # launch reconcile above) picked the most-specific scope per VAR
    # (system<agent<workset<box), so applying its ENV winners over the legacy
    # map is the documented config-level precedence.  Each ENV entry carries
    # the VAR name in ``box_dest`` and the resolved value in ``options``.  It
    # stays BELOW target state env and CLI -e.  ADDITIVE: with no `env.*` keys
    # configured the reconciled env set is empty -> byte-identical.
    container_env.update(
        {e.box_dest: e.options for e in reconciled.envs}
    )
    # SECRET category (spec §2a secret_path, 2026-07-06): the resolved
    # ``secret_path.<VAR>`` winners (any scope — agent/box/workset/system) are
    # delivered ARM'S-LENGTH — each host PATH is ro-bind-mounted to
    # SECRET_MOUNT_DIR/<VAR> and exported IN-BOX by a shim at agent start.
    # kanibako NEVER reads the file VALUE (it is never in container_env, never on
    # the podman argv, never in the snapshot/keystore/logs) — this REPLACES the
    # retired env-value read that pulled the secret into our memory + onto the
    # argv. Missing/unreadable/empty host file -> WARN + VAR unset (fail-soft).
    # ``secret_export_vars`` drives the box-side export shim below; an EMPTY list
    # means NO shim (a box with no secrets keeps the bare entrypoint byte-identical).
    secret_mounts, secret_export_vars = _emit_secret_mounts(reconciled, logger)
    extra_mounts.extend(secret_mounts)
    container_env.update(state_env)                        # target-derived state env

    # Merge per-run -e/--env KEY=VALUE vars (highest priority).
    container_env.update(_parse_cli_env(cli_env))

    # NOTE: Claude Code telemetry (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)
    # and the auto-updater disable (DISABLE_AUTOUPDATER) are now carried by
    # claude's descriptor.container_env, merged via state_env above.  The
    # former core `target.name == "claude"` special-case has been removed.

    # Inject instance identity for peer communication.
    if proj.name:
        container_env["KANIBAKO_NAME"] = proj.name

    # Instruction-delivery SEED (increment 2b): the box-absolute path to the
    # kickoff SEED, RO-bound at ~/.config/kanibako/kickoff.md.  The
    # SessionStart hooks (claude/codex) and the goose launch-flatten shim
    # reference this to flatten the ``@import`` directive chain into the
    # agent's native slot.  ABSOLUTE (not ``~``) so it resolves identically in
    # a hook shell and at exec time.  Global (agent-independent): the per-agent
    # FINAL-slot path arrives via each descriptor's KANIBAKO_DIRECTIVE_FINAL.
    # ⚑ READ BACK from the ONE declaration of the slot (``core-defaults.yaml``
    # ``kickoff.box_dest``, the source of core's own bind) rather than spelled a
    # second time here: the env var and the bind MUST name the same file, and the
    # kickoff is core-owned as of C-CANON R2 (P-5), so core has one spelling of it.
    container_env["KANIBAKO_DIRECTIVE_SEED"] = core_defaults.kickoff_guest_dest()

    # Stamp the resolved agent ON THE CONTAINER (NOT durable config — keeps
    # `--agent` ephemeral).  On a later `kanibako start` against this running
    # persistent box, the reattach fast-source reads this back so it can
    # refresh creds + attach without re-running the resolution cascade (which
    # would otherwise Gate-2a when there are 2+ agents and no default).  Only
    # stamped for a real agent launch; no-agent/shell launches (target is
    # None) carry no agent, so the var is left unset.
    if target is not None:
        # Stamp the NODE-name (full persona identity), NOT the harness
        # (``target.name``): the reattach fast-source + stop writeback read
        # this back and derive the harness via ``harness_of`` where a target
        # is needed. Bare node == harness == target.name (byte-identical).
        container_env["KANIBAKO_AGENT"] = agent_id

    # E2g / increment 4a: seed the per-agent liveness MARKERS DIR so every agent
    # session's start hook writes a per-PID marker ``<dir>/$PPID`` there; the
    # supervisor reads the SAME dir via --agent-markers-dir.  Seeded UNCONDITIONALLY
    # (not just warm-up): the E2b/E2c supervised-agent path (`kanibako start
    # [--detach]`) now ALSO enumerates the dir so run_forever detects a panel
    # newcomer (increment 4a), and the warm-up panel-watch path enumerates it for
    # panel-agent liveness.  Harmless for a non-claude/no-agent box (no marker hook
    # is seeded, so nothing writes it).
    container_env["KANIBAKO_AGENT_MARKERS_DIR"] = AGENT_MARKERS_DIR
    return container_env, secret_export_vars


def _start_helper_hub(
    *,
    runtime,
    image,
    container_name,
    proj,
    target,
    install,
    binary_mnts,
    tweakcc_entry,
    std,
    container_env,
    entrypoint,
    box_shell,
    agent_id,
    system_settings_path,
    agent_cfg_path,
    auth_src,
    extra_mounts,
):
    """Start the helper hub + append its mounts; return the started hub.

    Extracted verbatim from the ``if helpers_enabled:`` block of
    ``_run_container``.  Extends ``extra_mounts`` in place exactly as the inline
    block did and returns the started ``HelperHub``.
    """
    from kanibako.channels.helper_listener import HelperContext, HelperHub, MessageLog
    from kanibako.targets.base import Mount as _HMount

    # Socket must live in a short path to stay under the AF_UNIX
    # ``sun_path`` limit.  ``std.runtime`` is ``$XDG_RUNTIME_DIR/kanibako``
    # resolved through the hardened XDG resolver (honor-iff-absolute,
    # with a warn-on-fallback to /run/user/$UID or a 0700 temp dir) —
    # the single source of truth for the runtime base.
    _run_dir = std.runtime
    _run_dir.mkdir(parents=True, exist_ok=True)
    # Socket name = ``<box>-<ws>`` (box name + workset-name token), so a
    # project name reused across worksets gets a distinct socket.  The
    # combined identity is bounded so a long name can't overflow
    # ``sun_path``; reattach recomputes the same deterministic name.
    from kanibako.channels.channels import workset_name_token
    _box_name = proj.name if proj.name else short_hash(proj.project_hash)
    _ws_token = workset_name_token(proj)
    socket_path = _run_dir / bounded_socket_name(
        f"{_box_name}-{_ws_token}", _run_dir,
    )
    validate_socket_path(socket_path)
    # Per-box, per-mode HOST helper log — lives inside the box's own
    # workset/box tree (PRIMARY → primary_workset/logs/<box>.jsonl,
    # NAMED → <workset_root>/logs/<box>.jsonl, STANDALONE →
    # box_data/<box>.jsonl), not the old shared @config.data/logs/<id>/
    # location.  Guarantee-create the parent before the ro bind (L7).
    from kanibako.settings.paths import helper_log_path
    log_path = helper_log_path(std, proj)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure helpers/ dir exists in shell_path
    helpers_dir = proj.shell_path / "helpers"
    helpers_dir.mkdir(exist_ok=True)

    # Build context for helper container launches.  Helpers reuse the
    # agent's delivery binds so an in-helper agent finds the same binary;
    # `binary_mnts` is the descriptor-assembled delivery mount list built
    # above (only set when a descriptor-bearing target has a host
    # install — NoAgentTarget has none, so helpers get just the kanibako
    # binds).
    binary_mounts = _kanibako_mounts()
    if target and install:
        binary_mounts.extend(binary_mnts)

    # Share tweakcc cache with helpers so they reuse patched binaries
    if tweakcc_entry is not None:
        _tweakcc_cache_dir = std.cache_path / "tweakcc"
        if _tweakcc_cache_dir.is_dir():
            binary_mounts.append(_HMount(
                source=_tweakcc_cache_dir,
                destination=str(_tweakcc_cache_dir),
                options="ro",
            ))

    helper_ctx = HelperContext(
        runtime=runtime,
        image=image,
        container_name_prefix=container_name,
        shell_path=proj.shell_path,
        helpers_dir=helpers_dir,
        socket_path=socket_path,
        binary_mounts=binary_mounts,
        env=container_env,
        entrypoint=entrypoint,
        default_entrypoint=target.default_entrypoint if target else None,
        box_shell=box_shell,
        project_path=proj.project_path,
        data_path=std.data_path,
        boxes=std.boxes,
        registry=std.registry,
        primary_workset=std.primary_workset,
    )

    msg_log = MessageLog(log_path)
    hub = HelperHub()
    hub.start(socket_path, helper_ctx, log=msg_log)

    # Box-side socket + log dests are XDG_STATE_HOME-aware: derived from
    # the BOX's container env (honor-iff-absolute, else
    # ``$HOME/.local/state``), the single derivation shared with
    # ``helper-init.sh`` (`${XDG_STATE_HOME:-$HOME/.local/state}`) and
    # the in-box CLI (``xdg``) so host and box agree by construction.
    box_state_kanibako = box_state_home(container_env) / "kanibako"

    # Mount the live helper socket + the per-box message log into the box,
    # routed through the category resolver (Phase B) instead of hardwired
    # ``_HMount`` appends.  The runtime-derived box destinations
    # (``<box_state_kanibako>/helper.sock`` / ``…/helpers.jsonl``) and the
    # ``.exists()`` skip-if-missing gate are applied in the loader; the
    # socket keeps options="" (a LIVE unix socket the hub listens on — a
    # Z/U relabel/chown would break the shared socket topology).
    kanibako_dir = proj.shell_path / ".local" / "state" / "kanibako"
    kanibako_dir.mkdir(parents=True, exist_ok=True)
    # Late, CONDITIONAL resolve through the same snapshot pipeline,
    # carrying ONLY the helper table (include_base_families=False) — its
    # runtime-derived box_dests are disjoint from the main reconcile, so a
    # separate reconcile is byte-for-byte equivalent.
    _hub_snap, _hub_rec = _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name=agent_id,
        system_settings_path=system_settings_path,
        agent_cfg_path=agent_cfg_path,
        desc=None,
        install=None,
        target=None,
        box_state_kanibako=str(box_state_kanibako),
        socket_path=socket_path,
        log_path=log_path,
        deliver_creds=auth_src.creds_shared,
        include_base_families=False,
    )
    helper_hub_mounts = _emit_category_mounts(_hub_rec, label="helper")
    extra_mounts.extend(helper_hub_mounts)
    return hub



def _run_container(
    *,
    project_dir: str | None,
    entrypoint: str | None,
    image_override: str | None,
    new_session: bool,
    continue_override: bool = False,
    safe_mode: bool,
    autonomous: bool = False,
    resume_mode: bool,
    extra_args: list[str],
    no_helpers: bool = False,
    no_auto_auth: bool = False,
    browser: bool = False,
    share_images: bool = False,
    persistent: bool = False,
    detach: bool = False,
    model_override: str | None = None,
    cli_env: list[str] | None = None,
    box_shell_mode: bool = False,
    explicit_agent: str | None = None,
    setup_only: bool = False,
    print_container: bool = False,
    warm_only: bool = False,
) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    std = load_std_paths(config)
    # SYSTEM tier of the SETTINGS cascade = @config.settings = global/settings.yaml
    # (behavior keys), distinct from the kanibako_config.yaml CONFIG file (system.* layout).
    system_settings_path = std.settings

    # project_dir is the reconciled subject (positional OR --box) computed in
    # run_start/run_shell.  Route it through the path-or-name resolver so a bare
    # registered box name selects that box even when not cwd (§Design 8).
    # B3: register=False defers a NEW box's registration past the home seed — the
    # seed gate below runs marker -> seed -> register -> remove-marker so an
    # interrupted auto-create is forward-recoverable (registered ==> seeded).
    #
    # TRUE PRE-FLIGHT for a BOX-INDEPENDENT persona (Director rulings, Jei
    # 2026-07-03): when the effective agent is a persona resolvable WITHOUT the box
    # we DEFER box materialisation — resolve the box's PATHS only (``initialize=
    # False`` → NO mkdir, ``warn=False`` so the name flag is not doubled by the
    # later materialising resolve), run the persona load-or-error gate below, and
    # mkdir the box ONLY once it is known loadable.  An unloadable persona then
    # leaves NO box dir behind (no rmtree).
    #
    # A brand-new box derives its agent ONLY from the two box-INDEPENDENT sources —
    # explicit ``--agent`` OR the stored system default (``read_system_agent``); a
    # box-scoped ``pref.system.agent`` can only shadow an ALREADY-existing box (never
    # a brand-new one), so those two are exactly the sources that could otherwise
    # leave an empty unregistered box dir for a system-default persona (Director
    # RESIDUAL ruling).  A bare / non-persona launch materialises immediately,
    # exactly as before (single resolve, byte-identical).
    _defer_box = False
    _box_indep_ref = explicit_agent or read_system_agent(system_settings_path)
    if _box_indep_ref:
        try:
            _node, _harness = parse_agent_ref(_box_indep_ref)
            _defer_box = _node != _harness
        except ConfigError:
            _defer_box = False  # malformed ref: surfaced by select_agent below.

    # EXPLICIT-CREATE gate (Jei 2026-07-11g, v1.7.0 BREAKING): a launch NEVER
    # materialises a NEW box.  Creating a box is a deliberate act — it must go
    # through ``kanibako create``.  Probe non-materialisingly (``initialize=
    # False`` → no mkdir/name/seed) BEFORE the materialising resolve below; if no
    # EXISTING registered box resolves for the target, error out and point at
    # ``create`` rather than silently inventing a box for a typo'd project / wrong
    # cwd.  This single chokepoint covers every launch route (``start`` / bare
    # ``kanibako`` / ``code`` → start_detached → here / ``shell``).  Auto-START of
    # an EXISTING box is UNCHANGED — the probe passes and the flow continues.
    if _resolve_existing_box(std, config, project_dir) is None:
        print(_no_box_error(project_dir), file=sys.stderr)
        return 1

    proj = resolve_box_target(
        std, config, project_dir,
        initialize=not _defer_box, register=False, warn=not _defer_box,
    )
    if _defer_box:
        # The deferred probe did NOT materialise the box, so a brand-new box is
        # still nameless (name assigned only inside ``initialize=True``).  Carry the
        # deterministic name it WILL materialise under onto the probe so the persona
        # load-or-error gate below (``_resolve_box_launch_decisions`` →
        # ``box_channel_addresses``) resolves instead of raising "box has no name"
        # BEFORE it can verdict (F7).  An already-named (existing) box is untouched.
        _name_new_box_probe(std, proj)

    def _orphan_hint() -> None:
        # Hint about orphaned project data when initializing a new project.
        if proj.is_new and proj.group is not None and proj.group.is_default:
            from kanibako.settings.paths import iter_projects
            for _settings, _ppath in iter_projects(std, config):
                if _ppath is not None and not _ppath.is_dir():
                    print(
                        "hint: orphaned project data detected — "
                        "run 'kanibako box list' or use 'kanibako box remap' "
                        "if you moved a project.",
                        file=sys.stderr,
                    )
                    break

    _orphan_hint()

    # Load merged config (global + workset + project). The box scalars resolve
    # through the KEYSPACE inside the loader (B6); the two flags ride its §1A
    # CLI level via this transport (translated by the ONE builder inside).
    _cli_scalar_overrides: "dict[str, object]" = {}
    if image_override:
        _cli_scalar_overrides["box_image"] = image_override
    if share_images:
        _cli_scalar_overrides["box_share_images"] = True
    project_toml, workset_path = box_workset_settings_paths(proj)
    merged = load_merged_config(
        config_file,
        project_toml,
        workset_path=workset_path,
        cli_overrides=_cli_scalar_overrides or None,
    )

    image = merged.box_image
    # ``bootstrap`` relocated from the retired box-scope ``box.bootstrap_program``
    # to the AGENT scope (spec §2d), so the authoritative per-launch value is
    # resolved BELOW — after the agent is resolved (line ~1300) — off the settings
    # snapshot via :func:`_effective_bootstrap`, together with its ``none``-opt-out
    # persistence contradiction guard.  It cannot be read here (pre-agent).

    # Detach is inherently persistent: the box must survive as a background
    # keep-alive (a tmux session running a bare shell as PID-1) with no attached
    # terminal.  run_start already sets persistent=True for --detach; force it
    # here too so any other caller (e.g. the `kanibako code` auto-start path)
    # cannot reach the launch with detach=True but persistent=False.  The
    # `none`-opt-out contradiction below then still fires for a detach launch.
    if detach:
        persistent = True

    # ⚑ NO per-path flag persist here: the §1A CREATE EXCEPTION runs through the
    # ONE shared gate (``config.persist_creation_flags``), called ONCE below at
    # the point where BOTH the direct and the deferred-materialization arms have
    # a materialized ``proj`` — see the call after the ``_defer_box`` block.

    # Resolve target (agent plugin) and detect installation.
    #
    # W1 §Design 7: "resolve config FIRST ... before anything else."  Agent
    # resolution depends only on config/settings (merged config + system
    # default), NOT on the image being pulled or the bootstrap baseline.  So
    # resolve it up front — BEFORE ensure_image (image pull) and the tmux
    # baseline check below — so a user with 2+ agents and no default hits the
    # Gate-2a "pick an agent" error immediately, rather than paying a full
    # image pull and then a tmux baseline error.
    #
    # `kanibako shell` (box_shell_mode) and explicit-entrypoint launches skip
    # resolution entirely (they need no agent); this is unchanged.
    logger = get_logger("start")

    # Detect the container runtime up front: agent resolution below needs it to
    # honour a REATTACH to an already-running persistent box (the box's stored
    # agent supersedes the cascade), and the image step further down needs it
    # too.  Detection is cheap and side-effect-free.
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        print(
            "Error: No container runtime found.\n"
            "Install podman (https://podman.io/) or Docker, then try again.",
            file=sys.stderr,
        )
        return 1

    # Reattach fast-source: for a PERSISTENT box that is ALREADY RUNNING, the
    # box's identity is its container name (agent-independent) and `kanibako
    # start` should simply reattach.  The reattach path needs an agent only for
    # the per-agent credential refresh below.  W1's agent selection would Gate-2a
    # ("pick an agent") when 2+ agents exist with no default — even though the
    # box is happily running with a known agent.  So source that agent from the
    # container's KANIBAKO_AGENT stamp (set at launch) and feed it into the
    # cascade as the explicit choice.  The RUNNING BOX WINS over a differing
    # system default (silently); a differing EXPLICIT --agent is a hard error
    # (stop the box to relaunch with a different agent).  Boxes launched before
    # this change have no stamp -> inspect_env returns None -> normal resolution
    # (a default/--agent is then required, unchanged behaviour).
    reattach_running = False
    stored_agent: str | None = None
    if persistent and runtime.is_running(container_name_for(proj)):
        reattach_running = True
        stored_agent = runtime.inspect_env(
            container_name_for(proj), "KANIBAKO_AGENT"
        )
        if stored_agent:
            # KANIBAKO_AGENT stamps a NODE-NAME (canonical ``℘`` form); an
            # explicit ``--agent`` may still be a raw ``+`` ref. Canonicalise both
            # to node-form before comparing so pasting the same ref back (with
            # either separator) is idempotent, not a false mismatch.
            if explicit_agent is not None and (
                canonicalize_agent_ref(explicit_agent)
                != canonicalize_agent_ref(stored_agent)
            ):
                raise KanibakoError(
                    f"Box '{proj.name}' is already running agent "
                    f"'{display_agent_ref(stored_agent)}'; cannot reattach with "
                    f"--agent '{display_agent_ref(explicit_agent)}'. Stop it first "
                    f"(`kanibako stop {proj.name}`) to relaunch with a "
                    f"different agent."
                )
            explicit_agent = stored_agent

    is_agent_mode = entrypoint is None and not box_shell_mode
    target = None
    install = None
    agent_selection = None
    if is_agent_mode:
        from kanibako.settings.agent_select import select_agent
        # Resolve the agent through the ONE seam (spec §1A / §2h): the stored
        # ``system.agent`` < the workset's ``pref.system.agent`` < the box's <
        # ``--agent``, then the installed-count rule.  ``select_agent`` raises the
        # typed AgentResolutionError subclasses (Gate-2a/2b / adapter-missing),
        # which the top-level cli.py handler surfaces verbatim with a non-zero
        # exit, and REFUSES by name a box still carrying the retired
        # ``box.agent_name`` (migration M-4) rather than silently launching a
        # different agent.
        agent_selection = select_agent(
            std=std, proj=proj, explicit_agent=explicit_agent,
        )
        agent_name = agent_selection.node
        # ⚑⚑ THE TWO-VOCABULARY SEAM — the D-M6 guard (bifrost E-NULL, 2026-07-31).
        # ``agent_name`` is the NODE-name (persona identity) and the TARGET/plugin is
        # keyed by the HARNESS — BUT a SUPPRESSED box (``pref.system.agent: null``)
        # has NO node, and ``""`` means opposite things on the two sides of this
        # line: "no agent" to selection, "no name given → AUTO-DETECT" to
        # ``resolve_target``. Passing it straight through LAUNDERED the suppression
        # into auto-detection — measured on bifrost: the no-agent box launched
        # claude, with claude's binary, commons and CREDENTIALS. ``resolve_selected_target``
        # is the ONE translator between the two vocabularies and returns ``None``
        # here, which is the shipped plain-shell shape (identical to ``kanibako
        # shell``): every downstream gate keys on ``target is None``, so no agent
        # binds, no agent config, no cred delivery, no ``KANIBAKO_AGENT`` stamp, and
        # ``agent_id`` = ``"general"``.
        target = (
            resolve_target(harness_of(agent_name), proj.project_path)
            if agent_selection.has_agent
            else None
        )
        if target is None:
            logger.debug(
                "No agent for this box (selection source=%s) — plain shell.",
                agent_selection.source,
            )
        else:
            logger.debug("Resolved target: %s", target.display_name)
            # First detect: early-out / "is the agent present on the host". The
            # "Using host ...:" line is deferred until after prepare_host() (the
            # update gate) so it names the real, post-update version — see the
            # re-detect below.
            install = target.detect()
            if not install and target.has_binary:
                print(
                    f"Warning: {target.display_name} binary not found on host. "
                    f"Launching without agent.",
                    file=sys.stderr,
                )
                logger.debug("target.detect() returned None for %s", target.name)

    # ``agent_id`` is the NODE-name (persona identity), NOT the bare harness: it keys
    # the on-disk ``agents/<node>/`` dir, the ``agent.<node>.*`` keyspace slot, and
    # the active-agent snapshot discriminator.  ``with_harness`` swaps in the
    # ACTUALLY-resolved target name (a NoAgent/other fallback is reflected while the
    # persona name is preserved); for a bare agent whose target resolved as
    # requested node == harness == target.name.  Hoisted HERE (ahead of the baseline
    # probe) so the agent-scope ``bootstrap`` value can be resolved before the probe
    # consumes it.  ``general`` for a no-agent / shell launch (target is None) so the
    # ``agent.default`` bootstrap backstop still applies.
    agent_id = with_harness(agent_name, target.name) if target else "general"
    agent_cfg_path = agent_settings_path(std.agents, agent_id)

    # AGENT-scope ``bootstrap`` (spec §2d): the AUTHORITATIVE per-launch value,
    # resolved off the SAME settings snapshot the launch reads for ``model`` /
    # ``access`` (single-route, [[settings-must-map-to-keystore-key]]) — via the
    # active agent + its ``agent.default.bootstrap`` / ``agent.<agent>.bootstrap``
    # cascade — with the consumer default ``tmux`` when unset (byte-identical to the
    # retired ``box.bootstrap_program or "tmux"`` for the default case).  Resolved
    # HERE (before the baseline probe / bootstrap-wrap / reattach that consume it),
    # NOT pre-agent up top.
    bootstrap_program = _effective_bootstrap(
        proj, system_settings_path, agent_id, agent_path=agent_cfg_path,
    )
    no_bootstrap = _is_no_bootstrap(bootstrap_program)

    # `none` opt-out is fundamentally incompatible with persistence (there is no
    # bootstrap program to wrap or reattach to).  run_start already turns this into a
    # clean pre-flight error, but guard here too so no other caller (e.g. `kanibako
    # shell --persistent` on a box configured `none`, or the `kanibako code`
    # auto-start) can reach the bootstrap-wrap with `none` as the program —
    # foreground single-use is the only meaning of `none`.
    if persistent and no_bootstrap:
        print(
            "Error: agent.default.bootstrap=none (foreground opt-out) cannot run "
            "a persistent session. Unset it or set an installed bootstrap "
            "program (e.g. tmux) for persistent/reattachable sessions.",
            file=sys.stderr,
        )
        return 1

    # Resolve the rig name to a kind + prep action, then materialize it.
    # Templates BUILD their Containerfile; prefabs/bases are pull-only via
    # ensure_image (inspect -> pull; no local base build).
    containers_dir = std.data_path / "containers"
    registry = load_registry(registry_path(std))
    res = resolve_rig(image, runtime, std, merged, registry=registry)
    try:
        if (
            res.kind == "template"
            and res.containerfile is not None
            and not runtime.image_exists(res.image)
        ):
            print(
                f"Rig '{image}' isn't prepped — building...",
                file=sys.stderr,
            )
            rc = runtime.rebuild(
                res.image,
                res.containerfile,
                res.containerfile.parent,
                build_args=None,
            )
            if rc != 0:
                print(
                    f"Error: failed to build rig '{image}' "
                    f"(exit code {rc}).",
                    file=sys.stderr,
                )
                return 1
        else:
            # Prefab/base (or already-local template/extended): inspect, then
            # pull if missing. Base images are pull-only (no local build).
            runtime.ensure_image(res.image, containers_dir)
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    image = res.image

    # Capture the image's login shell (idempotent, never fatal) so the
    # box-shell resolver reads a stored value instead of probing in the hot path.
    from kanibako.launch.shells import capture_image_shell
    capture_image_shell(runtime, image, std)

    # `kanibako shell` (interactive, no agent): resolve the box.shell now, with
    # the runtime/image handle, so the image-default tier participates.  This
    # makes entrypoint concrete *before* the agent-vs-shell decision (line ~672)
    # and the exec-into-running check (line ~737), so neither regresses.
    if box_shell_mode and entrypoint is None:
        from kanibako.launch.shells import resolve_box_shell
        entrypoint, _src = resolve_box_shell(merged, std, runtime=runtime, image=image)

    from kanibako.runtime.freshness import check_image_freshness
    check_image_freshness(runtime, image, std.cache_path)

    # Two-tier launch verification (one ephemeral probe covers both tiers).
    # Only meaningful for a persistent, bootstrap-wrapped session: the bootstrap
    # program is launch-critical only when we actually run it. Ephemeral and
    # one-shot launches don't use it, so skip the probe (and its hard stop)
    # entirely — otherwise a minimal --image (no tmux) couldn't run ephemerally.
    #   TIER 1 (bootstrap) — missing → HARD STOP before launch.
    #   TIER 2 (rest of baseline) — missing → WARN only; persisted + surfaced
    #   after the bootstrap session closes.
    if persistent:
        if _check_launch_baseline(
            runtime, image, bootstrap_program, container_name_for(proj), std,
        ) is _BOOTSTRAP_MISSING:
            return 1

    # Load agent config.  ``agent_id`` / ``agent_cfg_path`` were hoisted ABOVE (the
    # agent-scope ``bootstrap`` resolution needs them ahead of the baseline probe);
    # they name the NODE-scoped ``agents/<node>/`` store + the active-agent snapshot
    # discriminator.
    # Load or GENERATE the agent config IN MEMORY — do NOT write it yet.  The
    # persona load-or-error pre-flight below MUST resolve loadability BEFORE any
    # artifact for the persona is created (JEI-CRITICAL ordering, dogfood
    # 2026-07-03): an unconfigured persona that cannot be loaded errors out here
    # with NOTHING left behind — no ``agents/<node>/`` store, no ``settings.yaml``,
    # no share symlinks, no box seed/registration, no ``KANIBAKO_AGENT`` stamp.
    agent_cfg_exists = bool(target) and agent_cfg_path.exists()
    if target and not agent_cfg_exists:
        # First-use: generate default agent config from the target plugin
        # (the WRITE is deferred until after the pre-flight passes).
        agent_cfg = target.generate_agent_config()
    else:
        agent_cfg = load_agent_config(agent_cfg_path)

    # PERSONA-GRATA STORE RECONCILE (the persona analog of credsync): a PERSONA
    # agent whose persona-grata store entry exists reparses the store EVERY
    # launch and verified-swaps the agent-scope values BEFORE the launch
    # decisions below consume ``agent_cfg`` (store = source of truth; agent
    # settings = the synced copy; higher scopes still override via the normal
    # cascade).  IN-MEMORY only here — the swap rides ``agent_cfg_dirty`` into
    # the existing gated persist AFTER the load-or-error pre-flight, so an
    # unloadable persona still errors out with NOTHING left behind.  Bare
    # agents and store-less personas do zero new work (the store is never
    # touched for a bare node).
    _persona_synced = False
    if target is not None and harness_of(agent_id) != agent_id:
        agent_cfg, _persona_synced = _reconcile_persona_store(
            std.agents, agent_id, target, agent_cfg, logger,
        )

    # Auth 3-tier SHARING chain + persona endpoint: resolve BOTH per-box decisions
    # ONCE off a SINGLE launch snapshot (single-route) — MOVED AHEAD of every
    # persona artifact so the load-or-error gate below is a TRUE pre-flight.  Reads
    # the cascade + the in-memory ``agent_cfg`` and tolerates a not-yet-written
    # ``agent_cfg_path`` (an absent file = empty agent tier).  Yields the AuthSource
    # (tier/source + enables) threaded to every credsync/gate consumer, AND the
    # active-node ``agent.<node>.endpoint``.  ``endpoint is None`` = <None>/unset =
    # BARE (byte-identical to today); a set endpoint is the cred-fork signal →
    # ``suppress_oauth`` drops the host OAuth cred sync so the Anthropic token never
    # reaches a box pointed at a third-party endpoint.
    auth_src, active_endpoint, active_model = _resolve_box_launch_decisions(
        std=std,
        proj=proj,
        target=target,
        agent_name=agent_id,
        agent_cfg=agent_cfg,
        system_settings_path=system_settings_path,
        agent_cfg_path=agent_cfg_path,
        selection_level=(
            agent_selection.selection_level if agent_selection is not None else None
        ),
    )

    # PERSONA LOAD-OR-ERROR (A + B3): a persona (node != harness) that cannot
    # resolve a loadable endpoint MUST error out here — never silently degrade to
    # bare host claude on the user's real account.  The pre-flight may B3-adopt the
    # persona's config from ``~/.config/claude/<persona>/`` (mutating the in-memory
    # ``agent_cfg``).  On a hard error we return BEFORE creating any artifact; for a
    # DEFERRED explicit persona (``_defer_box``) the box was never materialised, so
    # NOTHING is left behind — a true pre-flight, not a rollback.
    #
    # ``provider`` is the resolved codex CodexModelProvider for a config-file
    # (codex) persona (None for claude / non-persona / bare) — INC 3 threads it into
    # the codex config.toml write below (``Target.deliver_directive_hook`` →
    # ``seed_codex_config``).  Init None here so the non-persona launch reaches the
    # write with a None provider (byte-identical) instead of an unbound local.
    provider: CodexModelProvider | None = None
    agent_cfg_dirty = (
        target is not None and not agent_cfg_exists
    ) or _persona_synced
    if target is not None and harness_of(agent_id) != agent_id:
        (
            active_endpoint, persona_error, persona_adopted, provider,
        ) = _preflight_persona_load(
            agent_id, agent_cfg, active_endpoint, logger,
            target=target, keyspace_model=active_model,
        )
        if persona_error is not None:
            print(persona_error, file=sys.stderr)
            return 1
        agent_cfg_dirty = agent_cfg_dirty or persona_adopted

    # Loadability resolved → materialise the DEFERRED box now (the explicit-persona
    # path resolved paths only above; mkdir the box + set ``is_new`` here, then
    # replay the two ``is_new``-gated steps the deferred probe skipped).  A non-
    # deferred launch already materialised at 791 — this is a no-op for it.
    if _defer_box:
        proj = resolve_box_target(
            std, config, project_dir, initialize=True, register=False,
        )
        _orphan_hint()
        # REBIND every proj-derived local (Editor ADD-c): the deferred probe
        # resolved paths against the placeholder ``boxes/__unregistered__``
        # metadata_path (a brand-NEW box has no name/dir yet), so ``project_toml``/
        # ``workset_path``/``merged`` were bound to that placeholder.  Now that the
        # box is materialised its REAL metadata_path is known — recompute them so
        # the image-override persist AND every downstream box-tier read/write hit
        # the real ``box.toml``, never ``__unregistered__/``.  (For a deferred
        # EXISTING box the probe already had the real path; this recomputes the
        # same values — a harmless no-op.)
        project_toml, workset_path = box_workset_settings_paths(proj)
        merged = load_merged_config(
            config_file,
            project_toml,
            workset_path=workset_path,
            cli_overrides=_cli_scalar_overrides or None,
        )

    # §1A CREATE EXCEPTION (R-11a + the 2026-08-02 materialization ruling): a
    # shadowing flag's value persists ONLY while the box is BEING MATERIALIZED —
    # launch-materialization counts as creation, so the launch calls the SAME
    # gate ``kanibako create`` does, at the one point where BOTH arms (direct
    # and deferred-persona) hold a materialized ``proj`` and the REAL
    # ``project_toml``. For an EXISTING box (``is_new`` False) the gate no-ops:
    # ``start --image`` stays strictly ephemeral.
    persist_creation_flags(
        project_toml,
        materializing=proj.is_new,
        image=image_override,
        share_images=True if share_images else None,
    )

    # D5 CRITICAL integrity gate (host components).  ``proj`` is now fully
    # materialised in BOTH the deferred and non-deferred paths, so its required
    # host-side dirs (workspace + home) can be verified before committing to a
    # launch.  Runs HERE (launch time), not the shared resolve chokepoint, so a
    # `box list` / `archive` / `diagnose` of a MOVED box never hard-crashes.
    _component_error = _check_box_components(proj)
    if _component_error is not None:
        print(_component_error, file=sys.stderr)
        return 1

    # ``suppress_oauth`` and the persona endpoint are now settled; a persona ALWAYS
    # suppresses the host OAuth cred sync (guard + suppress move together — a
    # B3-adopted persona suppresses exactly as a keyspace-configured one does).
    suppress_oauth = active_endpoint is not None

    # Loadability resolved → NOW materialise the persona artifacts.  Persist the
    # agent config (freshly generated OR B3-adopted); the common-dir shim points
    # ``agents/<node>/common/{plugins,cache}`` at the harness's dirs BEFORE mount
    # assembly resolves them.  A bare agent (node == harness) is a no-op for the shim, and
    # writes only when the config is new — byte-identical to before this pre-flight.
    if target and agent_cfg_dirty:
        write_agent_config(agent_cfg_path, agent_cfg)
    ensure_persona_share_symlinks(std, agent_id, target)

    # Deterministic container name for stop/cleanup
    container_name = container_name_for(proj)

    logger.debug("Project: %s (mode=%s)", proj.project_path, proj.mode)
    logger.debug("Image: %s", image)
    logger.debug("Container: %s", container_name)

    # Plugin descriptor (None for legacy/no_agent targets).  Hoisted here so the
    # credential lifecycle sites below (init / refresh / writeback) and the
    # launch-assembly block all branch off a single value: descriptor-bearing
    # targets route their cred lifecycle through the credsync engine, legacy
    # targets keep the per-plugin refresh/writeback hooks.
    desc = target.descriptor if target else None

    # Persistent mode: reattach if already running, clean up stale containers
    if persistent:
        if runtime.is_running(container_name):
            if detach:
                # Detach = "ensure the box is Up in the background".  It already
                # is, so there is nothing to attach — report and return without
                # touching the running session.  We do NOT claim it is a
                # keep-alive box: a box already running the agent as PID-1 is
                # "running" but not a keep-alive, so the message stays neutral.
                # (`kanibako code` pre-checks is_running so it never auto-starts
                # a live box; `kanibako start --detach` on a live box lands here.)
                print(
                    f"Box '{proj.name}' is already running.",
                    file=sys.stderr,
                )
                # --print-container: emit the cname as the final stdout line so
                # the remote-code leg parses it even when the box is already up.
                if print_container:
                    print(container_name_for(proj))
                return 0
            # Heads-up to STDERR (never stdout — must not pollute the tmux/agent
            # stream we're about to attach to).
            # Show the NODE-name (persona identity) in user-facing ``+`` form; a
            # bare node == harness == target.name, so the label is byte-identical.
            agent_label = display_agent_ref(agent_id) if target else (
                display_agent_ref(stored_agent)
                if reattach_running and stored_agent else "shell"
            )
            print(
                f"Reattaching to running box '{proj.name}' "
                f"(agent: {agent_label}).",
                file=sys.stderr,
            )
            # Reconciled-projection heads-up (D1): a reattach does NOT re-deliver
            # the agent's launch-projected config, and rewriting under a live
            # app-server is unsafe.  Agents whose config is a reconciled
            # projection (codex) return a notice; core prints it WITHOUT touching
            # the live file (reconciled on next start).  Seam call, no
            # name-branching — a bare-config agent inherits the None default.
            if target is not None:
                _reattach_notice = target.reattach_config_notice()
                if _reattach_notice:
                    print(_reattach_notice, file=sys.stderr)
            # Refresh credentials before reattaching
            if target and auth_src.creds_shared:
                if desc is not None:
                    credsync.refresh_box_credentials(
                        desc, target, auth=auth_src, host_home=Path.home(),
                        project_home=proj.shell_path,
                        suppress_oauth=suppress_oauth,
                    )
                else:
                    target.refresh_credentials(proj.shell_path)
            reattach_rc = runtime.exec(
                container_name, _bootstrap_attach(bootstrap_program), attach=True
            )
            # FIX 1: the reattach session has ended (detach or in-box exit).
            # An in-box login during this attach must reach the host, so write
            # back here too — the reattach path (4a32871) previously skipped the
            # post-session cred lifecycle entirely.
            writeback_session_credentials(target, proj, auth_src=auth_src)
            # Two-state lifecycle ("d"): tear down on exit, keep on detach.
            _teardown_persistent_box(runtime, container_name)
            return reattach_rc
        # Stale stopped container: remove before recreating
        if runtime.container_exists(container_name):
            runtime.rm(container_name)
        # Persistent mode forces no helpers
        no_helpers = True
    else:
        # Interactive (shell/ephemeral) mode: if a container is already
        # running for this project AND we're in shell mode (entrypoint set,
        # no agent), exec into it instead of erroring — matches the natural
        # UX of `kanibako shell <name> -- cmd` against a live container.
        if runtime.is_running(container_name) and entrypoint is not None:
            exec_cmd = [entrypoint] + (extra_args or [])
            # Apply per-run -e/--env vars to the exec'd process. The container's
            # baseline env (env files, agent_cfg.env, KANIBAKO_NAME) was set at
            # launch and is inherited by exec; without this, per-run -e vars
            # would be silently dropped when the box is already running.
            return runtime.exec(
                container_name, exec_cmd, env=_parse_cli_env(cli_env)
            )
        if runtime.container_exists(container_name):
            print(
                "Error: A box is already running for this project.\n"
                "  Reattach:  kanibako start\n"
                "  Stop it:   kanibako stop",
                file=sys.stderr,
            )
            return 1

    # Concurrency lock (skip for persistent — container existence is the lock)
    lock_fd = None
    if not persistent:
        lock_file = proj.metadata_path / ".kanibako.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_file, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "Error: Another Kanibako session is already running for this project.\n"
                "  Stop it first:  kanibako stop\n"
                "  Or use a shell: kanibako shell",
                file=sys.stderr,
            )
            lock_fd.close()
            return 1

        # Record container name so `kanibako stop` can find it
        lock_fd.write(container_name + "\n")
        lock_fd.flush()

    try:
        # Auto-snapshot vault share-rw before launch.
        if proj.enable_vault and proj.vault_rw_path.is_dir():
            from kanibako.snapshots import auto_snapshot, detect_snapshot_strategy
            strategy = detect_snapshot_strategy(proj.vault_rw_path)
            snap = auto_snapshot(proj.vault_rw_path, strategy=strategy)
            if snap:
                print(f"Vault snapshot: {snap.name}", file=sys.stderr)

        # Upgrade shell (add shell.d support to existing shells).
        _upgrade_shell(proj.shell_path)

        # Shell directory hygiene: remove waste files, compress old logs, and
        # reap stale empty agent-critical bind mountpoints (the 0-byte
        # ~/.local/bin/<agent> decoy + empty ~/.local/share/<agent> a prior
        # AGENT launch leaves behind and a no-agent shell launch never overlays
        # — see _agent_critical_dests / hygiene._reap_stale_agent_mountpoints).
        from kanibako.launch.hygiene import cleanup_shell_dir
        hygiene_actions = cleanup_shell_dir(
            proj.shell_path,
            agent_critical_dests=_agent_critical_dests(),
        )
        if hygiene_actions:
            for action in hygiene_actions:
                logger.info(action)

        # Seed at CREATE, never at launch (keyspace spec §0/§5).  The one-time
        # home seed runs ATOMICALLY with box registration: registry MEMBERSHIP is
        # the seed signal, so a box that already exists was already seeded and
        # this launch must NOT re-seed it (user edits survive).  ``proj.is_new`` is
        # True exactly when THIS resolve materialized the box; `box create` seeds
        # via the same ``_seed_box_home`` from ``run_create``.  Seed application is
        # create-if-absent, so even a re-create into a leftover dir never clobbers.
        #
        # EXPLICIT-CREATE (Jei 2026-07-11g): a launch NEVER auto-creates a box —
        # the explicit-create gate at the top of ``_run_container`` already errored
        # out for any target that is not a fully-registered box, so in real
        # operation ``proj.is_new`` is False here and this block does not run (an
        # existing box is never is_new).  Two deliberate consequences:
        #   * The launch path no longer completes an INTERRUPTED create — the old
        #     ``or _pending_create_entry(...)`` clause is REMOVED.  A half-created,
        #     not-yet-registered box reads as "no box" at the gate and errors;
        #     forward-recovery of an interrupted create belongs to ``create`` ALONE
        #     (re-running ``kanibako create`` replays seed → register → clear-entry
        #     in ``run_create``).  The launch must not silently resurrect/complete
        #     someone's half-finished create.
        #   * The ``if proj.is_new:`` guard is retained (not dead semantics — it is
        #     the correct action IF a box were ever materialized on this path) and
        #     is the seam the ``_run_container`` seed-routing unit tests drive.
        if proj.is_new:
            _write_create_entry(std, proj)
            _seed_box_home(
                std=std, proj=proj, target=target, desc=desc,
                agent_id=agent_id, agent_cfg_path=agent_cfg_path,
                system_settings_path=system_settings_path,
                auth_src=auth_src, logger=logger,
                suppress_oauth=suppress_oauth,
            )
            # The canon skeleton (J-7), in the SAME position as ``run_create``'s:
            # after the seed, inside the journal window.  See that call site for why
            # both are load-bearing.
            core_defaults.materialize_canon_skeleton(proj.shell_path, logger=logger)
            _register_new_box(std, proj)
            _clear_create_entry(std, proj)

        # Synced copies (the `<scope>.synced.<name>` category) — applied on
        # EVERY launch (mtime-gated), unlike copy-once seeds.  Distinct from the
        # plugin descriptor's `cred_files` credsync engine above (that is
        # descriptor-driven; this is settings-driven), so there is no double
        # application.  ADDITIVE: with no `synced.*` keys configured the
        # reconciled copy set has no synced winners -> no-op.  The share gate
        # (D-M4) suppresses every synced entry for a PRIVATE box (deliver_creds False).
        _apply_synced_copies(
            std=std, proj=proj, agent_name=agent_id, target=target,
            global_config_path=system_settings_path,
            agent_config_path=agent_cfg_path,
            logger=logger, deliver_creds=auth_src.creds_shared,
        )

        # Plugin-owned pre-launch host preparation (agent-agnostic call).
        # The plugin owns everything agent-specific that must touch the host
        # before mounts: e.g. the Claude plugin runs a synchronous `claude
        # update` gate so the host binary/symlink are stable before we bind
        # them, then refreshes host auth with the auto-updater disabled.  This
        # runs BEFORE the binary validation below because the update step can
        # repoint the resolved binary.  The hook never raises.
        if target and install and is_agent_mode:
            target.prepare_host(
                install,
                auto_auth=bool(auth_src.creds_shared and not no_auto_auth),
                data_path=std.data_path,
            )
            # Re-detect after the update gate.  prepare_host() can repoint /
            # prune the host version (the synchronous `claude update`), so the
            # first `install` (frozen before that) is stale.  Re-detecting here
            # — agent-agnostic — yields ONE fresh install that validate, the
            # "Using host ...:" print, and binary_mounts all consume, so they
            # describe and bind the real post-update version.  With the
            # auto-updater disabled the version is then stable detect→mount.
            install = target.detect() or install

        # Announce the (post-update) host agent now that prepare_host has run.
        if target and install and is_agent_mode:
            print(
                f"Using host {target.display_name}: {install.binary}",
                file=sys.stderr,
            )

        # Validate the resolved HOST binary BEFORE anything execs it.  A 0-byte
        # or non-executable file passes binary_mounts()' is_file() check and
        # would be exec'd into a brick.  This MUST run before the auth check
        # below: target.check_auth() shells out to the host agent binary, so a
        # corrupt/empty binary there raises an uncaught OSError (Exec format
        # error) and the user sees a Python traceback instead of the actionable
        # message.  Scoped to the real-agent path (entrypoint is None and not a
        # plain box shell launch).
        if target and install and is_agent_mode:
            from kanibako.targets.base import _validate_agent_binary

            reason = _validate_agent_binary(install.binary)
            if reason:
                print(
                    f"Error: {target.display_name} host binary is unusable: "
                    f"{reason}.\n"
                    f"  binary: {install.binary}\n"
                    f"The host agent binary appears corrupt or empty; the "
                    f"container would launch into a non-executable file.\n"
                    f"Reinstall the host {target.display_name} (and prune any "
                    f"stale 0-byte versions in {install.install_dir}), then "
                    f"retry.\n"
                    f"Run 'kanibako system diagnose' for a full health check.",
                    file=sys.stderr,
                )
                return 1

        # Pre-launch auth check (skip for distinct auth — creds live in project).
        #
        # On failure: if the target declares an in-box setup command
        # (``setup_entrypoint`` — goose ``configure`` / codex ``login``), DEFER the
        # error and run that command interactively IN THE BOX just before launch
        # (the box must be assembled first — image/mounts/env are built below).  An
        # agent with no setup command (claude, by default) errors out here as
        # before.  ``needs_inbox_setup`` carries the decision to the launch block.
        needs_inbox_setup = False
        if target and install and auth_src.creds_shared:
            if not target.check_auth():
                if target.setup_entrypoint is not None:
                    needs_inbox_setup = True
                else:
                    print(
                        "Error: Authentication failed.\n"
                        "  Re-authenticate:  kanibako agent reauth\n"
                        "  Skip agent:       kanibako shell",
                        file=sys.stderr,
                    )
                    return 1

        # Credential refresh via target (skip for a private/distinct box)
        if target and auth_src.creds_shared:
            if desc is not None:
                credsync.refresh_box_credentials(
                    desc, target, auth=auth_src, host_home=Path.home(),
                    project_home=proj.shell_path,
                    suppress_oauth=suppress_oauth,
                )
            else:
                target.refresh_credentials(proj.shell_path)

        # tweakcc: patch agent binary if enabled
        tweakcc_entry = None
        tweakcc_cache_obj = None
        if target and install and agent_cfg.transform_settings:
            result = _apply_tweakcc(
                install, agent_cfg, std.cache_path, image, runtime.cmd, logger,
            )
            if result:
                install, tweakcc_entry, tweakcc_cache_obj = result

        # Block 7b (ruling A — the FULL read-path swap): build the ONE launch
        # snapshot HERE, before the behavior read, so BOTH the behavior read (just
        # below) AND the category reconcile (further down) consume the SAME snapshot
        # (S12 resolve-ONCE). It carries the behavior FLOOR (→ agent.default.*, OS1),
        # the per-agent file's flat behavior state (wrapped under agent.<active>),
        # the always-available category default tables, 7a's descriptor delivery
        # partial, and the resolved system.* tier.
        #
        # The §1A CLI LEVEL (P8): the resolved agent selection PLUS this launch's
        # ephemeral key-shadowing flags, built ONCE by the single flag→key table and
        # guarded inside ``build_launch_snapshot``. It replaces the post-resolve
        # ``effective_state["model"] = …`` patch and the ``resolve_new_session``
        # fold that used to re-apply the flags at each consumer.
        #
        # ⚑ ``active_agent`` is the agent slot the flag values are spelled against,
        # so it is given ONLY for a real agent launch. A ``kanibako shell`` /
        # ``--entrypoint`` box resolves under the ``"general"`` template slot, which
        # is NOT an agent — spelling ``agent.general.model`` there would fabricate a
        # key the closed keyspace does not declare, and the guard would (rightly)
        # refuse it. Neither parser exposes these flags for such a launch anyway.
        _cli_level = build_cli_level(
            selection=(
                agent_selection.selection_level
                if agent_selection is not None
                else None
            ),
            active_agent=agent_id if target is not None else None,
            model=model_override,
            new_session=new_session,
            continue_session=continue_override,
            resume=resume_mode,
            # B6: the box-scope flags ride EVERY runtime resolve uniformly (§1A —
            # box.image / box.share_images are agent-independent, so they install
            # for shell/no-agent launches too; the image itself was settled off
            # the merged-config resolve above, which carried the same level).
            image=image_override,
            share_images=share_images,
        )
        # RETIRED BEHAVIOR spellings (R-41 / RQ-2): refuse a stored
        # ``auto_approve`` by name, at the BEHAVIOR tier, BEFORE anything reads a
        # permission value. ``access`` superseded it, so the old key is now
        # UNDECLARED — and an undeclared stored key is SILENT, which on this axis
        # means a box deliberately set ``auto_approve: false`` would come up at
        # the ``full`` default with nothing printed. Every cascade tier is
        # checked, BASE included (a site-admin default reaches every box on the
        # machine), plus the ACTIVE AGENT's own file — the one tier the selection
        # refusal above does not read, and the very file ``crab set`` writes.
        _refuse_retired_behavior(
            proj=proj, agent_id=agent_id,
            system_settings_path=system_settings_path,
            agent_cfg_path=agent_cfg_path,
        )
        _snapshot, reconciled = _resolve_launch_snapshot(
            std=std,
            proj=proj,
            agent_name=agent_id,
            system_settings_path=system_settings_path,
            agent_cfg_path=agent_cfg_path,
            desc=desc,
            install=install,
            target=target,
            agent_cfg=agent_cfg,
            deliver_creds=auth_src.creds_shared,
            cli_level=_cli_level,
        )

        # allow_helpers is an AGENT-scope behavior key (spec §2d,
        # ``agent.default.allow_helpers | true``): resolve it off the ONE launch
        # snapshot (via ``effective_behavior``, the §2d active-over-default pick),
        # coerced to bool and DEFAULTING True when unset. Resolved here for BOTH the
        # agent and the no-agent/shell path (agent_id == "general") so the helper
        # hub gate below sees the effective value regardless of target — an unset
        # box keeps the True floor (helpers ON), matching the old flat default.
        from kanibako.settings import settings_launch as _settings_launch
        _ah = coerce_bool(
            _settings_launch.effective_behavior(
                _snapshot, active_agent=agent_id,
            ).get("allow_helpers")
        )
        helpers_allowed = True if _ah is None else _ah

        # E2b: the CONTINUE-mode agent grammar for a detached box's always-on
        # supervisor self-heal restart (threaded to the detach branch below via
        # ``--continue-cmd``).  Populated only for a descriptor-bearing agent whose
        # launch grammar (``meta.agent.<a>.mode``, B5) exposes a ``continue`` mode;
        # ``None`` otherwise (the supervisor then safely defaults its restart to
        # the start grammar).
        agent_continue_argv: list[str] | None = None

        # Build CLI args via target, merging agent run_args and state
        if target:
            # The LIVE behavior read (block 7b — ruling A): off the ONE snapshot via
            # the §2d active-over-default pick (agent.<active>.<k> | agent.
            # default.<k>), replacing the retired ``_build_effective_state`` LAUNCH
            # use. A target with NO declared settings has no behavior floor — its
            # effective state is just the per-agent file's raw state (preserved from
            # the old early-return), so read from the snapshot all the same.
            from kanibako.settings import settings_launch
            effective_state = settings_launch.effective_behavior(
                _snapshot, active_agent=agent_id,
            )
            # ⚑ NO post-resolve ``-M`` patch here any more (P8). ``-M/--model`` is a
            # key-shadowing flag, so it rides the §1A CLI LEVEL spliced above every
            # settings file and pref (``agent.<active>.model``, built by
            # ``build_cli_level``) and arrives through this read like any other
            # value. Patching the dict AFTER the cascade was the ad-hoc form of
            # exactly one level, and it could not be seen by anything that read the
            # snapshot rather than this dict.
            # PERSONA provider auto-pin: when this launch resolved an active persona
            # endpoint (active_endpoint is not None), FORCE the harness-declared
            # provider setting(s) so the endpoint's required provider can't be
            # forgotten/mis-set (goose pins ``provider=openai`` → the descriptor's
            # ``provider``→``GOOSE_PROVIDER`` env then emits ``GOOSE_PROVIDER=openai``).
            # Empty provider_pin (claude/codex) = no-op (byte-identical); a BARE box
            # (active_endpoint None) is NEVER touched.
            if active_endpoint is not None:
                for _pin_key, _pin_val in _persona_wiring(target).provider_pin:
                    effective_state[_pin_key] = _pin_val
            all_extra = list(agent_cfg.run_args) + list(extra_args)
            if desc is not None:
                # Descriptor path: assemble argv + container-env overlay
                # declaratively from the plugin descriptor (replaces the legacy
                # apply_state / build_cli_args hooks for descriptor-bearing
                # targets).
                #
                # THE PERMISSION AXIS (R-41, spec §2d). TWO tiers are resolved
                # here, deliberately, and they are NOT the same read:
                #
                #  * ``cascade_access`` — the box's stored ``access`` key
                #    (``agent.default.access | full``; every shipped descriptor
                #    sets safe_bypass.setting_key="access"), validated against the
                #    enum and DEFAULTING to ``full`` when unset. This is what the
                #    PROJECTED surfaces get: they are written into the box's own
                #    agent config files and OUTLIVE this launch, so an ephemeral
                #    flag must never reach them (spec §1A projected-surface
                #    exception).
                #  * ``launch_access`` — the same value with the per-launch
                #    ``-S``/``-A`` folded in (``-S`` ⇒ restricted, ``-A`` ⇒ full).
                #    This is what the ARGV/ENV get, and only they.
                #
                # An agent whose descriptor declares no safe_bypass.setting_key
                # falls back to the ``full`` default, as before.
                #
                # ⚑ UN-RENDERED TIER GATE, before ANY delivery. Both tiers are
                # checked against the descriptor's rows here, where the launch can
                # still stop: the projected-surface deliveries below are wrapped
                # best-effort and would SWALLOW the refusal, leaving the box
                # running at a tier the user did not ask for (goose has no
                # ``editing`` realization — the case this exists for).
                sb = desc.safe_bypass
                cascade_access = assembly.resolve_access_tier(
                    effective_state.get(sb.setting_key)
                    if sb is not None and sb.setting_key
                    else None
                )
                launch_access = assembly.effective_access(
                    secure=safe_mode,
                    autonomous=autonomous,
                    access=cascade_access,
                )
                # dict.fromkeys, not a set: dedupe while keeping a DETERMINISTIC
                # order (cascade first), so when both tiers are unrenderable the
                # error names the same one every run.
                for _tier in dict.fromkeys((cascade_access, launch_access)):
                    assembly.access_row(desc, _tier, agent=agent_id)
                _deliver_panel_permissions(
                    target=target,
                    proj=proj,
                    access=cascade_access,
                    provider=provider,
                    logger=logger,
                )
                # continue_mode is an AGENT-scope behavior key (spec §2d
                # ``agent.default.continue_mode | true``): resolve it off the SAME
                # launch snapshot via the §2d active-over-default pick, coerced to
                # bool and DEFAULTING True (continue) when unset — the same
                # snapshot read as the ``access`` resolve above.
                #
                # ⚑ P8: the per-launch ``-N``/``-C``/``-R`` flags are ALREADY folded
                # in — they rode the §1A CLI LEVEL as ``agent.<active>.continue_mode``
                # (``-N`` ⇒ False, ``-C``/``-R`` ⇒ True) and therefore beat every
                # settings file and pref through the ordinary cascade. So the
                # continue-vs-fresh decision is now exactly "did continue_mode
                # resolve false?", and ``assembly.resolve_new_session`` — whose whole
                # body was the per-launch-flags-over-the-key fold — is GONE. Keeping
                # it would apply the same fold twice, in two places, from two
                # different inputs.
                #
                # ``resume_mode`` is still passed to ``resolve_mode`` below: ``-R``
                # selects the resume MODE, which is a launch grammar choice, not a
                # key. (That is also why ``-R`` installs continue_mode=True: a
                # descriptor with no ``resume`` mode falls through to the
                # skip_continue test, where a stored ``continue_mode: false`` would
                # otherwise turn ``-R`` into a fresh start.)
                _cm = coerce_bool(effective_state.get("continue_mode"))
                continue_default = True if _cm is None else _cm
                effective_new_session = not continue_default
                # B5 (spec §2d, the §3.3 rulings): the launch GRAMMAR comes off
                # the ONE snapshot — ``meta.agent.<a>.mode`` / ``.exec``,
                # materialized from the descriptor in ``_launch_snapshot_inputs``
                # (``meta_agent_grammar_floor``). The descriptor is NOT read for
                # argv fragments here or below: the keyspace is the single
                # source, and a descriptor-direct read beside it would be the
                # two-sources drift shape B5 replaced.
                grammar = settings_launch.meta_agent_grammar(
                    _snapshot, active_agent=agent_id,
                )
                mode_key = assembly.resolve_mode(
                    resume_mode=resume_mode,
                    new_session=effective_new_session,
                    is_new_project=proj.is_new,
                    extra_args=all_extra,
                    available_modes=grammar.mode.keys(),
                )
                # First-launch death-race fix: the DEFAULT continue mode on a
                # box whose agent positively has nothing to resume is DOOMED
                # (goose `session --resume` -> "no session found to resume" ->
                # fast container death racing the attach into a raw podman
                # error).  Ask the target (host-side read of the box home only);
                # when it reports no resumable session, build the new-session
                # command directly — same as new_session=True.  This is now the
                # SOLE continue-vs-fresh guard (the launch-time crash-and-retry
                # net was removed with the dead-pane dependency): claude + goose
                # override the hook to detect an empty store; a target that keeps
                # the base default (True) always attempts continue.  Only the
                # DEFAULT continue path is affected: an explicit -R (which
                # resolves to "continue" for a picker-less agent like goose) is
                # left alone — `not resume_mode` short-circuits before the hook.
                if (
                    mode_key == "continue"
                    and not resume_mode
                    and not target.has_resumable_session(proj.shell_path)
                ):
                    logger.debug(
                        "%s reports no resumable session under %s; launching "
                        "a new session instead of a doomed continue",
                        target.name, proj.shell_path,
                    )
                    mode_key = "start"
                cli_args = assembly.assemble_argv(
                    desc,
                    # The selected mode's argv fragment, from the keyspace
                    # (meta.agent.<a>.mode[mode_key] — B5). Plain indexing so a
                    # grammar that lacks the resolved key fails LOUDLY, exactly
                    # as the retired descriptor-direct read did.
                    mode_fragment=grammar.mode[mode_key],
                    access=launch_access,
                    setting_values=effective_state,
                    op_fragment=None,
                    extra_args=all_extra,
                    agent=agent_id,
                )
                # E2b: ALSO assemble the CONTINUE-mode grammar (when the descriptor
                # exposes it) for the always-on supervisor's self-heal restart — a
                # resurrected agent RESUMES (``--continue`` re-reads the box session
                # history) instead of starting fresh (design §207).  This is the
                # PERSISTED continue form, INDEPENDENT of the per-launch mode
                # resolved above: the per-launch ``-N``/``-C``/``-R`` flags are
                # consumed into new_session/continue_override/resume_mode (not
                # ``all_extra``), so none of them leak into the continue grammar.
                if "continue" in grammar.mode:
                    agent_continue_argv = assembly.assemble_argv(
                        desc,
                        mode_fragment=grammar.mode["continue"],
                        access=launch_access,
                        setting_values=effective_state,
                        op_fragment=None,
                        extra_args=all_extra,
                        agent=agent_id,
                    )
                state_env = assembly.assemble_env(
                    desc,
                    access=launch_access,
                    setting_values=effective_state,
                    agent=agent_id,
                )
            else:
                # Descriptor-less target: the only one is NoAgentTarget (the
                # `kanibako shell` fallback), which launches a plain shell with
                # no agent argv and no state env.  The legacy build_cli_args /
                # apply_state hook dispatch was removed for the public release
                # (descriptor-only plugin system); a no-agent box needs neither.
                cli_args = []
                state_env = {}
        else:
            state_env = {}
            cli_args = list(extra_args)

        # Build extra mounts from target binary detection.
        #
        # Block 7b: the launch-time CATEGORY resolution runs through the ONE snapshot
        # + ONE reconcile built ABOVE (the same ``_snapshot`` / ``reconciled`` the
        # behavior read consumes — S12 resolve-ONCE). The always-available category
        # default tables (core / kani / channel / share / seeds), the resolved
        # ``system.*`` tier, and 7a's descriptor delivery partial are
        # all folded in there. The image + helper tables are CONDITIONAL and
        # late-bound (their inputs are computed further down), so they are resolved
        # at their own sites — their box_dests are disjoint from these families, so
        # a separate reconcile is byte-for-byte equivalent.
        extra_mounts = []

        # AGENT delivery binds: the AGENT_CRITICAL delivery binds (binary +
        # launcher) now flow through the snapshot's ``agent.<agent>.bindings.*`` subtree
        # (single-route, 7a) and are emitted by ``agent_delivery_mounts`` — a
        # missing/unresolvable AGENT_CRITICAL source raises BindingSourceError ->
        # clean safe-fail (preserved from ``descriptor_mounts``), not a crun crash.
        # Agent-scope shared dirs (claude's plugins/cache) are NOT delivery binds;
        # they flow through the category mounts below from ``default_common()``.
        # Placed FIRST in extra_mounts (matching the old binary_mnts position,
        # before kani/core), so podman order is preserved.  ``binary_mnts`` is
        # reused by the helper context further down (an in-helper agent reuses the
        # same delivery binds).
        binary_mnts: list = []
        if target and install and desc is not None:
            from kanibako.settings.settings_launch import agent_delivery_mounts
            from kanibako.targets.base import BindScope

            critical_keys = frozenset(
                b.key for b in desc.bindings
                if b.scope is BindScope.AGENT_CRITICAL
            )
            try:
                binary_mnts = agent_delivery_mounts(
                    reconciled.mounts, critical_keys=critical_keys,
                )
            except BindingSourceError as exc:
                logger.error("Agent delivery binding unusable: %s", exc)
                print(
                    f"Error: {target.display_name} mount source "
                    f"disappeared before launch: {exc}\n"
                    f"The host agent install changed while starting (e.g. "
                    f"an update pruned a version). Retry the launch.\n"
                    f"Run 'kanibako system diagnose' for a full health "
                    f"check.",
                    file=sys.stderr,
                )
                return 1
            extra_mounts.extend(binary_mnts)

        # The remaining category MOUNT winners (kani / core / channel / share —
        # the kanibako CLI binds, the box's own home/workspace/vault binds, the
        # per-mode channel binds, and any scoped bindings/caches/common), emitted
        # ONCE from the single reconcile (depth-sorted across all families
        # together; podman's last-``-v``-wins/depth-sort resolves nested dests).
        # ``masks`` (tmpfs, no host source) and the agent delivery binds are split
        # out.  L7 guarantee-create / ro-drop is preserved byte-for-byte.  The
        # channel side-effect (seeding the chat general/broadcast logs, §3c) is run
        # explicitly — it was a side-effect of the retired ``_build_channel_mounts``.
        _seed_channel_files(std, proj)
        extra_mounts.extend(_emit_category_mounts(reconciled, label="category"))

        # Masks: the ``box.masks`` tmpfs mask LIST (the reconciled ``masks``
        # winners).  There is NO default mask (the old ~/workspace/vault default
        # was dropped — the vault moved out of the workspace in 1.6.0); a box (or
        # any scope) may declare masks via ``box.masks`` / ``<scope>.masks``.  The
        # result drives runtime.run(tmpfs_masks=...) below.
        tmpfs_masks = [
            e.box_dest for e in reconciled.mounts if e.category == "masks"
        ]

        # Image sharing: mount host image storage read-only into child, routed
        # through the category resolver (Phase B, D-M8) instead of hardwired
        # Mounts.  The graph root is runtime-probed (feeding ONLY the
        # ``box.images_store`` default, ruled 11a) and the storage.conf is
        # GENERATED+written by ``write_storage_conf`` (still the SOURCE of the
        # bind); those probed/generated paths are injected at the seam into
        # the keyed ``box.bindings.ro.images_*`` binds.  CONDITIONAL: only when
        # sharing is requested AND the RESOLVED store (probed default or a set
        # ``box.images_store``) is present — probe-fail alone warns, not skips
        # silently.
        _assemble_image_sharing_mounts(
            merged=merged,
            proj=proj,
            runtime=runtime,
            std=std,
            agent_id=agent_id,
            system_settings_path=system_settings_path,
            agent_cfg_path=agent_cfg_path,
            auth_src=auth_src,
            extra_mounts=extra_mounts,
            logger=logger,
        )

        # Peer communication: the channel system (5 types, 2 scopes — TARGET §2f)
        # is now folded into the single launch reconcile above (the per-mode
        # channel binds from ``_channel_default_categories``, emitted by
        # ``_emit_category_mounts``); the chat ``general.md``/``broadcast.md`` seed
        # side-effect (``_seed_channel_files``) ran just before that emit.

        container_env, secret_export_vars = _assemble_launch_env(
            std=std,
            proj=proj,
            agent_cfg=agent_cfg,
            reconciled=reconciled,
            state_env=state_env,
            cli_env=cli_env,
            target=target,
            agent_id=agent_id,
            extra_mounts=extra_mounts,
            logger=logger,
        )

        # Helper hub: start listener before director, mount socket
        hub = None
        helpers_enabled = not no_helpers and helpers_allowed

        # Resolve the no-agent box.shell once, up front, so it can be threaded
        # into both the helper context (below) and the main launch decision
        # (further down).  The resolver is cheap/idempotent (reads the stored
        # image shell; no container spin-up once captured) and follows the
        # single-source-of-truth chain: box.shell -> $KANIBAKO_SHELL -> image's
        # recorded login shell -> sh.  Resolve it whenever it could be used: a
        # no-agent launch (the main entrypoint), or any helper spawn (helpers
        # need a shell fallback even under a real-agent director).  A real-agent
        # launch with helpers off never needs it, so skip the resolve there.
        no_agent_launch = not entrypoint and (
            target is None or target.default_entrypoint is None
        )
        # DETACH also needs the resolved box shell: its PID-1 keep-alive runs a
        # bare SHELL (not the agent), so resolve box.shell even for an agent
        # launch (where no_agent_launch is False).  E2c: a SUPERVISED foreground
        # agent (persistent, not detach) likewise needs it — the supervisor PID-1's
        # forward-compat FALLBACK is the same bare-shell keep-alive.  is_agent_mode
        # guarantees entrypoint becomes target.default_entrypoint below, so a target
        # with a default entrypoint is exactly the supervised case.
        supervised_agent_launch = (
            persistent
            and is_agent_mode
            and target is not None
            and target.default_entrypoint is not None
        )
        if no_agent_launch or helpers_enabled or detach or supervised_agent_launch:
            from kanibako.launch.shells import resolve_box_shell
            box_shell, _box_shell_source = resolve_box_shell(
                merged, std, runtime=runtime, image=image,
            )
        else:
            box_shell = None

        if helpers_enabled:
            hub = _start_helper_hub(
                runtime=runtime,
                image=image,
                container_name=container_name,
                proj=proj,
                target=target,
                install=install,
                binary_mnts=binary_mnts,
                tweakcc_entry=tweakcc_entry,
                std=std,
                container_env=container_env,
                entrypoint=entrypoint,
                box_shell=box_shell,
                agent_id=agent_id,
                system_settings_path=system_settings_path,
                agent_cfg_path=agent_cfg_path,
                auth_src=auth_src,
                extra_mounts=extra_mounts,
            )

        # Pre-launch validation: warn about missing mount sources.
        _validate_mounts(extra_mounts, logger)

        # Browser sidecar: on-demand headless Chrome for agent web access
        browser_sidecar = None
        if browser:
            try:
                from kanibako.browser_sidecar import (
                    BrowserSidecar,
                    ws_endpoint_for_container,
                )

                sidecar_name = f"{container_name}-browser"
                browser_sidecar = BrowserSidecar(
                    runtime=runtime,
                    container_name=sidecar_name,
                )
                ws_url = browser_sidecar.start()
                container_ws = ws_endpoint_for_container(ws_url)
                container_env["BROWSER_WS_ENDPOINT"] = container_ws
                logger.info("Browser sidecar started: %s", container_ws)
            except Exception as exc:
                logger.warning("Browser sidecar failed to start: %s", exc)
                browser_sidecar = None

        # Set agent entrypoint if not explicitly overridden.
        if not entrypoint and target:
            entrypoint = target.default_entrypoint

        # No-agent box: launch the configured box.shell (already resolved above
        # via the single-source-of-truth chain box.shell -> $KANIBAKO_SHELL ->
        # stored image shell -> sh, and threaded into the helper context).  A
        # real agent sets a non-None entrypoint here and is left untouched —
        # box_shell only feeds the no-agent launch below.

        # In-box setup (FIX 2): the pre-launch auth probe failed AND the target
        # declares an interactive setup command (goose ``configure`` / codex
        # ``login``).  Run it FOREGROUND in the now-assembled box (same
        # image/mounts/tmpfs/env as the real launch) so the user can configure /
        # log in IN THE BOX; the result lands in box-state and persists across
        # reattach (1.6.0 "no host-config import" design).
        #
        # GATING (refined FIX 2): the setup command's exit code is treated as a
        # CRASH check ONLY — a non-zero exit means the setup itself failed/aborted,
        # so we fast-fail and do NOT launch.  A clean (rc 0) exit does NOT prove a
        # bootable config (partial-config case), so we DON'T gate on it here: we
        # proceed to the REAL launch and let it validate the config.  A
        # post-launch :meth:`Target.should_run_setup` match against the session
        # logs (below, at the same persistent-path site as the resume retry) is
        # the ground-truth detector for "config did not take".  ``check_auth`` is
        # HOST-side and cannot see box-only state (goose/codex), so it is NOT
        # re-probed on the launch path.
        if target is not None and needs_inbox_setup:
            setup_ep = target.setup_entrypoint
            assert setup_ep is not None  # needs_inbox_setup implies it is set
            setup_args = list(target.setup_args)
            print(
                f"{target.display_name} is not configured; running "
                f"'{setup_ep} {' '.join(setup_args)}' in the box. "
                f"Complete the prompts to continue.",
                file=sys.stderr,
            )
            setup_rc = _run_setup_command(
                runtime=runtime,
                image=image,
                proj=proj,
                container_name=container_name,
                setup_entrypoint=setup_ep,
                setup_args=setup_args,
                extra_mounts=extra_mounts,
                tmpfs_masks=tmpfs_masks,
                container_env=container_env,
            )
            # Fast-fail: the setup command CRASHED (non-zero exit) -> don't launch.
            if setup_rc != 0:
                print(
                    f"Error: {target.display_name} setup did not complete "
                    f"(exit {setup_rc}).\n"
                    "  Re-run:      kanibako start\n"
                    "  Re-auth:     kanibako agent reauth\n"
                    "  Skip agent:  kanibako shell",
                    file=sys.stderr,
                )
                return setup_rc or 1
            if setup_only:
                # ``agent reauth`` path (setup_only): run the in-box setup and stop
                # — do NOT drop into a full agent session, and there is no launch
                # to validate against, so this path CANNOT use launch-detection.
                # A clean setup exit (rc 0) is success.  Best-effort host re-probe:
                # if the setup also updated host-readable state, surface a positive
                # confirmation; otherwise just report setup complete (box-only
                # state — host ``check_auth`` legitimately cannot see it).
                if target.check_auth():
                    print(
                        f"{target.display_name}: authenticated.", file=sys.stderr,
                    )
                else:
                    print(
                        f"{target.display_name}: setup complete.", file=sys.stderr,
                    )
                return 0

        # ``agent reauth`` reaches here only when setup was NOT needed (auth was
        # already OK); nothing more to do — don't launch a session.
        if setup_only:
            return 0

        # SECRET export shim (spec §2a secret_path): when the box has secret_path
        # winners, nest a ``sh -c 'export <VAR>=$(cat mount); …; exec <agent> "$@"'``
        # shim INSIDE the agent command so each secret is exported IN-BOX from its ro
        # mount at agent start. Applied to the INNERMOST program (the agent, or the
        # no-agent box.shell) BEFORE the tmux/bootstrap wrap, so it nests correctly.
        # ⚑ ONLY when secret_export_vars is non-empty — a box with NO secrets skips
        # the shim entirely and keeps the bare entrypoint BYTE-IDENTICAL (zero delta).
        # Delivery is agent-INDEPENDENT: a no-agent shell launch with a box secret
        # still gets the exports (the shim wraps box.shell).
        #
        # ⚑ DETACH CAVEAT (Finding 3): the shim wraps whatever PID-1 exec's — the
        # supervised AGENT on a detached AGENT box (E2b, so the always-on agent DOES
        # see its secrets), or the keep-alive SHELL on a no-agent detached box.  A
        # later `podman exec` (a VS Code integrated terminal, the claude-code panel's
        # terminal) is a SEPARATE process that does NOT inherit PID-1's shell env, so
        # an agent a user launches THERE won't see the exported secrets — a
        # pre-existing limitation of the arm's-length shim.  This does not affect the
        # VS Code PANEL, which self-serves auth host-side (decoupled from the in-box
        # CLI creds).
        #
        # Persistent mode: wrap command with the configured bootstrap program
        if persistent:
            # Shim application shared by the persistent paths: the SECRET export
            # shim (iff the box has secret_path winners) and the goose directive-
            # flatten shim (iff goose), nested INNERMOST so they wrap the actual
            # program (agent or box.shell) before any tmux/bootstrap/supervisor wrap.
            def _apply_persistent_shims(
                prog: str, prog_args: list[str],
            ) -> "tuple[str, list[str]]":
                if secret_export_vars:
                    prog, prog_args = _secret_export_shim(
                        prog, prog_args, secret_export_vars,
                    )
                # Instruction-delivery launch-flatten for AGENT launches (all
                # agents; NO per-agent name gate): flatten the directive SEED into
                # the agent's native FINAL slot before exec, so the full guide
                # reaches the native (uncapped) instruction file rather than the
                # 2K/10K-capped additionalContext hook.  Gated on is_agent_mode so
                # plain-shell / no-agent launches (nothing to deliver) are NOT
                # wrapped; the shim ALSO self-skips in-shell when no
                # KANIBAKO_DIRECTIVE_FINAL is set.  Nests OUTSIDE any secret shim.
                if (
                    is_agent_mode
                    and target is not None
                    and target.default_entrypoint is not None
                ):
                    prog, prog_args = _directive_flatten_shim(prog, prog_args)
                return prog, prog_args

            supervise_agent = (
                persistent
                and is_agent_mode
                and target is not None
                and entrypoint is not None
            )
            if supervise_agent:
                # ALWAYS-ON SUPERVISOR PID-1 (E2b + E2c, design §189/§217 + §85-96):
                # a persistent AGENT box makes the box_supervisor PID-1, which runs
                # the AGENT in a detached tmux session ("kanibako") and watches it —
                # so the box persists INDEPENDENT of any one agent session (principle
                # B).  E2c UNIFIES the foreground and detached launches through this
                # ONE supervisor: it fires for a real agent under `persistent`
                # whether or not `--detach`.  The LAUNCH-INTENT exit policy differs:
                #   * DETACHED (E2b) → `--on-agent-exit self-heal`: a background
                #     agent death restarts (with the --continue grammar + a
                #     continue-marker), always-on, no human watching.
                #   * FOREGROUND (E2c) → `--on-agent-exit teardown`: this terminal
                #     ATTACHES after launch, so an agent EXIT is a normal termination
                #     → the supervisor returns and the box closes (a human is present
                #     to see any error); no self-heal loop while a CLI is the driver.
                # ⚑ SEMANTICS (both): reattach (`kanibako start` → `tmux attach -t
                # kanibako`) attaches to the AGENT's session; Ctrl-B D leaves it
                # running.  In DETACHED mode the agent exiting self-heals; teardown
                # is an explicit `kanibako stop`.  box_shell is resolved above for the
                # supervised launch (fallback keep-alive), and a real agent
                # guarantees a non-None entrypoint.  _bootstrap_wrap is SKIPPED — the
                # supervisor manages tmux itself.
                assert box_shell is not None
                assert entrypoint is not None  # supervise_agent guarantees it
                # 1. Supervised AGENT command = entrypoint + cli_args + shims.  On a
                #    WARM-ONLY launch (E2f) NO agent runs at start (the panel is the
                #    agent), so this is used only as the SELF-HEAL grammar fallback.
                agent_ep, agent_argv = _apply_persistent_shims(
                    entrypoint, list(cli_args or []),
                )
                # 2. Fallback bare-shell keep-alive = TODAY'S EXACT detached command
                #    (box.shell + the same shims + tmux/bootstrap wrap), preserved
                #    byte-for-byte as the forward-compat `|| exec` degrade path.  For
                #    warm-only this is the correct case-3a floor (agent-INDEPENDENT).
                fb_cmd, fb_args = _apply_persistent_shims(box_shell, [])
                fallback_ep, fallback_argv = _bootstrap_wrap(
                    bootstrap_program, fb_cmd, fb_args,
                )
                # 3. Supervisor invocation — NOT tmux-wrapped (it manages tmux
                #    itself).  --session "kanibako" keeps attach/reattach compat.
                #    --on-agent-exit encodes the launch-intent policy: DETACHED
                #    self-heals (always-on), FOREGROUND tears down on agent exit
                #    (the attaching human is the driver).  (In warm-only PANEL-WATCH
                #    the policy is inert — the panel-watch loop ignores on-agent-exit
                #    — but it is passed for a uniform, forward-compatible argv.)
                supervisor_argv = [
                    "python3", "-m", "kanibako.box_supervisor",
                    "--session", "kanibako",
                    "--marker", CONTINUE_MARKER,
                    "--on-agent-exit", "self-heal" if detach else "teardown",
                ]
                # D Part 1: thread the box-local credential-writeback SIGNAL flag path
                # so the supervisor edge-triggers it on EVERY detach (all modes).  The
                # in-box path is GUEST_HOME/<relpath> — the SAME file the host reads at
                # proj.shell_path/<relpath> via the box-home bind mount (one relpath
                # constant, :data:`kanibako.launch.creds_watcher.CREDS_DIRTY_RELPATH`).  The
                # box only ever touches its OWN home; the trusted HOST watcher does the
                # privileged store writeback (the load-bearing trust invariant).
                from kanibako.launch.creds_watcher import CREDS_DIRTY_RELPATH
                from kanibako.settings.settings_resolve import GUEST_HOME as _GUEST_HOME_D
                supervisor_argv += [
                    "--creds-flag", f"{_GUEST_HOME_D}/{CREDS_DIRTY_RELPATH}",
                ]
                # E2g / increment 4a: thread the per-agent liveness MARKERS DIR (the
                # SAME dir seeded as KANIBAKO_AGENT_MARKERS_DIR above) to BOTH modes.
                # run_forever (E2b/E2c) enumerates it to DETECT a panel newcomer
                # (increment 4a, LOG-ONLY); panel-watch (E2f) enumerates it for
                # panel-agent liveness + newcomer detection.  One dir, one scheme.
                supervisor_argv += ["--agent-markers-dir", AGENT_MARKERS_DIR]
                # increment 4b ENFORCEMENT — DEFAULT-OFF, gated behind the experimental
                # env KANIBAKO_SESSION_TAKEOVER so 4b lands DORMANT and cannot fire in
                # the wild before the $PPID == agent-PID / panel invariant is validated
                # on Jei's desktop (a misidentification that evicts a legitimate SOLE
                # agent is catastrophic).  Unset/off ⇒ the supervisor's newcomer handling
                # is 4a LOG-ONLY (no signals/kills).  When on, run_forever PAUSEs a panel
                # newcomer, GRACEs + process-group-EVICTs the CLI incumbent, then RESUMEs
                # the newcomer as sole writer.  This is an internal/experimental switch,
                # NOT a spec settings key — promoting it to an agent.default.* key (and
                # flipping the default to ON) is a later spec-delta + desktop validation.
                if _env_flag_enabled(os.environ.get("KANIBAKO_SESSION_TAKEOVER")):
                    supervisor_argv += ["--session-takeover"]
                if warm_only:
                    # E2f — AGENT-INDEPENDENT warm-up: front the box with the
                    # supervisor in PANEL-WATCH mode.  It starts NO CLI agent, watches
                    # the per-PID markers dir + the vscode_server surface, and
                    # self-heals a CLI agent ONLY when the panel agent dies with the
                    # panel still connected (design §89-96).  The panel is the sole
                    # agent — no two-agent split-brain on one ~/.claude.
                    supervisor_argv += ["--panel-watch"]
                if agent_continue_argv is not None:
                    # Self-heal RESUMES: shim the continue command the SAME way so a
                    # resurrected agent still gets its secrets + goose directives.
                    # Passed as one shlex string the supervisor shlex-splits back.
                    cont_ep, cont_argv = _apply_persistent_shims(
                        entrypoint, list(agent_continue_argv),
                    )
                    supervisor_argv += [
                        "--continue-cmd", shlex.join([cont_ep, *cont_argv]),
                    ]
                elif warm_only:
                    # Panel-watch has NO trailing `-- start argv` to fall back on for
                    # self-heal, so it ALWAYS needs an explicit self-heal grammar —
                    # use the (shimmed) START grammar when the descriptor exposes no
                    # continue mode, so a panel-death self-heal still launches the box's
                    # agent.
                    supervisor_argv += [
                        "--continue-cmd", shlex.join([agent_ep, *agent_argv]),
                    ]
                if not warm_only:
                    # Non-warm (E2b/E2c): the supervised agent runs at start, as the
                    # `-- <agent argv>` payload.  Warm-only OMITS it (agentless start).
                    supervisor_argv += ["--", agent_ep, *agent_argv]
                # 4. Compose PID-1 as an import-gated `sh -c` (forward-compat: an
                #    old image lacking the supervisor module degrades to the
                #    fallback).  SKIPS _bootstrap_wrap — the supervisor IS PID-1.
                entrypoint, cli_args = _build_supervisor_pid1(
                    supervisor_argv, [fallback_ep, *fallback_argv],
                )
            elif detach:
                # NO real agent to supervise (shell / NoAgentTarget / custom
                # --entrypoint): today's BARE-SHELL keep-alive, UNCHANGED.  The tmux
                # session runs a bare shell as PID-1; separate `podman exec`
                # processes (VS Code terminals, the panel) don't touch it, so they
                # never stop the box.  box_shell is resolved above for any detach.
                assert box_shell is not None
                inner_cmd, inner_args = _apply_persistent_shims(box_shell, [])
                entrypoint, cli_args = _bootstrap_wrap(
                    bootstrap_program, inner_cmd, inner_args,
                )
            else:
                # Foreground persistent NO-AGENT (attach): a shell / NoAgentTarget /
                # custom --entrypoint box — nothing to supervise, so it stays the
                # agent-as-session / shell-as-session tmux wrap: the command IS the
                # tmux session, so exiting it ends the session and tears the box down
                # (UNCHANGED — E2c only rerouted the real-AGENT foreground case
                # through the supervisor above).  box_shell is None only on a real-
                # agent launch, which the supervise_agent branch already handled, so a
                # non-None entrypoint is always set here; mypy can't track that
                # cross-variable invariant.
                fg_cmd = entrypoint or box_shell
                assert fg_cmd is not None
                inner_cmd, inner_args = _apply_persistent_shims(
                    fg_cmd, list(cli_args or []),
                )
                entrypoint, cli_args = _bootstrap_wrap(
                    bootstrap_program, inner_cmd, inner_args,
                )
        else:
            if not entrypoint:
                # Non-persistent no-agent launch: run box.shell explicitly instead
                # of deferring to the image's default entrypoint.
                entrypoint = box_shell
            if secret_export_vars and entrypoint:
                entrypoint, cli_args = _secret_export_shim(
                    entrypoint, list(cli_args or []), secret_export_vars,
                )
            # Instruction-delivery launch-flatten for AGENT launches (see the
            # persistent path).  Gated on is_agent_mode (NOT a per-agent name) so
            # plain-shell / no-agent launches are not wrapped.
            if (
                is_agent_mode
                and entrypoint
                and target is not None
                and target.default_entrypoint is not None
            ):
                entrypoint, cli_args = _directive_flatten_shim(
                    entrypoint, list(cli_args or []),
                )

        # Preflight: rootless podman cannot overlay/pivot_root on a virtiofs
        # graph root, so the launch would die with a cryptic runtime error.
        # virtiofs-rootless is an unsupported configuration — fail gracefully
        # with an actionable message instead.  The check is cheap and only
        # fires when the runtime is rootless podman AND the graph root is
        # virtiofs; it stays silent (and never blocks a normal launch) under a
        # rootful shim, on docker, or whenever the state can't be determined.
        from kanibako.runtime.image_sharing import virtiofs_graphroot_message
        virtiofs_msg = virtiofs_graphroot_message(runtime.cmd)
        if virtiofs_msg is not None:
            print(virtiofs_msg, file=sys.stderr)
            return 1

        # Warn about binds that will shadow pre-existing host content under the
        # box home (best-effort; persisted now and reprinted after the session).
        _shadowed = detect_shadowed_mounts(
            proj.shell_path, proj.project_path, extra_mounts or None, proj.enable_vault
        )
        _persist_shadow_issues(std, container_name, _shadowed)

        try:
            # Run the container
            rc = runtime.run(
                image,
                shell_path=proj.shell_path,
                project_path=proj.project_path,
                vault_ro_path=proj.vault_ro_path,
                vault_rw_path=proj.vault_rw_path,
                extra_mounts=extra_mounts or None,
                tmpfs_masks=tmpfs_masks or None,
                enable_vault=proj.enable_vault,
                env=container_env,
                name=container_name,
                entrypoint=entrypoint,
                cli_args=cli_args or None,
                detach=persistent,
                post_start=_canon_reprotect_hook(proj, logger),
            )
        finally:
            # Stop helper hub after director exits
            if hub is not None:
                hub.stop()
            # Release tweakcc cache entry (shared lock)
            if tweakcc_entry is not None and tweakcc_cache_obj is not None:
                tweakcc_cache_obj.release(tweakcc_entry)
            # Stop browser sidecar
            if browser_sidecar is not None:
                try:
                    browser_sidecar.stop()
                except Exception as exc:
                    logger.debug("Browser sidecar cleanup: %s", exc)

        if persistent and detach:
            # DETACH: the box is launched as a background keep-alive; we DO NOT
            # attach this terminal.  Verify it came up, then return — leaving the
            # box Up (no teardown) so a later reattach / `kanibako code` / VS Code
            # exec terminal can use it.  There is no session-end here, so there is
            # nothing to write back yet (the agent has not run).
            import time
            for _attempt in range(10):
                if runtime.is_running(container_name):
                    break
                time.sleep(0.3)
            else:
                logs = _container_logs(runtime, container_name)
                if logs:
                    print(logs, file=sys.stderr)
                print(
                    "Error: the background box failed to start.\n"
                    "Check the logs above, or run 'kanibako system diagnose'.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"Box '{proj.name}' started in the background (keep-alive).\n"
                f"  Attach:    kanibako start {proj.name}\n"
                f"  VS Code:   kanibako code {proj.name}\n"
                f"  Stop it:   kanibako stop {proj.name}",
                file=sys.stderr,
            )
            # D Part 2: a detached launch leaves no foreground host process to write
            # back the creds the box signals on detach, so spawn the trusted per-box
            # HOST watcher — but ONLY for a SHARED-tier box (a private box never
            # propagates creds, so there is nothing to watch).
            if auth_src.creds_shared:
                _spawn_creds_watcher(proj)
            _print_launch_issues(std, container_name)
            _print_shadow_issues(std, container_name)
            # --print-container: the resolved container name as the FINAL stdout
            # line (all other detach chatter above went to stderr) — the
            # machine-readable surface `kanibako code --remote` parses.
            if print_container:
                print(container_name)
            return 0
        elif persistent:
            # Wait briefly for the detached container to start, then verify
            # it's actually running before trying to exec into it.
            import time
            for _attempt in range(10):
                if runtime.is_running(container_name):
                    break
                time.sleep(0.3)
            else:
                # Container never started or exited immediately.  The up-front
                # continue-vs-fresh decision (``has_resumable_session``) now picks
                # a new session when there is nothing to resume, so the old
                # crash-and-retry net was removed (it depended on the dead-pane
                # capture that a teardown launch no longer arms).
                logs = _container_logs(runtime, container_name)
                if logs:
                    print(logs, file=sys.stderr)
                # FIX 2 (launch-validation): the launched session is GROUND TRUTH
                # for a bootable config.  If its logs say the agent is still not
                # configured/authenticated, the in-box setup did NOT take.  This is
                # BOUNDED — setup already ran once this invocation, so we only ERROR
                # here, never loop back into setup.
                # ⚑⚑ TEAR THE EXITED BOX DOWN — this branch is the two-state
                # lifecycle's "exited" state arriving EARLY, and it was the one
                # arrival that did not honour it.
                #
                # The attached path below tears an exited box down (it is what makes
                # the next start fresh); this path returned 1 and left the container
                # behind. The difference was never intentional — the branch's own
                # comment says "Container never started or exited immediately", which
                # IS the exited state — it is simply that the teardown was only ever
                # wired into the path that got as far as attaching.
                #
                # It went unnoticed because reaching here at all is a RACE: the box
                # has to die inside the ~3 s poll window above. A crashing agent does
                # exactly that, and the window has been widening as the launch path
                # gained pre-attach work (the post-start canon re-protect runs three
                # ``podman unshare`` calls inline on the detached path before the
                # first probe; this phase added five more binds for podman to set
                # up). The e2e that caught it went green → flaky → deterministic
                # across three gates without its own subject changing once.
                #
                # Best-effort and never running: ``_teardown_persistent_box`` returns
                # immediately if the container came back up, and the logs above were
                # already printed, so nothing diagnosable is thrown away.
                _teardown_persistent_box(runtime, container_name)
                if target and logs and target.should_run_setup(logs):
                    _print_setup_did_not_take(target)
                    return 1
                print(
                    "Error: Container exited before session could attach.\n"
                    "Check the logs above, or run 'kanibako system diagnose'.",
                    file=sys.stderr,
                )
                return 1

            # Attach to the new bootstrap session.  The container may not be
            # fully ready for exec even though is_running() returned True
            # (podman race: "container state improper").  Retry a few times.
            _max_exec_attempts = 5
            for _exec_attempt in range(1, _max_exec_attempts + 1):
                # Readiness probe (CAPTURED) before the TTY-inheriting
                # interactive exec.  exec_ready runs the same operation with
                # output captured, so podman's raw "container state improper"
                # race error is swallowed instead of leaking to the user's TTY.
                # Only hand off to the interactive attach once a probe has just
                # succeeded; otherwise fall through to the log-showing path
                # (instant agent crash) or retry the transient startup race.
                if not runtime.exec_ready(container_name):
                    if not runtime.is_running(container_name):
                        # Container already died (e.g. instant agent crash)
                        # before we could attach.  The bootstrap session never
                        # ran, so rc still holds the launch value (0).  Adopt
                        # the container's real exit code (falling back to 1 —
                        # tmux often masks the inner program's code) so a
                        # crash-on-launch surfaces as a non-zero kanibako exit
                        # instead of a misleading success.
                        # Fall through to the log-showing path below.
                        rc = _container_exit_code(runtime, container_name) or 1
                        break
                    # Still running but not yet ready: transient startup race.
                    if _exec_attempt < _max_exec_attempts:
                        print(
                            f"Warning: container not ready for exec "
                            f"(attempt {_exec_attempt}/{_max_exec_attempts}), "
                            f"retrying...",
                            file=sys.stderr,
                        )
                        time.sleep(0.5)
                    continue
                rc = runtime.exec(
                    container_name, _bootstrap_attach(bootstrap_program), attach=True
                )
                if rc == 0:
                    break
                # Non-zero interactive exec exit — if the container died, fall
                # through to the log-showing code; otherwise (still running)
                # retry.
                if not runtime.is_running(container_name):
                    break
                if _exec_attempt < _max_exec_attempts:
                    print(
                        f"Warning: container not ready for exec "
                        f"(attempt {_exec_attempt}/{_max_exec_attempts}), "
                        f"retrying...",
                        file=sys.stderr,
                    )
                    time.sleep(0.5)
            # If agent exited, show container logs so the user can
            # see why (tmux swallows output on exit).
            if not runtime.is_running(container_name):
                # The supervised/foreground box has STOPPED after (or during) the
                # attach.  The tmux-attach exec's rc does NOT reflect the agent's
                # real code: under `--on-agent-exit teardown` the supervisor
                # propagates the agent's exit code as the CONTAINER's exit code
                # (E2d), but `tmux attach` returns its own (client) status — a
                # supervised agent crash after a brief attach would otherwise leave
                # rc=0.  Adopt the container's true exit code so a crash surfaces as
                # a non-zero kanibako rc; `or rc` is non-zero-ONLY, so a clean exit
                # keeps its rc and we never fabricate a failure.
                rc = _container_exit_code(runtime, container_name) or rc
                # (a) Terminal hygiene: the foreground attach just returned with the
                # box STOPPED.  A full-screen agent TUI (e.g. claude) that DIED without
                # restoring the screen leaves the host tty in the alternate screen with
                # dirty SGR state and a hidden cursor — the ``reset``-needed garble a
                # human hits on a foreground box exit.  Reset it now, but ONLY on a real
                # TTY (a human is attached); a non-tty consumer must stay byte-clean.
                _restore_host_terminal()
                logs = _container_logs(runtime, container_name)
                if logs:
                    # (b) The captured dead-agent pane (echoed by the supervisor so
                    # ``podman logs`` shows WHY the agent died) is escape-laden noise
                    # for a human who was just attached, so do NOT echo it at an
                    # interactive tty.  Tooling / ``podman logs`` / CI (non-tty) still
                    # get it byte-for-byte.  ``logs`` is still COMPUTED above and read
                    # by the setup check below on BOTH paths.
                    #
                    # Suppress the raw captured pane at an interactive tty ONLY on a
                    # CLEAN exit (rc == 0): the human saw the agent's output live and
                    # does not need the escape-laden pane echoed.  On a CRASH (rc != 0)
                    # the alt-screen the human watched was just torn down by
                    # _restore_host_terminal(), so we MUST still surface the logs — the
                    # death cause — even at a tty (restores commit 05f7f04's contract;
                    # the FF-10 suppression over-reached to the crash path).
                    if rc != 0 or not _interactive_host():
                        print(logs, file=sys.stderr)
                # FIX 2 (launch-validation): the launched session is GROUND TRUTH
                # for a bootable config.  If its logs say the agent is still not
                # configured/authenticated, the in-box setup did NOT take.  BOUNDED
                # — setup already ran once this invocation, so we only ERROR here,
                # never loop back into setup.  Still write back first: a partial
                # in-box login may have produced credentials worth propagating.
                if target and logs and target.should_run_setup(logs):
                    writeback_session_credentials(target, proj, auth_src=auth_src)
                    _print_setup_did_not_take(target)
                    return 1

            # FIX 1: writeback on the persistent session-end paths — DETACH
            # (container still running) AND clean exit (container stopped).  The
            # box's home is a host mount, so the in-box creds are readable
            # whether or not the container is still up; both are writeback
            # moments so an in-box login reaches the host.
            writeback_session_credentials(target, proj, auth_src=auth_src)
            # Two-state lifecycle ("d"): an exited box (tmux session ended ->
            # container not running) is torn down so the next start/shell is
            # fresh; a detached box (still running) is kept reattachable.
            _teardown_persistent_box(runtime, container_name)
        else:
            # Clean/ephemeral exit: writeback project -> host (FIX 1 helper).
            writeback_session_credentials(target, proj, auth_src=auth_src)

            # Hint when agent exits non-zero and --continue/--resume was used
            if rc != 0 and is_agent_mode and not new_session:
                print(
                    "hint: if the agent exited because there was no conversation "
                    "to continue, use 'kanibako start -N' to start fresh.",
                    file=sys.stderr,
                )

        # Surface any tier-2 baseline warnings now that the bootstrap session
        # has closed (the alt-screen has been torn down).
        _print_launch_issues(std, container_name)
        _print_shadow_issues(std, container_name)

        return rc

    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def _print_setup_did_not_take(target) -> None:
    """Error for the FIX-2 post-launch detection: the in-box config didn't boot.

    The launch is ground truth — its logs matched :meth:`Target.should_run_setup`,
    so the setup we already ran this invocation did not produce a working
    configuration.  This is the BOUNDED terminus: we surface remediation and
    return; we never loop back into setup.
    """
    print(
        f"Error: {target.display_name} setup did not produce a working "
        f"configuration.\n"
        "  Re-run:    kanibako start\n"
        "  Re-auth:   kanibako agent reauth\n"
        "  Skip:      kanibako shell",
        file=sys.stderr,
    )


def _spawn_creds_watcher(proj) -> None:
    """Spawn the per-box HOST credential-writeback watcher, DETACHED (D Part 2).

    On a DETACHED launch (``kanibako code`` warm-up / ``kanibako start --detach``) the
    ``kanibako`` command RETURNS, so no foreground host process lingers to run the
    post-detach credential writeback the box's supervisor signals.  This spawns the
    trusted :mod:`kanibako.launch.creds_watcher` daemon to do it: it watches the box's
    creds-dirty flag and does the privileged store writeback promptly.

    DETACHED via ``start_new_session=True`` (its own session / no controlling tty) +
    detached std streams, so it survives this command returning — like the box itself.
    It re-resolves the host config from the box SUBJECT (``proj.project_path``), the
    same way ``kanibako stop`` does, so cwd is irrelevant.  The watcher itself skips a
    private box + holds a single-instance lock, but the caller ALSO gates on
    ``auth_src.creds_shared`` (don't even spawn for a private box — cheaper).  Best-effort:
    a spawn failure is logged, never crashes the launch (the flag's lazy fallback on
    the next host op still covers the writeback).
    """
    try:
        subprocess.Popen(
            [sys.executable, "-m", "kanibako.launch.creds_watcher",
             "--box", str(proj.project_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        get_logger("start").debug("could not spawn creds watcher: %s", exc)


def writeback_session_credentials(
    target, proj, *, auth_src
) -> None:
    """Project -> selected-source credential writeback for a finished session.

    The SINGLE writeback site for ALL session-end paths (FIX 1): clean exit,
    DETACH, reattach-exit, and ``kanibako stop``.  An in-box login must reach the
    shared store regardless of how the session ends, so every path that releases a
    box funnels through here.

    Gated on *auth_src* (the resolved
    :class:`~kanibako.settings.settings_launch.AuthSource`): a PRIVATE box (tier ``"box"``,
    ``auth_src.creds_shared`` False) keeps its credentials project-local and never
    propagates them.  Otherwise the box writes BOTTOM-UP to the selected source
    (host home for GLOBAL, the workset dir for WORKSET, then up to global when
    ``global_sync``).  No-ops when *target* is None (no-agent box) or has no
    credential lifecycle.

    Writes the descriptor's SYNC ``cred_files`` back (creating a missing source
    destination — a deauthed host has no ``~/.claude/.credentials.json``) and the
    plugin's :meth:`~kanibako.targets.base.Target.writeback_extra` (claude merges
    ``oauthAccount`` from the box's ``.claude.json`` into the host's without
    clobbering machine-specific fields).  Best-effort: a writeback failure must
    never crash the lifecycle path that called it.
    """
    if target is None or not auth_src.creds_shared:
        return
    desc = target.descriptor
    host_home = Path.home()
    from kanibako.launch.creds_watcher import clear_creds_dirty, creds_store_lock
    try:
        # Serialize the store write against a concurrent host op / the D watcher
        # (the shared STORE-write lock lives in creds_watcher so the daemon and every
        # host writeback site funnel through the SAME mutex).
        with creds_store_lock():
            if desc is not None:
                credsync.writeback_box_credentials(
                    desc, target, auth=auth_src, host_home=host_home,
                    project_home=proj.shell_path,
                )
            else:
                target.writeback_credentials(proj.shell_path)
            # Plugin-specific writeback beyond the cred_files specs (e.g. claude's
            # .claude.json oauthAccount merge-back, not modelled as a cred_file
            # because its host->project IMPORT was removed in 1.6.0). Route it to the
            # SELECTED tier source root (the SAME destination the cred_files writeback
            # used), NOT unconditionally to host home — otherwise a workset-tier box
            # (which explicitly isolated its identity to the workset store) would leak
            # its oauthAccount to GLOBAL. The private/box tier is already excluded by
            # the ``auth_src.creds_shared`` guard above.
            extra_dest = credsync.selected_source_root(auth_src, host_home=host_home)
            if extra_dest is not None:
                target.writeback_extra(
                    project_home=proj.shell_path, host_home=extra_dest
                )
                # global_sync: mirror the workset store's .claude.json UP to global,
                # matching the cred_files bottom-up hop (box→workset→global).
                if (
                    auth_src.tier == "workset"
                    and auth_src.global_sync
                    and extra_dest != host_home
                ):
                    target.writeback_extra(
                        project_home=extra_dest, host_home=host_home
                    )
            # D Part 3 (flag hygiene / lazy fallback): a SUCCESSFUL writeback clears
            # the box's creds-dirty flag, so it never goes stale or gets double-
            # processed.  This covers EVERY host writeback path (start detach / clean
            # exit / reattach, stop) AND the watcher itself (it calls this) — a
            # universal safety net for foreground / old-image / dead-watcher boxes.
            clear_creds_dirty(proj.shell_path)
    except Exception as exc:  # never crash a teardown path on writeback
        get_logger("start").warning("Credential writeback failed: %s", exc)


def _teardown_persistent_box(runtime: ContainerRuntime, container_name: str) -> None:
    """Remove a persistent box once its session has truly ended.

    Two-state lifecycle ("d"): a persistent box TEARS DOWN on exit but a DETACH
    keeps it running and reattachable.  After the attach exec returns, the
    container being NOT running is GROUND TRUTH that the in-box shell/agent
    exited (the tmux session ended); a still-running container means the client
    detached (Ctrl-b d or a dropped client) and must be kept.

    Call AFTER the exited-logs reprint and writeback.  Best-effort: a removal
    failure logs a warning and never crashes the CLI or changes the exit code.
    Never touches a still-running (detached) container.
    """
    if runtime.is_running(container_name):
        # Detached (or dropped client) — keep it running and reattachable.
        return
    if not runtime.container_exists(container_name):
        return
    try:
        runtime.rm(container_name)
    except Exception as exc:  # never crash the lifecycle on cleanup
        get_logger("start").warning(
            "Could not remove exited box %s: %s", container_name, exc
        )


def _build_config_env(
    global_env_path,
    agent_env: dict[str, str],
    workset_env_path,
    project_env_path,
) -> dict[str, str]:
    """Layer config-level env vars, low->high: system < agent < workset < box.

    Shared between container launch (start) and ``box show --effective`` so
    the resolved config-env matches exactly. Runtime-only layers (target state
    env, per-run ``-e``) are applied by the caller ON TOP of this and are NOT
    config, so they are excluded here.
    """
    from kanibako.shellenv import read_env_file
    env: dict[str, str] = {}
    env.update(read_env_file(global_env_path))   # system
    env.update(agent_env)                        # agent
    if workset_env_path is not None:
        env.update(read_env_file(workset_env_path))  # workset
    env.update(read_env_file(project_env_path))  # box (highest config level)
    return env


def _emit_secret_mounts(reconciled, logger) -> "tuple[list, list[str]]":
    """Emit the SECRET-category (``secret_path``) ro Mounts + the export VAR list.

    ARM'S-LENGTH delivery (spec §2a SECRET category, 2026-07-06): each resolved
    ``secret_path.<VAR>`` winner maps a host PATH (a 0600 token file) to a ro bind
    mount at ``SECRET_MOUNT_DIR/<VAR>``. kanibako NEVER reads the file VALUE — it
    ships only the PATH: the value flows host→podman→box via the mount, and the
    box-side export shim (:func:`_secret_export_shim`) reads it into ``<VAR>`` at
    agent start. So the secret is never in our process memory, never on the podman
    argv (only the mount PATH is), never in the snapshot/keystore/logs.

    The winners come from the SINGLE launch reconcile (``reconcile_categories``
    already picked the per-VAR precedence winner — a box ``secret_path.<VAR>`` beats
    a workset one at the identical ``SECRET_MOUNT_DIR/<VAR>`` box_dest). Each entry's
    ``host_src`` is the cascade-resolved path (already ``~``/``$VAR`` host-expanded by
    the snapshot expand pass); it is re-expanded defensively (idempotent on an
    absolute path) so a hand-set relative ``~`` pointer still resolves.

    FAIL-SOFT: a missing / unreadable / EMPTY host file is WARNED (loudly, to stderr
    via the WARNING logger) and the VAR is left OUT of both the mount list AND the
    export list — never a crash. A persona then fails auth with a clear symptom.
    Emptiness is checked via ``stat`` (``st_size == 0``) — kanibako does NOT read the
    contents to decide. (A whitespace-only NON-empty file is not detected as empty —
    arm's-length cannot inspect contents — so it delivers a whitespace/empty VALUE
    in-box and auth then fails clearly; the inherent price of never reading the secret.)
    Every log line references the VAR name + source PATH only — the token VALUE is
    NEVER logged (nor read). An invalid VAR name (not a plain env identifier) is also
    dropped fail-soft: it would otherwise be interpolated into the export shim.

    Returns ``(mounts, export_vars)``: the ordered ``list[Mount]`` (ro, NO ``:U``)
    and the ordered ``list[str]`` of VAR names the shim must export. An EMPTY
    ``export_vars`` means NO shim is applied (the byte-identical no-secret path).
    """
    from kanibako.targets.base import Mount

    mounts: list = []
    export_vars: list[str] = []
    for e in reconciled.mounts:
        if e.category != "secret_path":
            continue
        var = e.name
        # DEFENSE-IN-DEPTH: the VAR is interpolated into a generated ``sh -c`` export
        # shim (:func:`_secret_export_shim`), so re-enforce the plain-identifier shape
        # HERE — a VAR that bypassed ``config set`` validation (hand-edited YAML, or a
        # broader settable surface) must never reach the shell. Fail-soft skip+warn.
        if not SECRET_VAR_RE.match(var):
            logger.warning(
                "secret_path: ignoring invalid VAR name %r (must be a plain env "
                "identifier [A-Za-z_][A-Za-z0-9_]*); not delivered", var,
            )
            continue
        # host_src is the cascade-resolved path; re-expand $VAR then ~ defensively.
        assert e.host_src is not None  # secret_path entries always carry a path.
        src = Path(os.path.expandvars(e.host_src)).expanduser()
        # FAIL-SOFT arm's-length checks — stat only, NEVER read the contents.
        try:
            st = src.stat()
        except FileNotFoundError:
            logger.warning(
                "secret_path: token file not found at %s; %s unset "
                "(agent may fail auth)", src, var,
            )
            continue
        except OSError as exc:
            logger.warning(
                "secret_path: cannot stat token file at %s (%s); %s unset "
                "(agent may fail auth)", src, exc.strerror or "unreadable", var,
            )
            continue
        if not src.is_file():
            logger.warning(
                "secret_path: %s is not a regular file; %s unset "
                "(agent may fail auth)", src, var,
            )
            continue
        if not os.access(src, os.R_OK):
            logger.warning(
                "secret_path: token file at %s is unreadable; %s unset "
                "(agent may fail auth)", src, var,
            )
            continue
        if st.st_size == 0:
            logger.warning(
                "secret_path: token file at %s is empty; %s unset "
                "(agent may fail auth)", src, var,
            )
            continue
        # ro mount, NO ``:U`` (never chown the host secret). box_dest is the fixed
        # SECRET_MOUNT_DIR/<VAR> the entry already carries.
        mounts.append(Mount(source=src, destination=e.box_dest, options="ro"))
        export_vars.append(var)
    return mounts, export_vars


def _secret_export_shim(
    program: str, args: list[str], export_vars: list[str],
) -> "tuple[str, list[str]]":
    """Wrap ``(program, args)`` in a ``sh -c`` shim that exports each secret VAR
    from its ro mount, then ``exec``s the agent — the box-side half of the
    arm's-length SECRET delivery.

    The shim runs, at agent start, ``export <VAR>="$(cat SECRET_MOUNT_DIR/<VAR>)"``
    for each *export_vars* entry, then ``exec``s the original *program* with its
    *args*. kanibako writes only the export STATEMENT referencing the MOUNT PATH —
    it never ``cat``s the file itself (the ``cat`` runs IN the box). The VARs are
    read from an EXPLICIT list (not a ``for f in .../*`` glob) so the shim depends on
    nothing but the mounts we placed, and an unresolved VAR simply never mounts.

    Returns ``("sh", ["-c", <script>, "sh", program, *args])`` so ``sh -c`` sets
    ``$0=sh`` and ``$@=program args`` and ``exec "$@"`` runs the agent with its args
    intact. This nests inside the existing tmux/bootstrap wrap (the caller passes the
    returned pair as that wrap's inner command). ONLY called when *export_vars* is
    non-empty — a box with NO secrets keeps the bare entrypoint BYTE-IDENTICAL.
    """
    import shlex

    lines: list[str] = []
    for var in export_vars:
        # Both the VAR name and the mount path are shell-quoted so a pathological
        # VAR (there is none — VAR is the [A-Za-z_][A-Za-z0-9_]* env-name shape) or
        # path can never break out of the export statement. The value is read IN the
        # box from the mount (``cat``) — kanibako never reads it.
        mount_path = f"{SECRET_MOUNT_DIR}/{var}"
        lines.append(
            f'export {var}="$(cat {shlex.quote(mount_path)})"'
        )
    script = "; ".join(lines) + '; exec "$@"'
    return "sh", ["-c", script, "sh", program, *args]


def _directive_flatten_shim(
    program: str, args: list[str],
) -> "tuple[str, list[str]]":
    """Wrap ``(program, args)`` in an ``sh -c`` shim that FLATTENS the instruction
    SEED into the agent's native instruction slot, then ``exec``s the agent — the
    DEFAULT instruction-delivery mechanism for EVERY agent (increment 2b; made the
    universal default 2026-07-12).

    Before ``exec``, the shim runs (in-box) the flattener in its SOURCE→DEST file
    mode — ``python3 <flattener> "$KANIBAKO_DIRECTIVE_SEED" "$KANIBAKO_DIRECTIVE_FINAL"``
    — writing the flattened ``@import`` directive chain to the agent's native slot
    (``$KANIBAKO_DIRECTIVE_FINAL``: claude ``~/.claude/CLAUDE.md``, codex
    ``~/.codex/AGENTS.md``, goose ``~/.config/goose/.additionalContext.md``).  This
    lands the FULL guide in the native, always-loaded instruction file — the strong,
    uncapped channel — instead of the 2K/10K-capped ``additionalContext`` hook (kept
    as a secondary/future channel).  The flattener is MACHINERY, not instructional
    text, so it does not live in the canon: it ships in the kanibako package itself
    and rides the existing unconditional ``kani_pkg`` bind (whole package dir, ro)
    to ``/opt/kanibako/kanibako/scripts/import-directives.py`` — no extra bind, no
    extra key.  No sudo — the native slots are agent-owned.  ⚑ The SAME literal is
    carried by the SessionStart hook command in :mod:`kanibako.vscode.vscode_config`; the
    two must move TOGETHER, because a one-sided change is SILENT (``|| true``
    swallows the failure and the box just loses its directives).  SILENT-SAFE
    (``|| true``) and GUARDED on
    ``$KANIBAKO_DIRECTIVE_FINAL`` being set, so a launch with NO directive slot
    (e.g. a no-agent shell) skips the flatten cleanly.

    Returns ``("sh", ["-c", <script>, "sh", program, *args])`` so ``sh -c`` sets
    ``$0=sh`` and ``$@=program args`` and ``exec "$@"`` runs the agent with its
    args intact — nesting inside the tmux/bootstrap wrap exactly like
    :func:`_secret_export_shim`.  Per-session refresh is via the SessionStart hook
    where available (racy — accepted); this launch-flatten is the reliable baseline.
    """
    script = (
        'if [ -n "$KANIBAKO_DIRECTIVE_FINAL" ]; then '
        'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" '
        '"$KANIBAKO_DIRECTIVE_SEED" "$KANIBAKO_DIRECTIVE_FINAL" || true; '
        'fi; '
        'exec "$@"'
    )
    return "sh", ["-c", script, "sh", program, *args]


# --------------------------------------------------------------------------- #
# Persona LOAD-OR-ERROR pre-flight (A + B3, Jei dogfood 2026-07-03).            #
# --------------------------------------------------------------------------- #
#
# A persona (node != harness, e.g. ``navigator℘claude``) that CANNOT resolve a
# loadable endpoint must ERROR before any launch or artifact — never silently
# degrade to bare host claude on the user's real Anthropic account.  "Loadable" =
# a resolvable endpoint from EITHER the keyspace (``agent.<node>.endpoint``) OR —
# when the persona is not recognised — auto-adopted (B3) from the host dir the
# class setup script writes, ``~/.config/claude/<persona>/``.
#
# The host-login OAuth env var (BASE_URL) and the bearer token reach the box
# through the EXISTING single-route channels (no bespoke copy): the endpoint via
# the descriptor's ``endpoint``->``ANTHROPIC_BASE_URL`` env (populated into
# ``agent_cfg.state``), the token via the ``secret_path`` category (arm's-length ro
# mount + in-box export), and any other non-secret settings.json env (the model-map
# ``ANTHROPIC_DEFAULT_*_MODEL``) via the agent ``env`` channel (``_build_config_env``).
# BASE_URL and the token are carried by their dedicated channels and excluded from
# the env overlay so each var has exactly ONE source.

#: The persona's bearer token env var (harness-agnostic here: the persona MVP
#: bearer channel).  Excluded from the model-map env overlay (delivered via the
#: ``secret_path`` category so the secret lives only in the host file).
_PERSONA_TOKEN_VAR = "ANTHROPIC_AUTH_TOKEN"


def _secret_pointer_usable(raw_path: str) -> bool:
    """True iff *raw_path* points at a usable secret file — WITHOUT reading it.

    ARM'S-LENGTH: expands ``$VAR``/``~`` then STATs the path (regular file,
    readable, non-empty) but NEVER opens it — the persona preflight only needs to
    know a token is PRESENT, not its value. Mirrors the fail-soft stat checks in
    :func:`_emit_secret_mounts`, so preflight-usable ≡ launch-mountable.
    """
    try:
        p = Path(os.path.expandvars(str(raw_path))).expanduser()
        st = p.stat()
    except OSError:
        return False
    return p.is_file() and os.access(p, os.R_OK) and st.st_size > 0
#: The base-URL env var carried by the ``endpoint`` descriptor (its single
#: source); excluded from the model-map env overlay so it is never double-sourced.
_PERSONA_BASE_URL_VAR = "ANTHROPIC_BASE_URL"


def _persona_host_dir(persona: str) -> Path:
    """The host config dir the class setup script writes for *persona*.

    ``$XDG_CONFIG_HOME/claude/<persona>/`` (``~/.config/claude/<persona>/`` by
    default) — the same convention a hand-set ``secret_path`` pointer expands.
    """
    return xdg("XDG_CONFIG_HOME", ".config") / "claude" / persona


def _adopt_persona_from_host_dir(
    persona: str,
) -> "tuple[str, dict[str, str], str] | None":
    """B3 auto-adopt: read a persona's config from its host dir.

    Reads ``~/.config/claude/<persona>/settings.json`` and returns
    ``(base_url, extra_env, token_path)`` when it yields an ``env.ANTHROPIC_BASE_URL``:

    * *base_url* — the alternate endpoint (drives ``agent.<node>.endpoint`` and,
      through it, the OAuth-suppress cred fork + the ``ANTHROPIC_BASE_URL`` env);
    * *extra_env* — the rest of the settings.json ``env`` block (the model-map
      ``ANTHROPIC_DEFAULT_*_MODEL``), MINUS the base-URL and bearer-token vars
      (each of those has its own single-source channel);
    * *token_path* — the ``token`` file path (delivered via the ``secret_path``
      category → arm's-length ro mount + in-box export; returned even when absent so
      the caller emits the token-missing error).

    Returns ``None`` when the dir / settings.json is absent, unreadable, not a
    JSON object, or carries no ``ANTHROPIC_BASE_URL`` (→ the caller hard-errors:
    an unrecognised, unadoptable persona is unloadable).  NEVER logs the token.
    """
    host_dir = _persona_host_dir(persona)
    settings = host_dir / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    env = data.get("env")
    if not isinstance(env, dict):
        return None
    base_url = env.get(_PERSONA_BASE_URL_VAR)
    if not isinstance(base_url, str) or not base_url:
        return None
    # Skip non-string env values (a JSON number/bool/null would otherwise be
    # str()'d into a Python repr and delivered as a bogus env value); the
    # base-URL and bearer token are carried by their own single-source channels.
    extra_env = {
        k: v
        for k, v in env.items()
        if isinstance(v, str) and k not in (_PERSONA_BASE_URL_VAR, _PERSONA_TOKEN_VAR)
    }
    token_path = host_dir / "token"
    return base_url, extra_env, str(token_path)


def _name_new_box_probe(std, proj) -> None:
    """Carry a deterministic gate-name onto a brand-NEW box PROBE (F5/F7 fix).

    A NON-materialising resolve (``initialize=False``) of a BRAND-NEW box yields
    ``proj.name == ''`` — the name is assigned only inside the ``initialize=True``
    branch, coupled to dir creation.  The persona load-or-error gate resolves the
    box's channel partition addresses via ``box_channel_addresses``, which RAISES
    for a nameless box (``channels.py``) — so without a name the gate crashes
    BEFORE it can verdict (F7 on the launch path, F5 on the create path).  Give the
    probe the name it WILL be materialised under so the gate resolves cleanly:

    * PRIMARY — :func:`~kanibako.settings.paths.pick_primary_box_name` (DETERMINISTIC: the
      workspace basename plus a collision check against the SAME primary
      membership/boxes state the later ``initialize=True`` resolve reads, so the
      probe name == the name the materialise picks — gate → materialise happen in
      ONE invocation with no intervening registry/dir writes; single-source guard met).
    * STANDALONE / other — the standalone identity is a ``<kuid>_<leaf>``
      assigned at materialise, so it cannot be predicted; but a standalone box's
      config lives at ``<root>/settings.yaml`` (name-INDEPENDENT) and is empty for a
      brand-new box, so the box NAME never influences the persona endpoint verdict.
      A stable placeholder (``short_hash`` of the project hash) unblocks the address
      derivation without selecting any config (no divergent-config hazard).

    A probe that already resolved a real name (an existing / registered box) is left
    untouched.  Performs NO filesystem or registry mutation.
    """
    if proj.name:
        return
    from kanibako.settings.paths import BoxMode

    if proj.mode is BoxMode.primary:
        from kanibako.settings.paths import pick_primary_box_name
        proj.name = pick_primary_box_name(
            std.primary_workset, std.registry,
            str(proj.project_path), boxes_dir=std.boxes,
        )
    else:
        proj.name = short_hash(proj.project_hash)


def _reconcile_persona_store(
    agents_root, agent_id: str, target, agent_cfg, logger,
) -> "tuple[AgentConfig, bool]":
    """Per-launch persona-grata store reconcile (the persona ANALOG of credsync).

    Called for a PERSONA agent only (caller gates ``harness_of != node``),
    BEFORE the launch decisions consume ``agent_cfg``.  Reparses the
    persona-grata store entry for *agent_id* (when one exists) into a
    CANDIDATE config and applies the DESIGN §2b/§5b verified-swap rules:

    * no store entry → not a store-managed persona: return unchanged;
    * store present but unusable / candidate unbuildable → WARN, keep the
      current (last-known-good) config, continue the launch;
    * candidate == current owned values (endpoint/model/secret_path) → nothing
      to sync (no probe spent);
    * changed + probe PASS → SWAP (return the candidate; the caller's normal
      gated persist writes it AFTER the load-or-error pre-flight, so an
      unloadable persona still errors out with nothing left behind);
    * changed + FAIL/unverifiable → WARN + KEEP last-known-good + continue
      (never clobber working creds; never block a launch on a blip);
    * changed + FIRST-EVER (no prior endpoint — nothing working to protect) →
      use the candidate UNVERIFIED + WARN, whatever the probe said (refusing
      would only re-error as "no endpoint configured", masking the cause).

    Returns ``(agent_cfg, synced)`` — the EFFECTIVE config for the launch and
    whether it changed (the caller folds *synced* into ``agent_cfg_dirty`` so
    the existing post-preflight persist commits the swap).  NEVER persists
    here and NEVER raises through (a reconcile failure warns and launches on
    the last-known-good values).
    """
    from kanibako.persona_store import build_candidate, locate_entry

    entry = locate_entry(agent_id)
    if entry is None:
        return agent_cfg, False
    display = display_agent_ref(agent_id)
    result = build_candidate(agents_root, entry, target, base=agent_cfg)
    if result.candidate is None:
        print(
            f"Warning: persona store entry for '{display}' is unusable "
            f"({result.error}); keeping the current agent settings.",
            file=sys.stderr,
        )
        return agent_cfg, False
    if result.warning is not None:
        print(f"Warning: {result.warning}", file=sys.stderr)

    candidate = result.candidate
    changed = (
        candidate.state.get("endpoint") != agent_cfg.state.get("endpoint")
        or candidate.state.get("model") != agent_cfg.state.get("model")
        or candidate.secret_path != agent_cfg.secret_path
    )
    if not changed:
        logger.debug("persona '%s': store values unchanged; no sync", display)
        return agent_cfg, False

    verdict: "bool | None" = None
    if (
        result.settings is not None
        and result.settings.endpoint
        and result.token_path is not None
    ):
        try:
            verdict = target.verify_persona(
                result.settings.endpoint, result.token_path, result.settings.model,
            )
        except Exception:
            # The probe contract is never-raise; hold a misbehaving plugin to
            # it here so no persona launch can die on a probe bug (verdict
            # stays None = unverifiable).
            verdict = None
    if verdict is True:
        logger.debug("persona '%s': store values verified; syncing", display)
        return candidate, True

    first_ever = not agent_cfg.state.get("endpoint")
    why = (
        "the endpoint rejected the token"
        if verdict is False
        else "the endpoint could not be verified (unreachable, or no probe "
             "was possible)"
    )
    if first_ever:
        print(
            f"Warning: persona '{display}': {why}; no last-known-good values "
            f"exist, so the store values are used UNVERIFIED.",
            file=sys.stderr,
        )
        return candidate, True
    print(
        f"Warning: persona '{display}': the store values changed but {why}; "
        f"keeping the last-known-good agent settings for this launch.",
        file=sys.stderr,
    )
    return agent_cfg, False


def _persona_wiring(target) -> "PersonaSpec":
    """The HARNESS-specific persona endpoint/token delivery for *target* (INC 2).

    Consults the resolved :class:`~kanibako.targets.base.Target`'s descriptor for a
    declared :class:`~kanibako.targets.base.PersonaSpec`; a target with no descriptor
    or no ``persona:`` block falls back to the claude-style shape (ENV endpoint
    delivery + the fixed ``ANTHROPIC_AUTH_TOKEN`` token var + B3 host-dir adopt) so
    any pre-seam / no-target caller resolves BYTE-IDENTICALLY to before this seam
    existed.  That shape is spelled out EXPLICITLY at the fallback below: it is a
    legacy compatibility shape, NOT the field defaults (``host_dir_adopt`` defaults
    to ``False`` — an undeclared harness never inherits claude's adoption).

    ⚑ NEUTRALIZING this fallback (the planned T3.1 "a plugin that declares no
    ``persona:`` block gets NO persona wiring") NEEDS A VERSION FLOOR on the agent
    plugins: the plugin packages are unpinned deps, so a base upgraded WITHOUT its
    agent-claude package would fall through to a neutral spec and silently strip B3
    host-dir adoption from claude personas that resolve through it today.
    """
    from kanibako.targets.base import PersonaSpec

    desc = getattr(target, "descriptor", None) if target is not None else None
    spec = getattr(desc, "persona", None) if desc is not None else None
    if isinstance(spec, PersonaSpec):
        # An empty token_var means DYNAMIC (config-file harness): the configured
        # secret_path key is the token var.  An empty ENV-delivery token_var still
        # means the claude default — every SHIPPED env harness declares its own
        # (claude ANTHROPIC_AUTH_TOKEN, goose OPENAI_API_KEY), so this per-field
        # fallback now covers only OUT-OF-TREE plugins and VERSION SKEW (a plugin
        # built against an older base that declared nothing).
        if spec.endpoint_delivery == "env" and not spec.token_var:
            from dataclasses import replace
            return replace(spec, token_var=_PERSONA_TOKEN_VAR)
        return spec
    # LEGACY fallback shape (no descriptor / no ``persona:`` block): claude as it
    # resolved before this seam.  ``host_dir_adopt`` is passed EXPLICITLY because the
    # field default is False — this site keeps the pre-seam behaviour byte-identical
    # and is what a later neutralization of the fallback would remove.
    return PersonaSpec(
        token_var=_PERSONA_TOKEN_VAR, endpoint_delivery="env", host_dir_adopt=True,
    )


def _resolve_codex_persona_env_key(agent_cfg, wiring) -> "str | None":
    """The config-file persona's bearer token var == the model-provider ``env_key``.

    For a FIXED-var harness this is ``wiring.token_var``.  For the DYNAMIC codex MVP
    (empty ``token_var``) it is the SINGLE configured ``agent.<node>.secret_path``
    key — that key names BOTH the in-box env var the token is exported to AND the
    ``[model_providers.<id>].env_key`` the generated config.toml reads, so they
    cannot drift.  Returns ``None`` when there is no single unambiguous key (zero →
    no token; >1 → ambiguous), which the caller turns into an actionable error.
    """
    if wiring.token_var:
        return wiring.token_var
    keys = sorted(agent_cfg.secret_path)
    if len(keys) == 1:
        return keys[0]
    return None


def _resolve_codex_persona_provider(
    agent_id: str, endpoint: str, env_key: str, model: str, wiring
) -> "CodexModelProvider":
    """Build the resolved codex model-provider bundle INC 3 wires into config.toml.

    A pure assembly of the persona's resolved values (no I/O) into the
    :class:`~kanibako.vscode.vscode_config.CodexModelProvider` INC 3 feeds through
    ``CodexTarget.deliver_directive_hook`` →
    :func:`~kanibako.vscode.vscode_config.seed_codex_config`:

    * *provider_id* / *name* — the persona segment (``navigator``); the table id +
      the human-readable provider name (INC 3/4 may add a display-name keyspace key).
    * *base_url* — the resolved endpoint.
    * *wire_api* — the harness default (``responses`` for codex; Codex removed the
      ``chat`` wire per openai/codex#7782, validated live against a real NaviGator key).
    * *env_key* — the token var (== the ``secret_path`` key the bearer token is
      delivered through), so codex reads the key kanibako exports.
    * *model* — the CASCADE-resolved active-node model (``agent.<node>.model`` off the
      box-level launch snapshot, harness default excluded — see
      :func:`_resolve_box_launch_decisions`), passed in by the caller.  It is a load
      GATE (a codex persona needs a real model id): the caller rejects an empty
      *model* BEFORE reaching here, so this is always non-empty.
    """
    from kanibako.vscode.vscode_config import CodexModelProvider

    persona = persona_of(agent_id)
    return CodexModelProvider(
        provider_id=persona,
        name=persona,
        base_url=endpoint,
        wire_api=wiring.wire_api,
        env_key=env_key,
        model=model,
    )


def _preflight_persona_load(
    agent_id: str,
    agent_cfg,
    keyspace_endpoint: str | None,
    logger,
    *,
    target=None,
    keyspace_model: str | None = None,
) -> "tuple[str | None, str | None, bool, CodexModelProvider | None]":
    """Resolve a persona's LOADABILITY (A + B3) — a TRUE pre-flight.

    Called ONLY for a persona (``harness_of(agent_id) != agent_id``), BEFORE any
    persona artifact is created.  Returns ``(endpoint, error, adopted, provider)``:

    * *endpoint* — the resolved endpoint URL on success (``error`` None); ``None``
      when unloadable;
    * *error* — an actionable message when the persona cannot be loaded (unresolved
      endpoint, or endpoint-but-no-token); ``None`` on success.  The caller prints
      it (start) or raises it (create) and refuses to launch.
    * *adopted* — True iff B3 mutated *agent_cfg* in place (so the caller persists
      the adopted config).
    * *provider* — for a CONFIG-FILE harness (codex) the resolved
      :class:`~kanibako.vscode.vscode_config.CodexModelProvider` INC 3 wires into
      ``~/.codex/config.toml``; ``None`` for an ENV harness (claude) — whose endpoint
      + token ride their existing single-source channels (the
      ``endpoint``→``ANTHROPIC_BASE_URL`` :class:`SettingArg` env + the
      ``secret_path`` mount), so no separate carry is needed.

    HARNESS-AWARE (INC 2): the token var and endpoint DESTINATION are chosen per the
    resolved target's :func:`_persona_wiring`, not hardwired to Anthropic.  Claude
    (``endpoint_delivery == "env"``) keeps the ``ANTHROPIC_AUTH_TOKEN`` token var,
    the ``ANTHROPIC_BASE_URL`` env endpoint, and B3 host-dir auto-adopt — BYTE-
    IDENTICAL to before this seam.  Codex (``config_file``) resolves the endpoint +
    ``secret_path`` key (== the provider ``env_key``) from the KEYSPACE ONLY (no B3),
    and returns the CodexModelProvider for INC 3.

    ``keyspace_endpoint`` is the endpoint the launch snapshot already resolved from
    ``agent.<node>.endpoint`` (explicit config wins).  For an ENV harness, when it is
    ``None`` the persona is not recognised → B3 adopts from
    ``~/.config/claude/<persona>/``, populating ``agent_cfg.state['endpoint']``
    (endpoint), ``agent_cfg.secret_path`` (bearer token pointer) and ``agent_cfg.env``
    (model-map).  A resolved endpoint with NO usable token is ALSO a hard error (a
    bearer endpoint with no token 401s inside the box).

    ``keyspace_model`` is the box-level cascade-resolved ``agent.<node>.model`` (the
    single-source resolution done by the caller off the launch snapshot, harness
    default excluded — see :func:`_resolve_box_launch_decisions`).  It is used ONLY by
    the config-file (codex) path, which REQUIRES a real model id: an empty/absent
    model is a hard error there (a NaviGator provider block with ``model = ""`` is
    meaningless).  It is ignored by the ENV (claude) path, whose model rides its
    existing channels.
    """
    persona = persona_of(agent_id)
    wiring = _persona_wiring(target)
    endpoint = keyspace_endpoint
    adopted = False
    display = display_agent_ref(agent_id)

    # B3 host-dir auto-adopt is CLAUDE-SHAPED (reads ~/.config/claude/<persona>/
    # settings.json) — ENV delivery AND ``host_dir_adopt`` (claude ONLY).  A
    # config-file harness (codex) and a NON-claude env harness (goose) both resolve
    # from the KEYSPACE only: an absent endpoint is a hard error, never a
    # claude-host-dir adoption attempt against a dir that plays no part in their
    # resolution.
    if endpoint is None and wiring.endpoint_delivery == "env" and wiring.host_dir_adopt:
        adoption = _adopt_persona_from_host_dir(persona)
        if adoption is not None:
            base_url, extra_env, token_path = adoption
            agent_cfg.state["endpoint"] = base_url
            # Deliver the bearer token via the SECRET category (arm's-length ro
            # mount + in-box export; the secret stays in the host file — kanibako
            # never reads it). Do not clobber a pre-set keyspace pointer. Written to
            # ``agent.<persona>.secret_path.ANTHROPIC_AUTH_TOKEN`` when persisted.
            agent_cfg.secret_path.setdefault(wiring.token_var, token_path)
            # Deliver the model-map (and any other non-secret settings.json env)
            # via the agent env channel; keyspace env wins (do not clobber).
            for var, val in extra_env.items():
                agent_cfg.env.setdefault(var, val)
            endpoint = base_url
            adopted = True

    if endpoint is None:
        return None, _persona_no_endpoint_error(agent_id, persona, wiring), False, None

    if wiring.endpoint_delivery == "config_file":
        return _preflight_config_file_persona(
            agent_id, agent_cfg, endpoint, keyspace_model, wiring, display,
        )

    # --- ENV harness, KEYSPACE-config (goose): NO claude host-dir --------------
    # A non-claude env harness (``host_dir_adopt`` False) resolves its bearer token
    # and (required) model from the KEYSPACE only — it must NEVER read/word against
    # the claude-shaped ``~/.config/claude/<persona>/`` dir.
    if not wiring.host_dir_adopt:
        return _preflight_env_keyspace_persona(
            agent_cfg, endpoint, keyspace_model, wiring, display,
        )

    # --- ENV harness (claude): byte-identical token gate ------------------------
    host_dir = _persona_host_dir(persona)
    # Endpoint resolved — require a usable BEARER token.  A KEYSPACE-recognised
    # persona whose ``secret_path`` carries no token FALLS BACK to the host-dir
    # ``token`` file (F4): the endpoint may come from the keyspace while the class
    # setup script supplies the token on disk.  Only when NEITHER a resolvable
    # ``secret_path`` token NOR the host token exists is it an error.
    host_token = host_dir / "token"
    if not agent_cfg.secret_path.get(wiring.token_var) and host_token.exists():
        agent_cfg.secret_path[wiring.token_var] = str(host_token)
        adopted = True  # secret_path mutated in place → caller persists it.
    # Check the TOKEN var SPECIFICALLY (N1): a usable result for some OTHER
    # secret_path var does not mean a bearer token is present. STAT the pointer
    # arm's-length (never read the value — the value flows only via the launch mount).
    token_ptr = agent_cfg.secret_path.get(wiring.token_var)
    if not token_ptr or not _secret_pointer_usable(token_ptr):
        return None, (
            f"Error: persona '{display}' has an endpoint ({endpoint}) but no "
            f"auth token.\n"
            f"  A custom-endpoint persona needs a bearer token; none was found "
            f"(neither a configured secret_path nor {host_token}).\n"
            f"  Run the class setup script to write {host_token}, then retry."
        ), adopted, None
    return endpoint, None, adopted, None


def _preflight_env_keyspace_persona(
    agent_cfg,
    endpoint: str,
    keyspace_model: str | None,
    wiring,
    display: str,
) -> "tuple[str | None, str | None, bool, CodexModelProvider | None]":
    """Token + (optional) model gate for a KEYSPACE-config ENV harness (goose, INC G1).

    The endpoint is resolved (keyspace only — no claude host-dir B3).  Unlike the
    claude ENV path this NEVER consults ``~/.config/claude/<persona>/``: the bearer
    token comes ONLY from ``agent.<node>.secret_path.<token_var>`` (goose:
    ``OPENAI_API_KEY``), and the error wording points at the keyspace ``config set``
    route — NOT the claude class-setup script / host dir.

    Gates (each an ACTIONABLE, harness-appropriate error):

    1. a usable bearer token under the FIXED ``wiring.token_var`` (goose
       ``OPENAI_API_KEY``): missing/unusable ⇒ error pointing at
       ``agent.<node>.secret_path.<token_var>``.
    2. when ``wiring.model_required`` (goose): a real cascade-resolved ``model`` id
       (``keyspace_model``): empty ⇒ error (a third-party OpenAI-compatible endpoint
       has no meaningful default model — parity with the codex model gate).

    NEVER mutates *agent_cfg* (keyspace-only ⇒ nothing to adopt/persist), so
    ``adopted`` is False; the endpoint + token ride their existing single-source
    channels (the ``endpoint``→env ``SettingArg`` + the ``secret_path`` mount), so
    there is no config-file provider to carry (``provider`` None).
    """
    token_ptr = agent_cfg.secret_path.get(wiring.token_var)
    if not token_ptr or not _secret_pointer_usable(token_ptr):
        return None, (
            f"Error: persona '{display}' has an endpoint ({endpoint}) but no "
            f"usable auth token.\n"
            f"  A custom-endpoint persona needs an API key delivered via a "
            f"`kanibako config set agent.{display}.secret_path.{wiring.token_var}="
            f"<path>` entry; none was found (or it points at an unusable file).\n"
            f"  Set the key for this persona, then retry."
        ), False, None
    model = (keyspace_model or "").strip()
    if wiring.model_required and not model:
        return None, (
            f"Error: persona '{display}' has an endpoint ({endpoint}) but no "
            f"model configured.\n"
            f"  A custom-endpoint persona must name the provider's model id "
            f"(e.g. `kanibako config set agent.{display}.model=<model-id>`); the "
            f"harness default is not used for a third-party provider.\n"
            f"  Set the model for this persona, then retry."
        ), False, None
    return endpoint, None, False, None


def _persona_no_endpoint_error(agent_id: str, persona: str, wiring) -> str:
    """The actionable "no endpoint configured" error, worded per harness.

    ENV+host_dir_adopt (claude) distinguishes "no host config" from
    "present-but-unusable" (N2) and points at the class setup script / B3 host dir.
    Every OTHER harness — CONFIG-FILE (codex, no B3) and a NON-claude ENV harness
    (goose, ``host_dir_adopt`` False) — points ONLY at the keyspace ``config set``
    route (endpoint + API key); it never references a host dir that plays no part in
    its resolution.
    """
    display = display_agent_ref(agent_id)
    harness = harness_of(agent_id)
    if not wiring.host_dir_adopt:
        # KEYSPACE-config harness (codex config_file OR goose env-no-adopt): word the
        # error for the keyspace route.  An ENV harness (goose) also names the API-key
        # secret_path so the user has the full recipe; a config-file harness (codex)
        # resolves its key downstream, so it names only the endpoint here.
        key_hint = ""
        if wiring.endpoint_delivery == "env" and wiring.token_var:
            key_hint = (
                f" and its API key "
                f"(`kanibako config set agent.{display}.secret_path."
                f"{wiring.token_var}=<path>`)"
            )
        return (
            f"Error: persona '{display}' cannot be loaded — no endpoint is "
            f"configured for it.\n"
            f"  Kanibako will not launch a persona as bare host {harness} on your "
            f"real account.\n"
            f"  Set the endpoint for this persona (e.g. "
            f"`kanibako config set agent.{display}.endpoint=<url>`){key_hint}, "
            f"then retry."
        )
    host_dir = _persona_host_dir(persona)
    # Distinguish "no host config at all" from "host config present but with no
    # usable endpoint" (N2): a settings.json that lacks ANTHROPIC_BASE_URL is a
    # PRESENT-but-unusable config, not an absent one.
    if (host_dir / "settings.json").exists():
        detail = (
            f"the host config at {host_dir}/ is not usable "
            f"(settings.json has no env.ANTHROPIC_BASE_URL)"
        )
    else:
        detail = f"no host config was found at {host_dir}/"
    return (
        f"Error: persona '{display}' cannot be loaded — no endpoint is "
        f"configured for it and {detail}.\n"
        f"  Kanibako will not launch a persona as bare host {harness} on your "
        f"real account.\n"
        f"  Run the class setup script to create {host_dir}/ (settings.json + "
        f"token), or set the endpoint for this persona, then retry."
    )


def _preflight_config_file_persona(
    agent_id: str,
    agent_cfg,
    endpoint: str,
    keyspace_model: str | None,
    wiring,
    display: str,
) -> "tuple[str | None, str | None, bool, CodexModelProvider | None]":
    """Token + model gate + provider resolve for a CONFIG-FILE harness (codex, INC 2/3).

    The endpoint is resolved (keyspace only — no B3).  Two load gates, each with an
    ACTIONABLE, SUB-CASE-SPECIFIC error (INC-3 fold-in), then the provider assemble:

    1. a usable bearer token under the DYNAMIC token var (the single configured
       ``secret_path`` key == the provider ``env_key``).  Distinguished sub-cases:
       * ZERO keys → "no API key configured";
       * >1 keys → "ambiguous: multiple keys configured" (can't pick the env_key);
       * the single key's pointer is missing/unreadable/empty → "points at an unusable
         file" (names the key + path) — NOT a blanket "none was found".
    2. a real ``model`` id (``keyspace_model``, the box-level cascade override): empty
       ⇒ error (a NaviGator ``[model_providers.<id>]`` block needs a model — never ship
       ``model = ""``).

    On success return the resolved :class:`~kanibako.vscode.vscode_config.CodexModelProvider`
    for INC 3.  NEVER mutates *agent_cfg* (keyspace-only ⇒ nothing to adopt/persist),
    so ``adopted`` is False.
    """
    token_err = _codex_persona_token_error(agent_cfg, wiring, endpoint, display)
    if token_err is not None:
        return None, token_err, False, None
    # token gate passed ⇒ a single, usable secret_path key resolves the env_key.
    env_key = _resolve_codex_persona_env_key(agent_cfg, wiring)
    assert env_key is not None  # guaranteed by the passed token gate above.
    model = (keyspace_model or "").strip()
    if not model:
        return None, (
            f"Error: persona '{display}' has an endpoint ({endpoint}) but no "
            f"model configured.\n"
            f"  A custom-endpoint codex persona must name the provider's model id "
            f"(e.g. `kanibako config set agent.{display}.model=<model-id>`); the "
            f"harness default is not used for a third-party provider.\n"
            f"  Set the model for this persona, then retry."
        ), False, None
    provider = _resolve_codex_persona_provider(
        agent_id, endpoint, env_key, model, wiring,
    )
    return endpoint, None, False, provider


def _codex_persona_token_error(agent_cfg, wiring, endpoint: str, display: str) -> "str | None":
    """The SUB-CASE-specific bearer-token error for a config-file persona, or ``None``.

    Differentiates the three ways the DYNAMIC token var can fail to resolve so the
    message is actionable (INC-3 fold-in — INC 2 collapsed all three into a single
    "none was found").  A FIXED-var harness (non-empty ``wiring.token_var``) uses that
    var directly; the DYNAMIC codex MVP derives it from the single configured
    ``secret_path`` key.  Returns ``None`` when a single, usable token resolves.
    """
    intro = (
        f"Error: persona '{display}' has an endpoint ({endpoint}) but no "
        f"usable auth token.\n"
    )
    tail = "  Set the key for this persona, then retry."
    keys = sorted(agent_cfg.secret_path)
    # DYNAMIC (empty token_var): the env_key IS the single configured secret_path key.
    if not wiring.token_var and len(keys) > 1:
        return (
            f"{intro}"
            f"  A custom-endpoint codex persona needs a SINGLE "
            f"`agent.{display}.secret_path.<ENV_KEY>=<path>` entry (that <ENV_KEY> "
            f"becomes the model-provider env_key), but {len(keys)} are configured "
            f"({', '.join(keys)}) — ambiguous.\n"
            f"  Leave exactly one, then retry."
        )
    env_key = _resolve_codex_persona_env_key(agent_cfg, wiring)
    token_ptr = agent_cfg.secret_path.get(env_key) if env_key else None
    if not env_key or not token_ptr:
        return (
            f"{intro}"
            f"  A custom-endpoint codex persona needs an API key delivered via a "
            f"single `agent.{display}.secret_path.<ENV_KEY>=<path>` entry (that "
            f"<ENV_KEY> becomes the model-provider env_key); none was found.\n"
            f"{tail}"
        )
    if not _secret_pointer_usable(token_ptr):
        return (
            f"{intro}"
            f"  The configured key `{env_key}` points at an unusable file "
            f"({token_ptr}) — it must be a readable, non-empty file.\n"
            f"{tail}"
        )
    return None


def _effective_behavior_for_display(
    target,
    agent_cfg,
    project_toml,
    *,
    system_settings_path,
    workset_config_path=None,
    node_name=None,
) -> dict[str, str]:
    """Resolve the effective agent BEHAVIOR state for the ``config --effective``
    DISPLAY, off the SAME KeyStore snapshot the live launch reads (block 7c).

    Single-route + launch-FIDELITY: this builds the behavior snapshot exactly as
    :func:`_resolve_launch_snapshot` does for a launch — the target's declared
    defaults fold in as the ``agent.default.*`` floor (OS1); the per-agent FILE
    state (``agent_cfg.state``, flat ``[agent]``) is injected as ``agent_state``
    (the active slot ``agent.<active>.*``); the box / workset / system settings
    files merge as their discriminated ``agent.default.*`` / ``agent.<name>.*``
    tables through ``assemble_levels`` — then :func:`~kanibako.settings.settings_launch.
    effective_behavior` does the §2d active-over-default value-pick. So the
    ``config --effective`` view MATCHES the real launch behavior read.

    This REPLACES the retired OLD per-file resolver (machine-tier + the
    per-file-active-over-default-THEN-cascade order). The machine ``/etc`` tier
    was CUT at launch (S14, block 7b), so the old display could MISREPRESENT the
    actual launch (show machine values + the old resolution order the launch no
    longer uses); the snapshot path eliminates that drift.

    With no declared descriptors the target has no behavior floor — the effective
    state is just the per-agent file's raw state (preserved from the old
    early-return). Values are scalars, used verbatim (behavior has no @-ref tier).
    """
    from kanibako.settings import settings_launch
    from kanibako.settings.paths import host_xdg_map
    from kanibako.settings.settings_resolve import ResolveCtx

    descriptors = target.setting_descriptors()
    if not descriptors:
        return dict(agent_cfg.state)

    behavior_floor = {d.key: d.default for d in descriptors}
    agent_state = dict(agent_cfg.state)

    # The behavior tables are keyed by the ACTIVE node-name (fix 4a): for a persona
    # (``navigator℘claude``) the per-node ``agents/<node>/settings.yaml`` state and
    # the ``agent.<node>.*`` cascade slot key on the node, NOT the harness
    # (``target.name``) — else ``config --effective`` shows the bare-harness view
    # for a persona. Bare: node==harness==target.name → byte-identical. Falls back to
    # ``target.name`` when a caller omits the node (legacy / test convenience).
    active = node_name if node_name is not None else target.name

    # Canonical host XDG map (never a partial one): stored settings-file values
    # (e.g. a 1.6.0-era ``$XDG_CACHE_HOME/...`` cache entry) expand through this
    # ctx too, and the resolver reads ONLY the map — an empty map raised
    # "Variable $XDG_CACHE_HOME is not set in this context" here.
    ctx = ResolveCtx(
        agent_name=active,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=host_xdg_map(),
    )
    # Behavior-only snapshot: the scope settings files (box/workset/system) feed
    # their discriminated agent tables; the floor folds in as agent.default.* and
    # the per-agent file state as the agent.<active> slot. No category tables /
    # agent-binding inputs (display reads behavior only). The machine tier is CUT
    # (S14) — assemble_levels never consults /etc machine, matching the launch.
    snapshot = settings_launch.build_launch_snapshot(
        agent_name=active,
        ctx=ctx,
        # The system SETTINGS file (@config.settings = global/settings.yaml) —
        # the SAME system-tier file derivation the launch snapshot uses
        # (std.settings, see _run_container), NEVER the kanibako_config.yaml
        # CONFIG file: a system-level settings value that is live at launch
        # must be equally visible to `show --effective` (F2/F3 sibling; the
        # parameter was formerly named global_config_path, which invited
        # exactly that wrong-file confusion).
        system_path=system_settings_path,
        agent_path=None,
        workset_path=workset_config_path,
        box_path=project_toml,
        behavior_floor=behavior_floor,
        agent_state=agent_state,
    )
    return settings_launch.effective_behavior(snapshot, active_agent=active)


def _resolve_box_auth_source(
    *,
    std,
    proj,
    agent_name: str,
    system_settings_path,
    agent_cfg_path,
    selection_level: "Mapping[str, object] | None",
):
    """Resolve the box's credential-SHARING SOURCE through the auth 3-tier chain.

    The SINGLE source of the launch's sharing decision (auth-level redesign):
    builds a FOCUSED launch snapshot carrying ONLY the auth ``auth.*`` chain floor
    (``settings_launch.auth_chain_floor`` for the box mode) plus the scope settings
    files + the meta identity floor (which carries the agent capability
    ``meta.agent.<agent>.auth.share_support`` the mirror views up), expands it
    ONCE, and reads the :class:`~kanibako.settings.settings_launch.AuthSource` off it
    (``resolve_auth_source`` — the per-box tier/source resolver, precedence
    workset>global). Same ``build_launch_snapshot`` → ``expand`` pipeline the
    launch uses (single-route).

    Computed ONCE per launch and threaded to every credsync/gate consumer (the
    early reattach / seed / refresh sites, the main ``reconcile_categories`` feed,
    and the auto-auth / writeback gates) so the decision is consistent everywhere.
    Private/no-share = ``box.auth.{global,workset}_enabled=false`` (settable via
    config); there is no flag to plumb.

    A scope settings FILE that overrides a settable chain key
    (``box.auth.global_enabled`` / ``workset.auth.share_allowed`` / …) wins by name
    through the cascade; the floor is the backstop.

    ⚑ *selection_level* is a REQUIRED keyword — deliberately NOT defaulted (P7).
    ``meta.box.auth.workset_path`` resolves ``@workset.auth.path/@system.agent``
    (spec §2c), so a caller that omits it silently collapses the per-agent
    credential dir to the workset auth ROOT: the launch would deliver from
    ``<auth>/<agent>`` while stop/watch/reauth read and WRITE ``<auth>/``, and two
    agents in one workset would share a directory. Pass ``None`` ONLY for a
    genuinely agent-less box; for a running box the ``KANIBAKO_AGENT`` stamp IS the
    resolved selection.

    ⚑ It is the SELECTION only — deliberately NOT the full §1A CLI level (P8). This
    resolve feeds the CREDENTIAL lifecycle, which creates and syncs directories on
    disk, and an ephemeral flag may never reach a resolve whose output is WRITTEN
    (spec §1A *"EPHEMERAL, always … NEVER mutates a stored value"*). The parameter
    keeps the narrow name so the seam says what may travel through it.
    """
    from kanibako.settings import settings_launch

    (
        ctx, resolved_sys, meta_runtime, meta_identity, workset_anchor,
        cascade_box_path, cascade_workset_path,
    ) = _launch_snapshot_inputs(std=std, proj=proj, agent_name=agent_name)
    chain = settings_launch.auth_chain_floor(
        mode=proj.mode.value,
        agent_name=agent_name,
    )
    # The resolved system.* tier is folded into the floor (default_categories
    # here carries ONLY system.* — no category families) so any @-ref in the
    # chain that reaches system.* resolves; this keeps the focused snapshot
    # consistent with the main one. The cascade box/workset tier file paths are the
    # mode-aware single source (P6c) — standalone reads <root>/settings.yaml as the
    # WORKSET tier, box tier empty.
    snapshot = settings_launch.build_launch_snapshot(
        agent_name=agent_name,
        ctx=ctx,
        system_path=system_settings_path,
        agent_path=agent_cfg_path,
        workset_path=cascade_workset_path,
        box_path=cascade_box_path,
        default_categories=dict(resolved_sys),
        auth_chain=chain,
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
        # ⚑ REQUIRED here (P7): ``meta.box.auth.workset_path`` is now spelled
        # ``@workset.auth.path/@system.agent`` (spec §2c), so this snapshot
        # must carry the RESOLVED selection or the per-agent credential source
        # would degenerate to the workset auth ROOT for any launch whose agent
        # came from ``--agent`` or the installed-count rule.
        cli_level=selection_level,
    )
    return settings_launch.resolve_auth_source(snapshot, mode=proj.mode.value)


def _resolve_box_launch_decisions(
    *,
    std,
    proj,
    target,
    agent_name: str,
    agent_cfg,
    system_settings_path,
    agent_cfg_path,
    selection_level: "Mapping[str, object] | None",
) -> "tuple[AuthSource, str | None, str | None]":
    """Resolve the launch's per-box decisions (auth SOURCE + persona endpoint + persona
    model) off ONE snapshot — the single-source consolidation of the auth resolve and
    the behavior (endpoint/model) resolve.

    ``build_launch_snapshot`` accepts BOTH the auth 3-tier ``auth_chain`` floor AND
    the behavior ``behavior_floor`` in a single call, so the box's sharing decision
    (:class:`~kanibako.settings.settings_launch.AuthSource`, ``resolve_auth_source``) and its
    active-node ``agent.<node>.endpoint`` (``effective_behavior``) are read off the
    SAME expanded snapshot — no duplicate build. Same pipeline the main launch uses
    (single-route).

    * *auth_src* — the credential-SHARING SOURCE (tier/source + enables), threaded to
      every credsync/gate consumer, exactly as :func:`_resolve_box_auth_source`.
    * *endpoint* — the resolved PERSONA endpoint URL, or ``None`` when unset
      (``<None>`` / empty / no descriptors / no target) — the cred-fork signal
      (non-None ⇒ suppress the OAuth cred). ``None`` is byte-identical to today.
    * *model* — the cascade-resolved active-node ``agent.<node>.model`` (the box-level
      override where set), or ``None`` when unset.  The codex-persona provider needs a
      real model id, so this DELIBERATELY excludes the harness ``model`` DEFAULT floor
      (e.g. codex's ``gpt-5.5`` — an own-endpoint default that is wrong for a
      third-party provider): an unset persona model resolves ``None`` here so the
      preflight surfaces an actionable empty-model error rather than shipping a bogus
      default into a NaviGator ``[model_providers.<id>]`` block.  Only consumed for a
      config-file (codex) persona; unused (harmless) for bare/claude launches, where
      the ``--model`` flag still resolves its own default via the main launch snapshot.

    The behavior floor folds in as ``agent.default.<key>`` (OS1) and the per-agent
    FILE state as the active ``agent.<node>`` slot; the §2d active-over-default pick
    yields the endpoint for the NODE (persona identity). A target with no declared
    settings contributes no floor → endpoint ``None`` (bare).

    ⚑ *selection_level* is the SELECTION only — deliberately NOT the full §1A CLI
    level (P8). The *model* this returns is threaded into ``_preflight_persona_load``
    and lands in the codex ``config.toml`` ``[model_providers.<id>]`` block, i.e. it
    is WRITTEN TO DISK; letting ``-M`` reach it would make an ephemeral flag mutate a
    stored value, which spec §1A forbids (*"EPHEMERAL, always"*). ``-M`` therefore
    rides the LAUNCH snapshot only (``_resolve_launch_snapshot``), where it reaches
    argv and the container env and nothing else.
    """
    from kanibako.settings import settings_launch

    (
        ctx, resolved_sys, meta_runtime, meta_identity, workset_anchor,
        cascade_box_path, cascade_workset_path,
    ) = _launch_snapshot_inputs(std=std, proj=proj, agent_name=agent_name)
    chain = settings_launch.auth_chain_floor(
        mode=proj.mode.value,
        agent_name=agent_name,
    )
    descriptors = target.setting_descriptors() if target is not None else []
    # A real target returns a list of TargetSetting; guard against a non-list (e.g.
    # a MagicMock target in unit tests) so the behavior floor / endpoint read is
    # skipped rather than iterating a mock — endpoint then stays None (bare).
    #
    # ``model`` is DELIBERATELY dropped from the floor (see the *model* return note):
    # the persona model must be EXPLICITLY configured (any cascade scope), never
    # backfilled by the harness's own-endpoint default — so the resolved model below
    # reflects only scopes that actually set ``agent.<node>.model`` and is empty/None
    # when unset.  Dropping it here is safe: this snapshot's ``effective`` is read for
    # endpoint + model only, and the bare-box ``--model`` flag resolves its own
    # default via the separate main-launch snapshot.
    behavior_floor = (
        {d.key: d.default for d in descriptors if d.key != "model"}
        if isinstance(descriptors, list)
        else {}
    )
    snapshot = settings_launch.build_launch_snapshot(
        agent_name=agent_name,
        ctx=ctx,
        system_path=system_settings_path,
        agent_path=agent_cfg_path,
        workset_path=cascade_workset_path,
        box_path=cascade_box_path,
        behavior_floor=behavior_floor or None,
        # agent_state (the active-node slot) is only needed when we actually read
        # behavior; gated on behavior_floor so a no-descriptor / mock target never
        # dereferences agent_cfg.state.
        agent_state=(
            dict(agent_cfg.state)
            if behavior_floor and agent_cfg is not None
            else None
        ),
        default_categories=dict(resolved_sys),
        auth_chain=chain,
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
        # ⚑ REQUIRED here (P7): ``meta.box.auth.workset_path`` is now spelled
        # ``@workset.auth.path/@system.agent`` (spec §2c), so this snapshot
        # must carry the RESOLVED selection or the per-agent credential source
        # would degenerate to the workset auth ROOT for any launch whose agent
        # came from ``--agent`` or the installed-count rule.
        cli_level=selection_level,
    )
    auth_src = settings_launch.resolve_auth_source(snapshot, mode=proj.mode.value)
    endpoint: str | None = None
    model: str | None = None
    if behavior_floor:
        effective = settings_launch.effective_behavior(
            snapshot, active_agent=agent_name
        )
        endpoint = effective.get("endpoint", "") or None
        # model resolves off the SAME snapshot (box-level cascade override where set);
        # the harness default was excluded from the floor above, so an unset persona
        # model is absent/empty here → None (empty-model error, not a bogus default).
        model = effective.get("model", "") or None
    return auth_src, endpoint, model


def _launch_snapshot_inputs(
    *,
    std,
    proj,
    agent_name: str,
):
    """Build the (ctx, resolved_sys, meta_runtime, meta_identity, workset_anchor,
    cascade_box_path, cascade_workset_path) the launch SNAPSHOT path needs.

    Constructs the host_home / xdg / workset name / resolved ``system.*`` map the
    ONE-resolve snapshot path (block 7b) feeds to ``build_launch_snapshot`` so
    every @-ref resolves. There is NO per-scope source-root table: a stored source
    resolves on its own (spec §2a) and the abstract categories are rooted
    at DECLARATION, so nothing is prefixed on the way to a mount. This is
    now the SOLE category-resolution input builder — the old per-family
    ``_category_resolution_inputs`` (a second LevelView-cascade route) was retired
    in block 7c; the snapshot pipeline is the single route for both reads and the
    seed/synced/channel/share resolves.

    *meta_runtime* (block B1) is the ``meta.runtime.*`` identity-anchor floor for
    *proj*'s mode (spec §1A) — built HERE because the per-mode treewalk
    values (``proj.mode`` / ``proj.group.root`` / the project dir) are known on
    *proj*. PRIMARY uses the ``@config.primary_workset`` @-ref; NAMED uses the
    detected workset root literal (``str(proj.group.root)``); STANDALONE uses the
    runtime project dir literal (``str(proj.project_path)``). Folded into the
    snapshot floor so ``expand`` resolves the @-ref chain ONCE (single-route).

    *cascade_box_path* / *cascade_workset_path* are the mode-aware box-tier /
    workset-tier settings-file paths the cascade mounts (``box_workset_settings_
    paths``): the SINGLE SOURCE the snapshot resolvers pass as
    ``build_launch_snapshot(box_path=…, workset_path=…)``, and the SAME box-tier path
    that materializes ``meta.box.settings`` — so the anchor and the cascade cannot
    drift. STANDALONE = ``(<root>/box_data/settings.yaml, <root>/settings.yaml)``:
    a real box tier (absent by default) over the ROOT file that plays the workset
    tier; primary/named unchanged.
    """
    from kanibako.settings import settings_launch as settings_launch_module
    from kanibako.settings.agent_select import launch_resolve_ctx
    from kanibako.settings.paths import ProjectError

    # ONE ctx builder (P7): the SELECTION pre-pass resolves against the identical
    # host-side namespace, so the two passes cannot disagree about what
    # ``@config.*`` / ``$XDG_*`` / ``~`` mean. See ``agent_select.launch_resolve_ctx``
    # for the resolver-SPLIT rationale this call carries.
    ctx = launch_resolve_ctx(std, proj, agent_name)

    # The Layer-2 system.* path tier the category @-refs resolve against.  These
    # are present IN the snapshot (folded into the floor as ``system.<leaf>``
    # keys) so ``expand`` resolves them — replicating the old ``_lookup``'s
    # ``resolved_sys`` map.  channelroot/template/canon each @-ref a config key,
    # already resolved into ``std`` by the flat foundation.
    # ⚑ ``commands/workset_cmd._print_effective_shares`` builds a SECOND map of the
    # same tier for ``workset config show --effective``.  Both must carry the same
    # keys or the display diverges from what a launch mounts; pinned by
    # ``tests/test_commands/test_workset_cmd.py::TestWorksetCmdSystemFloor``.
    resolved_sys = {
        "system.channelroot": str(std.channels),
        "system.template": str(std.template),
        # The SYSTEM-level CANON CONTRIBUTION root (spec §2g) — the source of the
        # handbook's SYS_CONTENTS.md + general chapter binds, and the install dest of
        # the packaged handbook. ⚑ It names a per-scope CONTRIBUTION root, NOT a copy
        # of the assembled canon: ``~/canon`` (guest) is the assembled tree,
        # ``@<scope>.canon`` (host) is what that scope contributes to it.
        "system.canon": str(std.canon),
        # B2b: the resolved system channel type-roots (spec §2g) — folded in so the
        # @system.channels.* ALL-PROJECTS channel binds (global_common/chat/share/
        # mailboxes, §2c) resolve from the snapshot.  Each equals the
        # corresponding ``std.channels_*`` (the same flat foundation resolves both),
        # so the @-ref-routed bind is byte-identical to the runtime-probed literal.
        "system.channels.common": str(std.channels_common),
        "system.channels.chat": str(std.channels_chat),
        "system.channels.share": str(std.channels_share),
        "system.channels.mailboxes": str(std.channels_mailboxes),
    }

    # meta.runtime.* identity anchors (block B1, spec §1A). The per-mode
    # treewalk values are known on ``proj``; surface them as the snapshot's RO
    # ``meta.runtime.*`` keys + the single-source re-root of meta.workset.path /
    # meta.workset.settings / meta.box.mode. PRIMARY → the @config.primary_workset
    # @-ref (live-propagates from the foundation); NAMED → the detected workset
    # root literal; STANDALONE → the project ROOT.
    mode = proj.mode.value
    if mode == "named":
        if proj.group is None:
            raise ProjectError(
                "named-mode project has no workset group (meta.runtime.ws_root)"
            )
        ws_root_literal = str(proj.group.root)
    elif mode == "standalone":
        # B2b FIX (was the B1 defect): standalone meta.runtime.ws_root must be the
        # project ROOT (<root>), NOT proj.project_path (= <root>/workspace, the
        # workspace SUBDIR).  Spec §2c + the §4 worked example require the
        # degenerate workset to root at the project dir itself.  ``resolve_standalone
        # _project`` sets ``metadata_path = root`` (the resolved project dir) and
        # ``project_path = root/"workspace"`` — so ``proj.metadata_path`` IS <root>
        # exactly (verified: it equals ``Path(raw).resolve()``).  Every standalone
        # layout anchor hangs off this: workset.boxes = @meta.workset.path/box_data
        # (hence meta.box.path, hence the home bind) and workset.vault_* =
        # @meta.workset.path/vault/*, all byte-identical to proj.shell_path /
        # proj.vault_*_path.
        ws_root_literal = str(proj.metadata_path)
    else:
        ws_root_literal = None  # primary uses the @config.primary_workset @-ref
    # The workset partition TOKEN (spec §1A meta.runtime.ws_name, 2026-07-04) —
    # SINGLE-SOURCED on channels.workset_name_token (primary=__PRIMARY__ ·
    # named=<detected name> · standalone=__STANDALONE__). Threaded into
    # meta_runtime_floor so the snapshot's meta.runtime.ws_name holds it and
    # meta.workset.name anchors into it (§2c); the SAME token drives the channel
    # partition (channels.box_channel_addresses below), so the two cannot drift.
    from kanibako.channels import channels as _channels

    ws_token = _channels.workset_name_token(proj)
    meta_runtime = settings_launch_module.meta_runtime_floor(
        mode=mode, ws_name=ws_token, ws_root_literal=ws_root_literal,
    )

    # meta.* IDENTITY-anchor materialization (block B2, spec §2c/§2d). The remaining
    # construct-time identity keys the @meta.*-routed core binds (workspace / inbox)
    # reference. Every value is the RESOLVED LITERAL the launch already computes —
    # the box name (proj.name; JC-B2-2 reuse), the workspace source
    # (str(proj.project_path)), the channel partition ADDRESSES
    # (channels.box_channel_addresses), and the plugin-set agent name — so an
    # @meta.box.workspace / @meta.box.inbox bind expands byte-identically (JC-B2-4).
    # (The workset partition token now lives on meta.runtime.ws_name — set above.)
    addr = _channels.box_channel_addresses(proj, std)
    # The agent's credential-SHARING CAPABILITY (spec §2d; auth-level design step 2):
    # the plugin-set RO ``meta.agent.<agent>.auth.share_support`` the box's mirror
    # views up. Read off the descriptor for the ACTIVE agent (single-source: the
    # plugin declares it in its *-defaults.yaml). Absent / NO-AGENT → False (the
    # box enables degenerate false). Best-effort: an unresolvable target is treated
    # as non-capable rather than crashing the snapshot build.
    agent_auth_support = False
    # The HARNESS descriptor, resolved ONCE here for BOTH plugin-set key families:
    # the auth capability below AND the B5 launch-grammar materialization
    # (meta.agent.<a>.{mode,exec} via meta_agent_grammar_floor — the single
    # descriptor→keyspace seam). None when no descriptor resolves.
    agent_desc = None
    if agent_name:
        from kanibako.targets import resolve_target

        try:
            agent_desc = resolve_target(
                harness_of(agent_name), proj.project_path
            ).descriptor
            agent_auth_support = bool(
                agent_desc.auth_share_support if agent_desc is not None else False
            )
        except (KeyError, ValueError):
            # GENUINELY ABSENT: no matching target (KeyError) or the target lacks a
            # meta.agent.<agent>.name (ValueError). Treat as non-capable (no
            # sharing). We do NOT swallow arbitrary exceptions — a transient
            # resolution error should NOT silently disable sharing for a capable
            # agent; it surfaces to the caller.
            get_logger("start").debug(
                "auth capability: no descriptor for agent %r → non-capable",
                agent_name,
            )
            agent_desc = None
            agent_auth_support = False
    # The SINGLE-SOURCE (box_tier, workset_tier) settings-file pair (M-8). It is
    # UNIFORM now: primary/named = (the box's own settings.yaml, the workset root's);
    # standalone = (<root>/box_data/settings.yaml, <root>/settings.yaml) — the box
    # tier is a real path in EVERY mode, merely ABSENT BY DEFAULT for standalone
    # (spec §2c ALL PROJECTS + §5). The SAME pair feeds BOTH the
    # meta.box.settings anchor (box tier path, below) AND the cascade box_path/
    # workset_path the snapshot resolvers pass to build_launch_snapshot (returned
    # last) — so the anchor and the cascade cannot drift.
    cascade_box_path, cascade_workset_path = box_workset_settings_paths(proj)
    meta_identity = settings_launch_module.meta_identity_floor(
        box_name=proj.name,
        project_path=str(proj.project_path),
        inbox=str(addr.inbox),
        share_global=str(addr.share_global),
        share_workset=(
            str(addr.share_workset) if addr.share_workset is not None else None
        ),
        # meta.box.settings — the box-TIER file path (str), in EVERY mode. The SAME
        # value as cascade_box_path, so the anchor names exactly the file the cascade
        # reads and `config set` writes (M-8). No per-mode branch: the box tier is
        # non-optional by TYPE (`box_workset_settings_paths` -> tuple[Path, ...]).
        box_settings=str(cascade_box_path),
        # The agent identity key (spec §2d): the cascade discriminator AND the
        # value are the resolved agent name (install.name). Omitted for a NO-AGENT
        # box (empty name) — it has no agent identity.
        agent_name=agent_name if agent_name else None,
        agent_real_name=agent_name if agent_name else None,
        agent_auth_share_support=agent_auth_support,
    )
    # B5: the plugin-set LAUNCH GRAMMAR ``meta.agent.<a>.{mode,exec}`` (spec §2d;
    # the §3.3 "it should exist and be used" / "we should be using this" rulings).
    # Materialized from the SAME harness descriptor resolved above — the single
    # descriptor→keyspace seam; the launch then COMPOSES its argv from these
    # snapshot keys (meta_agent_grammar), never from the descriptor directly.
    if agent_name:
        meta_identity.update(
            settings_launch_module.meta_agent_grammar_floor(agent_name, agent_desc)
        )

    # LAYOUT-anchor materialization (spec §2c/§2g): the workset-scope path anchors
    # AND the RO per-mode box root ``meta.box.path`` that the @-ref-routed core
    # home/vault/helper_log/workset-channel binds reference. The anchor VALUES are
    # the spec's own self-resolving @-ref formulas and are built ENTIRELY from
    # *mode* inside ``workset_anchor_floor`` — the per-mode variation lives THERE
    # and nowhere downstream (spec §2c), so this seam no longer derives any
    # per-mode root literal off ``proj``. The only proj-derived values still needed
    # are the workset-local channel roots — the helper-log path is no longer among
    # them: its bind is now the spec's own ``@workset.logs/@{meta.box.name}.jsonl``
    # @-ref chain (PHASE R), which resolves from the anchors below.
    # The resolved workset-local channel roots (PRIMARY/NAMED only; STANDALONE has
    # no workset-local channels, spec §2c).
    _wch = None if mode == "standalone" else _channels.workset_channel_paths(proj, std)
    _ws_channels = (
        {
            "common": str(_wch.common),
            "chat": str(_wch.chat),
            "share": str(_wch.share),
        }
        if _wch is not None
        else None
    )
    workset_anchor = settings_launch_module.workset_anchor_floor(
        mode=mode,
        workset_channels=_ws_channels,
    )
    return (
        ctx, resolved_sys, meta_runtime, meta_identity, workset_anchor,
        cascade_box_path, cascade_workset_path,
    )


def _resolve_launch_snapshot(
    *,
    std,
    proj,
    agent_name: str,
    system_settings_path,
    agent_cfg_path,
    desc,
    install,
    target=None,
    agent_cfg=None,
    box_state_kanibako: str | None = None,
    socket_path=None,
    log_path=None,
    graph_root=None,
    storage_conf_path=None,
    deliver_creds: bool = True,
    include_base_families: bool = True,
    extra_default_categories: "Mapping[str, object] | None" = None,
    guarantee_create: bool = True,
    cli_level: "Mapping[str, object] | None" = None,
    host_dest_keys: "frozenset[str]" = frozenset(),
):
    """Build the ONE launch snapshot + reconcile the launch CATEGORY winners.

    The single launch-time CATEGORY resolve (block 7b): aggregates every
    runtime ``default_categories`` table (core / kani / channel / share / seeds /
    masks, plus the CONDITIONAL helper + image tables) into ONE floor, folds in
    the resolved ``system.*`` tier so @-refs resolve from the snapshot, represents
    the agent's descriptor delivery binds via 7a's ``agent_default_partial``, and
    runs ``assemble_levels → merge → expand`` ONCE via
    :func:`kanibako.settings.settings_launch.build_launch_snapshot`.  The expanded snapshot
    is then adapted to ``CategoryEntry`` and reconciled ONCE.

    Returns ``(snapshot, reconciled)``.  AGENT_CRITICAL delivery binds
    now flow through the snapshot's ``agent.<agent>.bindings.*`` subtree (single-route),
    emitted by :func:`kanibako.settings.settings_launch.agent_delivery_mounts` at the call
    site — NOT a parallel ``descriptor_mounts`` route.

    The image + helper tables are CONDITIONAL: a table is included ONLY when its
    gate holds (image-sharing active → *storage_conf_path* given, *graph_root*
    only when the probe succeeded — it feeds ONLY the ``box.images_store``
    default, ruled 11a; helpers enabled →
    *box_state_kanibako*/*socket_path*/*log_path* given), so
    their binds do NOT appear otherwise — exactly as the per-family path emitted
    them only inside their conditional block.

    *guarantee_create* (default True — a LAUNCH) gates the core table's
    create-if-missing of the vault source dirs. A READ-ONLY consumer of this
    resolve — ``box config show --effective``, which renders the resolved
    categories — passes False: it must show what a launch WOULD mount without
    making it so. Nothing else about the resolve differs.

    *include_base_families* gates the always-available tables (core / kani /
    channel / common / seeded).  It is True for the MAIN launch snapshot and False
    for the late, conditional image/helper resolves (whose box_dests are disjoint),
    so the image/helper reconcile carries ONLY their own table + any config-file
    keys — byte-for-byte the old per-family ``_build_image_mounts`` /
    ``_build_helper_hub_mounts`` resolve (which injected only that one table).

    *host_dest_keys* names the category keys whose ``box_dest`` is an absolute HOST
    path rather than a guest one (the §2a seed layers). Passed by
    :func:`_apply_init_seeds`, which is the only caller that injects those keys and
    the only one that APPLIES their copies; every other resolve leaves it empty and
    is byte-identical. See ``settings_categories.CategoryEntry.dest_space`` for why
    the space must be carried rather than inferred from the path.

    *cli_level* is the §1A CLI LEVEL (P8), built by
    :func:`kanibako.settings.settings_cli_level.build_cli_level` and validated inside
    ``build_launch_snapshot``. This is the ONE resolve that may carry the EPHEMERAL
    flag values (``-M`` / ``-N``-``-C``-``-R``) as well as the resolved selection,
    because its output is this launch's argv / env / mounts and nothing here is
    written back to a settings file. The seed, persona-endpoint and ``--effective``
    resolves take a selection-ONLY level.
    """
    from kanibako.settings import settings_launch
    from kanibako.settings.agent_representation import (
        agent_common_for_node,
        agent_default_partial,
    )
    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.settings_categories import (
        derive_binding_keys,
        reconcile_categories,
    )
    from kanibako.settings.settings_prefs import collect_prefs
    from kanibako.settings.settings_resolve import SettingsError

    (
        ctx, resolved_sys, meta_runtime, meta_identity, workset_anchor,
        cascade_box_path, cascade_workset_path,
    ) = _launch_snapshot_inputs(std=std, proj=proj, agent_name=agent_name)

    # Aggregate every runtime default-categories table into ONE dict.  Keys are
    # disjoint across families (each uses its own ``<scope>.<category>.<key>``
    # namespace), so a plain union is well-defined.
    default_categories: dict[str, object] = {}
    if include_base_families:
        default_categories.update(_core_default_categories(
            std, proj, guarantee_create=guarantee_create,
        ))
        default_categories.update(core_defaults.kani_default_categories())
        # The KICKOFF LOADER (spec §2c ``box.bindings.ro.kickoff``, P-5): the
        # directive-chain ENTRY SLOT, core-owned since C-CANON R2 and delivered
        # beside the kani binds it is INTERNAL company to.
        # ⚑ The gate descriptor is ``desc`` when this resolve represents one, and
        # otherwise the RESOLVED TARGET's own.  Both, in that order, on purpose:
        # ``desc`` is what ``agent_default_partial`` (below) turns into agent-level
        # binds, so gating on it is gating on what this very snapshot will carry —
        # but ``box config show --effective`` resolves the launch with ``desc=None``
        # and a real ``target``, and a display that shows a kickoff bind the launch
        # would not emit is exactly the drift that call site's own comment forbids.
        default_categories.update(core_defaults.kickoff_default_categories(
            desc if desc is not None
            else (target.descriptor if target is not None else None)
        ))
        # The packaged CANON: five READ-ONLY SIBLING binds (spec §2c, J-7) — the
        # COLLECTION.md index and the bible's ROM_CONTENTS.md as FILE binds, plus one
        # whole-directory bind per packaged chapter (general/workset/box).  Each
        # lands on a mountpoint the box-create skeleton already made, so nothing
        # nests and no mountpoint has to live inside a bind source.
        default_categories.update(core_defaults.rom_default_categories())
        # The HANDBOOK: five READ-ONLY SIBLING binds (spec §2c) sourced from the four
        # ``<scope>.canon`` KEYS, plus the agent-scope ``canon`` floor those refs
        # resolve against. Unlike the rom, these are HOST content the user owns and
        # may repoint — through the keys, which is the spelling §2c blesses.
        default_categories.update(
            core_defaults.canon_default_categories(std, agent_name or None)
        )
        default_categories.update(_channel_default_categories(std, proj))
        if target is not None:
            # ⚑ Re-keyed to the ACTIVE NODE (P7): a plugin declares its commons
            # against its own HARNESS name, but the §2d pick reads
            # ``agent.<node>`` — so a PERSONA saw none of them (no
            # ``~/.claude/plugins``, no cache) while the symlink shim maintained
            # links nothing consumed. Identity for a bare agent.
            default_categories.update(agent_common_for_node(
                target.default_common(),
                node_name=agent_name,
                harness=target.name,
            ))
            # The plugin's BIBLE CHAPTER (spec §2c ``canon_bible_agent``), emitted
            # by CORE from the RESOLVED target beside the five core canon binds —
            # gated on the plugin actually shipping one.  A SIBLING of them (J-7),
            # not nested: it lands on ~/canon/bible/agent, a mountpoint the skeleton
            # always pre-creates whether or not this bind is emitted.  A box-scoped
            # INTERNAL bind, not an agent key — so the bible's agent chapter stays as
            # unrepointable as the rest of the book, and the bind follows the
            # resolved target however the agent was selected.
            default_categories.update(
                core_defaults.rom_agent_default_categories(target)
            )
            default_categories.update(target.default_seeds())
            # PLUGIN-declared @-ref-sourced agent binds (spec §2d): a generic
            # AGENT-scope category-bind extension point.  Unioned like a share; the
            # plugin builds each key DISCRIMINATED (``agent.<agent>.bindings.*``) and
            # its ``@``-ref source is resolved by ``expand`` from the ``resolved_sys``
            # floor.  Currently empty for all first-party plugins (the former
            # ``@system.instructions`` instructions bind was retired — the guide now
            # ships via the RO bundle + launch-flatten).
            default_categories.update(target.default_category_binds())
    # A NARROW caller (``include_base_families=False``) may inject ONLY its own
    # declared default-category table — e.g. ``_apply_init_seeds`` passing just
    # ``target.default_seeds()`` — so the seed/synced COPY resolve flows through
    # the SAME ``build_launch_snapshot`` pipeline (single-route, 7c) WITHOUT
    # pulling in the unrelated core/channel/share families. This mirrors the old
    # narrow ``_resolve_launch_categories`` agent-level ``defaults=`` injection.
    if extra_default_categories:
        default_categories.update(extra_default_categories)
    if (
        box_state_kanibako is not None
        and socket_path is not None
        and log_path is not None
    ):
        default_categories.update(
            core_defaults.helper_default_categories(
                box_state_kanibako=box_state_kanibako,
                socket_path=socket_path,
                log_path=log_path,
            )
        )
    # 11a: *storage_conf_path* alone gates the image table — *graph_root* may be
    # ``None`` (probe failed), in which case the table carries the binds but NO
    # ``box.images_store`` floor default (a set tier value, or nothing, decides).
    if storage_conf_path is not None:
        default_categories.update(
            core_defaults.image_default_categories(
                graph_root=graph_root,
                storage_conf_path=storage_conf_path,
            )
        )
    # Fold the resolved system.* tier into the floor so @-refs in category values
    # resolve from the snapshot itself (replicating the old ``_lookup`` map).
    default_categories.update(resolved_sys)

    agent_partial = (
        agent_default_partial(desc, install, node_name=agent_name)
        if desc is not None and install is not None
        else None
    )

    # Block 7b (ruling A — the FULL read-path swap): the BEHAVIOR cascade now flows
    # through THIS one snapshot too. The target's declared-default floor folds in as
    # ``agent.default.<key>`` (OS1); the per-agent FILE's flat ``[agent]`` state
    # (``agent_cfg.state``) is wrapped under ``agent.<active>`` (it is NOT the
    # discriminated tables ``assemble_levels`` reads from ``agent_path``, so it is
    # injected as ``agent_state`` — see ``build_launch_snapshot``). Only the MAIN
    # launch carries behavior (the conditional image/helper resolves do not).
    behavior_floor = None
    agent_state = None
    if include_base_families and target is not None:
        descriptors = target.setting_descriptors()
        if descriptors:
            behavior_floor = {d.key: d.default for d in descriptors}
        if agent_cfg is not None:
            agent_state = dict(agent_cfg.state)

    # ``pref.*`` REQUESTS (spec §2h) — collected ONCE here, at the single launch
    # aggregation point, and threaded into the snapshot build. This function runs
    # up to five times per ``kanibako start``, so collecting inside each build
    # would re-read the same two files five times over; more importantly, ONE
    # list means the five resolves cannot disagree about what was requested.
    # (P7's agent selection collects the same way, BEFORE the agent is known — a
    # SEPARATE read, since there is no launch-side list to share at that point. The
    # two cannot disagree: the file pair is a runtime treewalk and nothing writes
    # between them. See settings_prefs.collect_prefs.)
    prefs = collect_prefs(cascade_workset_path, cascade_box_path)

    snapshot = settings_launch.build_launch_snapshot(
        agent_name=agent_name,
        ctx=ctx,
        system_path=system_settings_path,
        agent_path=agent_cfg_path,
        workset_path=cascade_workset_path,
        box_path=cascade_box_path,
        behavior_floor=behavior_floor,
        default_categories=default_categories,
        agent_partial=agent_partial,
        agent_state=agent_state,
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
        prefs=prefs,
        cli_level=cli_level,
    )
    try:
        entries = settings_launch.snapshot_category_entries(
            snapshot, active_agent=agent_name, box_ctx=ctx,
            # SKIP-IF-ABSENT declarations, read from the SAME ``canon:`` rows that
            # declare the binds — applied unconditionally at this ONE site so a
            # display resolve and a launch resolve cannot disagree about which binds
            # may legitimately be missing.
            optional_keys=core_defaults.canon_optional_bind_keys(),
            host_dest_keys=host_dest_keys,
        )
    except SettingsError as exc:
        # A malformed CATEGORY shape raises here naming the DECLARATION key
        # (e.g. "category agent.claude.common.x is str, expected a Bind"). When a
        # pref installed that key the name is one the user never wrote, so the
        # same enrichment the collision path gets applies — otherwise the box
        # fails to start pointing at a key that is in none of their files.
        raise _annotate_pref_origin(exc, prefs) from None
    # MATERIALISE each ABSTRACT declaration's derived binding beside it, under
    # the reserved ``binding_derivations`` node at the snapshot root (R-8),
    # BEFORE the reconcile — the derivation is a property of the declaration,
    # not of whether it won.
    _install_derived_bindings(snapshot, derive_binding_keys(entries))
    try:
        reconciled = reconcile_categories(entries, deliver_creds=deliver_creds)
    except CategoryCollisionError as exc:
        # A collision names the DECLARATION key. When that declaration was
        # INSTALLED BY A PREF, the named key is one the user never wrote and
        # cannot write (``agent.claude.common.x`` from
        # ``pref.agent.claude.common.x``), so the message would send them looking
        # for a key that is not in any of their files. Enrich it HERE — the one
        # seam that holds both the error and the requests.
        raise _annotate_pref_origin(exc, prefs) from None
    emit_collision_warnings(reconciled.warnings)
    return snapshot, reconciled


def _annotate_pref_origin(exc, prefs):
    """Return *exc*, or a copy naming the ``pref`` that installed a participant.

    Covers BOTH launch failures a pref can cause by installing a key at a scope
    the user does not write:

    * ``CategoryCollisionError`` — carries its participants STRUCTURALLY
      (``entries`` = ``(key, host_src)`` pairs) precisely so a CLI seam can
      enrich the rendered text with what the pure resolver does not know.
    * a plain ``SettingsError`` from the category ADAPTER (an undeclared shape /
      a non-``Bind`` at a category leaf) — it has no structured participants, so
      the pref TARGETS are matched against the message text instead.

    Either way the point is the same: a message naming ``agent.claude.common.x``
    is useless to a user whose files only contain
    ``pref.agent.claude.common.x``.
    """
    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.settings_prefs import pref_origin

    def _line(key, req):
        return (
            f"'{key}' was installed by '{req.key}' in the {req.level} "
            f"settings file {req.where}"
        )

    entries = getattr(exc, "entries", None)
    if entries is not None:
        origins = [
            _line(key, req)
            for key, _src in entries
            if (req := pref_origin(key, prefs)) is not None
        ]
        if not origins:
            return exc
        return CategoryCollisionError(
            f"{exc}\n  ⚑ " + "; ".join(origins) + " — edit or remove that request.",
            kind=exc.kind,
            box_dest=exc.box_dest,
            entries=exc.entries,
        )

    # No structured participants: match the pref TARGETS against the message.
    text = str(exc)
    origins = [_line(req.target, req) for req in prefs if req.target in text]
    if not origins:
        return exc
    return type(exc)(
        f"{text}\n  ⚑ " + "; ".join(origins) + " — edit or remove that request."
    )


def _install_derived_bindings(snapshot, derived: "Mapping[str, object]") -> None:
    """Write the ``binding_derivations.*`` materialisation into *snapshot* in place.

    Mirrors ``settings_launch._materialize_box_agent_mirror``: a post-expand
    write into the built snapshot at ONE seam.  ``binding_derivations`` is the
    reserved INTERNAL node at the snapshot root (R-8, manifest
    ``not_keys.reserved_internal``) — not a key, guarded by TWO protections:
    the closed head dispatch refuses it (no config verb can address or write
    it) and assembly drops a file-borne top-level table with a warning
    (``settings_assemble._drop_upward_scopes``), so this seam is the node's
    ONLY producer.
    """
    from kanibako.settings.settings_store import insert_dotted

    for dotted, value in derived.items():
        insert_dotted(snapshot, dotted, value)


#: Process-scoped DISPLAY state for :func:`emit_collision_warnings`: the
#: ``(box_dest, scope)`` pairs already reported. It can change no resolution
#: outcome — the resolver returns collisions as DATA and stays pure — which is
#: exactly why it is allowed to be module-level at all.
#:
#: ⚑ It carries no BOX identity. One process resolving two different boxes would
#: report a shared ``(dest, scope)`` ambiguity once, not twice. That is correct
#: for the CLI (one box per invocation) and is recorded rather than defended: a
#: future multi-box process must key this by box.
_COLLISION_WARNED: "set[tuple[str, str]]" = set()


def reset_collision_warnings() -> None:
    """Clear the per-process collision-warning memo (test seam)."""
    _COLLISION_WARNED.clear()


def emit_collision_warnings(collisions) -> None:
    """Log each SAME-SCOPE abstraction ambiguity ONCE PER PROCESS (spec §0 row 5).

    §0 requires the warning "every launch (not deduplicated, not once-ever — a
    same-scope collision is a real ambiguity and stays visible until fixed)". The
    prohibition is on CROSS-LAUNCH suppression: there is no marker file, no
    registry flag, no per-box state, so the next launch warns again and the one
    after that, until the config is fixed.

    ⚑ What IS collapsed is the FIVE in-process re-resolutions of one declaration
    set: ``_resolve_launch_snapshot`` runs up to five times per ``kanibako
    start`` (the main resolve, the conditional image + helper resolves, the seed
    resolve, the ``synced`` copy resolve) and every one of them reads the user's
    settings FILES, so one same-scope collision declared in a box file surfaces in
    several of them. Printing it five times is one ambiguity reported five times,
    not five ambiguities — so the memo is keyed ``(box_dest, scope)`` and lives
    for the process, never beyond it.
    """
    logger = get_logger(__name__)
    for collision in collisions:
        memo = (collision.box_dest, collision.scope)
        if memo in _COLLISION_WARNED:
            continue
        _COLLISION_WARNED.add(memo)
        logger.warning("%s", collision.message())


def _emit_category_mounts(reconciled, *, label: str) -> list:
    """Emit every non-agent, non-mask reconciled MOUNT winner as :class:`Mount`s.

    The single-pass replacement for the per-family ``_emit_reconciled_mounts``
    calls: the snapshot reconcile already partitioned + depth-sorted ALL MOUNT
    winners together, so this emits them ONCE.  AGENT delivery binds
    (``scope == "agent"`` / ``bindings.{ro,rw}``) are emitted SEPARATELY by
    :func:`kanibako.settings.settings_launch.agent_delivery_mounts` (their AGENT_CRITICAL
    must-exist safe-fail differs from L7), and ``masks`` (tmpfs, no host source)
    are split out for ``tmpfs_masks`` — both are skipped here.  Keeps the L7
    guarantee-create / ro-drop logic byte-for-byte from ``_emit_reconciled_mounts``.
    """
    from pathlib import Path as _Path

    from kanibako.targets.base import Mount

    mounts: list = []
    for e in reconciled.mounts:
        if e.category == "masks":
            continue  # tmpfs masks have no host source; split into tmpfs_masks.
        if e.scope == "agent" and e.category in ("bindings.ro", "bindings.rw"):
            continue  # agent delivery binds → agent_delivery_mounts (must-exist).
        if e.category == "secret_path":
            continue  # SECRET category → _emit_secret_mounts (arm's-length ro mount
            # + box-side export shim; kept OUT of this ~-rooted depth-sorted emit).
        assert e.host_src is not None  # bind-shaped MOUNTs always have a source.
        src = _Path(e.host_src)
        if e.options != "ro":
            # rw bind: create the host source dir if absent (L7 guarantee-create).
            try:
                src.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass  # best-effort; podman will surface a genuinely bad source
        elif not src.exists():
            import logging
            if e.optional:
                # SKIP-IF-ABSENT (spec §2c): a declared-optional bind whose source is
                # absent is the NORMAL case, not a defect — the handbook's per-scope
                # chapters are the live users, and a workset or box that has written
                # no chapter is almost every box. Dropped SILENTLY (debug only); the
                # WARNING below stays for every other ro bind, where it is the safety
                # net for a mis-pathed source and must not be softened globally.
                logging.getLogger(__name__).debug(
                    "%s %s: optional source %s absent; bind omitted",
                    label, e.name, e.host_src,
                )
                continue
            # ro bind with a missing source: DROP with a warning (L7) instead of
            # letting rootless podman abort the launch on a dangling bind source.
            logging.getLogger(__name__).warning(
                "%s %s: read-only source %s does not exist; dropping mount",
                label, e.name, e.host_src,
            )
            continue
        mounts.append(
            Mount(source=src, destination=e.box_dest, options=e.options)
        )
    return mounts


def _seed_box_home(
    *,
    std,
    proj,
    target,
    desc,
    agent_id: str,
    agent_cfg_path,
    system_settings_path,
    auth_src,
    logger,
    suppress_oauth: bool = False,
) -> None:
    """Apply the one-time home seed for a freshly-created box (seed-at-create).

    The SINGLE seed implementation, shared by `box create` (`run_create`) and the
    `start` auto-create path.  Runs ATOMICALLY-after registration (the caller
    gates on the just-registered signal, ``proj.is_new``); it is NEVER run on a
    relaunch of an existing box.  Two ordered, create-if-absent steps (so a
    re-create into a leftover dir never clobbers user content):

    1. the descriptor's one-time credential seed (descriptor-bearing targets
       only; a descriptor-less / no-agent target has nothing to seed here).
    2. the configured copy-once-at-init ``seeded`` category winners — INCLUDING
       the layered ``seeded.template`` trio (system/base -> agent -> workset;
       later overlays earlier, per-file last-wins). The separate on-disk template
       staging route RETIRED (Q1): the template layers are now ordinary keystore
       ``seeded`` keys resolved off the committed snapshot in
       :func:`_apply_init_seeds`, which stages the ``~``-targeted layers in scope
       order there.

    The per-launch credsync REFRESH and the channel guarantee-create are SEPARATE
    per-launch mechanisms and are NOT part of this one-time seed.
    """
    if target and desc is not None:
        credsync.seed_box_credentials(
            desc, target, auth=auth_src, host_home=Path.home(),
            project_home=proj.shell_path,
            suppress_oauth=suppress_oauth,
        )
    _apply_init_seeds(
        std=std, proj=proj, agent_name=agent_id, target=target,
        global_config_path=system_settings_path, agent_config_path=agent_cfg_path,
        logger=logger, deliver_creds=auth_src.creds_shared,
    )


def persona_create_verdict(
    std, config, proj, *, explicit_agent: str | None = None
) -> str | None:
    """The persona LOAD-OR-ERROR verdict for the `box create` path — a read-only
    pre-check the caller runs BEFORE the lifecycle-journal write-entry.

    Director ruling (2026-07-03): the create guard MUST precede the journal entry
    (an abort after the entry would leave a pending entry whose recovery replays
    the seed).  So `run_create` calls this FIRST; a non-``None`` return is the
    actionable error message → the caller prints it and refuses to create, with NO
    journal entry / seed.  Returns ``None`` for a bare / non-persona / no-agent box
    (nothing to gate).

    Read-only: resolves the agent → endpoint against a THROWAWAY config copy and
    runs the same :func:`_preflight_persona_load` the launch uses; it never writes
    (the real adoption + persist happens inside :func:`seed_new_box`).
    """
    logger = get_logger("start")
    system_settings_path = std.settings
    selection = None
    try:
        from kanibako.settings.agent_select import select_agent
        selection = select_agent(
            std=std, proj=proj, explicit_agent=explicit_agent,
        )
        agent_name = selection.node
        target = (
            resolve_target(harness_of(agent_name), proj.project_path)
            if agent_name
            else None
        )
    except Exception:  # no-agent / unresolved → nothing persona to gate.
        return None
    if target is None:
        return None
    agent_id = with_harness(agent_name, target.name)
    if harness_of(agent_id) == agent_id:
        return None  # bare — no persona gate.
    agent_cfg_path = agent_settings_path(std.agents, agent_id)
    probe_cfg = (
        load_agent_config(agent_cfg_path)
        if agent_cfg_path.exists()
        else target.generate_agent_config()
    )
    _auth, endpoint, model = _resolve_box_launch_decisions(
        std=std, proj=proj, target=target, agent_name=agent_id,
        agent_cfg=probe_cfg, system_settings_path=system_settings_path,
        agent_cfg_path=agent_cfg_path,
        selection_level=selection.selection_level if selection else None,
    )
    _ep, error, _adopted, _provider = _preflight_persona_load(
        agent_id, probe_cfg, endpoint, logger, target=target, keyspace_model=model,
    )
    return error


def seed_new_box(std, config, proj, *, explicit_agent: str | None = None) -> None:
    """Seed a freshly-created box's home at CREATE time (`box create` entry).

    The keyspace spec seeds the box home ONCE, atomically with registration, at
    `create` — not at first launch.  `run_create` (and any other create path)
    calls this right after the resolver returns ``proj.is_new`` True.  It builds
    the minimal-sufficient seed context — the agent-resolution chain the seed
    needs (active agent → target/descriptor, the agent config path, and the
    effective group-auth gate) — WITHOUT the launch-only image pull/build, then
    delegates to the shared :func:`_seed_box_home`.

    A no-op-safe entry: a no-agent box (no resolvable target) still seeds its
    base/workset template layers (the agent layer + cred seed are skipped).
    """
    logger = get_logger("start")
    system_settings_path = std.settings

    # Resolve the active agent → target/descriptor (config/settings only; no
    # image work).  A box with no resolvable agent seeds template-only.
    target = None
    desc = None
    agent_name = ""
    selection = None
    try:
        from kanibako.settings.agent_select import select_agent
        selection = select_agent(
            std=std, proj=proj, explicit_agent=explicit_agent,
        )
        agent_name = selection.node
        target = (
            resolve_target(harness_of(agent_name), proj.project_path)
            if agent_name
            else None
        )
    except Exception:  # pragma: no cover - no-agent / unresolved → template-only
        logger.debug("seed_new_box: no agent resolved; template-only seed", exc_info=True)
        target = None

    # NODE-name (persona identity) keys the agents/<node>/ dir + agent.<node>.*
    # slot; with_harness swaps in the actually-resolved target name (fallback-safe),
    # persona preserved. Bare + as-requested -> node == harness == target.name.
    agent_id = with_harness(agent_name, target.name) if target else "general"
    agent_cfg_path = agent_settings_path(std.agents, agent_id)
    # Load or GENERATE the agent config IN MEMORY (mirrors the launch path) — the
    # WRITE + share shim are deferred until after the persona load-or-error
    # pre-flight passes, so an unloadable persona `box create` seeds NOTHING.
    agent_cfg_exists = bool(target) and agent_cfg_path.exists()
    if target is not None:
        seed_agent_cfg = (
            load_agent_config(agent_cfg_path)
            if agent_cfg_exists
            else target.generate_agent_config()
        )
    else:
        seed_agent_cfg = None
    if target is not None:
        desc = target.descriptor

    # Auth SOURCE + persona endpoint off ONE snapshot (single-source). At CREATE, a
    # fresh custom-endpoint box is seeded WITHOUT the host OAuth cred (fail-safe;
    # <None>/no-target = bare, byte-identical to today).
    auth_src, active_endpoint, active_model = _resolve_box_launch_decisions(
        std=std, proj=proj, target=target, agent_name=agent_id,
        agent_cfg=seed_agent_cfg, system_settings_path=system_settings_path,
        agent_cfg_path=agent_cfg_path,
        selection_level=selection.selection_level if selection else None,
    )

    # PERSONA LOAD-OR-ERROR (A + B3) — the SAME gate the launch path runs, before
    # any seed/artifact.  An unloadable persona raises (surfaced by cli.py with a
    # non-zero exit); the create journal keeps the half-built box forward-
    # recoverable (a cleanup command is a separate follow-up).  The provider itself
    # is NOT consumed here (create seeds creds + templates; the codex config.toml —
    # hook + provider — materialises at first launch via _run_container), but the
    # model gate must run so an unconfigured-model codex persona is rejected at
    # CREATE, symmetric with the launch path.
    agent_cfg_dirty = target is not None and not agent_cfg_exists
    if target is not None and harness_of(agent_id) != agent_id:
        (
            active_endpoint, persona_error, persona_adopted, _provider,
        ) = _preflight_persona_load(
            agent_id, seed_agent_cfg, active_endpoint, logger,
            target=target, keyspace_model=active_model,
        )
        if persona_error is not None:
            raise KanibakoError(persona_error)
        agent_cfg_dirty = agent_cfg_dirty or persona_adopted

    suppress_oauth = active_endpoint is not None

    # Loadability resolved → materialise the persona artifacts (write the fresh /
    # B3-adopted config, then the common-dir shim) BEFORE the seed reconcile reads them.
    if target is not None and agent_cfg_dirty:
        assert seed_agent_cfg is not None  # target set ⇒ config built above.
        write_agent_config(agent_cfg_path, seed_agent_cfg)
    ensure_persona_share_symlinks(std, agent_id, target)

    _seed_box_home(
        std=std, proj=proj, target=target, desc=desc,
        agent_id=agent_id, agent_cfg_path=agent_cfg_path,
        system_settings_path=system_settings_path,
        auth_src=auth_src, logger=logger,
        suppress_oauth=suppress_oauth,
    )


# ---------------------------------------------------------------------------
# Interrupted-create recovery via the LIFECYCLE JOURNAL (J1, Jei 2026-06-30b).
#
# A write-ahead journal entry (``kanibako.launch.journal``) records an in-flight create
# so an interrupted `create`/auto-create-at-launch (a crash between seed-start
# and registry write) is forward-recoverable.  Sequence per create:
#   write-entry -> seed -> register -> clear-entry.
# The seed gate becomes "is_new OR pending create entry", so the next
# create/launch re-seeds (create-if-absent, no clobber), registers idempotently,
# and clears the entry.  HARD INVARIANT: registered ==> no pending entry at rest
# — the entry is cleared as the IMMEDIATE step right after the registry write,
# never before it.  IMPORT/CONNECT (register-only) boxes NEVER get a create
# entry — they do not seed (a create entry on them would wrongly trigger
# re-seed).  This SUPERSEDES the B3 ``.seeding`` file marker; the journal is a
# TRUE write-ahead store (the entry predates any directory), and lives globally
# beside the registry (``config.journal``), so recovery is "pending create entry
# for this box path? -> replay" rather than fighting import_reconcile + is_new.
# ---------------------------------------------------------------------------


def _box_journal_key(proj) -> str:
    """Journal entry KEY for *proj*: the host-side box dir (UNIFORM all modes).

    ``str(Path(proj.shell_path).parent)`` — ``shell_path`` always ends in
    ``home/``, so its parent is the host-side box dir that CONTAINS ``home/``
    (PRIMARY/NAMED ``boxes/<name>``; STANDALONE ``<root>/box_data``).  Known at
    write-ahead time; no per-mode special-casing.

    ⚑ THIS IS ``meta.box.path``, SPELLED BY HAND — deliberately, and it is the one
    place that is allowed to be.  Everything else that needs the box root reads the
    ``meta.box.path`` anchor from the resolved snapshot (that is the whole point of
    the anchor), so a second hand-derivation would normally be drift.  This one
    cannot use the anchor: the journal entry is WRITE-AHEAD.  It is written BEFORE
    the box directory exists and before any settings snapshot is built — recording
    the INTENT to create is precisely what makes recovery possible — so there is no
    keystore to read at the moment this value is needed.  A key that must exist
    before the thing that resolves keys cannot come from it.  If the box-root
    derivation ever changes, this is the site that must change with it.
    """
    return str(Path(proj.shell_path).parent)


def _write_create_entry(std, proj) -> None:
    """Write the write-ahead ``create`` journal entry for *proj* (intent)."""
    from kanibako.launch import journal

    workset = proj.group.name if getattr(proj, "group", None) is not None else None
    journal.write_entry(
        std.journal, _box_journal_key(proj),
        op="create", name=proj.name, mode=proj.mode.value, workset=workset,
        workspace=str(proj.project_path),
    )


def _clear_create_entry(std, proj) -> None:
    """Clear the ``create`` journal entry for *proj* (no-op if already absent)."""
    from kanibako.launch import journal

    journal.clear_entry(std.journal, _box_journal_key(proj))


def _pending_create_entry(std, proj) -> dict | None:
    """Return the pending ``create`` journal entry for *proj*, or ``None``.

    The recovery signal: a non-``None`` result means a create was started for
    this box but never completed (crash before the entry was cleared).
    """
    from kanibako.launch import journal

    return journal.pending_create(std.journal, _box_journal_key(proj))


def _register_new_box(std, proj, *, force: bool = False) -> None:
    """Register a freshly-created box (idempotent), mode-appropriate (B3).

    The deferred-registration commit step: the create paths resolve with
    ``register=False`` (the resolver creates the dir + meta + sets ``is_new`` but
    does NOT write the registry), seed the home, then call this to register.

    *force* is forwarded to the PRIMARY registration only (the cross-kind
    workset-name refusal is bypassable; SAME-kind primary-box uniqueness is not).
    A ``box create --name <workset-name> --force`` create passes it so the
    deferred commit does not re-refuse what the up-front CLI check already
    allowed.

    Idempotent for the SAME box (recovery re-entry after a crash in the tiny
    register -> clear-entry window leaves the box already registered): PRIMARY
    uses :func:`paths.register_primary_box_name_if_absent` (writes the primary
    per-workset ``boxes:`` membership — the sole store since the global
    ``projects:`` section retired; no-op iff the identical name->path mapping is
    present, re-raises a real collision); STANDALONE uses
    :func:`registry_store.register_standalone` (already idempotent — overwrites a
    matching name->root).  NAMED boxes carry no deferred registry entry on create
    (the workset membership is written eagerly at resolve), so there is nothing to
    defer or register here.
    """
    from kanibako.settings.paths import BoxMode

    if proj.mode is BoxMode.standalone:
        from kanibako.project import registry_store
        # STANDALONE root == metadata_path (resolve_standalone sets it to root).
        registry_store.register_standalone(
            std.registry, proj.name, Path(proj.metadata_path),
        )
    elif proj.mode is BoxMode.primary:
        from kanibako.settings.paths import register_primary_box_name_if_absent
        register_primary_box_name_if_absent(
            std.primary_workset, std.registry,
            proj.name, str(proj.project_path), force=force,
        )
    # NAMED: no deferred registration on create (membership written at resolve).


def _synced_uptodate(src: Path, dest: Path) -> bool:
    """True when the box copy is at least as new as the host source (mtime gate).

    Synced entries reapply only when the host source is NEWER than the box copy;
    a ``stat`` failure returns False so the copy proceeds (preserving the former
    inline ``try/except OSError: pass`` fall-through).
    """
    try:
        return dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def _apply_shell_copy(
    src: Path,
    dest: Path,
    *,
    label: str,
    name: str,
    host_src: str,
    logger,
    if_absent: bool,
    skip_if: "Callable[[Path, Path], bool] | None" = None,
) -> None:
    """Copy one seed/synced entry's *src* into the box shell-dir *dest*.

    The single copy block shared by :func:`_apply_init_seeds`
    (``if_absent=True``: copy-once, NEVER clobber existing box content) and
    :func:`_apply_synced_copies` (``if_absent=False``: overwrite, mtime-gated via
    *skip_if*):

    * Missing source -> the ``%s %s: host_src %r does not exist; skipping``
      warning (label ``"seed"`` / ``"synced"``) then return.
    * *skip_if* (the synced mtime gate) -> return before copying when it reports
      the dest is already up to date.
    * Directory source -> :func:`~kanibako.launch.templates.copy_resource_tree_if_absent`
      (create-if-absent) when *if_absent* else ``copytree(dirs_exist_ok=True)``
      (overwrite).
    * File source -> ``copy2`` (skipped for an existing dest when *if_absent*).
    """
    from kanibako.launch.templates import copy_resource_tree_if_absent

    if not src.exists():
        logger.warning(
            "%s %s: host_src %r does not exist; skipping",
            label, name, host_src,
        )
        return
    if skip_if is not None and skip_if(src, dest):
        return
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        if if_absent:
            copy_resource_tree_if_absent(src, dest)
        else:
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
    elif not if_absent or not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))


def _host_copy_dest(
    box_dest: str, box_root: Path, *, label: str, name: str, logger,
) -> Path | None:
    """Validate a HOST-space COPY destination and return it, or None to skip.

    The host arm of the two-namespace split (see
    ``settings_categories.CategoryEntry.dest_space``).  A host dest is already an
    absolute host path, so there is nothing to translate — what there IS to do is
    §2a's enforcement point 2: **assert the dest lands inside the BOX STORE ROOT**
    before anything is written.  Seed dests are fixed by KEYS, so a template cannot
    choose where it writes; the residual escape is a settings-declared dest such as
    ``@box.canon/../../../etc``, and this is where that is refused.

    *box_root* is ``@meta.box.path`` — obtained as ``shell_path.parent``, which
    equals it in EVERY mode by construction (the shell dir is ``<box_dir>/home``:
    ``boxes/<name>/home`` for primary, ``<project>/box_data/home`` for standalone).

    Returns ``None`` (with a warning) rather than raising: a mis-declared seed must
    not cost the user the box's other seeds, and the warning names the key's dest.
    """
    dest = Path(box_dest)
    try:
        contained = dest.resolve().is_relative_to(box_root.resolve())
    except OSError:  # pragma: no cover - defensive (unresolvable path)
        contained = False
    if not contained:
        logger.warning(
            "%s %s: host destination %r is outside the box store %s; skipping",
            label, name, box_dest, box_root,
        )
        return None
    return dest


def _apply_init_seeds(
    *,
    std,
    proj,
    agent_name: str,
    target=None,
    global_config_path,
    agent_config_path,
    logger,
    deliver_creds: bool = True,
) -> None:
    """Copy configured copy-once-at-init seeds into the new project's shell dir.

    ADDITIVE: with no seed config and no target default seeds, copies nothing.
    Routes the category config through the reconcile model
    (:func:`_resolve_launch_categories`) and applies the COPY winners whose
    category is ``seeded``, translating each guest_dest (/home/agent/X) to a host
    path under proj.shell_path and copying host_src -> that path once.

    The LAYERED ``seeded.template`` trio (system/base -> agent -> workset; spec
    §2a, Q1) flows through THIS route too — no separate on-disk staging pass. The
    template layers all target ``~`` (the create-time home); the seeded-category
    reconcile keeps every same-dest COPY (copies OVERLAY, they do not shadow —
    :func:`kanibako.settings.settings_categories.reconcile_categories`), so here the
    ``~``-group is a list of layer sources in scope apply order that
    :func:`kanibako.launch.templates.stage_layers` stages PER-FILE LAST-WINS then copies
    into home CREATE-IF-ABSENT (never clobbering user content). A layer whose
    source dir is absent (e.g. an unpopulated ``@workset.template``) is skipped.

    The credential gate (D-M4) is applied during reconcile: a credential-flagged
    ``seeded`` entry is suppressed for a PRIVATE box (*deliver_creds* False).

    ⚑⚑ TWO DESTINATION NAMESPACES.  The six §2a seed keys resolve to absolute HOST
    paths under the box store (``@meta.box.path/home``, ``@box.canon/handbook``); a
    user-declared ``<scope>.seeded.<name>`` stays GUEST-space and is translated by
    ``container._guest_dest_to_host`` exactly as before.  The two are textually
    indistinguishable — on a host whose user home is ``/home/agent`` a host store
    path STARTS WITH the guest home prefix — so the entry carries its space and this
    function branches on it.  Translating a host dest would silently write the box's
    handbook chapter somewhere nothing reads, and report success.
    """
    from kanibako.settings.settings_resolve import GUEST_HOME
    from kanibako.launch.templates import (
        seed_keys_of,
        stage_layers,
        template_seed_defaults,
    )

    default_seeds = target.default_seeds() if target is not None else {}
    # The layered template seeds (spec §2a) as ordinary keystore ``seeded`` keys —
    # folded in alongside the target's cred seeds so they resolve + apply through
    # the SAME single seeded-category route (Q1: everything through the keystore).
    template_seeds = template_seed_defaults(proj, agent_name)

    # Single-route (7c): resolve the seed COPY winners off the ONE committed
    # KeyStore snapshot pipeline (``build_launch_snapshot`` → reconcile, via
    # ``_resolve_launch_snapshot``), replacing the retired second resolver route
    # (the retired by-name category resolver, now the frozen
    # ``tests/support/flawed_oracle.py`` baseline).
    # NARROW injection: ``include_base_families=False`` +
    # ``extra_default_categories`` injects ONLY the target's declared seeds + the
    # template layer keys (NOT the unrelated core/channel/share families). The
    # agent-binding inputs (``desc``/``install``) are omitted — they feed only
    # ``agent.<agent>.bindings.*`` MOUNTs, never the seeded COPY winners.
    _snapshot, reconciled = _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name=agent_name,
        system_settings_path=global_config_path,
        agent_cfg_path=agent_config_path,
        desc=None,
        install=None,
        target=target,
        agent_cfg=None,
        include_base_families=False,
        extra_default_categories={**default_seeds, **template_seeds},
        deliver_creds=deliver_creds,
        host_dest_keys=seed_keys_of(template_seeds),
    )

    # Group the seeded COPY winners by (dest_space, resolved dest), PRESERVING the
    # reconcile apply order (system -> agent -> workset -> box) within each dest — so
    # a dest targeted by multiple layers (the template trio at the box home, the
    # handbook trio at @box.canon/handbook) is staged in that order, later overlaying
    # earlier.  ⚑ The SPACE is part of the key for the same reason it is in
    # ``reconcile_categories``: the two namespaces can produce the same STRING.
    by_dest: dict[tuple[str, str], list] = {}
    for seed in reconciled.copies:
        if seed.category != "seeded":
            continue  # synced copies are applied by _apply_synced_copies.
        by_dest.setdefault((seed.dest_space, seed.box_dest), []).append(seed)

    box_root = Path(proj.shell_path).parent
    for (dest_space, box_dest), group in by_dest.items():
        if dest_space == "host":
            # HOST-space (the §2a seed keys): the dest IS an absolute host path
            # already — use it DIRECTLY, after the §2a containment check. Feeding it
            # to the guest translator is the silent bug this branch exists to close.
            dest = _host_copy_dest(
                box_dest, box_root, label="seed", name=group[0].name, logger=logger,
            )
        else:
            dest = _guest_dest_to_host(
                box_dest, proj.shell_path, proj.project_path, map_home_root=True,
            )
            if dest is None:
                logger.warning(
                    "seed %s: guest_dest %r is outside %s; skipping",
                    group[0].name, box_dest, GUEST_HOME,
                )
        if dest is None:
            continue
        if len(group) > 1:
            # A LAYERED dest (the ``seeded.template`` trio at the box home, the
            # ``seeded.handbook`` trio at @box.canon/handbook): stage the layer
            # sources IN ORDER (per-file last-wins) then seed create-if-absent.
            # ``stage_layers`` skips any absent-dir source (skip-if-absent — e.g. an
            # unpopulated @workset.template).
            for e in group:
                assert e.host_src is not None  # seeds always have a source.
            dest.mkdir(parents=True, exist_ok=True)
            stage_layers(dest, [Path(e.host_src) for e in group])
            continue
        # Single-source dest — the ordinary per-seed copy (unchanged behavior).
        # Create-if-absent (``if_absent=True``): a seed delivers content ONCE;
        # existing home content is owned by the box and must never be overwritten
        # by a re-seed (the playbook-clobber bug).
        seed = group[0]
        assert seed.host_src is not None  # seeds always have a source.
        _apply_shell_copy(
            Path(seed.host_src), dest,
            label="seed", name=seed.name, host_src=seed.host_src,
            logger=logger, if_absent=True,
        )


def _apply_synced_copies(
    *,
    std,
    proj,
    agent_name: str,
    target=None,
    global_config_path,
    agent_config_path,
    logger,
    deliver_creds: bool = True,
) -> None:
    """Apply the ``<scope>.synced.<name>`` category copies into the box shell dir.

    Unlike copy-once seeds, synced entries are reapplied on EVERY launch, but
    only when the host source is NEWER than the box-side copy (mtime gating) so
    an unchanged source is a no-op.  Routes the category config through the
    reconcile model (:func:`_resolve_launch_categories`) and applies the COPY
    winners whose category is ``synced``, translating each guest_dest
    (/home/agent/X) to a host path under ``proj.shell_path``.

    This is the settings-driven synced path, DISTINCT from the plugin
    descriptor's ``cred_files`` credsync engine (descriptor-driven) — the two do
    not overlap, so there is no double application.

    The credential gate (D-M4) is applied during reconcile: every ``synced``
    entry is suppressed for a PRIVATE box (*deliver_creds* False).

    ADDITIVE: with no ``synced.*`` keys configured (and no target default synced
    entries) the reconciled copy set has no ``synced`` winners -> copies nothing.
    """
    from kanibako.settings.settings_resolve import GUEST_HOME

    # Single-route (7c): resolve the synced COPY winners off the ONE committed
    # KeyStore snapshot pipeline (``build_launch_snapshot`` → reconcile, via
    # ``_resolve_launch_snapshot``), replacing the retired second resolver route.
    # NOTE: synced entries come ONLY from settings `<scope>.synced.<name>` keys —
    # plugin descriptors do NOT yet declare default synced entries (Phase 8) — so
    # there is NO default-category table to inject; the narrow
    # ``include_base_families=False`` (no ``extra_default_categories``) resolves
    # synced purely from the cascade config files, byte-for-byte the old resolve.
    # *target* is accepted for call-site symmetry and contributes nothing until
    # descriptors declare default synced entries.
    _snapshot, reconciled = _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name=agent_name,
        system_settings_path=global_config_path,
        agent_cfg_path=agent_config_path,
        desc=None,
        install=None,
        target=target,
        agent_cfg=None,
        include_base_families=False,
        deliver_creds=deliver_creds,
    )

    box_root = Path(proj.shell_path).parent
    for sync in reconciled.copies:
        if sync.category != "synced":
            continue  # seeded copies are applied by _apply_init_seeds.
        assert sync.host_src is not None  # synced entries always have a source.
        if sync.dest_space == "host":
            # HOST-space arm — built HERE, ahead of its first user, deliberately:
            # spec §2d's ``synced`` dests are moving host-side with the plugin
            # packages (``@meta.box.path/home/.claude/.credentials.json`` and
            # friends), and that phase must NOT have to re-derive this branch. Today
            # no shipped declaration sets it, so this is inert; the moment one does,
            # it is already correct rather than silently mapped into the box home.
            dest = _host_copy_dest(
                sync.box_dest, box_root,
                label="synced", name=sync.name, logger=logger,
            )
        else:
            # Shared translator (audit P3): a ``~/workspace/...`` dest now maps under
            # proj.project_path (the workspace bind) rather than the shadowed
            # shell_path/workspace stub the former inline branch computed.
            dest = _guest_dest_to_host(
                sync.box_dest, proj.shell_path, proj.project_path,
                map_home_root=True,
            )
            if dest is None:
                logger.warning(
                    "synced %s: guest_dest %r is outside %s; skipping",
                    sync.name, sync.box_dest, GUEST_HOME,
                )
        if dest is None:
            continue
        # if_absent=False -> overwrite (reapplied every launch), mtime-gated so an
        # unchanged source is a no-op.
        _apply_shell_copy(
            Path(sync.host_src), dest,
            label="synced", name=sync.name, host_src=sync.host_src,
            logger=logger, if_absent=False, skip_if=_synced_uptodate,
        )


# Default ``box.masks`` (decision B): there is NO default tmpfs mask.  The vault
# moved OUT of ``~/workspace`` in 1.6.0, so there is nothing in the workspace to
# hide behind a tmpfs; the old vestigial ``~/workspace/vault`` mask is DROPPED.
# The seam is kept so a box (or any scope) may still declare masks via
# ``box.masks`` / ``<scope>.masks`` — the category resolver injects this default
# (empty) at the AGENT level and reconciles any explicit declarations on top.
#
# Per spec §2a ``masks`` is a real ``list[box_dest]`` (NOT a comma-string), so
# the default is a LIST (empty) — the resolver iterates it as real entries.  The
# STATIC value lives in the shipped system/core defaults file (P6b coalesce);
# this module is a thin reader (:func:`kanibako.settings.core_defaults.vault_mask_default`),
# which now returns an empty list.
VAULT_MASK_DEST = core_defaults.vault_mask_default()


def _channel_default_categories(std, proj) -> dict[str, tuple[str, str]]:
    """Build the per-mode channel bind table as ``default_categories`` (§2c/§2f).

    Thin reader over :func:`kanibako.settings.core_defaults.channel_default_categories`:
    the STATIC structure + box-side destinations live in the shipped system/core
    defaults file (P6b coalesce); the loader injects the runtime-probed host
    sources.  Injected through the category resolver (D-B1 precedence + depth-sort
    + L7 guarantee-create) exactly like masks/common.  PRIMARY + NAMED get the
    three workset-local roots; STANDALONE OMITS them (A10).
    """
    return core_defaults.channel_default_categories(std, proj)


def _seed_channel_files(std, proj) -> None:
    """Guarantee-create the chat log files inside the channel sources (§3c).

    ``chat/general.md`` (default log) + ``chat/broadcast.md`` (reserved broadcast
    log) at the SYSTEM chat dir (every mode); and at the WORKSET chat dir for
    primary/named.  Create-if-absent (idempotent — never overwrites a user-edited
    log); the partition source DIRS themselves are mkdir'd by the L7 rw-branch.
    Keeps ``_rotate_file`` on each ``broadcast.md`` (A5 — parity with the legacy
    ``broadcast.log`` rotation).
    """
    from kanibako.channels import channels as _ch

    chat_dirs = [std.channels_chat]
    wch = _ch.workset_channel_paths(proj, std)
    if wch is not None:
        chat_dirs.append(wch.chat)

    for chat_dir in chat_dirs:
        try:
            chat_dir.mkdir(parents=True, exist_ok=True)
            general = chat_dir / "general.md"
            if not general.exists():
                general.touch()
            broadcast = chat_dir / "broadcast.md"
            if not broadcast.exists():
                broadcast.touch()
            _rotate_file(broadcast)
        except OSError:
            # Best-effort: a genuinely unwritable source surfaces at launch.
            pass


def _core_default_categories(
    std, proj, *, guarantee_create: bool = True,
) -> dict[str, tuple[str, str, str]]:
    """Build the core box mounts as ``default_categories`` (step 3).

    Thin reader over :func:`kanibako.settings.core_defaults.core_default_categories`: the
    STATIC structure + box-side destinations + per-entry mount options live in the
    shipped system/core defaults file (``core:`` list); the loader injects the
    runtime-probed host sources off ``ProjectPaths``.  Injected through the category
    resolver (D-B1 precedence + depth-sort + L7 guarantee-create) exactly like
    masks/common/channels.  home + workspace are unconditional; the vault binds are
    gated on ``proj.enable_vault`` AND the source dir existing (reproducing the old
    hardwired ``if enable_vault and path.is_dir()`` skip-if-missing behavior).
    """
    return core_defaults.core_default_categories(
        std, proj, enable_vault=proj.enable_vault, mode=proj.mode.value,
        guarantee_create=guarantee_create,
    )


def _canon_reprotect_hook(proj, logger):
    """Return the POST-START canon re-protect callable for *proj*.

    ⚑⚑ WHY POST-START, NOT AT CREATE ONLY (bifrost, 2026-07-31).  The box home is
    bound with podman's ``:U`` option (``core-defaults.yaml`` key ``home``), which
    RECURSIVELY RE-CHOWNS the bind SOURCE to the container user's mapping every time
    a container is created.  So the ownership ``box create`` established is undone by
    the very next launch: measured host-side on one box as ``165536 165536 555``
    after create → ``1000 1000 555`` after start.  Modes survive; ownership does not
    — and 555 the agent OWNS is no protection at all, because the owner can simply
    ``chmod u+w``.  A gate agent proved exactly that: ``mkdir ~/canon/scratch``
    succeeded inside a started box.

    ``:U`` is NOT the thing to fix: it is the load-bearing keep-id correction for
    hosts whose user is not uid 1000 (the ``1549e39`` class).  Re-asserting after the
    chown is the cheap, correct answer — the same idempotent ``podman unshare``
    chown+chmod pass, a handful of calls.

    ⚑ ACCEPTED WINDOW — be precise about its size, it is NOT instantaneous.  On the
    DETACHED path the hook runs inline the moment ``podman run -d`` returns, so the
    gap is a single podman round-trip.  On the FOREGROUND path the box is left
    agent-owned for up to ONE WATCHER POLL INTERVAL (0.25s) into the session, and a
    container shorter-lived than that is only repaired when the session ENDS — the
    on-disk state always ends protected, but a very short ephemeral run is
    unprotected for its whole life.  Tolerable because this mechanism is
    ANTI-POLLUTION (keep an agent from littering ~/canon), not a security boundary —
    an agent that wanted to write there could already do so by other means.

    Returns None when there is nothing to protect.
    """
    shell_path = getattr(proj, "shell_path", None)
    if shell_path is None:
        return None

    def _reprotect() -> None:
        # quiet=True: this runs as the terminal is handed to the agent, and on a
        # docker/unshare-less host the degraded report would fire on EVERY launch,
        # painting into the live TUI/tmux session.  Box create already said it once,
        # loudly; the per-launch re-assert logs at DEBUG.
        core_defaults.materialize_canon_skeleton(
            shell_path, logger=logger, quiet=True,
        )

    return _reprotect


def _kanibako_mounts():
    """Build bind mounts for the kanibako CLI inside containers.

    Returns two mounts:
      1. Package dir → ``{KANIBAKO_PKG_MOUNT_ROOT}/kanibako`` (ro)
      2. Entry script → /home/agent/.local/bin/kanibako (ro)

    The package-dir dest is derived from :data:`KANIBAKO_PKG_MOUNT_ROOT` — the SAME
    constant the supervisor PID-1 injects as PYTHONPATH and scrubs back out — so the
    mount location and the import path can never drift.
    """
    import importlib.resources

    import kanibako
    from kanibako.targets.base import Mount

    pkg_dir = Path(kanibako.__file__).parent

    entry_ref = importlib.resources.files("kanibako.scripts").joinpath("kanibako-entry")
    entry_path = Path(str(entry_ref))

    return [
        Mount(pkg_dir, f"{KANIBAKO_PKG_MOUNT_ROOT}/kanibako", "ro"),
        Mount(entry_path, "/home/agent/.local/bin/kanibako", "ro"),
    ]


# AF_UNIX ``sun_path`` length limit.  The platform value is 108 bytes on Linux
# and 104 on macOS/BSD.  We use the smaller (104) as a conservative
# cross-platform floor so a socket path validated here is portable to either —
# never the larger value, which would let a Linux-only path slip past and then
# fail on macOS.
_UNIX_SOCKET_PATH_LIMIT = 104


def _run_setup_command(
    *,
    runtime: ContainerRuntime,
    image: str,
    proj,
    container_name: str,
    setup_entrypoint: str,
    setup_args: list[str],
    extra_mounts: list,
    tmpfs_masks,
    container_env: dict[str, str],
) -> int:
    """Run a target's one-time interactive setup command in a fresh box.

    Mirrors the normal launch ``runtime.run`` (same image/mounts/env so the
    setup writes into the box's mounted home), but with the SETUP entrypoint
    (e.g. ``goose configure``) and run in the FOREGROUND (``detach=False``) so
    it inherits stdio and the user can answer its prompts.  Any prior container
    under *container_name* is removed first; the setup container is removed on
    completion so the subsequent normal relaunch starts clean.

    Returns the setup command's exit code.
    """
    # Clear any leftover (exited) container occupying the name.
    if runtime.container_exists(container_name):
        runtime.rm(container_name)
    rc = runtime.run(
        image,
        shell_path=proj.shell_path,
        project_path=proj.project_path,
        vault_ro_path=proj.vault_ro_path,
        vault_rw_path=proj.vault_rw_path,
        extra_mounts=extra_mounts or None,
        tmpfs_masks=tmpfs_masks or None,
        enable_vault=proj.enable_vault,
        env=container_env,
        name=container_name,
        entrypoint=setup_entrypoint,
        cli_args=setup_args or None,
        detach=False,
        post_start=_canon_reprotect_hook(proj, None),
    )
    # Remove the setup container so the relaunch recreates it fresh.
    if runtime.container_exists(container_name):
        runtime.rm(container_name)
    return rc


def _container_logs(runtime: ContainerRuntime, name: str) -> str:
    """Return recent container logs, or empty string on failure."""
    result = subprocess.run(
        [runtime.cmd, "logs", "--tail", "50", name],
        capture_output=True, text=True,
    )
    return (result.stdout + result.stderr).strip() if result.returncode == 0 else ""


def _container_exit_code(runtime: ContainerRuntime, name: str) -> int:
    """Return the container's last exit code, or 0 if undeterminable.

    Best-effort: a runtime whose ``inspect`` fails to run (missing binary /
    ``OSError``), returns non-zero, or emits unparseable output all resolve to 0
    (undeterminable) — this reads a code for error surfacing (E2d) and must never
    itself raise, so a callers' ``_container_exit_code(...) or rc`` fallback holds.
    """
    try:
        result = subprocess.run(
            [runtime.cmd, "inspect", "--format", "{{.State.ExitCode}}", name],
            capture_output=True, text=True,
        )
    except OSError:
        return 0
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _interactive_host() -> bool:
    """True iff the log-echo target (``sys.stderr``) is a real TTY (human attached).

    Gates the interactive-only terminal hygiene on the foreground box-exit path:
    a human attached at ``sys.stderr`` must NOT get the raw captured pane echoed
    at them.  Keyed on ``sys.stderr`` SPECIFICALLY — the stream the raw ``logs``
    are printed to — NOT stdout, so a consumer that redirected only stderr to
    capture the logs (``kanibako start 2> logfile``, stdout still a tty) still
    gets them.  Piped output / ``podman logs`` / CI (stderr not a TTY) keep the
    byte-for-byte log-echo downstream tooling consumes.  Tolerant: if ``isatty``
    raises, treat as not-a-tty (print the logs — never lose diagnostics).
    """
    try:
        return bool(sys.stderr.isatty())
    except Exception:
        return False


def _restore_host_terminal() -> None:
    """Reset the host TTY after an interactive foreground attach returns.

    A full-screen agent TUI (e.g. claude) that DIES without cleanup leaves the
    host terminal in the alternate screen with dirty SGR state and a hidden
    cursor — the ``reset``-needed garble a human hits on a foreground box exit.
    Emit the minimal restore trio — leave alt-screen (``\\033[?1049l``), reset
    SGR (``\\033[0m``), show cursor (``\\033[?25h``) — so a TUI that never ran
    ``rmcup`` does not leave the tty wedged.  Idempotent on an already-clean
    terminal.  No-op on a non-TTY (piped output / CI): those consumers stay
    byte-clean.  Tolerant: any stream error is swallowed (hygiene must never
    raise on a teardown path).
    """
    for stream in (sys.stderr, sys.stdout):
        try:
            if not stream.isatty():
                continue
            stream.write("\033[?1049l\033[0m\033[?25h")
            stream.flush()
            return
        except Exception:
            continue


def _validate_mounts(mounts: list, logger) -> None:
    """Warn about mount sources that don't exist on the host.

    Called before ``runtime.run()`` to catch issues early with a clear
    message instead of a cryptic Podman error.
    """
    for mount in mounts:
        src = mount.source
        if not src.exists():
            logger.warning("Mount source missing: %s → %s", src, mount.destination)
            print(
                f"Warning: mount source does not exist: {src}",
                file=sys.stderr,
            )


_ROTATE_MAX_BYTES = 1_048_576  # 1 MiB


def _rotate_file(path: Path) -> None:
    """Rotate *path* if it exceeds the size threshold."""
    try:
        size = path.stat().st_size
        if not isinstance(size, int) or size < _ROTATE_MAX_BYTES:
            return
    except (OSError, TypeError):
        return
    backup = path.with_suffix(path.suffix + ".1")
    path.rename(backup)
    path.touch()


# Length (hex chars) of the bounded hash fallback for an over-long box name.
# 16 hex chars = 64 bits of a SHA-256 prefix: ample collision resistance for
# the per-user set of boxes while keeping the basename tiny (``<16>.sock``).
_SOCKET_HASH_LEN = 16


def bounded_socket_name(identity: str, run_dir: Path) -> str:
    """Return a bounded, deterministic ``.sock`` basename for *identity*.

    The host helper socket lives at ``run_dir / <name>``.  *identity* is the
    combined ``<box>-<ws>`` string (box name + workset-name token, per
    ``@system.runtime/<box>-<ws>.sock``) — the box name alone is NOT unique
    across worksets that reuse a project name, so the ws token is required.
    When ``<identity>.sock`` fits under the AF_UNIX limit at *run_dir* it is
    used verbatim; otherwise the name is replaced by a fixed-width SHA-256
    prefix of *identity* (deterministic per identity — so a later reattach
    computes the same socket — and collision-safe across boxes).
    """
    verbatim = f"{identity}.sock"
    if len(str(run_dir / verbatim)) < _UNIX_SOCKET_PATH_LIMIT:
        return verbatim
    return f"{short_hash(project_hash(identity), _SOCKET_HASH_LEN)}.sock"


def validate_socket_path(socket_path: Path) -> None:
    """Raise ValueError if *socket_path* exceeds the AF_UNIX length limit."""
    path_len = len(str(socket_path))
    if path_len >= _UNIX_SOCKET_PATH_LIMIT:
        raise ValueError(
            f"Socket path too long ({path_len} >= {_UNIX_SOCKET_PATH_LIMIT}): "
            f"{socket_path}"
        )
