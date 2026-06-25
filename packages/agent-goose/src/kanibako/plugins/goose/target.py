"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.agent_defaults import load_descriptor
from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    PluginDescriptor,
    Target,
    TargetSetting,
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
# The descriptor's declarative default-set lives in this plugin's shipped
# ``goose-defaults.yaml`` (P6c coalesce) and is read by the thin
# :mod:`kanibako.agent_defaults` loader — the file documents each non-obvious
# field (goose 1.37.0, empirically verified): the bare ``session`` /
# ``session --resume`` mode grammar; the ``run --no-session -t`` exec op; the
# SYMMETRIC ENV GOOSE_MODE safe-bypass (auto/approve — the secure value is
# MANDATORY because goose's unset default is ``auto``); model/provider routed as
# ENV GOOSE_MODEL/GOOSE_PROVIDER with NO default value (goose falls back to its
# own config.yaml from ``goose configure``); the always-on
# GOOSE_DISABLE_KEYRING container env; and the three two-way SYNC cred files
# (secrets.yaml + config.yaml + custom_providers/) that persist in-box ``goose
# configure`` back to the host.  The CRITICAL host binary path stays
# code-resolved in ``detect()`` (the contract constant below; origin=binary).
_DEFAULTS_PACKAGE = "kanibako.plugins.goose"
_DEFAULTS_FILE = "goose-defaults.yaml"

_GOOSE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)


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
        # Matched case-insensitively: the exact casing of goose's stderr is not
        # pinned by the repo, so a phrasing-casing tweak still trips the detector
        # (the secure default is conservative — only this specific phrase fires).
        return "no session found to resume" in output.lower()

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

    # NOTE: no ``refresh_credentials`` / ``writeback_credentials`` overrides.
    # goose is descriptor-bearing, so its secrets.yaml host<->box SYNC is the
    # ``CredFileSpec`` in ``goose-defaults.yaml`` realized by the credsync engine
    # (seed_cred_files / refresh_cred_files / writeback_cred_files) — the §2d
    # ``synced`` category view.  The base no-op hooks are correct here: the legacy
    # per-plugin refresh/writeback path is reached only when ``desc is None``,
    # which never holds for goose.

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

