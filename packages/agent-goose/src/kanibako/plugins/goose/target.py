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
    from kanibako.vscode.vscode_config import CodexModelProvider

logger = get_logger("targets.goose")

# Per-agent contract path.  ⚑ Anchor detection and the delivery bind here, NEVER
# ``shutil.which("goose")``: ``which`` trusts ``$PATH`` to pick a binary we then bind
# into the box, a PATH-injection vector.  (llm-doc: "The contract path".)
_BINARY = Path.home() / ".local" / "bin" / "goose"


# Declarative descriptor for the generalized plugin interface.  LIVE: core start.py
# assembles goose's launch argv / env / delivery mounts / credential lifecycle from it
# (the legacy build_cli_args / binary_mounts / apply_state / refresh/writeback hooks
# are bypassed).  ⚑ provider/model reach the box as GOOSE_PROVIDER / GOOSE_MODEL through
# the ``settings:`` ENV realizations below — that is the route ``apply_state`` used to
# duplicate, and the reason removing the retired hook took no capability with it.
# The default-set itself lives in this plugin's shipped ``goose-defaults.yaml`` (P6c
# coalesce), read by the thin :mod:`kanibako.settings.agent_defaults` loader; that file
# documents each non-obvious field, and the llm-doc summarizes what it declares.
# ⚑ The host binary path stays code-resolved in ``detect()`` (``_BINARY``; origin=binary).
_DEFAULTS_PACKAGE = "kanibako.plugins.goose"
_DEFAULTS_FILE = "goose-defaults.yaml"

#: goose's own config file, under ``~/.config/goose/`` -- goose's name, not a kanibako key.
GOOSE_CONFIG_FILE = "config.yaml"

#: Where goose (1.38: plugins only) finds kanibako's hooks, under the box home; the
#: files written there, from ``data/agents-plugin/hooks/``, with their modes.
_HOOKS_DIR = (".agents", "plugins", "kanibako", "hooks")
_HOOK_FILES = (("hooks.json", 0o644), ("mail-stop.sh", 0o755))

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

        ⚑ A writeback never overwrites the host's own ``GOOSE_MODE``: a box's
        permission mode stays in that box.  Every other key the user changed in-box
        still flows to the host.  Defensive throughout: a missing source
        or malformed YAML degrades to a no-op, never raises.  Cases: llm-doc.
        """
        import shutil

        from kanibako.plugins.goose.credentials import read_yaml, write_yaml

        if src is None or not Path(src).is_file():
            return
        if not spec.home_rel.endswith(GOOSE_CONFIG_FILE):
            # Defensive: any other filtered spec falls back to a wholesale copy.
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            return
        if direction == "in":
            # host->box: wholesale copy.
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

    def cred_writeback_error(self, spec: "CredFileSpec", data: bytes) -> str | None:
        """Refuse a box ``secrets.yaml`` that is not a non-empty YAML mapping.

        The mapping is what :func:`~kanibako.plugins.goose.credentials.read_yaml`
        reads; an empty one is refused as the ``config.yaml`` writeback refuses one.
        """
        from kanibako.plugins.goose.credentials import parse_yaml

        if spec.home_rel.endswith("secrets.yaml") and not parse_yaml(data):
            return "not a non-empty YAML mapping"
        return None

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

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Write the kanibako goose plugin that wires the canon hooks, at EVERY start.

        goose 1.38 loads hooks only from plugins, so the box gets the kanibako-owned
        ``~/.agents/plugins/kanibako/hooks/`` — this plugin's shipped ``hooks.json``
        and its ``mail-stop.sh`` adapter — rewritten when it differs, so existing boxes
        get it too.  Every file op goes through :mod:`kanibako.boxfs`; a symlink on the
        path skips the write with a warning.  *access* / *model_provider* are unused.
        The event map, and the claude events goose has no equivalent for: llm-doc.
        """
        from importlib.resources import files

        from kanibako import boxfs

        src = files(_DEFAULTS_PACKAGE).joinpath("data", "agents-plugin", "hooks")
        dest = config_root.joinpath(*_HOOKS_DIR)
        wrote = False
        for name, mode in _HOOK_FILES:
            path = dest / name
            if not boxfs.no_symlink_or_warn(path, config_root):
                return wrote
            data = src.joinpath(name).read_bytes()
            try:
                st = boxfs.stat(path, config_root)
                current = boxfs.read_bytes(path, config_root)
            except FileNotFoundError:
                pass
            else:
                if current == data and (st.st_mode & 0o777) == mode:
                    continue
            boxfs.write_bytes(path, config_root, data, mode=mode)
            wrote = True
        return wrote

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

    # NOTE: goose's cred SYNC is the ``CredFileSpec`` set in ``goose-defaults.yaml``,
    # realized by the credsync engine.

    def check_auth(self) -> bool:
        """Check if Goose is configured with API keys — binary, config.yaml, secrets.yaml.

        Returns True when the binary is absent (defers to later warnings).  Anchors to
        the contract path (``_BINARY``); never consults ``$PATH``.
        """
        if not (_BINARY.exists() or _BINARY.is_symlink()):
            return True

        secrets = Path.home() / ".config" / "goose" / "secrets.yaml"
        config = Path.home() / ".config" / "goose" / GOOSE_CONFIG_FILE

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

        ⚑ The ``name="Goose"`` this wrote is gone (2026-09-15).  It was not a settings
        key, so it had no place in a settings file at all; the description is declared
        as ``label`` in ``goose-defaults.yaml``'s ``behavior:`` floor —
        ``agent.goose.label``, spec §2d.
        """
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(state={})

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Goose runtime settings — ``provider``, ``model``, ``endpoint``.

        ⚑ The keys and their FLOOR values live in ``goose-defaults.yaml``'s
        ``behavior:`` section, not here: a default written in plugin code is a second
        declaration site for what the shipped file already owns.  None of the three sets a
        value (``provider`` is ``<None>``; ``model``/``endpoint`` inherit ``agent.default``),
        and that file states why.  What each key does, and why no value is load-bearing
        for provider/model: llm-doc.
        """
        return list(_GOOSE_BEHAVIOR)

