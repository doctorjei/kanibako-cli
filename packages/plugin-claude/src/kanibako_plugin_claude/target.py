"""ClaudeTarget: Claude Code agent target implementation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.log import get_logger
from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target, TargetSetting

from kanibako_plugin_claude.credentials import (
    filter_settings,
    refresh_host_to_project,
    writeback_project_to_host,
)

if TYPE_CHECKING:
    from kanibako.agents import AgentConfig

logger = get_logger("targets.claude")


class ClaudeTarget(Target):
    """Target for Claude Code."""

    @property
    def name(self) -> str:
        return "claude"

    @property
    def display_name(self) -> str:
        return "Claude Code"

    @property
    def default_entrypoint(self) -> str | None:
        return "claude"

    @property
    def config_dir_name(self) -> str:
        return ".claude"

    def credential_check_path(self, home: Path) -> Path | None:
        return home / ".claude" / ".credentials.json"

    def invalidate_credentials(self, home: Path) -> None:
        """Remove credential files from a shell directory."""
        creds = home / ".claude" / ".credentials.json"
        settings = home / ".claude.json"
        for f in (creds, settings):
            if f.is_file():
                f.unlink()

    def detect(self) -> AgentInstall | None:
        """Detect Claude Code installation on the host.

        Resolves the ``claude`` symlink to find the real binary, then walks up
        the directory tree to locate the ``claude/`` installation root.
        """
        claude_path = shutil.which("claude")
        logger.debug("shutil.which('claude') = %s", claude_path)
        if not claude_path:
            return None

        binary = Path(claude_path)

        try:
            resolved = binary.resolve()
        except OSError:
            logger.debug("Failed to resolve symlink: %s", binary)
            return None

        logger.debug("Resolved binary: %s", resolved)

        # Walk up from the resolved binary to find the 'claude' directory.
        install_dir = resolved.parent
        while install_dir.name != "claude" and install_dir != install_dir.parent:
            install_dir = install_dir.parent

        # Sanity check: if we hit the filesystem root without finding 'claude',
        # fall back to the immediate parent of the binary.
        if install_dir.name != "claude":
            install_dir = resolved.parent

        logger.debug("Install dir: %s", install_dir)
        return AgentInstall(name="claude", binary=binary, install_dir=install_dir)

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        """Return mounts for Claude install dir and binary.

        Validates that mount sources exist to avoid Podman creating
        empty stubs at mount destinations.
        """
        mounts: list[Mount] = []
        if install.install_dir.is_dir():
            mounts.append(Mount(
                source=install.install_dir,
                destination="/home/agent/.local/share/claude",
                options="ro",
            ))
        if install.binary.is_file():
            mounts.append(Mount(
                source=install.binary,
                destination="/home/agent/.local/bin/claude",
                options="ro",
            ))
        return mounts

    def init_home(self, home: Path, *, auth: str = "shared") -> None:
        """Initialize Claude-specific files in the project home.

        Creates ``.claude/`` directory.  When *auth* is ``"shared"``, copies
        credentials and filtered settings from the host.  When ``"distinct"``,
        skips credential copy (project manages its own auth).
        """
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        if auth != "distinct":
            # Copy credentials from host ~/.claude/.credentials.json
            host_creds = Path.home() / ".claude" / ".credentials.json"
            if host_creds.is_file():
                shutil.copy2(str(host_creds), str(claude_dir / ".credentials.json"))

            # Copy filtered .claude.json from host
            host_settings = Path.home() / ".claude.json"
            if host_settings.is_file():
                filter_settings(host_settings, home / ".claude.json")
            else:
                (home / ".claude.json").touch()
        else:
            # Distinct auth: create empty .claude.json
            (home / ".claude.json").touch()

    def generate_agent_config(self) -> AgentConfig:
        """Return default Claude Code agent configuration."""
        from kanibako.agents import AgentConfig as _AgentConfig

        return _AgentConfig(
            name="Claude Code",
            shell="standard",
            state={"model": "opus", "access": "permissive"},
            shared_caches={"plugins": ".claude/plugins"},
        )

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate Claude Code state values into CLI args and env vars.

        Recognized keys:
          - ``model``: passed as ``--model <value>``

        Unknown keys are silently ignored.
        """
        cli_args: list[str] = []
        env_vars: dict[str, str] = {}

        model = state.get("model")
        if model:
            cli_args.extend(["--model", model])

        return cli_args, env_vars

    def check_auth(self) -> bool:
        """Check if the user is authenticated with Claude.

        Runs ``claude auth status --json`` and checks the ``loggedIn`` field.
        If not logged in, runs ``claude auth login`` interactively and
        re-checks status afterward.

        Returns True if authentication is confirmed (or if the claude binary
        is not found — the missing-binary warning already covers that case).
        """
        claude_path = shutil.which("claude")
        if not claude_path:
            return True

        # Check current auth status.
        try:
            result = subprocess.run(
                [claude_path, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True

        if result.returncode != 0:
            # Could not determine status; skip check.
            return True

        try:
            status = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return True

        if status.get("loggedIn"):
            return True

        # Not logged in — prompt interactive login.
        print(
            "Claude is not authenticated. Running 'claude auth login'...",
            file=sys.stderr,
        )
        try:
            login_result = subprocess.run(
                [claude_path, "auth", "login"],
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        if login_result.returncode != 0:
            return False

        # Re-check after login.
        try:
            recheck = subprocess.run(
                [claude_path, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            recheck_status = json.loads(recheck.stdout)
            return bool(recheck_status.get("loggedIn"))
        except Exception:
            return False

    def resource_mappings(self) -> list[ResourceMapping]:
        """Declare Claude Code resource sharing scopes.

        Shared: plugin binaries only (identical across projects).
        Seeded: settings.json, CLAUDE.md (copied from workset template at creation).
        Project: everything else (caches, stats, telemetry, session data, tasks).
        """
        return [
            # Shared at workset/account level
            ResourceMapping("plugins/", ResourceScope.SHARED, "Plugin binaries and registry"),
            ResourceMapping("cache/", ResourceScope.PROJECT, "General cache"),
            ResourceMapping("stats-cache.json", ResourceScope.PROJECT, "Usage stats cache"),
            ResourceMapping("statsig/", ResourceScope.PROJECT, "Feature flags"),
            ResourceMapping("telemetry/", ResourceScope.PROJECT, "Telemetry data"),
            # Seeded from workset template at project creation
            ResourceMapping("settings.json", ResourceScope.SEEDED, "Permissions and enabled plugins"),
            ResourceMapping("CLAUDE.md", ResourceScope.SEEDED, "Agent instructions template"),
            # Project-specific (fresh per project)
            ResourceMapping("projects/", ResourceScope.PROJECT, "Session data and memory"),
            ResourceMapping("session-env/", ResourceScope.PROJECT, "Session environment state"),
            ResourceMapping("history.jsonl", ResourceScope.PROJECT, "Conversation history"),
            ResourceMapping("tasks/", ResourceScope.PROJECT, "Task tracking"),
            ResourceMapping("todos/", ResourceScope.PROJECT, "Todo lists"),
            ResourceMapping("plans/", ResourceScope.PROJECT, "Plan mode files"),
            ResourceMapping("file-history/", ResourceScope.PROJECT, "File edit history"),
            ResourceMapping("backups/", ResourceScope.PROJECT, "File backups"),
            ResourceMapping("debug/", ResourceScope.PROJECT, "Debug logs"),
            ResourceMapping("paste-cache/", ResourceScope.PROJECT, "Clipboard state"),
            ResourceMapping("shell-snapshots/", ResourceScope.PROJECT, "Shell state snapshots"),
        ]

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Claude Code runtime settings.

        - ``model``: freeform (Claude adds models regularly).
        - ``access``: constrained to permissive/default.
        """
        return [
            TargetSetting(
                key="model",
                description="Claude model to use",
                default="opus",
            ),
            TargetSetting(
                key="access",
                description="Permission mode",
                default="permissive",
                choices=("permissive", "default"),
            ),
        ]

    def refresh_credentials(self, home: Path) -> None:
        """Refresh Claude credentials from host into project home.

        Syncs host ``~/.claude/.credentials.json`` into ``home/.claude/.credentials.json``
        using mtime-based freshness.
        """
        host_creds = Path.home() / ".claude" / ".credentials.json"
        project_creds = home / ".claude" / ".credentials.json"

        refresh_host_to_project(host_creds, project_creds)

    def writeback_credentials(self, home: Path) -> None:
        """Write back refreshed credentials from project home to host."""
        project_creds = home / ".claude" / ".credentials.json"

        writeback_project_to_host(project_creds)

    def build_cli_args(
        self,
        *,
        safe_mode: bool,
        resume_mode: bool,
        new_session: bool,
        is_new_project: bool,
        extra_args: list[str],
    ) -> list[str]:
        """Build CLI arguments for Claude Code."""
        cli_args: list[str] = []

        if not safe_mode:
            cli_args.append("--dangerously-skip-permissions")

        if resume_mode:
            cli_args.append("--resume")
        else:
            skip_continue = new_session or is_new_project
            if any(a in ("--resume", "-r") for a in extra_args):
                skip_continue = True
            if not skip_continue:
                cli_args.append("--continue")

        cli_args.extend(extra_args)
        return cli_args
