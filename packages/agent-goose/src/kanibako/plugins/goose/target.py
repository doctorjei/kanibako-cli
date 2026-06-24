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
#   * secrets.yaml syncs bidirectionally (SYNC).
#   * config.yaml + custom_providers/ ALSO sync bidirectionally (SYNC) as of the
#     2026-06-24 goose-config-persistence fix.  This intentionally revisits the
#     1.6.0 D-M15 "no host-config" stance for goose's PROVIDER config (Jei-
#     approved): the user configures goose interactively IN THE BOX (``goose
#     configure`` writes provider/model into config.yaml + any custom_providers/
#     entry); without writeback the box-only config is invisible to the host-side
#     ``check_auth`` (which reads ~/.config/goose/config.yaml), so kanibako re-ran
#     ``goose configure`` on EVERY start.  Syncing config.yaml + custom_providers/
#     back to the host fixes the re-prompt and gives goose PARITY with how claude
#     (.credentials.json) and codex (auth.json) write back to the host.  These
#     files reference the provider API key by env-var NAME, not by value (the
#     value lives in secrets.yaml under GOOSE_DISABLE_KEYRING), so they are
#     unfiltered wholesale copies — no secret leaks into config.yaml/
#     custom_providers/.  The OLD 1.6.0 host config.yaml IMPORT (the
#     extensions/instructions allowlist seed) stays removed; this is a plain SYNC
#     of the box's own config, not a re-introduction of the host-extensions
#     carve-out.
_GOOSE_DESCRIPTOR = PluginDescriptor(
    command=("goose",),
    bindings=(
        Binding("binary", HostSrcOrigin.BINARY, "/home/agent/.local/bin/goose", BindKind.FILE, BindScope.AGENT_CRITICAL, ro=True),
    ),
    mode={"start": ("session",), "continue": ("session", "--resume")},
    operations={"exec": Operation(("run", "--no-session", "-t"))},
    safe_bypass=SafeBypass(Channel.ENV, env_var="GOOSE_MODE", env_value="auto", secure_env_value="approve", setting_key=""),
    # The SettingArg only wires the ENV CHANNEL (the env-var NAME).  There is
    # deliberately NO default VALUE for provider/model: ``setting_descriptors()``
    # below declares them with an EMPTY default, so the resolver floor yields ""
    # when the user hasn't set ``agent.goose.provider`` / ``agent.goose.model``,
    # and ``assemble_env`` (``if value:``) then OMITS GOOSE_PROVIDER/GOOSE_MODEL
    # entirely — goose falls back to its own config.yaml (driven by ``goose
    # configure``).  Pinning a default here / in setting_descriptors would
    # override the user's in-box ``goose configure`` choice (goose env vars win
    # over config.yaml), so we don't.  An EXPLICIT setting still emits the var.
    settings=(
        SettingArg("model", Channel.ENV, env_var="GOOSE_MODEL"),
        SettingArg("provider", Channel.ENV, env_var="GOOSE_PROVIDER"),
    ),
    # GOOSE_DISABLE_KEYRING is ALWAYS set for goose boxes (static, not a
    # setting): inside the box the OS keyring / D-Bus secret-service is
    # unavailable/broken (rootful goose errors "Unable to create DBus keyring
    # when setuid"), so ``goose configure`` cannot store the provider API key and
    # launch then fails with "Configuration value not found: ANTHROPIC_API_KEY".
    # goose's documented remedy makes it store/read secrets in the file
    # ``~/.config/goose/secrets.yaml`` instead — the file kanibako already syncs
    # host<->box and writes back (see cred_files / writeback_secrets).
    container_env={"GOOSE_DISABLE_KEYRING": "true"},
    cred_files=(
        CredFileSpec(".config/goose/secrets.yaml", ".config/goose/secrets.yaml", cadence=Cadence.SYNC, mtime_gate=True, filtered=False),
        # config.yaml: provider/model selection + base config from in-box ``goose
        # configure``.  SYNC so it persists back to the host -> host check_auth
        # passes on the next start (no re-prompt).  Unfiltered (key-by-NAME only).
        CredFileSpec(".config/goose/config.yaml", ".config/goose/config.yaml", cadence=Cadence.SYNC, mtime_gate=True, filtered=False),
        # custom_providers/: the custom-provider DEFINITIONS dir (e.g.
        # custom_navigator.json — base URL, model list, the api_key ENV-VAR NAME).
        # is_dir -> recursive sync (no mtime gate); credsync mirrors it both ways.
        CredFileSpec(".config/goose/custom_providers", ".config/goose/custom_providers", cadence=Cadence.SYNC, mtime_gate=True, filtered=False, is_dir=True),
    ),
    host_prep=False,
    init_dirs=(".config/goose", ".local/share/goose/sessions"),
)


class GooseTarget(Target):
    """Target for Goose (https://github.com/block/goose)."""

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _GOOSE_DESCRIPTOR

    # NOTE: no ``transform_cred`` override.  The old host config.yaml IMPORT
    # (extensions/instructions allowlist filter) stays removed; all of goose's
    # cred specs are UNFILTERED SYNC entries — secrets.yaml + config.yaml (files)
    # and custom_providers/ (dir) — which the credsync engine wholesale-copies +
    # chmods 0600 without ever calling transform_cred.

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

    def should_retry_new_session(self, output: str) -> bool:
        # ``continue`` builds ``goose session --resume``; on a fresh box there is
        # no prior session to resume, so goose exits with this stderr.  Signal
        # start.py's fallback to relaunch with a new (bare ``session``) session.
        return "No session found to resume" in output

    def should_run_setup(self, output: str) -> bool:
        # Launch-time ground truth that goose configure did NOT produce a bootable
        # config: goose's verbatim line is "Goose is not configured. Run 'goose
        # configure' to set up."  Match case-insensitively on either the "not
        # configured" phrase or the "goose configure" remediation hint so a
        # phrasing tweak in either half still trips the detector.
        low = output.lower()
        return "not configured" in low or "run 'goose configure'" in low

    @property
    def setup_entrypoint(self) -> str | None:
        """``goose configure`` is goose's one-time interactive provider setup.

        When the pre-launch :meth:`check_auth` probe fails (goose unconfigured),
        ``start.py`` runs ``goose configure`` interactively IN THE BOX so the user
        can select a provider/model and enter a key, then proceeds with launch.
        Box-state persists across reattach, so this is a one-time step per box.
        """
        return "goose"

    @property
    def setup_args(self) -> list[str]:
        return ["configure"]

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
        """Return default Goose crab configuration.

        ``state`` is intentionally EMPTY: kanibako must NOT pin goose's
        provider/model.  Forcing GOOSE_PROVIDER/GOOSE_MODEL as defaults
        overrides the user's in-box ``goose configure`` choice (goose env vars
        win over its own config.yaml), which would clobber a provider/key the
        user selected interactively.  When the user has NOT explicitly set
        ``agent.goose.provider`` / ``agent.goose.model`` in kanibako settings,
        the env vars are omitted entirely and goose falls back to its own
        config.yaml (driven by ``goose configure``).  An explicit setting still
        emits the env var (see :meth:`setting_descriptors` / the descriptor's
        provider/model SettingArgs).
        """
        from kanibako.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name="Goose", state={})

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
        """Declare Goose runtime settings.

        provider/model carry NO default (empty ``default=""``): kanibako must
        not pin goose's provider/model.  goose's env vars (GOOSE_PROVIDER /
        GOOSE_MODEL) override its own config.yaml, so a forced default would
        clobber the user's in-box ``goose configure`` choice.  The resolver
        floor therefore resolves these to empty when unset, and
        ``assemble_env`` (``if value:``) omits the env vars entirely — goose
        then reads provider/model from its config.yaml.  An EXPLICIT
        ``agent.goose.provider`` / ``agent.goose.model`` setting still wins the
        cascade and IS emitted, so a user who wants to pin a provider via
        kanibako settings still can.
        """
        return [
            TargetSetting(
                key="provider",
                description="LLM provider (unset = use goose configure / config.yaml)",
                default="",
            ),
            TargetSetting(
                key="model",
                description="Model to use (unset = use goose configure / config.yaml)",
                default="",
            ),
        ]

