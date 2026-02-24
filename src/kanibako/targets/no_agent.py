"""NoAgentTarget: built-in fallback target that runs a plain shell."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.targets.base import AgentInstall, Mount, Target

if TYPE_CHECKING:
    from kanibako.agents import AgentConfig


class NoAgentTarget(Target):
    """Fallback target that launches /bin/sh without any agent binary."""

    @property
    def name(self) -> str:
        return "no_agent"

    @property
    def display_name(self) -> str:
        return "Shell"

    @property
    def has_binary(self) -> bool:
        return False

    def detect(self) -> AgentInstall | None:
        return None

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        return []

    def init_home(self, home: Path, *, auth: str = "shared") -> None:
        pass

    def refresh_credentials(self, home: Path) -> None:
        pass

    def writeback_credentials(self, home: Path) -> None:
        pass

    def build_cli_args(
        self,
        *,
        safe_mode: bool,
        resume_mode: bool,
        new_session: bool,
        is_new_project: bool,
        extra_args: list[str],
    ) -> list[str]:
        return []

    def generate_agent_config(self) -> AgentConfig:
        from kanibako.agents import AgentConfig as _AgentConfig

        return _AgentConfig(name="Shell")
