"""Box CLIENT-ATTACHMENT detection — the shared PID-1 lifecycle primitive.

The always-on-instance design (`split-brain-persistence-DESIGN.md`, "Mechanism —
PID-1 lifecycle watcher") has the box's keep-alive PID-1 WATCH which clients are
attached — the in-box VS Code server (a panel/attach) and a tmux TERMINAL client
(an interactive shell) — so it can emit ATTACH / DETACH transitions.  Those
transitions drive two later increments: the always-on agent self-heals on DETACH
(E2), and the credential writeback fires on DETACH (D / GAP-1).

This module is INCREMENT 1: the PURE detection + transition logic plus a thin,
INJECTABLE system-probe layer.  It makes NO launch-model changes and runs no
supervisor loop — E2 imports :func:`snapshot_attach_state` /
:func:`canonical_tmux_session_pid` and drives the loop; D subscribes to the
:data:`LifecycleEvent.DETACH` its :func:`classify_transition` emits.

Everything is deterministic and side-effect free EXCEPT the two probe functions
at the bottom, whose only impurity (reading ``/proc`` cmdlines and shelling
``tmux``) is funnelled through injectable parameters so tests never touch real
processes or a real tmux server.  Every probe is tolerant: an absent tmux, an
empty/garbled output, or a malformed ``/proc`` entry resolves to a safe falsy /
``None`` — never an exception.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from glob import glob
from pathlib import Path

# ---------------------------------------------------------------------------
# VS Code remote-server directory markers (canonical — shared with code_cmd).
# ---------------------------------------------------------------------------

# The remote-server tree DIR names a running in-box VS Code (or Cursor) server
# materialises under ``$HOME``.  ``.vscode-server`` covers Stable; the ``-*``
# suffixes cover the Insiders/OSS channels; ``.cursor-server`` is Cursor's fork.
# ⚑ This tuple is ILLUSTRATIVE — the enumerated KNOWN channels for readers, NOT
# the matching rule.  The authoritative test is :func:`is_vscode_server_path_part`
# (a ``.vscode-server`` PREFIX, deliberately broader so a future ``.vscode-server-*``
# channel still matches); editing this tuple does NOT change matching.  Keep them
# in loose sync for documentation, but the function's prefix rule is what runs.
# :mod:`kanibako.commands.code_cmd` imports that function rather than re-spelling it.
VSCODE_SERVER_DIR_MARKERS: tuple[str, ...] = (
    ".vscode-server",
    ".vscode-server-insiders",
    ".vscode-server-oss",
    ".cursor-server",
)


def is_vscode_server_path_part(part: str) -> bool:
    """True when a single path SEGMENT names a VS Code remote-server dir.

    Captures the exact rule ``code_cmd`` used inline: any ``.vscode-server*``
    variant (Stable / Insiders / OSS) OR the ``.cursor-server`` fork.  Operates
    on ONE path component (e.g. from :attr:`pathlib.Path.parts`), so a caller
    tests each segment of a resolved/cmdline path independently.
    """
    return part.startswith(".vscode-server") or part == ".cursor-server"


# ---------------------------------------------------------------------------
# Pure surface detectors.
# ---------------------------------------------------------------------------

def vscode_server_present(proc_cmdlines: Iterable[str]) -> bool:
    """True iff any process command line indicates a running in-box VS Code server.

    PURE: operates over ALREADY-collected process command lines (the impure
    collection lives in :func:`_collect_proc_cmdlines`).  A cmdline counts as a
    server when any ``/``-delimited SEGMENT of it names a remote-server dir (see
    :func:`is_vscode_server_path_part`) — so
    ``/home/agent/.vscode-server/bin/<hash>/node`` matches on its
    ``.vscode-server`` segment while ``/usr/bin/node`` or
    ``/home/agent/.local/bin/claude`` do not.  An empty iterable → ``False``.
    """
    for cmdline in proc_cmdlines:
        if any(is_vscode_server_path_part(part) for part in cmdline.split("/")):
            return True
    return False


def tmux_terminal_attached(list_clients_output: str) -> bool:
    """True iff ``tmux list-clients`` output shows ≥1 attached terminal client.

    PURE.  The contract is the output of ``tmux list-clients -t <session>``:
    tmux prints ONE LINE per attached client and NOTHING when no client is
    attached.  So a non-empty, non-whitespace output ⇒ at least one terminal is
    attached; ``""`` (the caller's normalisation of an empty listing, a non-zero
    exit, or an absent tmux) or all-whitespace ⇒ not attached.  The caller
    (:func:`snapshot_attach_state`) is responsible for passing ``""`` on any
    tmux error so this stays a pure string test.
    """
    return bool(list_clients_output.strip())


# ---------------------------------------------------------------------------
# Attach-state model + transition classifier.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttachState:
    """Immutable snapshot of which client surfaces are attached to the box.

    Two surfaces today — the in-box VS Code server (:attr:`vscode_server`) and a
    tmux TERMINAL client (:attr:`tmux_terminal`) — kept as named bools so a new
    surface is a single added field (and :func:`classify_transition` keeps
    working via :attr:`_surfaces`).  Frozen so a prev/cur pair is safe to hold
    across a watcher tick.
    """

    vscode_server: bool = False
    tmux_terminal: bool = False

    @property
    def any_attached(self) -> bool:
        """True when ANY client surface is currently attached."""
        return self.vscode_server or self.tmux_terminal

    @property
    def _surfaces(self) -> tuple[bool, ...]:
        """The per-surface flags, positionally stable across states.

        :func:`classify_transition` compares two states surface-by-surface via
        this tuple, so extending the model with a new surface field (added to
        this tuple) needs no change to the classifier.
        """
        return (self.vscode_server, self.tmux_terminal)


class LifecycleEvent(Enum):
    """The transition a watcher tick produces (consumed by E2 self-heal / D)."""

    ATTACH = "attach"
    DETACH = "detach"
    NONE = "none"


def classify_transition(prev: AttachState, cur: AttachState) -> LifecycleEvent:
    """Classify the attach-state transition ``prev`` → ``cur``.

    PURE.  Rule (DETACH-biased for mixed transitions):

    * :data:`LifecycleEvent.DETACH` — ANY surface that was present in ``prev`` is
      gone in ``cur``.  This wins even when a DIFFERENT surface simultaneously
      appeared (a mixed tick: e.g. a terminal detaches while a panel attaches).
    * :data:`LifecycleEvent.ATTACH` — no surface was lost AND at least one new
      surface appeared.
    * :data:`LifecycleEvent.NONE` — no surface changed (includes the idempotent
      same-state tick).

    The "any surface LOST ⇒ DETACH" bias is deliberate and safety-critical: D
    fires the credential writeback on DETACH, so MISSING a detach (dropping a
    just-refreshed token) is strictly worse than an EXTRA detach (a redundant,
    idempotent writeback).  When a tick both loses and gains a surface we
    therefore report the loss.  A watcher polling fast enough that a real
    detach+attach collapses into one tick is the case this protects.
    """
    lost = any(p and not c for p, c in zip(prev._surfaces, cur._surfaces))
    if lost:
        return LifecycleEvent.DETACH
    gained = any(c and not p for p, c in zip(prev._surfaces, cur._surfaces))
    if gained:
        return LifecycleEvent.ATTACH
    return LifecycleEvent.NONE


# ---------------------------------------------------------------------------
# Thin INJECTABLE system-probe layer (the ONLY side-effecting code).
# ---------------------------------------------------------------------------

# The subprocess-runner signature the probes call.  ``subprocess.run`` matches
# it; tests inject a fake to avoid touching a real tmux.
_Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _collect_proc_cmdlines() -> list[str]:
    """Collect running-process command lines from ``/proc/*/cmdline``.

    The impure companion to :func:`vscode_server_present`: reads each
    ``/proc/<pid>/cmdline`` (NUL-delimited argv), joins the argv with spaces,
    and returns the non-empty lines.  Tolerant — a vanished PID, a permission
    error, or a kernel-thread's empty cmdline is SKIPPED, never raised — so it
    is safe to call from PID-1 on any box.  Returns ``[]`` on a system with no
    readable ``/proc``.
    """
    cmdlines: list[str] = []
    for entry in glob("/proc/[0-9]*/cmdline"):
        try:
            raw = Path(entry).read_bytes()
        except OSError:
            # PID vanished between glob and read, or unreadable — skip it.
            continue
        # cmdline is NUL-delimited argv (trailing NUL); decode leniently.
        text = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if text:
            cmdlines.append(text)
    return cmdlines


def _tmux_clients_output(session: str, run: _Runner) -> str:
    """Return ``tmux list-clients -t <session>`` stdout, or ``""`` on any failure.

    Normalises EVERY not-attached / no-tmux condition to the empty string that
    :func:`tmux_terminal_attached` reads as "no terminal": a non-zero exit (no
    such session, no server) → ``""``; a missing tmux binary
    (``FileNotFoundError``) → ``""``; any other ``OSError`` → ``""``.  ``-F ""``
    is NOT used — the default one-line-per-client output is exactly the presence
    signal we test.
    """
    try:
        proc = run(
            ["tmux", "list-clients", "-t", session],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def snapshot_attach_state(
    session: str,
    *,
    run: _Runner = subprocess.run,
    proc_cmdlines: Iterable[str] | None = None,
) -> AttachState:
    """Probe the CURRENT client-attachment state of the box (the impure snapshot).

    Composes the two pure detectors over freshly probed system state:

    * VS Code server presence — from process command lines; by default collected
      via :func:`_collect_proc_cmdlines`, but a caller (or a test) may inject an
      explicit *proc_cmdlines* iterable to skip the ``/proc`` read entirely.
    * tmux terminal attachment — from ``tmux list-clients -t <session>`` run via
      the injectable *run*, tolerating an absent tmux / dead session as "not
      attached".

    All probing is confined here and to the two ``_*`` helpers.  Returns an
    :class:`AttachState`; never raises.
    """
    cmdlines = (
        _collect_proc_cmdlines() if proc_cmdlines is None else list(proc_cmdlines)
    )
    return AttachState(
        vscode_server=vscode_server_present(cmdlines),
        tmux_terminal=tmux_terminal_attached(_tmux_clients_output(session, run)),
    )


def canonical_tmux_session_pid(
    session: str,
    *,
    run: _Runner = subprocess.run,
) -> int | None:
    """Best-effort PID of the tmux server hosting *session* (the "live marker").

    Runs ``tmux display-message -p -t <session> '#{pid}'`` — ``#{pid}`` is the
    tmux SERVER pid — and parses the first integer it prints.  Returns that pid,
    or ``None`` when the session/server is absent, tmux is not installed, the
    command fails, or the output is unparseable.  Tolerant of a missing tmux;
    never raises.  E2 uses this to identify the canonical always-on instance.
    """
    try:
        proc = run(
            ["tmux", "display-message", "-p", "-t", session, "#{pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    try:
        return int(text)
    except ValueError:
        return None
