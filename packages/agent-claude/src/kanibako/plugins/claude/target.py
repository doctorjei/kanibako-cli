"""ClaudeTarget: Claude Code agent target implementation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.log import get_logger
from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target, TargetSetting

from kanibako.plugins.claude.credentials import (
    filter_settings,
    refresh_host_to_project,
    writeback_project_to_host,
)

if TYPE_CHECKING:
    from kanibako.crabs import CrabConfig

logger = get_logger("targets.claude")

# Timeout (seconds) for the synchronous ``claude update`` gate run before
# launch.  Generous: an update may download + install a new version.
_UPDATE_TIMEOUT = 300


def _autoupdater_disabled_env() -> dict[str, str]:
    """Return a copy of os.environ with the Claude auto-updater disabled.

    Every host exec of the ``claude`` binary that kanibako owns runs with
    ``DISABLE_AUTOUPDATER=1`` so we never wake Claude's async background
    auto-updater mid-launch (it would prune/repoint the version we are about
    to bind, racing the mount).  The only update in our window is the explicit
    synchronous ``claude update`` gate.
    """
    env = dict(os.environ)
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


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

    def should_retry_new_session(self, output: str) -> bool:
        return "No conversation found" in output

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

        logger.debug("Resolved binary: %s (from %s)", resolved, binary)

        # Walk up from the resolved binary to find the 'claude' directory.
        install_dir = resolved.parent
        while install_dir.name != "claude" and install_dir != install_dir.parent:
            install_dir = install_dir.parent

        # Sanity check: if we hit the filesystem root without finding 'claude',
        # fall back to the immediate parent of the binary.
        if install_dir.name != "claude":
            install_dir = resolved.parent

        logger.debug("Install dir: %s", install_dir)
        # Use the resolved (symlink-free) binary path so that mount sources
        # are real files, avoiding symlink resolution issues in nested
        # containers (e.g. podman inside LXC).
        return AgentInstall(name="claude", binary=resolved, install_dir=install_dir)

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        """Return the two AS-IS host binds that deliver Claude to the box.

        Host owns the binary + its update lifecycle; the container reflects it
        faithfully via two binds and never freezes a resolved version:

        1. ``~/.local/bin/claude`` bound **as-is** (the launcher symlink).  The
           bind dereferences the source symlink itself and grabs the inode at
           mount time, so the file is *pinned*: later host churn (prune /
           repoint after a self-update) cannot pull it out from under the
           running container.  One path, no ``lstat``, no link-vs-real-binary
           branch.
        2. ``~/.local/share/claude`` bound **whole** (the install dir / data
           files — we do not assume the binary is self-contained).

        The destinations are cleared to clean non-symlink mountpoints every
        launch by the cli (``_precreate_mount_stubs``) so these binds actually
        take instead of following a dest symlink into the share subtree.

        Sources are validated to exist so a missing source produces a clean,
        catchable kanibako error at mount-build time rather than a cryptic crun
        dangling-exec crash.
        """
        mounts: list[Mount] = []

        # Install-dir / data files: bind whole.
        if install.install_dir.is_dir():
            mounts.append(Mount(
                source=install.install_dir,
                destination="/home/agent/.local/share/claude",
                options="ro",
            ))

        # The host launcher (~/.local/bin/claude) bound AS-IS.  Prefer the
        # launcher path on $PATH (a symlink whose target the bind resolves at
        # mount time); fall back to the resolved binary if the launcher is
        # gone.  Either way the bind pins the inode at mount time.
        launcher = shutil.which("claude")
        bin_source = Path(launcher) if launcher else install.binary
        if bin_source.exists():
            mounts.append(Mount(
                source=bin_source,
                destination="/home/agent/.local/bin/claude",
                options="ro",
            ))

        return mounts

    def init_home(self, home: Path, *, group_auth: bool = True) -> None:
        """Initialize Claude-specific files in the project home.

        Creates ``.claude/`` directory.  When *group_auth* is ``True``, copies
        credentials and filtered settings from the host.  When ``False``,
        skips credential copy (project manages its own auth).
        """
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        if group_auth:
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

    def generate_crab_config(self) -> CrabConfig:
        """Return default Claude Code crab configuration."""
        from kanibako.crabs import CrabConfig as _CrabConfig

        return _CrabConfig(
            name="Claude Code",
            shell="standard",
            state={"model": "opus", "access": "permissive"},
        )

    def default_shares(self) -> dict[str, str]:
        """Plugins are shared across all Claude crabs (identical binaries/registry)."""
        return {"crab.path.share_rw.plugins": "plugins:~/.claude/plugins"}

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate Claude Code state values into CLI args and env vars.

        Recognized keys:
          - ``model``: passed as ``--model <value>``

        Unknown keys are silently ignored.
        """
        cli_args: list[str] = []
        # Disable the in-container agent's self-updater: a mid-session update
        # would repoint the writable ~/.local/bin/claude to a version the
        # read-only host bind cannot have, breaking the running session.
        env_vars: dict[str, str] = {"DISABLE_AUTOUPDATER": "1"}

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

        # Every host exec of the binary runs with the auto-updater disabled so
        # this auth probe doesn't wake the async updater mid-launch.
        host_env = _autoupdater_disabled_env()

        # Check current auth status.
        try:
            result = subprocess.run(
                [claude_path, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
                env=host_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            # OSError covers FileNotFoundError and, critically, an
            # "Exec format error" from a corrupt/0-byte binary -- never crash
            # the launch with a traceback; treat auth status as unknown.
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
                env=host_env,
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
                env=host_env,
            )
            recheck_status = json.loads(recheck.stdout)
            return bool(recheck_status.get("loggedIn"))
        except Exception:
            return False

    def prepare_host(self, install: AgentInstall, *, auto_auth: bool, data_path: Path) -> None:
        """Update the host Claude binary, then refresh host auth.

        Owns all the Claude-specific host work that must happen before mounts
        are built:

        1. **Synchronous update gate.** Run ``claude update`` and wait for it,
           so the host binary + ``~/.local/bin/claude`` symlink are at a stable
           version BEFORE we bind them.  With the background auto-updater
           disabled (``DISABLE_AUTOUPDATER=1`` on every exec we own + in the
           container), this is the only update in our launch window — no async
           race.  A foreground ``claude update`` is expected to block until the
           install + symlink repoint complete.
        2. **Auto auth refresh** (when *auto_auth* is set) with the updater
           disabled, so this host exec doesn't wake the background updater.

        This method MUST NOT crash the launch: every step is best-effort and
        failures are logged, not raised.
        """
        claude_path = shutil.which("claude")
        if not claude_path:
            return

        host_env = _autoupdater_disabled_env()

        # 1. Synchronous update gate.  DISABLE_AUTOUPDATER does not disable an
        # *explicit* update; it only suppresses the async background updater.
        try:
            result = subprocess.run(
                [claude_path, "update"],
                capture_output=True,
                text=True,
                timeout=_UPDATE_TIMEOUT,
                env=host_env,
            )
            if result.returncode != 0:
                logger.debug(
                    "claude update returned %s: %s",
                    result.returncode,
                    (result.stderr or result.stdout or "").strip(),
                )
            else:
                logger.debug("claude update completed")
        except subprocess.TimeoutExpired:
            logger.warning(
                "claude update timed out after %ss; proceeding with current "
                "host binary.", _UPDATE_TIMEOUT,
            )
        except OSError as exc:
            # FileNotFoundError / Exec format error on a corrupt binary, etc.
            logger.debug("claude update could not run: %s", exc)

        # 2. Automated OAuth refresh (best-effort), with the updater disabled.
        if auto_auth:
            try:
                from kanibako.auth_browser import auto_refresh_auth

                auto_result = auto_refresh_auth(
                    str(install.binary), data_path, env=host_env,
                )
                if auto_result.success:
                    logger.info("Auto-auth succeeded")
                else:
                    logger.debug("Auto-auth skipped: %s", auto_result.error)
            except Exception as exc:
                logger.debug("Auto-auth failed: %s", exc)

    def resource_mappings(self) -> list[ResourceMapping]:
        """Declare Claude Code resource sharing scopes.

        Seeded: settings.json, CLAUDE.md (copied from workset template at creation).
        Project: everything else (caches, stats, telemetry, session data, tasks).

        Plugins are served separately as a crab-scoped default share
        (see ``default_shares``), not as a SHARED resource mapping.
        """
        return [
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

    def instruction_files(self) -> list[str]:
        """Return instruction files to merge across template levels."""
        return ["CLAUDE.md"]

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
