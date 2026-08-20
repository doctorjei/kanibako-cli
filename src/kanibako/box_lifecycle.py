"""Box CLIENT-ATTACHMENT detection — the shared PID-1 lifecycle primitive.

⚑ Half of the PID-1 pair with :mod:`kanibako.box_supervisor`: PINNED FLAT,
STDLIB-ONLY, invoked in-box by a dotted literal.  Do not package or move it, and
do not add a non-stdlib import.

⚑ Every probe here is TOLERANT — an absent tmux, empty/garbled output or a
malformed ``/proc`` entry resolves to a safe falsy / ``None``, never an
exception.  This module runs as PID-1; a raise takes the box down.

Reasoning, the increment map and the design refs: ``llm-docs/kanibako/box_lifecycle.py.md``.
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

# ⚑ ILLUSTRATIVE — the known channels, for readers.  NOT the matching rule:
# :func:`is_vscode_server_path_part` is authoritative and deliberately broader,
# so editing this tuple does NOT change matching.
VSCODE_SERVER_DIR_MARKERS: tuple[str, ...] = (
    ".vscode-server",
    ".vscode-server-insiders",
    ".vscode-server-oss",
    ".cursor-server",
)


def is_vscode_server_path_part(part: str) -> bool:
    """True when a single path SEGMENT names a VS Code remote-server dir.

    Takes ONE path component (e.g. from :attr:`pathlib.Path.parts`), so a
    caller tests each segment of a resolved/cmdline path independently.
    """
    return part.startswith(".vscode-server") or part == ".cursor-server"


# ---------------------------------------------------------------------------
# Pure surface detectors.
# ---------------------------------------------------------------------------

def vscode_server_present(proc_cmdlines: Iterable[str]) -> bool:
    """True iff any process command line indicates a running in-box VS Code server.

    PURE, over ALREADY-collected cmdlines: a cmdline counts when any
    ``/``-delimited SEGMENT of it names a remote-server dir — so
    ``/home/agent/.vscode-server/bin/<hash>/node`` matches while
    ``/usr/bin/node`` does not.  An empty iterable → ``False``.
    """
    for cmdline in proc_cmdlines:
        if any(is_vscode_server_path_part(part) for part in cmdline.split("/")):
            return True
    return False


def tmux_terminal_attached(list_clients_output: str) -> bool:
    """True iff ``tmux list-clients`` output shows ≥1 attached terminal client.

    PURE: tmux prints one LINE per attached client and nothing otherwise.
    ⚑ The CALLER must normalise any tmux error to ``""`` to keep this a pure
    string test — :func:`_tmux_clients_output` is what does that.
    """
    return bool(list_clients_output.strip())


# ---------------------------------------------------------------------------
# Attach-state model + transition classifier.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttachState:
    """Immutable snapshot of which client surfaces are attached to the box.

    Two surfaces today, kept as named bools so a new one is a single added
    field.  Frozen so a prev/cur pair is safe to hold across a watcher tick.
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

        A new surface added to this tuple needs no classifier change.
        """
        return (self.vscode_server, self.tmux_terminal)


class LifecycleEvent(Enum):
    """The transition a watcher tick produces (consumed by E2 self-heal / D)."""

    ATTACH = "attach"
    DETACH = "detach"
    NONE = "none"


def classify_transition(prev: AttachState, cur: AttachState) -> LifecycleEvent:
    """Classify the attach-state transition ``prev`` → ``cur`` (PURE).

    * DETACH — ANY surface present in ``prev`` is gone in ``cur``, even when a
      DIFFERENT surface appeared in the same tick.
    * ATTACH — no surface was lost AND at least one new surface appeared.
    * NONE — no surface changed (includes the idempotent same-state tick).

    ⚑ That DETACH bias is deliberate and safety-critical: MISSING a detach
    drops a just-refreshed token, while an EXTRA detach costs only a redundant,
    idempotent writeback.  Do not "fix" the mixed tick — see the llm-doc.
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

# The subprocess-runner signature the probes call; tests inject a fake.
_Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _collect_proc_cmdlines() -> list[str]:
    """Collect running-process command lines from ``/proc/*/cmdline``.

    The impure companion to :func:`vscode_server_present`.  Tolerant: a
    vanished PID, a permission error or a kernel thread's empty cmdline is
    SKIPPED, never raised.
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

    Normalises EVERY not-attached / no-tmux condition to the empty string
    :func:`tmux_terminal_attached` reads as "no terminal".
    ⚑ ``-F ""`` is NOT used: the default one-line-per-client output IS the
    presence signal being tested.
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

    Composes the two pure detectors over freshly probed system state; an
    explicit *proc_cmdlines* skips the ``/proc`` read entirely.  ⚑ All probing
    in this module is confined here and to the two ``_*`` helpers.  Never
    raises.
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

    ⚑ ``#{pid}`` is the tmux SERVER pid, not a pane or client pid.  ``None``
    when the session is absent, tmux is missing, or the output is unparseable.
    E2 uses this to identify the canonical always-on instance.
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
