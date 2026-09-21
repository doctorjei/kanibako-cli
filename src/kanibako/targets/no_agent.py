"""NoAgentTarget: built-in fallback target that runs a plain shell."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.targets.base import AgentInstall, Target

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig


class NoAgentTarget(Target):
    """Fallback target that launches /bin/sh without any agent binary."""

    @property
    def name(self) -> str:
        # ⚑ ``shell`` — the §2d pseudo-agent's OWN slot ([R174]/[R175], Q22).
        # The rename reached the display long ago (``display_name`` below); the
        # NAME is what the registry, the store dir and the cascade slot are
        # spelled from, so it moves only with the D6 reservation scoped to let
        # the built-in through (``targets.__init__``) and the selection seam
        # admitting it (``settings.config.resolve_agent``).
        return "shell"

    @property
    def display_name(self) -> str:
        return "Shell"

    @property
    def has_binary(self) -> bool:
        return False

    def detect(self) -> AgentInstall | None:
        return None

    def refresh_credentials(self, home: Path) -> None:
        pass

    def writeback_credentials(self, home: Path) -> None:
        pass

    def generate_agent_config(self) -> AgentConfig:
        # ⚑ EMPTY — the file holds user intent only; the description is
        # ``agent.<agent>.label`` (spec §2d), not a field of this file.
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig()
