"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import shutil
import stat
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
    Mount,
    Operation,
    PluginDescriptor,
    ResourceMapping,
    ResourceScope,
    SafeBypass,
    SettingArg,
    Target,
    TargetSetting,
)

from kanibako.plugins.goose.credentials import (
    filter_config,
    refresh_secrets,
    writeback_secrets,
)

if TYPE_CHECKING:
    from kanibako.crabs import CrabConfig

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
# init_home / refresh/writeback hooks are bypassed for goose).
#
# Notes on a few non-obvious fields (goose 1.37.0, empirically verified):
#   * mode uses the BARE ``session`` subcommand (new) / ``session --resume``
#     (continue-last).  goose 1.37.0 REMOVED the ``session start`` / ``session
#     resume`` subcommands the legacy build_cli_args emitted — that grammar now
#     errors out; this descriptor is the fix.
#   * exec is the standalone headless op ``goose run --no-session -t "<prompt>"``
#     (the prompt is the VALUE of -t; --no-session keeps automation clean).
#   * safe-bypass is the ENV channel ``GOOSE_MODE=auto`` (there is NO --approve-all
#     flag in 1.37.0); model/provider are likewise portable ENV vars
#     (GOOSE_MODEL / GOOSE_PROVIDER) — the ENV form works for `session`, whereas
#     --model/--provider exist only on `goose run`.
#   * binary binding uses the BINARY origin (install.binary) — goose has no
#     separate launcher symlink, so there is no LAUNCHER binding.
#   * secrets.yaml syncs bidirectionally (SYNC); config.yaml is seeded once
#     (SEED_ONCE) through the filter_config allowlist (filtered=True).
_GOOSE_DESCRIPTOR = PluginDescriptor(
    command=("goose",),
    bindings=(
        Binding("binary", HostSrcOrigin.BINARY, "/home/agent/.local/bin/goose", BindKind.FILE, BindScope.AGENT_CRITICAL, ro=True),
    ),
    mode={"start": ("session",), "continue": ("session", "--resume")},
    operations={"exec": Operation(("run", "--no-session", "-t"))},
    safe_bypass=SafeBypass(Channel.ENV, env_var="GOOSE_MODE", env_value="auto", setting_key=""),
    settings=(
        SettingArg("model", Channel.ENV, env_var="GOOSE_MODEL"),
        SettingArg("provider", Channel.ENV, env_var="GOOSE_PROVIDER"),
    ),
    container_env={},
    cred_files=(
        CredFileSpec(".config/goose/secrets.yaml", ".config/goose/secrets.yaml", cadence=Cadence.SYNC, mtime_gate=True, filtered=False),
        CredFileSpec(".config/goose/config.yaml", ".config/goose/config.yaml", cadence=Cadence.SEED_ONCE, filtered=True),
    ),
    host_prep=False,
    init_dirs=(".config/goose", ".local/share/goose/sessions"),
)


class GooseTarget(Target):
    """Target for Goose (https://github.com/block/goose)."""

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _GOOSE_DESCRIPTOR

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        """Filter the goose config.yaml (PURE content op; engine owns gating).

        Called by the credential-sync engine for ``filtered=True`` specs:

        * ``.config/goose/config.yaml`` (SEED_ONCE, "in"): a host source is
          allowlist-filtered to safe keys via :func:`filter_config` (mirrors the
          legacy ``init_home`` config copy).  Unlike claude's ``.claude.json``,
          goose has no empty-config requirement, so ``src is None`` is a no-op.
        * ``secrets.yaml`` is ``filtered=False`` -> the engine copies it directly
          (+ chmod 0600); this hook is never called for it.

        Anything else falls back to the base plain-copy.
        """
        if spec.home_rel == ".config/goose/config.yaml":
            if src is not None and Path(src).is_file():
                filter_config(src, dst)
            return
        super().transform_cred(spec, src, dst, direction)

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

    def init_home(self, home: Path, *, group_auth: bool = True) -> None:
        """Initialize Goose-specific files in the project home.

        Creates ``.config/goose/`` directory.  When *group_auth* is ``True``,
        copies filtered config and secrets from the host.  When ``False``,
        creates a minimal empty config.
        """
        config_dir = home / ".config" / "goose"
        config_dir.mkdir(parents=True, exist_ok=True)

        project_config = config_dir / "config.yaml"

        if group_auth:
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

    def generate_crab_config(self) -> CrabConfig:
        """Return default Goose crab configuration."""
        from kanibako.crabs import CrabConfig as _CrabConfig

        return _CrabConfig(
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
            # Session DB lives under the data dir, NOT the config dir: anchor it
            # via `base` to ~/.local/share/goose/sessions (goose 1.37.0 — no
            # Block/ segment).  PROJECT-scope resources aren't core-mounted, so
            # this is correctness/cosmetic; the dir itself is created by the
            # descriptor's init_dirs.
            ResourceMapping("sessions.db", ResourceScope.PROJECT, "Session history database", base=".local/share/goose/sessions"),
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
