"""Box always-on-agent SUPERVISOR — the PID-1 keep-alive that outlives sessions.

PID-1 pair with :mod:`kanibako.box_lifecycle`: pinned flat, STDLIB-ONLY, invoked in-box
by dotted literal.  ALSO the single source of the host-side launch literals
:data:`CONTINUE_MARKER` / :data:`KANIBAKO_PKG_MOUNT_ROOT` / :data:`PINNED_ROOT_RELPATH`,
which ``commands/start.py`` imports at MODULE scope.

Terminology — SELF-HEAL: restart a dead agent with the continue grammar + marker.
PANEL-WATCH: the E2f agentless ``code`` warm-up loop.  NEWCOMER: a live marker PID that
is not the supervisor's own agent.  TAKEOVER: the 4b single-writer evict (DEFAULT-OFF).
REAP (markers): delete a marker file whose PID is dead, or alive but not the agent
SESSION, so a leaked marker is an EDGE the supervisor sees once instead of a level it
sees forever.  ⚑ SESSION, not "an agent process": an agent runs helpers under its own
binary, and a marker naming one of those is not a second agent holding the session.
DIRECTIVE FRESHNESS: re-flatten the agent's native instruction slot when one of the
directive sources it was rendered from changes (:class:`DirectiveWatch`).

⚑ Every probe and action here is TOLERANT — PID-1 must never die on a hiccup.
⚑ Every impure op — subprocess, sleep, marker probe, process signal, child reap — funnels
through an INJECTABLE seam on :class:`BoxSupervisor`, so a test never touches a real tmux,
a real clock or a real process.
⚑ Prose lives in ``llm-docs/kanibako/box_supervisor.py.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import FrameType

from kanibako.box_lifecycle import (
    AttachState,
    LifecycleEvent,
    classify_transition,
    snapshot_attach_state,
)
from kanibako.log import get_logger

#: The continue-marker a self-heal restart delivers via ``tmux send-keys`` as a
#: real acting turn (design §108).  ⚑ Single-sourced HERE — ``commands/start.py``
#: passes it as ``--marker``; never duplicate the literal.
CONTINUE_MARKER = "[Agent handoff - Continue prior task(s)]"

#: The heads-up 4b ENFORCEMENT delivers to a CLI INCUMBENT about to be evicted
#: (design §346).  ⚑ Injection is feasible ONLY toward the tmux (CLI) agent — the
#: DIRECTIONAL limit — so this is used only in the CRITICAL direction.
TAKEOVER_HEADS_UP = (
    "[Session takeover - another surface is taking over this session; "
    "wind down and checkpoint now if you have work in progress]"
)

#: In-box read-only bind-mount ROOT of the HOST kanibako package.
#: ⚑ Single-sourced HERE, the lowest module that needs it: ``commands/start.py``
#: imports it and :func:`scrub_bootstrap_pythonpath` strips it back out, so the
#: mount dest and the injected/scrubbed PYTHONPATH cannot drift.  ⚑ ``scripts/
#: kanibako-entry`` and ``data/core-defaults.yaml`` carry the same literal (neither
#: can import a constant) — keep all three in sync.
KANIBAKO_PKG_MOUNT_ROOT = "/opt/kanibako"


#: The FIXED box-side root for state that CANNOT resolve without a live box but
#: MUST be placed BEFORE one exists.
#: ⚑ QUARANTINED DUPLICATE of
#: :data:`kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH`, deliberately
#: NOT imported: PID-1 is stdlib-only by contract, and widening the import surface
#: would put every launch's forward-compat probe at the mercy of the settings
#: package importing cleanly.  A test pins the two; ``scripts/helper-init.sh``
#: carries the third (bash can import neither).
PINNED_ROOT_RELPATH = ".kanibako"

#: The XDG facets served from the pinned root once the box is LIVE:
#: ``(env var, XDG spec default suffix, facet dir under the pinned root)``.
#: ⚑ ONE ROW TODAY, deliberately a TABLE — a second facet is a ROW here, never a
#: second mechanism.
XDG_PROJECTIONS: tuple[tuple[str, str, str], ...] = (
    ("XDG_STATE_HOME", ".local/state", "state"),
)

#: The link's own basename under each projected XDG base — ``$XDG_STATE_HOME/kanibako``.
#: ⚑ Single-sourced: BOTH halves of the projection spell it.
XDG_LINK_NAME = "kanibako"

log = get_logger("box_supervisor")


def project_pinned_xdg(
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Serve the box's real XDG locations from the pinned root — the POST-BOOT half."""
    base = Path.home() if home is None else home
    env = os.environ if environ is None else environ
    created: list[str] = []
    for var, default_suffix, facet in XDG_PROJECTIONS:
        pinned = base / PINNED_ROOT_RELPATH / facet
        raw = env.get(var, "")
        # XDG spec: honor the var iff set AND absolute; else the default under $HOME.
        xdg_base = Path(raw) if raw and os.path.isabs(raw) else base / default_suffix
        link = xdg_base / XDG_LINK_NAME
        if os.path.normpath(link) == os.path.normpath(pinned):
            continue
        try:
            pinned.mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                if os.path.realpath(link) != os.path.realpath(pinned):
                    log.warning(
                        "%s is a symlink to something else (%s); leaving it alone. "
                        "kanibako state lives at %s.",
                        link, os.path.realpath(link), pinned,
                    )
                continue
            if link.exists():
                log.warning(
                    "%s already exists as a real path; leaving it alone. kanibako "
                    "state lives at %s — remove %s to have it served there too.",
                    link, pinned, link,
                )
                continue
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(pinned, target_is_directory=True)
            created.append(str(link))
            log.info("projected %s -> %s", link, pinned)
        except OSError as exc:
            log.warning("could not project %s onto %s: %s", link, pinned, exc)
    return created


def xdg_projection_sh() -> str:
    """The POSIX-``sh`` twin of :func:`project_pinned_xdg`, GENERATED from the table."""
    blocks: list[str] = []
    for var, default_suffix, facet in XDG_PROJECTIONS:
        blocks.append(
            f'_kb_pin="$HOME/{PINNED_ROOT_RELPATH}/{facet}"\n'
            f'_kb_xdg="${{{var}:-}}"\n'
            # XDG spec: honor the var iff set AND absolute (the `/*` arm); else the default.
            f'case "$_kb_xdg" in /*) ;; *) _kb_xdg="$HOME/{default_suffix}" ;; esac\n'
            f'_kb_link="$_kb_xdg/{XDG_LINK_NAME}"\n'
            # ⚑ The NESTING mirrors the Python's statement order exactly and each rung is
            # observable; flattening it would quietly stop creating the pinned dir on a box
            # that declines the link.  ⚑ `-L` before `-e`: `-e` is FALSE for a dangling link.
            'if [ "$_kb_link" != "$_kb_pin" ]; then\n'
            '    mkdir -p "$_kb_pin" 2>/dev/null || true\n'
            '    if [ ! -L "$_kb_link" ] && [ ! -e "$_kb_link" ]; then\n'
            '        mkdir -p "$_kb_xdg" 2>/dev/null '
            '&& ln -s "$_kb_pin" "$_kb_link" 2>/dev/null || true\n'
            '    fi\n'
            'fi'
        )
    blocks.append("unset _kb_pin _kb_xdg _kb_link")
    return "\n".join(blocks)


def scrub_bootstrap_pythonpath(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Strip the injected :data:`KANIBAKO_PKG_MOUNT_ROOT` entry from PYTHONPATH."""
    env = os.environ if environ is None else environ
    raw = env.get("PYTHONPATH")
    if raw is None:
        return
    kept = [p for p in raw.split(os.pathsep) if p != KANIBAKO_PKG_MOUNT_ROOT]
    if kept:
        env["PYTHONPATH"] = os.pathsep.join(kept)
    else:
        env.pop("PYTHONPATH", None)


# The subprocess-runner signature the tmux actions call; tests inject a fake so
# nothing touches a real tmux server.  Shared in spirit with box_lifecycle's.
_Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# The ``time.sleep`` signature the loop / backoff use; injectable so tests never wait.
_Sleeper = Callable[[float], None]

# The agent-marker probes (E2f liveness + 4a detection), injectable so unit tests never
# touch the real FS / os.  Defaults below are the real PID-1 implementations.
_PidAlive = Callable[[int], bool]
_MarkersLister = Callable[[str], "list[int]"]
# The marker IDENTITY probe and the REAP, same rule.  ⚑ The identity answer is
# THREE-valued: True/False decide, and ``None`` means CANNOT JUDGE — which keeps the
# marker, because deleting a live agent's marker is the worst outcome available here.
_CmdlineOf = Callable[[int], "list[str] | None"]
_AgentCheck = Callable[[int], "bool | None"]
_MarkerRemover = Callable[[str, int], None]

# The PROCESS-CONTROL primitives — the 4b takeover signals, the pane-group evict and the
# PID-1 reap duty.  Injectable exactly like ``run`` / ``sleep``, so a unit test can never
# signal a REAL process; the defaults are the stdlib ops (and the module reaper) below.
_Signaller = Callable[[int, int], None]   # os.kill / os.killpg
_GroupOf = Callable[[int], int]           # os.getpgid
_OwnGroup = Callable[[], int]             # os.getpgrp
_Reaper = Callable[[], int]               # reap_zombie_children


def _parse_stat_state(stat_text: str) -> str | None:
    """PURE: the process STATE character from a ``/proc/<pid>/stat`` line."""
    _, sep, tail = stat_text.rpartition(")")
    if not sep:
        return None
    fields = tail.split()
    return fields[0] if fields else None


def _proc_stat_state(pid: int) -> str | None:
    """The kernel state char for *pid* from ``/proc/<pid>/stat``, or ``None``."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return _parse_stat_state(text)


def _proc_cmdline(pid: int) -> list[str] | None:
    """Real ``_CmdlineOf``: *pid*'s ARGV, or ``None`` if it cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    # NUL-delimited argv with a trailing NUL; decode leniently, as
    # ``box_lifecycle._collect_proc_cmdlines`` does for the whole-table read.  ⚑ Kept
    # SPLIT, not joined: which token is argv[0] and which is argv[1] is what separates
    # an agent's session from its helpers, and joining throws that away.  A kernel
    # thread's EMPTY cmdline is ``None`` — unreadable, not "not an agent".
    text = raw.rstrip(b"\x00").decode("utf-8", "replace")
    if not text:
        return None
    return text.split("\x00")


def _default_pid_alive(pid: int) -> bool:
    """Real ``_PidAlive``: is *pid* a live (non-zombie) process?"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return _proc_stat_state(pid) != "Z"


def reap_zombie_children(max_reaps: int = 32) -> int:
    """Bounded non-blocking reap of this process's exited children (PID-1 duty)."""
    reaped = 0
    for _ in range(max_reaps):
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except OSError:
            log.debug("reap_zombie_children: waitpid raised; ending sweep", exc_info=True)
            break
        if pid == 0:  # children exist but none exited — done this tick
            break
        reaped += 1
        log.debug("reaped exited child pid %d", pid)
    return reaped


def _default_list_marker_pids(markers_dir: str) -> list[int]:
    """Real ``_MarkersLister``: the PIDs named by the marker FILES in *markers_dir*."""
    try:
        names = os.listdir(markers_dir)
    except OSError:
        return []
    pids: list[int] = []
    for name in names:
        try:
            pids.append(int(name))
        except ValueError:
            continue
    return pids


def _default_remove_marker(markers_dir: str, pid: int) -> None:
    """Real ``_MarkerRemover``: REAP the marker file naming *pid*; race-tolerant."""
    try:
        os.unlink(os.path.join(markers_dir, str(pid)))
    except FileNotFoundError:
        # The agent's own ``pid-rm.sh``, or a concurrent scan, got there first.  Two
        # removers agreeing on one file is the NORMAL case, not a failure.
        pass
    except OSError as exc:
        log.debug("could not reap marker %s/%d: %s", markers_dir, pid, exc)


def _argv_head(argv: Iterable[str]) -> tuple[str, str | None] | None:
    """PURE: an argv's ``(PROGRAM basename, SUBCOMMAND)`` head, or ``None`` if it has none.

    ⚑ ``argv[0]`` is SPLIT ON WHITESPACE first.  A harness that rewrites its process
    TITLE packs the subcommand into argv[0] rather than argv[1] — measured on a live
    box, ``claude bg-spare`` arrives as a single argv entry — so reading argv[1] alone
    would miss the very distinction this head exists to draw.

    The SUBCOMMAND slot is the first following token that is not an option; an argv
    that goes straight to flags has ``None``, which is itself the head shape a bare
    agent launch has.  Only the head is read: everything after it (session ids,
    permission flags, a model) is where a launch and the process it became DISAGREE.
    """
    parts = list(argv)
    if not parts:
        return None
    words = parts[0].split()
    if not words:
        return None
    program = os.path.basename(words[0])
    # A leading OPTION means the front of this argv is not a program at all.
    if not program or program.startswith("-"):
        return None
    rest = words[1:] + parts[1:]
    sub = rest[0] if rest and not rest[0].startswith("-") else None
    return program, sub


def agent_launch_heads(*argvs: Iterable[str]) -> set[tuple[str, str | None]]:
    """PURE: the ``(PROGRAM, SUBCOMMAND)`` heads the supervisor was launched to run.

    The launch grammars in :class:`SupervisorConfig` are the ONLY thing PID 1 knows
    about which agent this box runs, so they are what an "is this pid the agent" test
    has to be built from — never a hardcoded ``claude`` / ``codex`` / ``goose``, and
    never a list of the helper subcommands to exclude.

    ``commands/start.py`` wraps an agent launch in one ``sh -c <script> sh <program>
    <args...>`` shim per concern (secret export, directive flatten), nesting outward;
    every layer sets ``$0=sh`` and ``exec "$@"``s the next, so the agent is what
    remains once the layers are peeled off the front.

    BOTH grammars contribute, because start and continue modes may differ in the
    subcommand slot (``codex`` starts bare and continues as ``codex resume``), and the
    process may be either.

    ⚑ The shim shape is QUARANTINED KNOWLEDGE of a sibling module, deliberately not
    imported: PID 1 is stdlib-only, and importing the CLI would put every marker scan
    at the mercy of the whole command package importing cleanly.  ⚑ It is SAFE UNDER
    SKEW — a shim shape this does not recognise leaves the wrapper's own ``sh`` as the
    head, which matches no real agent, so :func:`agent_session_verdict` falls through
    to ``None`` and KEEPS the marker rather than reaping on a misread.
    """
    heads: set[tuple[str, str | None]] = set()
    for argv in argvs:
        rest = list(argv)
        while len(rest) >= 5 and rest[0] == "sh" and rest[1] == "-c" and rest[3] == "sh":
            rest = rest[4:]
        head = _argv_head(rest)
        if head is not None:
            heads.add(head)
    return heads


def agent_session_verdict(
    argv: Iterable[str], heads: set[tuple[str, str | None]],
) -> bool | None:
    """PURE: is *argv* the agent SESSION this supervisor supervises?  ``None`` ⇒ unjudgeable.

    ⚑ THE PROGRAM NAME ALONE IS NOT ENOUGH.  An agent runs its own helpers under its
    own binary — a daemon, a background pty host, a spare — and a basename test calls
    every one of them the agent, which is how a helper's marker reads as a second
    agent holding the session.  The HEAD is what separates them: the session's argv
    begins the way the launch grammar begins, and a helper's begins with a subcommand
    the supervisor was never launched to run.

    Only the head is compared, because the rest of the argv legitimately DIVERGES — a
    box launched ``claude --continue --dangerously-skip-permissions --model opus`` was
    measured running as ``claude --resume <uuid> --allow-dangerously-skip-permissions
    --model opus --permission-mode bypassPermissions``.  Matching further would reject
    real sessions, and rejecting a real session deletes its marker.

    ``None`` — CANNOT JUDGE — covers the two ways this can be wrong rather than
    guessing: nothing to match against, and a DIFFERENT program whose argv still names
    the agent (an interpreter launching it, a shell holding its path).
    """
    if not heads:
        return None
    head = _argv_head(argv)
    if head is None:
        return None
    if head in heads:
        return True
    if head[0] in {program for program, _ in heads}:
        return False        # the agent's binary, but not the session's subcommand
    if _names_an_agent(argv, heads):
        return None
    return False


def _names_an_agent(argv: Iterable[str], heads: set[tuple[str, str | None]]) -> bool:
    """PURE: does *argv* NAME an agent program anywhere, without being one?

    The loose test :func:`agent_session_verdict` falls back on, mirroring
    :func:`kanibako.box_lifecycle.vscode_server_present`: PREFIX-test every
    ``/``-delimited segment.  It exists to catch the shapes a head cannot judge — an
    interpreted install (``node …/claude-code/cli.js``), a shell whose argv carries an
    agent path — and it answers only INCONCLUSIVE, never "agent".
    """
    names = {program for program, _ in heads}
    return any(
        part.startswith(name)
        for part in " ".join(argv).split("/")
        for name in names
    )


def scan_marker_pids(
    markers_dir: str,
    *,
    list_pids: _MarkersLister,
    pid_alive: _PidAlive,
    is_agent: _AgentCheck | None = None,
    remove: _MarkerRemover | None = None,
) -> tuple[set[int], set[int]]:
    """Partition the markers dir into (LIVE pids, STALE pids), REAPING the stale ones.

    A marker is STALE when the pid it names is not alive — or, when *is_agent* is
    given, is alive but is not the agent SESSION.  That second case covers both a
    leaked marker whose pid the kernel has REISSUED, and a marker naming one of the
    agent's OWN helper processes, which is not a second agent holding the session.

    Both are handed to *remove*, so a stale marker becomes an EDGE the supervisor
    sees once instead of a level it sees forever: this call still RETURNS the stale
    pid, so the panel agent's death still reaches :func:`decide_panel` on the tick
    that noticed it, and no later tick re-decides on the same corpse.

    ⚑ NEVER REAP WHAT YOU CANNOT JUDGE.  A probe that raises, and an *is_agent* that
    answers ``None`` (nothing to match against, or ``/proc`` unreadable), both leave
    the marker alone — a pid that is genuinely gone reads dead on a later scan, while
    a wrongly deleted marker is a live agent the supervisor has stopped seeing.
    """
    live: set[int] = set()
    stale: set[int] = set()
    for pid in list_pids(markers_dir):
        if pid <= 0:
            continue
        try:
            alive = pid_alive(pid)
            if alive and is_agent is not None and is_agent(pid) is False:
                alive = False
        except Exception:
            log.debug("scan_marker_pids: a probe raised for pid %d; skipping", pid)
            continue
        if alive:
            live.add(pid)
            continue
        stale.add(pid)
        if remove is not None:
            remove(markers_dir, pid)
    return live, stale


def newcomer_pids(live_pids: set[int], own_pids: set[int]) -> set[int]:
    """PURE: the LIVE marker PIDs that are NOT the supervisor's OWN agent."""
    return live_pids - own_pids


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------

#: Flatten-manifest format this watcher understands.  Anything else — a newer
#: flattener, a truncated file, no file at all — reads as STALE and costs one
#: re-flatten; never parse an unknown shape defensively into a wrong answer.
#: ⚑ The WRITER (``kanibako/scripts/import-directives.py``, ``MANIFEST_VERSION``)
#: carries the same number.  It is a standalone box-side script that can import
#: nothing from the package, so the two cannot share a constant — the skew is made
#: SAFE by this rule instead of being prevented.
DIRECTIVE_MANIFEST_VERSION = 1

#: Seconds PID 1 will wait for one re-flatten before giving up on it (see
#: :meth:`BoxSupervisor._reflatten_directives`).  A cap on a HANG, not a work budget.
FLATTEN_TIMEOUT = 60.0


@dataclass(frozen=True)
class DirectiveWatch:
    """What PID 1 needs to keep the flattened instruction slot FRESH — all four, or none.

    The launch shim flattens the directive chain into the agent's native slot ONCE
    per agent launch, so a source edited mid-container-life leaves that file stale
    and silent.  PID 1 outlives every session and every harness, so it is where the
    file becomes continuously true rather than true-at-launch.

    Every path is DECIDED BY THE LAUNCHER and arrives in argv; the supervisor derives
    none of them.  Grouping them in one value is the guard: a half-wired watcher (a
    manifest with no flattener to run, a dest with no seed to render it from) cannot
    be represented, so no loop has to check for one.

    * *seed* / *dest* — the flattener's SOURCE and the agent's native slot, exactly as
      the launch shim spells them (``$KANIBAKO_DIRECTIVE_SEED`` / ``_FINAL``).
    * *manifest* — the receipt the flattener leaves; see the flattener's
      ``build_manifest``.
    * *flattener* — in-box path of the flattener script.  ⚑ PASSED, never spelled
      here: that literal is already carried twice (``start._directive_flatten_shim``
      and ``vscode.vscode_config``), and a third copy would be a third thing to move
      in step, silently, when the script moves.
    * *interval* — target seconds between checks, converted to whole ticks against
      ``poll_interval``.  Cheap enough (a few sha256 of small files) to run often and
      far below any human edit-to-read gap.
    """

    seed: str
    dest: str
    manifest: str
    flattener: str
    interval: float = 5.0


@dataclass(frozen=True)
class SupervisorConfig:
    """Immutable configuration for a :class:`BoxSupervisor`."""

    session: str
    start_argv: list[str]
    continue_argv: list[str]
    marker: str
    poll_interval: float = 2.0
    max_restart_retries: int = 3
    backoff_base: float = 0.5
    send_keys_retries: int = 3
    send_keys_delay: float = 0.1
    on_agent_exit: str = "self-heal"
    session_takeover: bool = False
    takeover_grace: float = 5.0
    panel_watch: bool = False
    agent_markers_dir: str | None = None
    creds_flag: str | None = None
    #: Directive-freshness watch, or ``None`` (the watcher is then inert and every
    #: tick is byte-unchanged) — a launch with no directive slot threads nothing.
    directives: DirectiveWatch | None = None
    # Bounded scrollback echoed to PID-1's stdout so ``podman logs`` surfaces the dead
    # agent's final output to the host (:meth:`BoxSupervisor.capture_agent_output`).
    capture_history: int = 200


# ---------------------------------------------------------------------------
# Action model + the PURE decision function (the heart of the unit tests).
# ---------------------------------------------------------------------------

class ActionKind(Enum):
    """What the supervisor must DO for a tick (besides the detach hook)."""

    NONE = "none"
    SELF_HEAL = "self_heal"


@dataclass(frozen=True)
class SupervisorAction:
    """The decision a single watch-loop tick produces."""

    kind: ActionKind = ActionKind.NONE
    fire_detach_hook: bool = False


def decide(
    prev_state: AttachState,
    cur_state: AttachState,
    agent_alive: bool,
) -> SupervisorAction:
    """PURE: decide a tick's action from the attach-state transition + agent liveness."""
    fire_detach_hook = classify_transition(prev_state, cur_state) is LifecycleEvent.DETACH
    kind = ActionKind.NONE if agent_alive else ActionKind.SELF_HEAL
    return SupervisorAction(kind=kind, fire_detach_hook=fire_detach_hook)


# ---------------------------------------------------------------------------
# PANEL-WATCH model (E2f) — the agent-independent `code` warm-up path.
# ---------------------------------------------------------------------------

class PanelAgentState(Enum):
    """Liveness of the PANEL-launched agent, from the per-PID markers dir (E2f)."""

    NONE = "none"
    ALIVE = "alive"
    DEAD = "dead"


class PanelActionKind(Enum):
    """What a PANEL-WATCH tick must DO (besides the detach hook)."""

    NONE = "none"
    SELF_HEAL_CLI = "self_heal_cli"
    TEARDOWN = "teardown"


@dataclass(frozen=True)
class PanelAction:
    """The decision a single panel-watch tick produces."""

    kind: PanelActionKind = PanelActionKind.NONE


def decide_panel(
    tmux_alive: bool,
    panel: PanelAgentState,
    vscode_server: bool,
    any_attached: bool,
    seen_surface: bool,
) -> PanelAction:
    """PURE: decide a panel-watch tick's action (E2f state machine, design 3a/3b)."""
    if tmux_alive or panel is PanelAgentState.ALIVE:
        return PanelAction(PanelActionKind.NONE)
    if panel is PanelAgentState.DEAD and vscode_server:
        return PanelAction(PanelActionKind.SELF_HEAL_CLI)
    if any_attached:
        return PanelAction(PanelActionKind.NONE)
    if seen_surface:
        return PanelAction(PanelActionKind.TEARDOWN)
    return PanelAction(PanelActionKind.NONE)


# ---------------------------------------------------------------------------
# DIRECTIVE FRESHNESS model — is the flattened instruction slot still true?
# ---------------------------------------------------------------------------

class DirectiveVerdict(Enum):
    """What one freshness check found."""

    FRESH = "fresh"              # the slot still matches its receipt — do nothing
    STALE = "stale"              # re-render and rewrite the slot
    HAND_EDITED = "hand_edited"  # inputs unchanged, slot changed — LEAVE IT ALONE


def decide_directives(
    manifest: object,
    seed: str,
    dest: str,
    probe: Mapping[str, str | None],
) -> DirectiveVerdict:
    """PURE: read a flatten receipt against the current world and decide.

    *manifest* is the parsed receipt (or ``None`` / anything unparseable). *probe*
    maps every path this verdict may consult — each of the receipt's inputs, plus
    *dest* — to the sha256 of its content NOW, or ``None`` when nothing can be read
    from it.  Hashes, never mtimes: the sources span an NFS home and read-only
    package binds, where mtime is not comparable and an upgrade can land new content
    under an old timestamp.

    Every way of not understanding the receipt returns ``STALE``.  That is the
    property which makes this safe inside PID 1: the worst outcome of any confusion
    is one unnecessary re-render, and there is no state in which the watcher can do
    something worse.

    The two verdicts that are NOT "stale" both exist to protect a user:

    * ``HAND_EDITED`` — the inputs are untouched but the generated file is not, so
      someone edited it by hand.  Rewriting it would be the seed-clobber class of
      data loss; refreshing it on the next real source change is not.
    * ``FRESH`` — nothing to do, which must stay the common case: this runs every
      few seconds for the life of the box.
    """
    if not isinstance(manifest, dict):
        return DirectiveVerdict.STALE
    if manifest.get("version") != DIRECTIVE_MANIFEST_VERSION:
        return DirectiveVerdict.STALE
    if manifest.get("seed") != seed or manifest.get("dest") != dest:
        return DirectiveVerdict.STALE   # it describes a different job than ours
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        return DirectiveVerdict.STALE
    for entry in inputs:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return DirectiveVerdict.STALE
        current = probe.get(entry["path"])
        if entry.get("absent"):
            # 🛑 THE MISS SIDE.  An import that pointed nowhere and now yields
            # content changes the render, and NOTHING on the hit side moved — a
            # watcher that only re-checks what it found would never fire.  The test
            # is "can it be read", not "does it exist", so a directory or a still
            # unreadable file does not re-trigger every tick forever.
            if current is not None:
                return DirectiveVerdict.STALE
        elif current != entry.get("sha256"):
            # Content changed, or the source can no longer be read (``None``).
            # Both change what a re-render produces.
            return DirectiveVerdict.STALE
    output = probe.get(dest)
    if output is None:
        return DirectiveVerdict.STALE   # the artifact is gone; DEST beats the receipt
    if output != manifest.get("output_sha256"):
        return DirectiveVerdict.HAND_EDITED
    return DirectiveVerdict.FRESH


# ---------------------------------------------------------------------------
# The supervisor.
# ---------------------------------------------------------------------------

class BoxSupervisor:
    """PID-1 keep-alive that supervises an agent in a detached tmux session."""

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        run: _Runner = subprocess.run,
        sleep: _Sleeper = time.sleep,
        proc_cmdlines: Iterable[str] | None = None,
        pid_alive: _PidAlive = _default_pid_alive,
        list_marker_pids: _MarkersLister = _default_list_marker_pids,
        cmdline_of: _CmdlineOf = _proc_cmdline,
        remove_marker: _MarkerRemover = _default_remove_marker,
        kill: _Signaller = os.kill,
        killpg: _Signaller = os.killpg,
        getpgid: _GroupOf = os.getpgid,
        getpgrp: _OwnGroup = os.getpgrp,
        reap: _Reaper = reap_zombie_children,
    ) -> None:
        self.config = config
        self._run = run
        self._sleep = sleep
        # A fixed process-cmdline listing for snapshot_attach_state (tests skip /proc);
        # ``None`` ⇒ each snapshot collects fresh from ``/proc`` (the real PID-1 path).
        self._proc_cmdlines = None if proc_cmdlines is None else list(proc_cmdlines)
        # Agent-marker probes, injectable; defaults are the real PID-1 implementations.
        self._pid_alive = pid_alive
        self._list_marker_pids = list_marker_pids
        self._cmdline_of = cmdline_of
        self._remove_marker = remove_marker
        # The agent launch HEADS this box runs, recovered from the launch grammars —
        # the only thing PID 1 knows about WHICH agent it supervises.  Config-derived
        # and immutable, so they are computed once; EMPTY disarms the identity half of
        # the marker scan entirely (see :meth:`_is_agent_pid`).
        self._agent_heads = agent_launch_heads(
            config.start_argv, config.continue_argv,
        )
        # Process-control primitives, injectable; NOTHING here may reach ``os`` directly,
        # or a unit test would signal a REAL process.  Defaults are the stdlib ops.
        self._kill = kill
        self._killpg = killpg
        self._getpgid = getpgid
        self._getpgrp = getpgrp
        self._reap = reap
        # 4a: newcomers already logged, so each is announced ONCE.  Re-pinned to the
        # current newcomer set each tick, so a departed-then-returning PID re-logs.
        self._reported_newcomers: set[int] = set()
        # Directive-freshness cadence + a ONE-SHOT latch for the hand-edit notice, so a
        # user's edited instruction file says so once instead of every few seconds.
        # Cleared whenever the slot is fresh again or gets re-rendered.
        self._ticks = 0
        self._directive_hand_edit_logged = False
        self._stop = False

    # -- tmux action helpers (impure; tolerant; injectable ``run``) ----------

    def _run_tmux(self, args: list[str]) -> int | None:
        """Run ``tmux <args>`` via the injected runner; return its rc, or ``None``."""
        try:
            proc = self._run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            log.debug("tmux %s failed to run: %s", args[0] if args else "", exc)
            return None
        return proc.returncode

    def _tmux_output(self, args: list[str]) -> str | None:
        """Run ``tmux <args>`` via the injected runner; return its STDOUT, or ``None``."""
        try:
            proc = self._run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            log.debug("tmux %s failed to run: %s", args[0] if args else "", exc)
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _start_session_argv(self, session_argv: list[str]) -> list[str]:
        """tmux argv that arms ``remain-on-exit``, starts the detached session, and SCOPES the arm."""
        return [
            "set-option", "-g", "remain-on-exit", "on", ";",
            "new-session", "-d", "-s", self.config.session, "--", *session_argv, ";",
            "set-option", "-t", self.config.session, "remain-on-exit", "on", ";",
            "set-option", "-g", "remain-on-exit", "off",
        ]

    def _arm_and_start_session(self, session_argv: list[str]) -> int | None:
        """Arm ``remain-on-exit`` (NOT under the teardown policy) + start the session; return the rc."""
        if self.config.on_agent_exit == "teardown":
            # No remain-on-exit: the pane closes on exit, no dead pane ever.
            return self._run_tmux(
                ["new-session", "-d", "-s", self.config.session, "--", *session_argv]
            )
        if ";" not in session_argv:
            return self._run_tmux(self._start_session_argv(session_argv))
        rc = self._run_tmux(
            ["new-session", "-d", "-s", self.config.session, "--", *session_argv]
        )
        if rc == 0:
            self._run_tmux(
                ["set-option", "-t", self.config.session, "remain-on-exit", "on"]
            )
        return rc

    def start_agent_session(self) -> bool:
        """Start the agent DETACHED via :meth:`_arm_and_start_session`; ``True`` on rc 0."""
        rc = self._arm_and_start_session(self.config.start_argv)
        if rc != 0:
            log.warning("start_agent_session: tmux new-session rc=%s", rc)
            return False
        return True

    def restart_agent_session(self) -> bool:
        """Restart the agent for self-heal, with the CONTINUE grammar + marker."""
        self.kill_agent_session()
        rc = self._arm_and_start_session(self.config.continue_argv)
        if rc != 0:
            log.warning("restart_agent_session: tmux new-session rc=%s", rc)
            return False
        if not self._send_marker():
            log.warning("restart_agent_session: continue-marker send-keys did not land")
        return True

    def _send_keys_text(self, text: str) -> bool:
        """Send *text* to the session as a real user turn via ``tmux send-keys`` (bounded retry)."""
        for attempt in range(1, self.config.send_keys_retries + 1):
            rc = self._run_tmux(
                ["send-keys", "-t", self.config.session, text, "Enter"]
            )
            if rc == 0:
                return True
            if attempt < self.config.send_keys_retries:
                self._sleep(self.config.send_keys_delay)
        return False

    def _send_marker(self) -> bool:
        """Send the continue-marker to the session (self-heal restart); see :meth:`_send_keys_text`."""
        return self._send_keys_text(self.config.marker)

    def _send_takeover_heads_up(self) -> bool:
        """Send the 4b takeover heads-up (:data:`TAKEOVER_HEADS_UP`) to the CLI incumbent."""
        return self._send_keys_text(TAKEOVER_HEADS_UP)

    def agent_pane_dead_status(self) -> int | None:
        """Return the agent pane's DEAD exit status, or ``None`` when it is not dead."""
        out = self._tmux_output(
            ["display-message", "-p", "-t", self.config.session, "#{pane_dead_status}"]
        )
        if out is None:
            return None
        out = out.strip()
        if not out:
            return None
        try:
            return int(out)
        except ValueError:
            log.debug("agent_pane_dead_status: unparseable pane_dead_status %r", out)
            return None

    def capture_agent_output(self) -> str | None:
        """Return the agent pane's captured MAIN-screen text (scrollback), or None."""
        out = self._tmux_output(
            [
                "capture-pane", "-p",
                "-S", f"-{self.config.capture_history}",
            # ⚑ ``-E -`` = capture through the END OF HISTORY.  Without it the end
            # defaults to the VISIBLE screen, which for a dead pane is tmux's "Pane is
            # dead" overlay — the agent's actual output would NOT be returned.
                "-E", "-",
                "-t", self.config.session,
            ]
        )
        if out is None:
            return None
        # Strip tmux's trailing blank padding; keep the body verbatim (the host greps it).
        return out.rstrip("\n") or None

    def agent_session_alive(self) -> bool:
        """True iff the agent session EXISTS and its pane is NOT dead."""
        if self._run_tmux(["has-session", "-t", self.config.session]) != 0:
            return False
        return self.agent_pane_dead_status() is None

    def _kill_process_group(self, pid: int, sig: int) -> bool:
        """Send *sig* to *pid*'s whole PROCESS GROUP (child-kill-with-parent); tolerant."""
        if pid <= 1:
            log.warning("kill process group: refusing unsafe pid %d (would risk PID-1)", pid)
            return False
        try:
            pgid = self._getpgid(pid)
        except OSError:
            pgid = pid
        try:
            own_pgrp = self._getpgrp()
        except OSError:
            own_pgrp = -1
        if pgid <= 1 or pgid == own_pgrp:
            log.warning(
                "kill process group: refusing unsafe target pgid %d for pid %d "
                "(0/1/supervisor group → would risk PID-1 / the box)",
                pgid, pid,
            )
            return False
        try:
            self._killpg(pgid, sig)
        except ProcessLookupError:
            return False
        except OSError as exc:
            log.debug("kill process group %d (pid %d) failed: %s", pgid, pid, exc)
            return False
        return True

    def kill_agent_session(self) -> None:
        """Evict the supervised agent: process-group kill each pane agent, then the session."""
        for pid in sorted(self._own_agent_pids()):
            self._kill_process_group(pid, signal.SIGTERM)
        rc = self._run_tmux(["kill-session", "-t", self.config.session])
        if rc not in (0, None):
            log.debug("kill_agent_session: tmux kill-session rc=%s", rc)

    # -- signal-handler install ----------------------------------------------

    def install_signal_handlers(self) -> None:
        """Install the SIGTERM handler (best-effort; a no-op off the main thread)."""
        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except (ValueError, OSError) as exc:
            log.debug("could not install SIGTERM handler: %s", exc)

    # -- snapshot ------------------------------------------------------------

    def _snapshot(self) -> AttachState:
        """Probe the current client-attach state (delegates to box_lifecycle)."""
        return snapshot_attach_state(
            self.config.session,
            run=self._run,
            proc_cmdlines=self._proc_cmdlines,
        )

    def _other_surface_attached(self, state: AttachState) -> bool:
        """True when a surface OTHER than the foreground CLI's own terminal is attached."""
        return state.vscode_server

    # -- agent markers: liveness (E2f) + newcomer detection (4a) -------------

    def _is_agent_pid(self, pid: int) -> bool | None:
        """Is the LIVE *pid* the agent SESSION?  ``None`` when it cannot be judged."""
        if not self._agent_heads:
            # No launch grammar to match against.  Judging nothing is right; judging
            # everything a non-agent would reap every marker in the dir.
            return None
        argv = self._cmdline_of(pid)
        if argv is None:
            # ``/proc`` unreadable, or the pid went away between the two probes.
            # Keep the marker — the NEXT scan sees a dead pid and reaps it then.
            return None
        return agent_session_verdict(argv, self._agent_heads)

    def _scan_markers(self) -> tuple[set[int], set[int]]:
        """Enumerate the markers dir → (LIVE pids, STALE pids), REAPING the stale."""
        markers_dir = self.config.agent_markers_dir
        if not markers_dir:
            return set(), set()
        try:
            return scan_marker_pids(
                markers_dir,
                list_pids=self._list_marker_pids,
                pid_alive=self._pid_alive,
                is_agent=self._is_agent_pid,
                remove=self._remove_marker,
            )
        except Exception:
            log.debug("_scan_markers: enumeration raised; treating as no markers")
            return set(), set()

    def _own_agent_pids(self) -> set[int]:
        """The PIDs of the agent(s) the supervisor itself launched in its tmux session."""
        out = self._tmux_output(
            ["list-panes", "-s", "-t", self.config.session, "-F", "#{pane_pid}"]
        )
        if not out:
            return set()
        pids: set[int] = set()
        for tok in out.split():
            try:
                pids.add(int(tok))
            except ValueError:
                continue
        return pids

    def _log_newcomers(self, live_pids: set[int], own_pids: set[int]) -> None:
        """LOG-ONLY (increment 4a): warn once per newcomer; take NO action."""
        newcomers = newcomer_pids(live_pids, own_pids)
        for pid in sorted(newcomers - self._reported_newcomers):
            log.warning(
                "newcomer agent PID %s detected on session "
                "(increment 4a: detection only, no action)",
                pid,
            )
        self._reported_newcomers = newcomers

    # -- 4b ENFORCEMENT: single-writer takeover (grace + pause + evict) -------

    def _signal_pid(self, pid: int, sig: int) -> bool:
        """A tolerant SINGLE-process signal (SIGSTOP/SIGCONT) via the injected ``kill``."""
        try:
            self._kill(pid, sig)
        except ProcessLookupError:
            log.debug("session takeover: pid %d gone before signal %s", pid, sig)
            return False
        except OSError as exc:
            log.debug("session takeover: signal %s to pid %d failed: %s", sig, pid, exc)
            return False
        return True

    def _resume(self, pids: list[int]) -> None:
        """``SIGCONT`` every PID in *pids* — the reversible undo of the newcomer PAUSE."""
        for pid in pids:
            self._signal_pid(pid, signal.SIGCONT)

    def _takeover(self, own_pids: set[int], newcomers: set[int]) -> bool:
        """4b ENFORCEMENT: pause the newcomer, grace the incumbent, evict it, resume (NEW-wins)."""
        log.warning(
            "session takeover (4b): panel newcomer(s) %s over live CLI incumbent(s) %s "
            "— pausing newcomer, gracing + evicting incumbent (NEW-wins)",
            sorted(newcomers), sorted(own_pids),
        )
        paused: list[int] = []
        try:
            # 1. PAUSE the newcomer(s) BEFORE they can write (reversible).
            for pid in sorted(newcomers):
                if self._signal_pid(pid, signal.SIGSTOP):
                    paused.append(pid)
            if not paused:
                # Every newcomer vanished before it could be paused → nothing to hand
                # the session to; do NOT evict the legitimate incumbent.
                log.warning(
                    "session takeover: no newcomer could be paused; aborting (no eviction)"
                )
                return False
            # 2. HEADS-UP to the incumbent (best-effort) + 3. GRACE (reversible).
            self._send_takeover_heads_up()
            self._sleep(self.config.takeover_grace)
        except Exception:
            log.exception(
                "session takeover: a pre-evict step failed; "
                "resuming newcomer(s), NOT evicting the incumbent"
            )
            self._resume(paused)
            return False
        # 4. EVICT (the single DESTRUCTIVE op): process-group kill the pane incumbent.
        log.warning(
            "session takeover: grace elapsed; evicting incumbent process group(s) %s",
            sorted(own_pids),
        )
        try:
            self.kill_agent_session()
        finally:
            # 5. RESUME the newcomer — single-writer is now guaranteed (incumbent gone).
            self._resume(paused)
        return True

    def panel_agent_state(self) -> PanelAgentState:
        """Liveness of the PANEL-launched agent from the per-PID markers dir (E2f)."""
        if not self.config.agent_markers_dir:
            return PanelAgentState.NONE
        own = self._own_agent_pids()
        live, stale = self._scan_markers()
        if live - own:
            return PanelAgentState.ALIVE
        if stale - own:
            return PanelAgentState.DEAD
        return PanelAgentState.NONE

    # -- self-heal -----------------------------------------------------------

    def _self_heal(self) -> bool:
        """Restart a dead agent with bounded retry + exponential backoff."""
        for attempt in range(1, self.config.max_restart_retries + 1):
            log.info("self-heal: restart attempt %d/%d", attempt, self.config.max_restart_retries)
            self.restart_agent_session()
            if self.agent_session_alive():
                log.info("self-heal: agent session live after attempt %d", attempt)
                return True
            if attempt < self.config.max_restart_retries:
                self._sleep(self.config.backoff_base * (2 ** (attempt - 1)))
        log.error(
            "self-heal: exhausted %d restart attempts; giving up",
            self.config.max_restart_retries,
        )
        return False

    # -- detach hook (best-effort POINT filled by increment D) ---------------

    def _on_detach(self) -> None:
        """Best-effort HOOK fired on a DETACH tick — write the creds-dirty SIGNAL flag."""
        path = self.config.creds_flag
        if not path:
            log.debug("on-detach: no creds-flag configured; nothing to signal")
            return
        try:
            flag = Path(path)
            flag.parent.mkdir(parents=True, exist_ok=True)
            # A tiny edge-trigger MARKER — the host watcher only checks EXISTENCE, so
            # the contents are immaterial; a single byte keeps it a non-empty file.
            flag.write_text("1")
            log.debug("on-detach: wrote creds-dirty flag %s", path)
        except OSError as exc:
            log.debug("on-detach: could not write creds-dirty flag %r: %s", path, exc)

    def _safe_on_detach(self) -> None:
        """Call :meth:`_on_detach`, swallowing ANY exception (the loop must not die)."""
        try:
            self._on_detach()
        except Exception:
            log.exception("on-detach hook raised; ignored (supervisor loop continues)")

    # -- directive freshness (the flattened instruction slot) ----------------

    @staticmethod
    def _sha256_of(path: str) -> str | None:
        """sha256 of *path*'s content, or ``None`` if nothing can be read from it."""
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()

    def _read_directive_manifest(self, path: str) -> object:
        """Parse the flatten receipt at *path*; ``None`` if it cannot be read as JSON."""
        try:
            with open(path, "rb") as fh:
                return json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            log.debug("directive manifest %s unusable (%s); treating as stale", path, exc)
            return None

    def _probe_directives(self, manifest: object, dest: str) -> dict[str, str | None]:
        """Hash every path :func:`decide_directives` may consult for this receipt."""
        paths = [dest]
        if isinstance(manifest, dict) and isinstance(manifest.get("inputs"), list):
            for entry in manifest["inputs"]:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    paths.append(entry["path"])
        return {path: self._sha256_of(path) for path in paths}

    def _reflatten_directives(self, watch: DirectiveWatch) -> bool:
        """Re-run the flattener as a SUBPROCESS; ``True`` iff it reported success.

        The flattener owns the write (atomically, receipt included) — the supervisor
        never renders or writes the slot itself, so there is exactly one producer of
        both the artifact and its receipt.  A failure here is logged and dropped: an
        instruction file left stale is bad, PID 1 dying is fatal.

        ⚑ BOUNDED.  This is a synchronous call inside PID 1's tick, and the directive
        sources sit on an NFS home — a stalled mount would otherwise wedge the reap,
        the detach hook and self-heal behind a read.  The bound is enormous next to a
        real flatten (tens of milliseconds); it exists to cap a HANG, not to time work.
        """
        argv = [
            sys.executable or "python3", watch.flattener, watch.seed, watch.dest,
            "--manifest", watch.manifest,
        ]
        try:
            proc = self._run(
                argv, capture_output=True, text=True, check=False,
                timeout=FLATTEN_TIMEOUT,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.warning("directive re-flatten could not run %r: %s", watch.flattener, exc)
            return False
        stderr = (getattr(proc, "stderr", "") or "").strip()
        if proc.returncode != 0:
            log.warning(
                "directive re-flatten failed (rc=%s): %s", proc.returncode, stderr,
            )
            return False
        if stderr:
            # The flattener warns on stderr about unresolved imports and unterminated
            # comments; that is diagnostic, not failure.
            log.debug("directive re-flatten warnings: %s", stderr)
        log.info("directive sources changed; re-flattened %s", watch.dest)
        return True

    def check_directives(self) -> DirectiveVerdict | None:
        """One freshness check of the flattened instruction slot; ``None`` when unwatched."""
        watch = self.config.directives
        if watch is None:
            return None
        manifest = self._read_directive_manifest(watch.manifest)
        verdict = decide_directives(
            manifest, watch.seed, watch.dest, self._probe_directives(manifest, watch.dest),
        )
        if verdict is DirectiveVerdict.HAND_EDITED:
            if not self._directive_hand_edit_logged:
                log.warning(
                    "%s differs from the flatten that produced it while its sources are "
                    "unchanged — leaving the hand-edited file alone (it will refresh on "
                    "the next directive-source change)",
                    watch.dest,
                )
                self._directive_hand_edit_logged = True
            return verdict
        self._directive_hand_edit_logged = False
        if verdict is DirectiveVerdict.STALE:
            self._reflatten_directives(watch)
        return verdict

    def _safe_check_directives(self) -> None:
        """Call :meth:`check_directives`, swallowing ANY exception (the loop must not die)."""
        try:
            self.check_directives()
        except Exception:
            log.exception("directive freshness check raised; ignored (loop continues)")

    def _directive_tick_period(self) -> int:
        """Ticks between freshness checks — the cadence is in SECONDS, converted here."""
        watch = self.config.directives
        if watch is None:
            return 1
        poll = self.config.poll_interval if self.config.poll_interval > 0 else 1.0
        return max(1, round(watch.interval / poll))

    def _directive_tick(self) -> None:
        """Advance the tick counter and run a freshness check on the Nth one."""
        self._ticks += 1
        if self.config.directives is None:
            return
        if self._ticks % self._directive_tick_period() == 0:
            self._safe_check_directives()

    # -- the SIGTERM handler -------------------------------------------------

    def _handle_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """SIGTERM handler → :meth:`teardown` (factored out so tests call it directly)."""
        log.info("received signal %s", signum)
        self.teardown()

    # -- the watch loop ------------------------------------------------------

    def run_forever(self) -> int:
        """Run the supervise loop until teardown, agent-exit, or self-heal exhaustion."""
        self.install_signal_handlers()
        if self.config.panel_watch:
            # ⚑ E2f runs a DISTINCT loop that starts no CLI agent; the E2b-E2e path below
            # is untouched (only reached when NOT panel_watch).
            return self._run_panel_watch()
        teardown_on_exit = self.config.on_agent_exit == "teardown"
        # One-shot guard so the agentless keep-alive state logs ONCE on entry, not per tick.
        keepalive_announced = False
        # Startup runs BEFORE the per-tick guard, so guard it too — a raising probe must
        # not kill PID-1 before the loop begins; degrade to "no attach" and enter it.
        # ⚑ ``started`` = did the agent ever COME UP?  It separates a NEVER-STARTED
        # failure (rc 1) from a ran-then-exited quit (rc 0) in the teardown branch.
        # Defaults True so a startup hiccup prefers 0 — never fabricate a failure.
        started = True
        try:
            if not self.agent_session_alive():
                log.info("no live agent session at startup; starting one detached")
                started = self.start_agent_session()
                if not started:
                    # ⚑ NO special-case return: a failed INITIAL start is handled
                    # UNIFORMLY by the loop's first tick, which keeps the teardown
                    # decision in exactly ONE place (design §86-88).
                    log.warning("initial agent start failed; deferring to the loop policy")
            prev = self._snapshot()
        except Exception:
            log.exception("supervisor startup probe raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                # ⚑ PID-1 duty FIRST: reap orphan-reparented children so a dead panel
                # cannot sit as a zombie and read ALIVE to the marker probes below.
                self._reap()
                # Keep the flattened instruction slot TRUE while the box lives (every
                # Nth tick; inert unless the launcher threaded a watch).  Independent
                # of the agent — a source edit must land whether or not one is running.
                self._directive_tick()
                cur = self._snapshot()
                alive = self.agent_session_alive()
                # Newcomer detection, gated on a configured markers dir so an old launcher
                # (or a test) that threads none is byte-unchanged.
                if self.config.agent_markers_dir:
                    live_markers, _stale = self._scan_markers()
                    own = self._own_agent_pids()
                    if self.config.session_takeover:
                        # ⚑ 4b: act ONLY when there IS a pane incumbent to evict (own
                        # non-empty) — never evict on a bare marker PID.  On success
                        # hand off to the agentless panel-watch keep-alive.
                        newcomers = newcomer_pids(live_markers, own)
                        if newcomers and own and self._takeover(own, newcomers):
                            return self._run_panel_watch()
                    else:
                        # Flag OFF (default): 4a LOG-ONLY — no signals, no kills.
                        self._log_newcomers(live_markers, own)
                action = decide(prev, cur, alive)
                if action.fire_detach_hook:
                    self._safe_on_detach()
                if action.kind is ActionKind.SELF_HEAL:
                    if teardown_on_exit:
                        # ⚑ SURFACE-AWARE teardown: the agent is dead, but a box must
                        # PERSIST while any OTHER surface (a panel) is still attached.
                        if self._other_surface_attached(cur):
                            # Stay an AGENTLESS keep-alive: do NOT tear down, and do NOT
                            # self-heal a CLI while the panel is live (one-agent
                            # invariant).  A LATER tick tears down once it detaches.
                            if not keepalive_announced:
                                log.info(
                                    "agent exited but another client surface (panel) "
                                    "is attached; staying up as an agentless keep-alive"
                                )
                                keepalive_announced = True
                        else:
                            # ⚑ Truthful-as-possible code: honor a present dead-pane
                            # status; else 0 if the agent CAME UP and exited (a clean
                            # quit must not read as failure — a sole-agent CRASH also
                            # lands here as 0 until pipe-pane restores crash codes);
                            # else 1, it never came up.
                            status = self.agent_pane_dead_status()
                            if status is not None:
                                code = status
                            else:
                                code = 0 if started else 1
                            # Echo the captured pane so ``podman logs`` shows WHY the
                            # agent died; under teardown the pane has closed, so this
                            # normally captures nothing (best-effort).
                            captured = self.capture_agent_output()
                            if captured:
                                print(captured, flush=True)
                            log.info(
                                "agent exited under teardown policy (dead_status=%s); "
                                "no other surface attached — closing box with rc %d",
                                status,
                                code,
                            )
                            return code
                    elif not self._self_heal():
                        return 0
                prev = cur
            except Exception:
                log.exception("supervisor tick failed; continuing")
            self._sleep(self.config.poll_interval)
        return 0

    def _run_panel_watch(self) -> int:
        """The PANEL-WATCH loop (E2f): agent-independent ``code`` warm-up."""
        log.info("panel-watch mode: agentless keep-alive fronting the VS Code panel")
        # ``seen_surface`` LATCHES True once any surface has EVER attached, so an
        # attached-then-detached box tears down while a never-attached one stays up
        # through the startup grace.  The pre-loop snapshot is guarded like above.
        seen_surface = False
        # 4a: the fronted panel incumbent PID (first live non-own marker).  Latched so
        # the fronted panel is not itself flagged; cleared when it departs the live set.
        panel_incumbent: int | None = None
        try:
            prev = self._snapshot()
            if prev.any_attached:
                seen_surface = True
        except Exception:
            log.exception("panel-watch startup snapshot raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                # ⚑ PID-1 duty FIRST: a dead panel agent must not sit as a zombie and hold
                # panel_agent_state at ALIVE forever, wedging SELF_HEAL_CLI and TEARDOWN.
                self._reap()
                # A warm-only box has directives and a panel agent reading them, so the
                # freshness watch belongs in BOTH loops — the slot must not go stale
                # just because this box was fronted by the panel instead of a CLI.
                self._directive_tick()
                cur = self._snapshot()
                if cur.any_attached:
                    seen_surface = True
                if classify_transition(prev, cur) is LifecycleEvent.DETACH:
                    self._safe_on_detach()
                tmux_alive = self.agent_session_alive()
                panel = self.panel_agent_state()
                # 4a LOG-ONLY: own = the self-healed CLI's pane(s) PLUS the latched fronted
                # panel incumbent; a live marker outside that set is a newcomer.
                if self.config.agent_markers_dir:
                    own = self._own_agent_pids()
                    live_markers, _stale = self._scan_markers()
                    non_own_live = live_markers - own
                    if panel_incumbent is not None and panel_incumbent not in non_own_live:
                        panel_incumbent = None
                    if panel_incumbent is None and non_own_live:
                        panel_incumbent = min(non_own_live)
                    incumbent = (
                        {panel_incumbent} if panel_incumbent is not None else set()
                    )
                    self._log_newcomers(live_markers, own | incumbent)
                action = decide_panel(
                    tmux_alive, panel, cur.vscode_server, cur.any_attached, seen_surface,
                )
                if action.kind is PanelActionKind.SELF_HEAL_CLI:
                    log.info(
                        "panel agent died with the panel still connected; "
                        "self-healing a CLI agent in tmux (the §89-96 fallback)"
                    )
                    if not self._self_heal():
                        return 0
                elif action.kind is PanelActionKind.TEARDOWN:
                    log.info(
                        "all client surfaces and agents gone; "
                        "tearing down the warmed panel-watch box"
                    )
                    return 0
                prev = cur
            except Exception:
                log.exception("panel-watch tick failed; continuing")
            self._sleep(self.config.poll_interval)
        return 0

    # -- teardown ------------------------------------------------------------

    def teardown(self) -> None:
        """Total teardown: signal the loop to exit and kill the agent session."""
        log.info("teardown requested; killing agent session and exiting loop")
        self._stop = True
        self.kill_agent_session()


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the ``python3 -m kanibako.box_supervisor`` argument parser (options only)."""
    parser = argparse.ArgumentParser(
        prog="python3 -m kanibako.box_supervisor",
        description="Box always-on-agent supervisor (PID-1 keep-alive).",
    )
    parser.add_argument("--session", required=True, help="tmux session name for the agent")
    parser.add_argument(
        "--marker", required=True, help="continue-marker sent to a self-healed agent"
    )
    parser.add_argument(
        "--poll", type=float, default=2.0, help="seconds between watch-loop ticks"
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="bounded self-heal restart attempts"
    )
    parser.add_argument(
        "--continue-cmd",
        default=None,
        help="agent grammar (shlex) for a self-heal restart; defaults to the start argv",
    )
    parser.add_argument(
        "--on-agent-exit",
        choices=("self-heal", "teardown"),
        default="self-heal",
        help=(
            "policy when the agent exits: 'self-heal' (default; detached — bounded-retry "
            "restart) or 'teardown' (foreground CLI — close the box on agent exit)"
        ),
    )
    parser.add_argument(
        "--session-takeover",
        action="store_true",
        help=(
            "4b ENFORCEMENT (default OFF): enable single-writer TAKEOVER — a panel "
            "newcomer over a live CLI incumbent is paused, the incumbent graced + "
            "process-group evicted, then the newcomer resumed.  Omit for 4a LOG-ONLY "
            "(no signals/kills).  Threaded from the experimental KANIBAKO_SESSION_TAKEOVER"
        ),
    )
    parser.add_argument(
        "--takeover-grace",
        type=float,
        default=5.0,
        help="seconds to grace the CLI incumbent before the 4b process-group evict",
    )
    parser.add_argument(
        "--panel-watch",
        action="store_true",
        help=(
            "PANEL-WATCH mode (E2f): start NO CLI agent; enumerate the per-agent markers "
            "dir (--agent-markers-dir) + the VS Code server surface and self-heal a CLI "
            "agent only when the panel agent dies with the panel still connected"
        ),
    )
    parser.add_argument(
        "--agent-markers-dir",
        default=None,
        help=(
            "box-local per-agent liveness MARKERS dir (each agent writes <dir>/$PPID): "
            "enumerated for panel-agent liveness (--panel-watch) and LOG-ONLY newcomer "
            "detection (increment 4a) in both modes"
        ),
    )
    parser.add_argument(
        "--creds-flag",
        default=None,
        help=(
            "box-local path to the credential-writeback SIGNAL flag (increment D): "
            "written on EVERY detach so a trusted HOST watcher does the privileged "
            "store writeback; omit to signal nothing"
        ),
    )
    # DIRECTIVE FRESHNESS — all four or none (see :class:`DirectiveWatch`).  Every one
    # is a path the LAUNCHER decided; the supervisor spells none of them, which is what
    # keeps the flattener literal from gaining a third carrier.
    parser.add_argument(
        "--directive-seed",
        default=None,
        help="flattener SOURCE — the directive-chain entry slot ($KANIBAKO_DIRECTIVE_SEED)",
    )
    parser.add_argument(
        "--directive-dest",
        default=None,
        help="agent's native instruction slot to keep fresh ($KANIBAKO_DIRECTIVE_FINAL)",
    )
    parser.add_argument(
        "--directive-manifest",
        default=None,
        help=(
            "box-local path of the flatten RECEIPT: the watcher re-hashes what it names "
            "and re-flattens when any of it moves; the flattener rewrites it"
        ),
    )
    parser.add_argument(
        "--directive-flattener",
        default=None,
        help="in-box path of the flattener script to re-run (import-directives.py)",
    )
    return parser


def _directive_watch(ns: argparse.Namespace) -> DirectiveWatch | None:
    """Assemble the four directive-freshness paths, or ``None``.

    A PARTIAL set is a launcher bug, and the only safe response inside PID 1 is to
    say so and stay inert: erroring out would refuse to start a box over a watch that
    is not the box's purpose, and guessing a missing path would put a path decision
    back in the supervisor.
    """
    parts = {
        "--directive-seed": ns.directive_seed,
        "--directive-dest": ns.directive_dest,
        "--directive-manifest": ns.directive_manifest,
        "--directive-flattener": ns.directive_flattener,
    }
    if all(parts.values()):
        return DirectiveWatch(
            seed=ns.directive_seed,
            dest=ns.directive_dest,
            manifest=ns.directive_manifest,
            flattener=ns.directive_flattener,
        )
    if any(parts.values()):
        log.warning(
            "directive freshness NOT armed: %s given without %s",
            sorted(k for k, v in parts.items() if v),
            sorted(k for k, v in parts.items() if not v),
        )
    return None


def config_from_argv(argv: list[str]) -> SupervisorConfig:
    """Parse *argv* (without the program name) into a :class:`SupervisorConfig`."""
    parser = _build_parser()
    if "--" in argv:
        idx = argv.index("--")
        opt_args, start_argv = argv[:idx], argv[idx + 1 :]
    else:
        opt_args, start_argv = list(argv), []
    ns = parser.parse_args(opt_args)
    if not start_argv and not ns.panel_watch:
        parser.error("no agent argv given after '--'")
    continue_argv = shlex.split(ns.continue_cmd) if ns.continue_cmd else list(start_argv)
    return SupervisorConfig(
        directives=_directive_watch(ns),
        session=ns.session,
        start_argv=list(start_argv),
        continue_argv=continue_argv,
        marker=ns.marker,
        poll_interval=ns.poll,
        max_restart_retries=ns.max_retries,
        on_agent_exit=ns.on_agent_exit,
        session_takeover=ns.session_takeover,
        takeover_grace=ns.takeover_grace,
        panel_watch=ns.panel_watch,
        agent_markers_dir=ns.agent_markers_dir,
        creds_flag=ns.creds_flag,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: parse args, build the supervisor, run the watch loop forever."""
    args = list(sys.argv[1:] if argv is None else argv)
    config = config_from_argv(args)
    # ⚑ Serve the box's XDG locations from the pinned root now that the box is LIVE.
    # This is the earliest SAFE point and the only correct one: every podman mount is
    # already in place, so the link is invisible to mount-time symlink resolution.
    project_pinned_xdg()
    # Strip the host-package mount from PYTHONPATH before the loop spawns tmux / the
    # agent, so those children do not inherit our import path.
    scrub_bootstrap_pythonpath()
    supervisor = BoxSupervisor(config)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
