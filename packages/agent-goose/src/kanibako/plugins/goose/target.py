"""GooseTarget: Goose agent target implementation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.settings.agent_defaults import (
    load_behavior,
    load_category_binds,
    load_descriptor,
    load_envs,
)
from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    CategoryBindDefaults,
    CredFileSpec,
    PluginDescriptor,
    Target,
    TargetSetting,
)

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig

logger = get_logger("targets.goose")

# Per-agent contract path.  ⚑ Anchor detection and the delivery bind here, NEVER
# ``shutil.which("goose")``: ``which`` trusts ``$PATH`` to pick a binary we then bind
# into the box, a PATH-injection vector.  (llm-doc: "The contract path".)
_BINARY = Path.home() / ".local" / "bin" / "goose"


# Declarative descriptor for the generalized plugin interface.  LIVE: core start.py
# assembles goose's launch argv / env / delivery mounts / credential lifecycle from it
# (the legacy build_cli_args / binary_mounts / refresh/writeback hooks are bypassed).
# The default-set itself lives in this plugin's shipped ``goose-defaults.yaml`` (P6c
# coalesce), read by the thin :mod:`kanibako.settings.agent_defaults` loader; that file
# documents each non-obvious field, and the llm-doc summarizes what it declares.
# ⚑ The host binary path stays code-resolved in ``detect()`` (``_BINARY``; origin=binary).
_DEFAULTS_PACKAGE = "kanibako.plugins.goose"
_DEFAULTS_FILE = "goose-defaults.yaml"

_GOOSE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
# The declared BEHAVIOR floor (the file's `behavior:` section) — no default value is
# written in this module.  goose's three are EMPTY on purpose; the file says why.
_GOOSE_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)


class GooseTarget(Target):
    """Target for Goose (https://github.com/block/goose)."""

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _GOOSE_DESCRIPTOR

    def default_category_binds(self) -> CategoryBindDefaults:
        """Declare goose's AGENT-scope category binds — from the defaults file, now EMPTY."""
        return load_category_binds(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)

    def default_envs(self) -> dict[str, str]:
        """Declare goose's AGENT-scope env defaults (spec §2d ``agent.goose.env.*``).

        ⚑ Only the PLUGIN-REQUIRED variables are declared; ``GOOSE_PROVIDER`` /
        ``GOOSE_MODEL`` are deliberately absent — goose owns those in its own config.
        """
        return load_envs(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)

    def transform_cred(
        self,
        spec: "CredFileSpec",
        src: "Path | None",
        dst: Path,
        direction: str,
    ) -> None:
        """Filter goose ``config.yaml`` so the box-local GOOSE_MODE is not written back.

        ⚑ ``GOOSE_MODE`` in the box is a PANEL-parity value, NOT user config, so a
        writeback must never overwrite the host's own setting; every other key the user
        changed in-box still flows to the host.  Defensive throughout: a missing source
        or malformed YAML degrades to a no-op, never raises.  Cases: llm-doc.
        """
        import shutil

        from kanibako.plugins.goose.credentials import read_yaml, write_yaml

        if src is None or not Path(src).is_file():
            return
        if not spec.home_rel.endswith("config.yaml"):
            # Defensive: any other filtered spec falls back to a wholesale copy.
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
            # ⚑ An empty / unparseable box config.yaml must NOT clobber the host's real
            # config down to an empty file — a no-op writeback, never host data loss.
            return
        host_cfg = read_yaml(dst)
        if "GOOSE_MODE" in host_cfg:
            box_cfg["GOOSE_MODE"] = host_cfg["GOOSE_MODE"]
        else:
            # Host had no GOOSE_MODE: drop the box-local one rather than introduce it.
            box_cfg.pop("GOOSE_MODE", None)
        if box_cfg == host_cfg:
            # Nothing but the box-local GOOSE_MODE differed — leave the host file (and
            # its mtime) untouched.
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

        On a box's FIRST agent launch the store is empty and ``goose session --resume``
        is DOOMED (fast container death -> raw attach-race error); ``False`` sends
        start.py straight to a new session.  *home* is the box home as seen from the
        HOST (the home bind source), so the store is readable without touching the
        container.  ⚑ FAIL-SAFE: ``False`` only
        when the store positively contains no entry; ANY entry — or any read error —
        returns ``True``, so a real resume is never wrongly denied.  Why the dir-entry
        check splits exactly right, and the ``GOOSE_PATH_ROOT`` limit: llm-doc.
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
        ``GOOSE_MODE`` in the box's ``~/.config/goose/config.yaml`` (FF-5 parity).

        ⚑ ``restricted`` writes ``approve`` EXPLICITLY — an unset ``GOOSE_MODE``
        defaults to permissive ``auto``, so clearing would silently restore permissive.
        ⚑ ``editing`` is REFUSED: goose has no mode that realizes it.  Merge-preserving
        + idempotent (:func:`kanibako.vscode.vscode_config.seed_goose_mode`).  Why the
        panel needs this at all: llm-doc.
        """
        from kanibako.vscode.vscode_config import seed_goose_mode

        # ⚑ Pass the descriptor: the tier→GOOSE_MODE rows are the same ones the launch
        # emits, so the panel cannot drift from the CLI, and core must not reach back
        # into a named plugin to read them.
        return seed_goose_mode(
            config_root / ".config" / "goose" / "config.yaml",
            access=access,
            descriptor=_GOOSE_DESCRIPTOR,
        )

    def should_run_setup(self, output: str) -> bool:
        # Launch-time ground truth that goose configure did NOT produce a bootable
        # config.  goose's verbatim line is "Goose is not configured. Run 'goose
        # configure' to set up."  Match either half case-insensitively, so a phrasing
        # tweak in one half still trips the detector.
        low = output.lower()
        return "not configured" in low or "run 'goose configure'" in low

    @property
    def setup_entrypoint(self) -> str | None:
        """``goose configure`` is goose's one-time interactive provider setup, run in-box."""
        return "goose"

    @property
    def setup_args(self) -> list[str]:
        return ["configure"]

    def detect(self) -> AgentInstall | None:
        """Detect Goose installation on the host — the contract path, never ``$PATH``.

        Installed iff ``_BINARY`` exists *or* is a (possibly dangling) symlink.
        ``binary`` is the RESOLVED path, so nested-container mount sources are real
        files; ``launcher`` stays ``None`` (goose has no separate launcher).
        """
        if not (_BINARY.exists() or _BINARY.is_symlink()):
            logger.debug("goose binary not present at %s", _BINARY)
            return None

        try:
            resolved = _BINARY.resolve()
        except OSError:
            logger.debug("Failed to resolve binary: %s", _BINARY)
            # Dangling/broken binary: still "installed" per the contract; the binary
            # validation surfaces the real problem downstream.
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

    # NOTE: no ``refresh_credentials`` / ``writeback_credentials`` overrides — goose is
    # descriptor-bearing, so its cred SYNC is the ``CredFileSpec`` set in
    # ``goose-defaults.yaml``, realized by the credsync engine.  ⚑ The base no-op hooks
    # are correct: the legacy per-plugin path is reached only when ``desc is None``,
    # which never holds for goose.

    def check_auth(self) -> bool:
        """Check if Goose is configured with API keys — binary, config.yaml, secrets.yaml.

        Returns True when the binary is absent (defers to later warnings).  Anchors to
        the contract path (``_BINARY``); never consults ``$PATH``.
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
        """Return default Goose agent configuration.

        ⚑ ``state`` is intentionally EMPTY: kanibako must NOT pin goose's
        provider/model.  goose's env vars win over its own config.yaml, so a default
        here would clobber the provider the user picked with ``goose configure``.  An
        EXPLICIT setting still emits the env var (see :meth:`setting_descriptors`).
        """
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name="Goose", state={})

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate ``provider`` / ``model`` state into GOOSE_PROVIDER / GOOSE_MODEL.

        Goose overrides provider/model by env var, not CLI flag, so the args list is
        always empty.
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
        """Declare Goose runtime settings — ``provider``, ``model``, ``endpoint``.

        ⚑ The keys and their FLOOR values live in ``goose-defaults.yaml``'s
        ``behavior:`` section, not here: a default written in plugin code is a second
        declaration site for what the shipped file already owns.  All three floors are
        the EMPTY STRING, and that file states why.  What each key does, and why an
        empty floor is load-bearing for provider/model: llm-doc.
        """
        return list(_GOOSE_BEHAVIOR)

