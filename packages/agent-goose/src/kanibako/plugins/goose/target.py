"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    Operation,
    PluginDescriptor,
    SafeBypass,
    SettingArg,
    Target,
    TargetSetting,
)

from kanibako.plugins.goose.credentials import (
    refresh_secrets,
    writeback_secrets,
)

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig

logger = get_logger("targets.goose")

# Per-agent contract path.  Goose is a single self-contained ELF at
# ``~/.local/bin/goose``.  We anchor detection + the delivery bind to this known
# install location instead of ``shutil.which("goose")``: ``which`` trusts
# ``$PATH`` to locate a binary we then bind into the box, a PATH-injection vector
# (an earlier-PATH planted ``goose`` would smuggle a malicious agent into the
# container).  Anchoring confines trust to the user's home dir (mirrors claude).
_BINARY = Path.home() / ".local" / "bin" / "goose"


# Declarative descriptor for the generalized plugin interface.  LIVE: core
# start.py assembles goose's launch argv / env / delivery mounts / credential
# lifecycle from this descriptor (the legacy build_cli_args / binary_mounts /
# refresh/writeback hooks are bypassed for goose).
#
# Notes on a few non-obvious fields (goose 1.37.0, empirically verified):
#   * mode uses the BARE ``session`` subcommand (new) / ``session --resume``
#     (continue-last).  goose 1.37.0 REMOVED the ``session start`` / ``session
#     resume`` subcommands the legacy build_cli_args emitted — that grammar now
#     errors out; this descriptor is the fix.
#   * exec is the standalone headless op ``goose run --no-session -t "<prompt>"``
#     (the prompt is the VALUE of -t; --no-session keeps automation clean).
#   * safe-bypass is the ENV channel ``GOOSE_MODE`` (there is NO --approve-all
#     flag in 1.37.0).  It is SYMMETRIC: safe-OFF/-A emits ``GOOSE_MODE=auto``
#     (tools auto-run); safe-ON/-S emits ``GOOSE_MODE=approve`` (confirm before
#     running ANY tool).  The secure emission is MANDATORY here because goose's
#     UNSET GOOSE_MODE default is itself ``auto`` (verified: goose docs + the
#     1.37.0 binary's embedded `goose_mode TEXT NOT NULL DEFAULT 'auto'`), so
#     emitting nothing on -S would leave goose in auto and -S would not be safe.
#     ``approve`` is the faithful meaning of -S; ``smart_approve`` is a
#     lighter-touch alternative (swap the secure_env_value to switch).
#     model/provider are likewise portable ENV vars
#     (GOOSE_MODEL / GOOSE_PROVIDER) — the ENV form works for `session`, whereas
#     --model/--provider exist only on `goose run`.
#   * binary binding uses the BINARY origin (install.binary) — goose has no
#     separate launcher symlink, so there is no LAUNCHER binding.
#   * secrets.yaml syncs bidirectionally (SYNC).  The host config.yaml IMPORT
#     (extensions/instructions allowlist seed) was removed in 1.6.0: a box gets
#     its curated config.yaml (extensions/instructions) from the agent template,
#     and provider/model from the GOOSE_PROVIDER/GOOSE_MODEL env settings — not
#     from the host config (D-M15: no host-extensions carve-out).
_GOOSE_DESCRIPTOR = PluginDescriptor(
    command=("goose",),
    bindings=(
        Binding("binary", HostSrcOrigin.BINARY, "/home/agent/.local/bin/goose", BindKind.FILE, BindScope.AGENT_CRITICAL, ro=True),
    ),
    mode={"start": ("session",), "continue": ("session", "--resume")},
    operations={"exec": Operation(("run", "--no-session", "-t"))},
    safe_bypass=SafeBypass(Channel.ENV, env_var="GOOSE_MODE", env_value="auto", secure_env_value="approve", setting_key=""),
    # The SettingArg only wires the ENV CHANNEL (the env-var NAME); the default
    # VALUES (GOOSE_MODEL="claude-sonnet-4-20250514", GOOSE_PROVIDER="anthropic",
    # per settings-keyspace §2d) are supplied by ``setting_descriptors()`` below
    # (the resolver's least-specific "floor" tier in start.py), not here.
    settings=(
        SettingArg("model", Channel.ENV, env_var="GOOSE_MODEL"),
        SettingArg("provider", Channel.ENV, env_var="GOOSE_PROVIDER"),
    ),
    container_env={},
    cred_files=(
        CredFileSpec(".config/goose/secrets.yaml", ".config/goose/secrets.yaml", cadence=Cadence.SYNC, mtime_gate=True, filtered=False),
    ),
    host_prep=False,
    init_dirs=(".config/goose", ".local/share/goose/sessions"),
)


class GooseTarget(Target):
    """Target for Goose (https://github.com/block/goose)."""

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _GOOSE_DESCRIPTOR

    # NOTE: no ``transform_cred`` override.  The host config.yaml IMPORT (the
    # extensions/instructions allowlist filter) was removed in 1.6.0; goose's
    # only cred file is the unfiltered ``secrets.yaml`` (SYNC), which the credsync
    # engine wholesale-copies + chmods 0600 without ever calling transform_cred.

    @property
    def name(self) -> str:
        return "goose"

    @property
    def display_name(self) -> str:
        return "Goose"

    @property
    def config_dir_name(self) -> str:
        return ".config/goose"

    @property
    def default_entrypoint(self) -> str | None:
        """Goose binary as container entrypoint."""
        return "goose"

    def detect(self) -> AgentInstall | None:
        """Detect Goose installation on the host.

        Anchors to the per-agent contract path (``_BINARY`` =
        ``~/.local/bin/goose``) rather than ``shutil.which`` — we never let
        ``$PATH`` choose a binary we bind into the box.

        Goose is treated as installed iff the contract path exists *or* is a
        (possibly dangling) symlink.  ``binary`` is the resolved (symlink-free)
        path so nested-container mount sources are real files; ``install_dir`` is
        its parent.  ``launcher`` stays ``None`` (goose has no separate launcher;
        its binding uses the BINARY origin = ``install.binary``).
        """
        if not (_BINARY.exists() or _BINARY.is_symlink()):
            logger.debug("goose binary not present at %s", _BINARY)
            return None

        try:
            resolved = _BINARY.resolve()
        except OSError:
            logger.debug("Failed to resolve binary: %s", _BINARY)
            # Dangling/broken binary: still "installed" per the contract; the
            # binary validation surfaces the real problem downstream.
            resolved = _BINARY

        logger.debug("Resolved binary: %s (from %s)", resolved, _BINARY)
        return AgentInstall(
            name="goose",
            binary=resolved,
            install_dir=resolved.parent,
        )

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

        Anchors the binary reference to the contract path (``_BINARY``); never
        consults ``$PATH``.
        """
        if not (_BINARY.exists() or _BINARY.is_symlink()):
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
        """Return default Goose crab configuration."""
        from kanibako.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(
            name="Goose",
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

