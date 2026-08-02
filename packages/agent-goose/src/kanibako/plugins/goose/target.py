"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.settings.agent_defaults import load_category_binds, load_descriptor
from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    BindDefault,
    CredFileSpec,
    PluginDescriptor,
    Target,
    TargetSetting,
)

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig

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
# :mod:`kanibako.settings.agent_defaults` loader — the file documents each non-obvious
# field (goose 1.37.0, empirically verified): the bare ``session`` /
# ``session --resume`` mode grammar; the ``run --no-session -t`` exec op; the
# SYMMETRIC ENV GOOSE_MODE access realization (auto/approve — the ``restricted``
# value is MANDATORY because goose's unset default is ``auto``); model/provider routed as
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

    def default_category_binds(self) -> dict[str, BindDefault]:
        """Declare goose's AGENT-scope ``@``-ref-sourced category binds.

        Read from ``goose-defaults.yaml`` (via the loader).  Currently EMPTY: the
        former ``@system.instructions`` → ``~/.config/goose/KANIBAKO.md``
        instructions bind was retired — the box guide now ships INSIDE the RO
        whole-dir canon bind at ``~/canon/bible`` + the flattened FINAL file.
        """
        return load_category_binds(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)

    def transform_cred(
        self,
        spec: "CredFileSpec",
        src: "Path | None",
        dst: Path,
        direction: str,
    ) -> None:
        """Filter goose ``config.yaml`` so the box-local GOOSE_MODE is not written back.

        Only ``config.yaml`` is FILTERED (secrets.yaml + custom_providers/ stay
        unfiltered wholesale-copies); the engine calls this hook for that spec alone.

        * ``"in"`` (host->box seed/refresh): wholesale copy — the box gets the host's
          config.yaml verbatim (its box-local panel-parity GOOSE_MODE is (re)seeded
          separately by :meth:`deliver_panel_permissions` at launch).
        * ``"out"`` (box->host writeback): merge the box's config.yaml back to the
          host BUT preserve the host's OWN GOOSE_MODE.  ``GOOSE_MODE`` in the box is a
          box-local PANEL-parity value (the panel's yolo), NOT user config, so it must
          never overwrite the host's setting.  Every OTHER key the user changed
          in-box (provider/model/extensions/...) still flows to the host.

        Defensive throughout (matches the warn-and-skip credential helpers): a missing
        source or a malformed YAML degrades to a safe no-op / empty dict, never raises.
        """
        import shutil

        from kanibako.plugins.goose.credentials import read_yaml, write_yaml

        if src is None or not Path(src).is_file():
            return
        if not spec.home_rel.endswith("config.yaml"):
            # Defensive: only config.yaml is flagged filtered, but any other filtered
            # spec falls back to a plain wholesale copy (the base-class default).
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            return
        if direction == "in":
            # host->box: wholesale copy (panel-parity GOOSE_MODE re-seeded at attach).
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            return
        # direction "out": box->host writeback, preserving the host's own GOOSE_MODE.
        box_cfg = read_yaml(Path(src))
        if not box_cfg:
            # An empty / unparseable box config.yaml (``read_yaml`` degrades to ``{}``)
            # must NOT clobber the host's real config down to an empty file — a
            # malformed box file is a no-op writeback, never data loss on the host.
            return
        host_cfg = read_yaml(dst)
        if "GOOSE_MODE" in host_cfg:
            box_cfg["GOOSE_MODE"] = host_cfg["GOOSE_MODE"]
        else:
            # Host had no GOOSE_MODE: drop the box-local one rather than introduce it.
            box_cfg.pop("GOOSE_MODE", None)
        if box_cfg == host_cfg:
            # Nothing but the box-local GOOSE_MODE differed — leave the host file (and
            # its mtime) untouched so the writeback is a true no-op.
            return
        write_yaml(dst, box_cfg)

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

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether goose has a session to resume under the box home.

        ``continue`` builds ``goose session --resume``, which resumes the most
        recent session in goose's data-dir session store —
        ``~/.local/share/goose/sessions`` in the box (pre-created EMPTY by the
        descriptor's ``init_dirs``).  Goose 1.37 keeps sessions in a sqlite db
        INSIDE that dir (``sessions/sessions.db`` + ``-wal``/``-shm``), and a
        FAILED resume attempt itself creates the db — so the dir-entry check
        splits exactly right: a FRESH box (init_dirs only, dir empty) reads
        ``False`` (the fix), while a box whose earlier doomed attempt left an
        empty db reads ``True``.  *home* is the box home as seen from the HOST
        (the home bind source), so the store is readable without touching the
        container.

        KNOWN LIMIT: ``GOOSE_PATH_ROOT`` is user-settable (the env category /
        ``-e``) and would redirect goose's store; this hook's fixed signature
        (*home* only) cannot see that, so it checks the DEFAULT store
        location the descriptor lays down.  Kanibako itself never sets
        ``GOOSE_PATH_ROOT`` (the descriptor's ``container_env`` is only
        GOOSE_DISABLE_KEYRING).

        On a box's FIRST agent launch the store is empty and the resume is
        DOOMED ("no session found to resume" -> fast container death -> raw
        attach-race error); returning ``False`` lets start.py go straight to a
        new session.  FAIL-SAFE: ``False`` only when the store positively
        contains no entry (missing dir or empty dir); ANY entry — or any read
        error — returns ``True`` so a real resume is never wrongly denied.
        """
        sessions = home / ".local" / "share" / "goose" / "sessions"
        try:
            if not sessions.is_dir():
                return False
            return next(iter(sessions.iterdir()), None) is not None
        except OSError as exc:
            logger.debug(
                "cannot inspect goose session store %s (%s); "
                "assuming a resumable session exists (fail-safe)",
                sessions, exc,
            )
            return True

    def deliver_panel_permissions(
        self, *, config_root: Path, access: str,
    ) -> bool:
        """Persist the box's CASCADE-resolved ``access`` TIER as the top-level
        ``GOOSE_MODE`` in the box's in-box ``~/.config/goose/config.yaml`` (FF-5
        permission parity): the ``block.vscode-goose`` panel spawns its own
        in-box goose WITHOUT kanibako's launch env, so it never sees the
        ``GOOSE_MODE`` env var the CLI entrypoint sets.

        ASYMMETRIC vs claude: ``restricted`` writes ``approve`` EXPLICITLY (an
        unset ``GOOSE_MODE`` defaults to permissive ``auto`` — clearing would
        silently restore permissive).  ⚑ ``editing`` is REFUSED: goose has no
        mode that realizes it (``smart_approve`` prompts for writes — the
        inverse of acceptEdits), and the launch has already refused that tier
        before delivery.  Merge-preserving + idempotent (see
        :func:`kanibako.vscode.vscode_config.seed_goose_mode`).  goose declares NO
        directive-hook surface, so ``deliver_directive_hook`` stays the
        inherited no-op.
        """
        from kanibako.vscode.vscode_config import seed_goose_mode

        return seed_goose_mode(
            config_root / ".config" / "goose" / "config.yaml",
            access=access,
        )

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
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

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

        ``endpoint`` (persona): the alternate OpenAI-compatible base-URL; unset =
        bare/harness-default.  Delivered via the descriptor's
        ``endpoint``→``OPENAI_HOST`` ENV SettingArg (goose's built-in ``openai``
        provider reads ``OPENAI_HOST``); declared here to make it a first-class
        SETTABLE + cascade-resolved behavior key (``config set``/``--effective``),
        MIRRORING claude's ``endpoint`` descriptor.
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
            TargetSetting(
                key="endpoint",
                description="Alternate OpenAI-compatible base-URL endpoint (persona); "
                "unset uses the harness default and syncs the goose login",
                default="",
            ),
        ]

