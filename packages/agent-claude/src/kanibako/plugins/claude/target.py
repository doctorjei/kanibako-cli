"""ClaudeTarget: Claude Code agent target implementation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.agent_defaults import load_descriptor, load_shares
from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    BindDefault,
    CredFileSpec,
    PluginDescriptor,
    Target,
    TargetSetting,
)

from kanibako.plugins.claude.credentials import (
    merge_oauth_account_out,
    merge_oauth_in,
    refresh_host_to_project,
    writeback_project_to_host,
)

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig

logger = get_logger("targets.claude")

# Timeout (seconds) for the synchronous ``claude update`` gate run before
# launch.  Generous: an update may download + install a new version.
_UPDATE_TIMEOUT = 300

# Per-agent contract paths.  We anchor every host exec + bind to these known
# install locations instead of ``shutil.which("claude")``.  ``which`` trusts
# ``$PATH`` to locate a binary we then execute on the host (``claude update``,
# ``auth status``) AND bind into the box — a PATH-injection vector (an earlier-
# PATH planted ``claude`` would mean host code-exec + a malicious agent in the
# container).  Anchoring confines trust to the user's home dir.
_LAUNCHER = Path.home() / ".local" / "bin" / "claude"
_INSTALL_DIR = Path.home() / ".local" / "share" / "claude"


# Declarative descriptor for the generalized plugin interface.  LIVE: core
# start.py assembles claude's launch argv / env / delivery mounts / credential
# lifecycle from this descriptor (the legacy build_cli_args / binary_mounts /
# refresh/writeback hooks are bypassed for claude).
#
# The descriptor's declarative default-set lives in this plugin's shipped
# ``claude-defaults.yaml`` (P6c coalesce) and is read by the thin
# :mod:`kanibako.agent_defaults` loader — the file documents each field
# (mode={start,continue} only; both DISABLE_AUTOUPDATER +
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC env vars; the two AGENT_CRITICAL
# delivery binds; the synced filtered credentials).  The CRITICAL host
# binary/launcher paths stay code-resolved in ``detect()`` (the contract
# constants below).
_DEFAULTS_PACKAGE = "kanibako.plugins.claude"
_DEFAULTS_FILE = "claude-defaults.yaml"

_CLAUDE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)


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
    def descriptor(self) -> PluginDescriptor | None:
        return _CLAUDE_DESCRIPTOR

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        """Merge a claude credential file (PURE content op).

        Called by the credential-sync engine for ``filtered=True`` specs; the
        engine owns all mtime/existence gating, so this hook performs NO mtime
        checks.  It mirrors the legacy refresh / writeback content ops exactly:

        * ``.credentials.json`` "in": claudeAiOauth merge host->project via
          :func:`merge_oauth_in` (the gate-free refresh content op).
        * ``.credentials.json`` "out": wholesale project->host copy (the gate-
          free writeback content op).

        Defensive throughout: never raises on a malformed file (matching the
        warn-and-skip helpers).

        NOTE: the host ``.claude.json`` config IMPORT (allowlist-filtered seed of
        ``oauthAccount``/``hasCompletedOnboarding``) was removed in 1.6.0 — a box
        gets its onboarding stub from the curated agent template, and auth flows
        from the synced ``.credentials.json`` alone.
        """
        if spec.home_rel.endswith(".credentials.json"):
            if direction == "out":
                # project->host writeback: wholesale copy (engine gated mtime).
                if src is not None and Path(src).is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
                return
            # direction "in": host->project claudeAiOauth merge.
            if src is not None and Path(src).is_file():
                merge_oauth_in(src, dst)
            return

    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None:
        """Merge the box's ``.claude.json`` ``oauthAccount`` back to the host.

        After an in-box login, the box's ``~/.claude.json`` carries the
        ``oauthAccount`` block; the ``.credentials.json`` writeback (a cred_file
        spec) carries the token, but the account info lives only here.  We splice
        JUST ``oauthAccount`` into the host ``~/.claude.json`` — never a wholesale
        copy, which would clobber the host's machine-specific ``machineID`` /
        ``userID`` / ``projects``.  Defensive (never raises).
        """
        merge_oauth_account_out(
            project_home / ".claude.json", host_home / ".claude.json",
        )

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

        Anchors to the per-agent contract paths (``_LAUNCHER`` /
        ``_INSTALL_DIR``) rather than ``shutil.which`` — we never let ``$PATH``
        choose a binary we execute on the host and bind into the box.

        Claude is treated as installed iff the contract launcher exists *or* is
        a (possibly dangling) symlink: a present-but-dangling launcher still
        counts here — the synchronous update gate / binary validation handles a
        broken install downstream.  ``binary`` is the resolved (symlink-free)
        path so nested-container mount sources are real files; ``launcher`` is
        the contract launcher recorded as-is for the bind.
        """
        if not (_LAUNCHER.exists() or _LAUNCHER.is_symlink()):
            logger.debug("claude launcher not present at %s", _LAUNCHER)
            return None

        try:
            resolved = _LAUNCHER.resolve()
        except OSError:
            logger.debug("Failed to resolve launcher: %s", _LAUNCHER)
            # Dangling/broken launcher: still "installed" per the contract; the
            # update gate / validation surfaces the real problem.  Fall back to
            # the launcher path itself as the binary.
            resolved = _LAUNCHER

        logger.debug(
            "Resolved binary: %s (from %s); install_dir: %s",
            resolved, _LAUNCHER, _INSTALL_DIR,
        )
        return AgentInstall(
            name="claude",
            binary=resolved,
            install_dir=_INSTALL_DIR,
            launcher=_LAUNCHER,
        )

    def generate_agent_config(self) -> AgentConfig:
        """Return default Claude Code crab configuration."""
        from kanibako.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(
            name="Claude Code",
            state={"model": "opus", "access": "permissive"},
        )

    def default_shares(self) -> dict[str, BindDefault]:
        """Declare claude's AGENT-scope shared dirs (plugins + cache).

        Both are shared across every box that runs claude and rooted under the
        per-agent store dir ``@meta.agent.claude.path`` = ``@system.agents/claude``
        (core's ``agent.shared`` scope-root).  The relative ``host_src`` (the key
        name) joins under that root, so the resolved host paths are
        ``<data>/agents/claude/{plugins,cache}`` bound rw to ``~/.claude/{plugins,cache}``:

        * ``plugins`` — installed claude plugins (was an AGENT-scope SHARED_STORE
          ``Binding`` under the removed ``shared/<agent_id>/`` store).
        * ``cache``   — general cache (just changelog.md); promoted from a PROJECT
          ``ResourceMapping`` so it persists across boxes of this agent.

        The base ``default_shares()`` returns ``{}``; this override injects these
        as the AGENT level's declared defaults (overridable/suppressible by the
        user at a more-specific level).  The share keys + box_dests are declared
        in this plugin's ``claude-defaults.yaml`` (read via the loader).
        """
        return load_shares(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)

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
        # Anchor to the contract launcher; never let $PATH choose the binary we
        # exec on the host.
        if not (_LAUNCHER.exists() or _LAUNCHER.is_symlink()):
            return True
        claude_path = str(_LAUNCHER)

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
        # Anchor to the contract launcher; never let $PATH choose the binary we
        # exec on the host.
        if not (_LAUNCHER.exists() or _LAUNCHER.is_symlink()):
            return
        claude_path = str(_LAUNCHER)

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

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Claude Code runtime settings.

        - ``model``: freeform (Claude adds models regularly).
        - ``access``: constrained to permissive/restricted.
        - ``endpoint``: alternate base-URL (persona); unset = bare/harness-default.
        """
        return [
            TargetSetting(
                key="model",
                description="Claude model to use",
                default="opus",
            ),
            TargetSetting(
                key="endpoint",
                description="Alternate base-URL endpoint (persona); "
                "unset uses the harness default and syncs the OAuth login",
                default="",
            ),
            TargetSetting(
                key="access",
                description="Permission mode (permissive bypasses, restricted enforces)",
                default="permissive",
                choices=("permissive", "restricted"),
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
