"""CodexTarget: OpenAI Codex CLI agent target implementation (descriptor-native).

This plugin is the *new* first-class target proving the generalized,
descriptor-native plugin interface holds for an agent that did not exist when
the interface was designed.  It implements ONLY the irreducible surface:

* ``name`` / ``display_name`` — identity.
* ``detect`` — the one genuinely codex-specific bit: resolve the REAL native
  binary to bind into the box, honoring the host's recorded host-binary
  preference order — **machine-code-compiled executable > self-contained /
  contained package (SEA, AppImage — still a single bindable executable) >
  runtime-dependent package managers (npm/pip), LAST**.  The rationale is to
  avoid the brittleness of requiring node/python on the host.  Concretely on
  Linux:

  - PRIMARY: if ``codex`` on ``$PATH`` resolves (symlinks followed) to a
    directly-bindable **ELF** (a Rust native build OR a Node SEA — both carry
    the ``\x7fELF`` magic), bind THAT executable directly.
  - FALLBACK: otherwise (PATH ``codex`` absent, or it is the npm Node *shim* — a
    ``#!node`` text script, NOT bindable standalone) resolve THROUGH npm to the
    native binary the shim vendors (see below).
* ``descriptor`` — the declarative :class:`PluginDescriptor`; core ``start.py``
  assembles launch argv / env / delivery binds / credential lifecycle from it.
* ``check_auth`` — lenient credential presence check.
* the declarative helpers ``setting_descriptors`` / ``generate_agent_config``.

Everything else (``build_cli_args`` / ``binary_mounts`` /
``refresh_credentials`` / ``writeback_credentials`` / ``transform_cred``) is
inherited from the step-3a concrete :class:`Target` defaults — codex needs no
overrides there (both its cred files are wholesale copies; the descriptor's
``init_dirs`` create ``.codex``).

⚑ E2E-GATED: ``detect``'s two paths — (1) the PRIMARY standalone-ELF-on-PATH
bind and (2) the FALLBACK npm-shim -> native-binary resolution (the exact
vendored hoist location and target triple) — are implemented best-effort
against the documented codex 0.140.0 layout but MUST be verified on a real
codex install: a standalone-extracted ELF on PATH (primary) and the npm Node
shim that vendors a musl static-pie ELF (fallback).  codex is not present on
the dev box, so the tests mock both.  See the ``detect`` docstring.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.agent_defaults import load_category_binds, load_descriptor
from kanibako.log import get_logger
from kanibako.targets.base import (
    AgentInstall,
    BindDefault,
    PersonaSettings,
    PluginDescriptor,
    Target,
    TargetSetting,
)

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig

logger = get_logger("targets.codex")

# Timeout (seconds) for the best-effort ``npm root -g`` probe in ``detect``.
_NPM_ROOT_TIMEOUT = 10


# Declarative descriptor for the generalized plugin interface.  LIVE: core
# start.py assembles codex's launch argv / env / delivery mounts / credential
# lifecycle from this descriptor.  codex implements no legacy hooks.
#
# The descriptor's declarative default-set lives in this plugin's shipped
# ``codex-defaults.yaml`` (P6c coalesce) and is read by the thin
# :mod:`kanibako.agent_defaults` loader — the file documents each non-obvious
# field (codex 0.140.0): the bare ``codex`` / ``codex resume --last`` mode
# grammar; the ``codex exec`` op; the FLAG
# ``--dangerously-bypass-approvals-and-sandbox`` per-launch-only safe-bypass; the
# ``--model`` FLAG; the single SYNC ``.codex/auth.json`` cred file (filtered=False
# wholesale copy, an E2E gate); and the ``.codex`` init dir.  The box-side binary
# destination is fixed in the file; the CRITICAL host binary path is
# runtime-PROBED in ``detect()`` (ELF-on-PATH primary / npm-vendored fallback;
# origin=binary).
_DEFAULTS_PACKAGE = "kanibako.plugins.codex"
_DEFAULTS_FILE = "codex-defaults.yaml"

_CODEX_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)


# Map (os, machine) -> (npm platform-package suffix, vendored target triple).
# The npm ``@openai/codex`` shim vendors a per-platform package
# ``@openai/codex-<suffix>`` containing ``vendor/<triple>/bin/codex``.
# ⚑ E2E-GATED: the exact suffix/triple strings below match the documented
# 0.140.0 packaging but must be confirmed against a real install.
def _platform_pkg_and_triple() -> tuple[str, str] | None:
    """Return (npm-platform-pkg-suffix, vendored-target-triple) for this host.

    e.g. linux + x86_64 -> ("codex-linux-x64", "x86_64-unknown-linux-musl").
    Returns ``None`` for an unrecognized OS/arch (detect then falls back to the
    glob search, and ultimately to "not installed").
    """
    sysname = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize arch aliases.
    is_x64 = machine in ("x86_64", "amd64", "x64")
    is_arm64 = machine in ("aarch64", "arm64")

    if sysname == "linux":
        if is_x64:
            return "codex-linux-x64", "x86_64-unknown-linux-musl"
        if is_arm64:
            return "codex-linux-arm64", "aarch64-unknown-linux-musl"
    elif sysname == "darwin":
        if is_x64:
            return "codex-darwin-x64", "x86_64-apple-darwin"
        if is_arm64:
            return "codex-darwin-arm64", "aarch64-apple-darwin"
    return None


def _npm_root_global() -> Path | None:
    """Return the npm global ``node_modules`` root, or ``None`` on any failure.

    Best-effort: runs ``npm root -g`` with a short timeout and tolerates every
    failure mode (npm absent, timeout, nonzero, garbage output) by returning
    ``None`` — codex detection NEVER crashes on this.
    """
    try:
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            timeout=_NPM_ROOT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    if not out:
        return None
    root = Path(out)
    return root if root.is_dir() else None


def _resolve_vendored_binary(npm_root: Path) -> Path | None:
    """Resolve the real native codex binary under the npm global *npm_root*.

    The npm ``@openai/codex`` package is a Node SHIM; the real binary lives in a
    per-platform package ``@openai/codex-<suffix>`` at
    ``vendor/<triple>/bin/codex``.  That platform package may be:

    * HOISTED to the top level:  ``<root>/@openai/codex-<suffix>``
    * NESTED under the shim:     ``<root>/@openai/codex/node_modules/@openai/codex-<suffix>``

    Both are checked (in that order) for the resolved (suffix, triple).  As a
    final fallback we glob ``<root>/**/@openai/codex-*/vendor/*/bin/codex`` (any
    layout / any vendored triple) so a packaging quirk still resolves.

    Returns the first existing real binary path, or ``None``.
    """
    pkg_triple = _platform_pkg_and_triple()
    if pkg_triple is not None:
        suffix, triple = pkg_triple
        rel = Path("vendor") / triple / "bin" / "codex"
        candidates = [
            npm_root / "@openai" / suffix / rel,                                    # hoisted
            npm_root / "@openai" / "codex" / "node_modules" / "@openai" / suffix / rel,  # nested
        ]
        for cand in candidates:
            if cand.is_file():
                logger.debug("Resolved vendored codex binary: %s", cand)
                return cand

    # Fallback: glob any @openai/codex-* platform package's vendored binary.
    for cand in sorted(npm_root.glob("**/@openai/codex-*/vendor/*/bin/codex")):
        if cand.is_file():
            logger.debug("Resolved vendored codex binary via glob: %s", cand)
            return cand

    return None


def _is_elf(path: Path) -> bool:
    """Return ``True`` iff *path* begins with the ELF magic ``\\x7fELF``.

    This is the discriminator between a directly-bindable machine-code / SEA
    executable (Rust native build OR a Node single-executable-application — both
    are ELF on Linux) and the npm ``@openai/codex`` Node *shim* (a ``#!node``
    text script, NOT bindable standalone).  Swallows any ``OSError``
    (missing/unreadable/dir) -> ``False`` so detection never crashes.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def _resolve_path_executable() -> Path | None:
    """Resolve ``codex`` on ``$PATH`` to its real (symlink-followed) target.

    codex's host install location is genuinely user-chosen — there is no fixed
    contract path like claude/goose have — so a ``$PATH`` lookup is the right
    primitive here; we follow symlinks and verify ELF magic (read-only) before
    ever trusting/binding the result, so this is not the PATH-injection vector
    that anchoring guards against for the fixed-path agents.

    Returns the resolved real path, or ``None`` if ``codex`` is not on ``$PATH``
    (or cannot be resolved).  Never raises.
    """
    found = shutil.which("codex")
    if not found:
        return None
    try:
        return Path(found).resolve()
    except OSError:
        return None


class CodexTarget(Target):
    """Target for the OpenAI Codex CLI (https://github.com/openai/codex)."""

    @property
    def name(self) -> str:
        return "codex"

    @property
    def display_name(self) -> str:
        return "OpenAI Codex CLI"

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _CODEX_DESCRIPTOR

    def default_category_binds(self) -> dict[str, BindDefault]:
        """Declare codex's AGENT-scope ``@``-ref-sourced category binds.

        Read from ``codex-defaults.yaml`` (via the loader).  Currently EMPTY: the
        former ``@system.instructions`` → ``~/.codex/AGENTS.md`` instructions bind
        was retired — the box guide now ships via the RO ``~/playbook/kanibako``
        bundle + the flattened per-agent FINAL file.
        """
        return load_category_binds(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)

    @property
    def default_entrypoint(self) -> str | None:
        """Codex binary as container entrypoint."""
        return "codex"

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether codex has a recorded session to resume under the box home.

        ``continue`` mode builds ``codex resume --last`` (codex-defaults.yaml), which
        replays the MOST-RECENT recorded session — a "rollout" ``.jsonl`` file codex
        persists under ``$CODEX_HOME/sessions/<year>/<MM>/<DD>/rollout-<ts>-<uuid>.jsonl``
        (verified against openai/codex ``codex-rs/rollout/src``: ``SESSIONS_SUBDIR =
        "sessions"`` + the ``year/month/day`` push in ``recorder.rs``).  ``CODEX_HOME``
        defaults to ``~/.codex`` and kanibako sets NO ``CODEX_HOME`` (the descriptor's
        ``container_env`` is empty), so the box store is ``<home>/.codex/sessions/``.
        ``resume --last`` is workdir-AGNOSTIC (the newest session regardless of cwd),
        so — unlike claude's per-project transcript dir — this checks the WHOLE store.

        On a FRESH box the store is absent/empty, so ``resume --last`` is DOOMED (no
        session -> fast exit); returning ``False`` lets start.py launch a new session
        instead (the launch-time crash-and-retry net was removed).  Any rollout
        ``*.jsonl`` (recursively, to cover the date nesting) ⇒ ``True``.  Tolerant:
        any stat/glob error ⇒ ``False`` (a fresh start is always safe).
        """
        sessions = home / ".codex" / "sessions"
        try:
            if not sessions.is_dir():
                return False
            return next(sessions.rglob("*.jsonl"), None) is not None
        except OSError:
            return False

    @property
    def setup_entrypoint(self) -> str | None:
        """``codex login`` is codex's interactive in-box login.

        When the pre-launch :meth:`check_auth` probe fails (no ``auth.json`` and
        no ``OPENAI_API_KEY``), ``start.py`` runs ``codex login`` interactively
        IN THE BOX so the user can complete the ChatGPT/OAuth flow, then proceeds
        with launch.  Box-state persists across reattach.
        """
        return "codex"

    @property
    def setup_args(self) -> list[str]:
        return ["login"]

    def should_run_setup(self, output: str) -> bool:
        # Launch-time ground truth that ``codex login`` did NOT produce a bootable
        # auth state: codex's session reports it needs a login / authentication
        # failed.  Match case-insensitively on codex's known login-needed signals
        # ("not logged in", the "codex login" remediation hint, "please log in",
        # "authentication failed", "401 unauthorized") so a phrasing change in any
        # one of them still trips the detector.
        low = output.lower()
        return (
            "not logged in" in low
            or "run 'codex login'" in low
            or "codex login" in low
            or "please log in" in low
            or "please sign in" in low
            or "authentication failed" in low
            or "401 unauthorized" in low
        )

    def detect(self) -> AgentInstall | None:
        """Detect the Codex installation, resolving a directly-bindable binary.

        Honors the host-binary preference order (machine-code > self-contained
        package > runtime package manager) so we bind a standalone executable
        whenever one exists and only fall back to npm — which would otherwise
        require node on the host — as a last resort.

        **PRIMARY — standalone executable on ``$PATH``.** Resolve ``codex`` on
        ``$PATH`` (symlinks followed).  If the real target is an **ELF** (first
        four bytes ``\\x7fELF`` — a Rust native build OR a Node single-executable
        application, both directly bindable) bind THAT file:
        :class:`AgentInstall` with ``binary`` = the resolved ELF and
        ``install_dir`` = its parent.  No node in-box required.

        **FALLBACK — npm vendored native binary.** If ``codex`` is absent from
        ``$PATH``, OR it resolves to a *non*-ELF (the npm ``@openai/codex`` Node
        *shim*, a ``#!node`` text script that is NOT bindable standalone), fall
        through to the npm path:

        1. Find the npm global ``node_modules`` root via ``npm root -g``.
        2. Under it, locate the per-platform package
           ``@openai/codex-<os>-<arch>`` and its vendored binary at
           ``vendor/<triple>/bin/codex`` (checking the hoisted + nested layouts,
           then a glob fallback).
        3. Return an :class:`AgentInstall` pointing ``binary`` at that real ELF.
           The descriptor's BINARY binding uses ``install.binary``, so the
           static-pie musl ELF binds into the box and runs with no node.

        ⚑ Both paths are E2E-gated: the PRIMARY standalone-ELF bind and the
        FALLBACK shim -> vendored-native resolution (the precise vendored hoist
        location and target triple, documented for codex 0.140.0) MUST be
        verified on a real codex install — codex is not installed on the dev box
        (the unit tests mock ``$PATH`` + the npm root + a fake vendored tree).

        Returns ``None`` (never crashes) when neither a standalone binary nor an
        npm-vendored one is found.
        """
        # PRIMARY: a standalone machine-code / SEA executable on $PATH.
        path_bin = _resolve_path_executable()
        if path_bin is not None and _is_elf(path_bin):
            logger.debug("Detected standalone codex ELF on PATH: %s", path_bin)
            return AgentInstall(
                name="codex",
                binary=path_bin,
                install_dir=path_bin.parent,
            )

        # FALLBACK: the native binary the npm Node shim vendors (needs npm, not
        # node-in-box, to resolve — but is the LAST resort by preference order).
        npm_root = _npm_root_global()
        if npm_root is None:
            logger.debug("no standalone codex on PATH and npm global root unavailable; codex not detected")
            return None

        binary = _resolve_vendored_binary(npm_root)
        if binary is None:
            logger.debug("codex vendored binary not found under %s", npm_root)
            return None

        logger.debug("Detected codex native binary via npm fallback: %s", binary)
        return AgentInstall(
            name="codex",
            binary=binary,
            install_dir=binary.parent,
        )

    def check_auth(self) -> bool:
        """Lenient credential presence check for Codex.

        Returns ``True`` (do not block launch) when either:

        * ``~/.codex/auth.json`` exists and is non-empty, OR
        * the ``OPENAI_API_KEY`` environment variable is set.

        Otherwise returns ``False``.  Never crashes — any stat error is treated
        as "cannot tell" and returns ``True`` (matching goose's lenient style of
        not blocking when it cannot determine auth state).
        """
        try:
            auth = Path.home() / ".codex" / "auth.json"
            if auth.is_file() and auth.stat().st_size > 0:
                return True
        except OSError:
            # Cannot determine -> don't block the launch.
            return True

        if os.environ.get("OPENAI_API_KEY"):
            return True

        return False

    def read_persona_settings(self, config_dir: Path) -> PersonaSettings | None:
        """Extract persona values from a rendered codex ``config.toml``.

        The persona-grata store renders a codex persona as the SAME
        ``[model_providers.<id>]`` shape kanibako itself emits at launch
        (``vscode_config.CodexModelProvider`` → ``_build_codex_provider_region``),
        so this reader parses the inverse: ``base_url`` → endpoint, ``env_key``
        → auth_env (codex configs SELF-NAME the bearer var), and the top-level
        ``model``.  Provider-table selection: the top-level ``model_provider``
        key when it names a present table (what kanibako writes), else the
        single table when exactly one exists; zero tables or an unresolvable
        ambiguity → ``None``.

        FAIL-SOFT (base-class contract): absent / unreadable / malformed TOML,
        no usable provider table, or a missing/empty ``base_url``/``env_key``
        (a codex persona is meaningless without both) → ``None``.  Pure read
        via stdlib ``tomllib``; never touches the token.
        """
        import tomllib

        cfg = config_dir / "config.toml"
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # OSError: absent/unreadable; ValueError covers both
            # tomllib.TOMLDecodeError and UnicodeDecodeError.
            return None
        providers = data.get("model_providers")
        if not isinstance(providers, dict) or not providers:
            return None
        selected = data.get("model_provider")
        if isinstance(selected, str) and selected in providers:
            table = providers[selected]
        elif len(providers) == 1:
            table = next(iter(providers.values()))
        else:
            return None  # several tables, none selected -> ambiguous
        if not isinstance(table, dict):
            return None
        base_url = table.get("base_url")
        env_key = table.get("env_key")
        if not isinstance(base_url, str) or not base_url:
            return None
        if not isinstance(env_key, str) or not env_key:
            return None
        model = data.get("model")
        if not isinstance(model, str) or not model:
            model = None
        return PersonaSettings(endpoint=base_url, model=model, auth_env=env_key)

    def generate_agent_config(self) -> AgentConfig:
        """Return default Codex crab configuration."""
        from kanibako.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(
            name=self.display_name,
            state={"model": "gpt-5.5"},
        )

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Codex runtime settings.

        - ``model`` (freeform; OpenAI adds models regularly).
        - ``endpoint``: alternate model-provider base-URL (persona); unset =
          bare/harness-default.  Unlike claude, a codex endpoint is delivered via
          the ``~/.codex/config.toml`` ``[model_providers.<id>]`` block (see the
          descriptor ``persona.endpoint_delivery: config_file``), NOT an env var —
          it is declared here only to make it a first-class SETTABLE + cascade-
          resolved behavior key (``config set``/``--effective``).

        Safe-bypass is NOT a setting descriptor: it rides the uniform ``auto_approve``
        key (the descriptor's ``safe_bypass.setting_key`` is ``auto_approve``),
        persisted + cascade-resolved, default permissive; ``-A``/``-S`` override per
        launch.
        """
        return [
            TargetSetting(
                key="model",
                description="Model to use",
                default="gpt-5.5",
            ),
            TargetSetting(
                key="endpoint",
                description="Alternate model-provider base-URL (persona); "
                "unset uses the harness default and syncs the codex login",
                default="",
            ),
        ]
