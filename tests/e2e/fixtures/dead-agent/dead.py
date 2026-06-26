"""DeadTarget: a TESTING-ONLY agent plugin that is dead on arrival.

This is a real kanibako ``Target`` discovered through the directory-plugin
tier (``$XDG_DATA_HOME/kanibako/plugins/`` or a project's
``box_data/plugins/``), so a box launched with it goes through the genuine
agent-resolution -> binding-delivery -> bootstrap path.  Its delivered
"binary" is a tiny script (``dead-agent``) that prints a marker to stderr
and exits non-zero, so the launch always reaches kanibako's crash/death
handling.  Used by the interactive (PTY) error-recovery tests to exercise
the real attach-on-a-dying-container path that the stub-claude e2e cannot
reach in a non-TTY harness.

TESTING-ONLY: this lives under ``tests/`` and is NEVER packaged or
published.  It is not in any ``pyproject`` ``packages`` list, entry-points,
or ``build-all.sh`` — it can only be discovered when a test explicitly drops
it into a plugin directory.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings_resolve import GUEST_HOME
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    BindScope,
    Binding,
    HostSrcOrigin,
    PluginDescriptor,
    Target,
)

# Per-agent contract path (mirrors goose/claude): detection + the delivery bind
# anchor to a known install location, never ``$PATH``.
_BINARY = Path.home() / ".local" / "bin" / "dead-agent"

# Minimal authentic descriptor: ONE AGENT_CRITICAL binding delivers the dead
# executable into the box read-only, ``command`` runs it, ``mode`` adds no
# flags.  No creds, no settings, no setup — it is meant to crash on launch.
_DEAD_DESCRIPTOR = PluginDescriptor(
    command=("dead-agent",),
    bindings=(
        Binding(
            key="binary",
            origin=HostSrcOrigin.BINARY,
            box_dest=f"{GUEST_HOME}/.local/bin/dead-agent",
            kind=BindKind.FILE,
            scope=BindScope.AGENT_CRITICAL,
            ro=True,
        ),
    ),
    mode={"start": (), "continue": ()},
)


class DeadTarget(Target):
    """A real agent target whose binary always exits non-zero on launch."""

    @property
    def name(self) -> str:
        return "dead"

    @property
    def display_name(self) -> str:
        return "Dead Agent (test)"

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _DEAD_DESCRIPTOR

    @property
    def default_entrypoint(self) -> str | None:
        return "dead-agent"

    def detect(self) -> AgentInstall | None:
        if not (_BINARY.exists() or _BINARY.is_symlink()):
            return None
        try:
            resolved = _BINARY.resolve()
        except OSError:
            resolved = _BINARY
        return AgentInstall(
            name="dead",
            binary=resolved,
            install_dir=resolved.parent,
        )

    def check_auth(self) -> bool:
        # No auth — it is supposed to fail at run time, not auth time.
        return True
