"""LiveTarget: a TESTING-ONLY agent plugin that stays alive (sleeps).

The sibling of the dead-agent plugin (see ../dead-agent/dead.py), used to
exercise the DETACH branch of the two-state box lifecycle: a real agent that
keeps running so its tmux session persists, letting an interactive (PTY) test
send ``Ctrl-b d`` and verify the box is KEPT running + reattachable (vs the
dead agent, whose exit drives the crash/teardown path).

A real kanibako ``Target`` discovered through the directory-plugin tier; its
delivered "binary" is a tiny script (``live-agent``) that prints a marker and
then sleeps.  TESTING-ONLY: lives under ``tests/`` and is NEVER packaged.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.settings_resolve import GUEST_HOME
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    BindScope,
    Binding,
    HostSrcOrigin,
    PluginDescriptor,
    Target,
)

_BINARY = Path.home() / ".local" / "bin" / "live-agent"

_LIVE_DESCRIPTOR = PluginDescriptor(
    command=("live-agent",),
    bindings=(
        Binding(
            key="binary",
            origin=HostSrcOrigin.BINARY,
            box_dest=f"{GUEST_HOME}/.local/bin/live-agent",
            kind=BindKind.FILE,
            scope=BindScope.AGENT_CRITICAL,
            ro=True,
        ),
    ),
    mode={"start": (), "continue": ()},
)


class LiveTarget(Target):
    """A real agent target whose binary stays alive (sleeps) after launch."""

    @property
    def name(self) -> str:
        return "live"

    @property
    def display_name(self) -> str:
        return "Live Agent (test)"

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _LIVE_DESCRIPTOR

    @property
    def default_entrypoint(self) -> str | None:
        return "live-agent"

    def detect(self) -> AgentInstall | None:
        if not (_BINARY.exists() or _BINARY.is_symlink()):
            return None
        try:
            resolved = _BINARY.resolve()
        except OSError:
            resolved = _BINARY
        return AgentInstall(
            name="live",
            binary=resolved,
            install_dir=resolved.parent,
        )

    def check_auth(self) -> bool:
        return True
