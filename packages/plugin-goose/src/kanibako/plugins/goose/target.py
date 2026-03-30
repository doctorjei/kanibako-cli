"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.log import get_logger
from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target, TargetSetting

from kanibako.plugins.goose.credentials import (
    filter_config,
    read_yaml,
    refresh_secrets,
    writeback_secrets,
)

if TYPE_CHECKING:
    from kanibako.agents import AgentConfig

logger = get_logger("targets.goose")


class GooseTarget(Target):
    """Target for Goose (https://github.com/block/goose)."""

    @property
    def name(self) -> str:
        return "goose"

    @property
    def display_name(self) -> str:
        return "Goose"

    @property
    def config_dir_name(self) -> str:
        return ".config/goose"

    def detect(self) -> AgentInstall | None:
        """Detect Goose installation on the host.

        Resolves the ``goose`` symlink to find the real binary.
        """
        goose_path = shutil.which("goose")
        logger.debug("shutil.which('goose') = %s", goose_path)
        if not goose_path:
            return None

        binary = Path(goose_path)

        try:
            resolved = binary.resolve()
        except OSError:
            logger.debug("Failed to resolve symlink: %s", binary)
            return None

        logger.debug("Resolved binary: %s (from %s)", resolved, binary)
        return AgentInstall(
            name="goose",
            binary=resolved,
            install_dir=resolved.parent,
        )

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        """Mount the goose binary into the container (read-only).

        Validates that the binary exists to avoid Podman creating empty stubs.
        """
        mounts: list[Mount] = []
        if install.binary.is_file():
            mounts.append(Mount(
                source=install.binary,
                destination="/home/agent/.local/bin/goose",
                options="ro",
            ))
        return mounts

    def init_home(self, home: Path, *, auth: str = "shared") -> None:
        """Initialize Goose-specific files in the project home.

        Creates ``.config/goose/`` directory.  When *auth* is ``"shared"``,
        copies filtered config and secrets from the host.  When ``"distinct"``,
        creates a minimal empty config.
        """
        config_dir = home / ".config" / "goose"
        config_dir.mkdir(parents=True, exist_ok=True)

        project_config = config_dir / "config.yaml"

        if auth != "distinct":
            # Copy filtered config from host (only safe keys)
            if not project_config.exists():
                host_config = Path.home() / ".config" / "goose" / "config.yaml"
                if host_config.is_file():
                    filter_config(host_config, project_config)
                else:
                    project_config.touch()

            # Copy secrets from host
            host_secrets = Path.home() / ".config" / "goose" / "secrets.yaml"
            project_secrets = config_dir / "secrets.yaml"
            if host_secrets.is_file() and not project_secrets.exists():
                shutil.copy2(str(host_secrets), str(project_secrets))
                project_secrets.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        else:
            # Distinct auth: create empty config
            if not project_config.exists():
                project_config.touch()

        # Create data directory for sessions DB
        data_dir = home / ".local" / "share" / "Block" / "goose"
        data_dir.mkdir(parents=True, exist_ok=True)

    def credential_check_path(self, home: Path) -> Path | None:
        """Path to check for credential existence."""
        return home / ".config" / "goose" / "secrets.yaml"

    def invalidate_credentials(self, home: Path) -> None:
        """Remove secrets.yaml if it exists."""
        secrets = home / ".config" / "goose" / "secrets.yaml"
        if secrets.is_file():
            secrets.unlink()

    def refresh_credentials(self, home: Path) -> None:
        """Refresh Goose secrets from host into project home.

        Syncs host ``~/.config/goose/secrets.yaml`` into the project's
        secrets.yaml using mtime-based freshness.
        """
        host_secrets = Path.home() / ".config" / "goose" / "secrets.yaml"
        project_secrets = home / ".config" / "goose" / "secrets.yaml"
        refresh_secrets(host_secrets, project_secrets)

    def writeback_credentials(self, home: Path) -> None:
        """Write back secrets from project home to host."""
        project_secrets = home / ".config" / "goose" / "secrets.yaml"
        writeback_secrets(project_secrets)

    def check_auth(self) -> bool:
        """Check if Goose is configured with API keys.

        Checks for the goose binary and both config.yaml and secrets.yaml.
        Returns True if binary is not found (defers to later warnings).
        """
        goose_path = shutil.which("goose")
        if not goose_path:
            return True

        secrets = Path.home() / ".config" / "goose" / "secrets.yaml"
        config = Path.home() / ".config" / "goose" / "config.yaml"

        if not secrets.is_file() or secrets.stat().st_size == 0:
            print(
                "Goose is not configured. Run 'goose configure' to set up.",
                file=sys.stderr,
            )
            return False

        if not config.is_file():
            print(
                "Goose is not configured. Run 'goose configure' to set up.",
                file=sys.stderr,
            )
            return False

        return True

    def generate_agent_config(self) -> AgentConfig:
        """Return default Goose agent configuration."""
        from kanibako.agents import AgentConfig as _AgentConfig

        return _AgentConfig(
            name="Goose",
            shell="standard",
            state={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        )

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate Goose state values into CLI args and env vars.

        Recognized keys:
          - ``provider``: set as ``GOOSE_PROVIDER`` env var
          - ``model``: set as ``GOOSE_MODEL`` env var

        Goose uses env vars for provider/model override, not CLI flags.
        """
        cli_args: list[str] = []
        env_vars: dict[str, str] = {}

        provider = state.get("provider")
        if provider:
            env_vars["GOOSE_PROVIDER"] = provider

        model = state.get("model")
        if model:
            env_vars["GOOSE_MODEL"] = model

        return cli_args, env_vars

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Goose runtime settings."""
        return [
            TargetSetting(
                key="provider",
                description="LLM provider",
                default="anthropic",
            ),
            TargetSetting(
                key="model",
                description="Model to use",
                default="claude-sonnet-4-20250514",
            ),
        ]

    def resource_mappings(self) -> list[ResourceMapping]:
        """Declare Goose resource sharing scopes.

        Paths are relative to config_dir_name (.config/goose/).
        """
        return [
            # Seeded from workset template at project creation
            ResourceMapping("config.yaml", ResourceScope.SEEDED, "Goose configuration"),
            # Project-specific
            ResourceMapping("secrets.yaml", ResourceScope.PROJECT, "API keys and secrets"),
            # Data directory resources (.local/share/Block/goose/)
            ResourceMapping("sessions.db", ResourceScope.PROJECT, "Session history database"),
        ]

    def build_cli_args(
        self,
        *,
        safe_mode: bool,
        resume_mode: bool,
        new_session: bool,
        is_new_project: bool,
        extra_args: list[str],
    ) -> list[str]:
        """Build CLI arguments for Goose.

        Maps kanibako flags to goose CLI semantics:
        - ``resume_mode=True`` -> ``session resume``
        - default -> ``session start``
        - ``safe_mode=False`` -> ``--approve-all`` (auto-approve)
        """
        if resume_mode:
            args = ["session", "resume"]
        else:
            args = ["session", "start"]

        if not safe_mode:
            args.append("--approve-all")

        args.extend(extra_args)
        return args
