"""CodexTarget: OpenAI Codex CLI agent target implementation (descriptor-native).

This plugin is the *new* first-class target proving the generalized,
descriptor-native plugin interface holds for an agent that did not exist when
the interface was designed.  It implements ONLY the irreducible surface:

* ``name`` / ``display_name`` — identity.
* ``detect`` — the one genuinely codex-specific bit: resolve the REAL native
  Rust binary that the npm ``@openai/codex`` *shim* vendors (see below).
* ``descriptor`` — the declarative :class:`PluginDescriptor`; core ``start.py``
  assembles launch argv / env / delivery binds / credential lifecycle from it.
* ``check_auth`` — lenient credential presence check.
* the declarative helpers ``setting_descriptors`` / ``generate_crab_config`` /
  ``resource_mappings``.

Everything else (``build_cli_args`` / ``binary_mounts`` / ``init_home`` /
``refresh_credentials`` / ``writeback_credentials`` / ``transform_cred``) is
inherited from the step-3a concrete :class:`Target` defaults — codex needs no
overrides there (both its cred files are wholesale copies; the descriptor's
``init_dirs`` create ``.codex``).

⚑ E2E-GATED: ``detect``'s npm-shim -> native-binary resolution (the exact
vendored hoist location and target triple) is implemented best-effort against
the documented codex 0.140.0 layout but MUST be verified on a real codex
install — codex is not present on the dev box, so the tests mock it.  See the
``detect`` docstring.
"""

from __future__ import annotations

import os
import platform
import subprocess
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
    ResourceMapping,
    ResourceScope,
    SafeBypass,
    SettingArg,
    Target,
    TargetSetting,
)

if TYPE_CHECKING:
    from kanibako.crabs import CrabConfig

logger = get_logger("targets.codex")

# Per-agent contract path for the delivered binary INSIDE the box.  Codex ships
# as a single self-contained native Rust ELF (musl static-pie); we bind the
# resolved host binary to this stable box path.  (Detection resolves the real
# host binary dynamically — see ``detect`` — but the box destination is fixed.)
_BINARY_BOX_DEST = "/home/agent/.local/bin/codex"

# Timeout (seconds) for the best-effort ``npm root -g`` probe in ``detect``.
_NPM_ROOT_TIMEOUT = 10


# Declarative descriptor for the generalized plugin interface.  LIVE: core
# start.py assembles codex's launch argv / env / delivery mounts / credential
# lifecycle from this descriptor.  codex implements no legacy hooks.
#
# Notes on a few non-obvious fields (codex 0.140.0, empirically verified):
#   * mode: new session = the BARE ``codex`` (no subcommand, so "start" -> ());
#     continue-last = the ``codex resume --last`` SUBCOMMAND.  There is no
#     dedicated resume PICKER mode, so -R/--resume falls through to continue.
#   * exec is the standalone headless op ``codex exec`` (spliced after the
#     command; no session mode).
#   * safe-bypass is the FLAG ``--dangerously-bypass-approvals-and-sandbox``.
#     The box is externally sandboxed, so bypassing codex's *internal* approval
#     prompts + sandbox is the intended use.  (NOTE: the older ``--yolo`` /
#     ``--full-auto`` flags do NOT exist in 0.140.0.)  There is no persisted
#     safe-bypass setting (setting_key="") — it is a per-launch -A/-S toggle,
#     like goose.
#   * model = the ``--model`` FLAG (also -m).
#   * the binary binding uses the BINARY origin (install.binary) = the resolved
#     real native ELF; codex has no separate launcher symlink, so no LAUNCHER
#     binding.  Binding the static-pie musl ELF works standalone — no node
#     in-box.
#   * cred files (both filtered=False -> the credsync engine wholesale-copies
#     them; NO transform_cred override needed):
#       - ``.codex/auth.json`` (SYNC, mtime-gated): the credential; absent until
#         login and inode-swaps on re-login (delete+recreate), hence copy-sync.
#       - ``.codex/config.toml`` (SEED_ONCE): config; NOT auto-created (codex
#         runs on built-in defaults), so it is seeded only if the user authored
#         one on the host.
#     ⚑ OPEN QUESTION (flagged for E2E): whether ``auth.json`` mixes a portable
#     API key with NON-portable fields (e.g. an installation-bound token /
#     machine id) that would need an allowlist, and whether ``config.toml``
#     carries any secrets.  If either proves true, flip its spec to
#     ``filtered=True`` and add a ``transform_cred`` allowlist (mirroring goose's
#     config.yaml).  For now both are wholesale copies.
#   * init_dirs creates ``.codex`` (codex does not create it itself).  The live
#     mutating per-project state (sqlite/WAL, sessions/, installation_id, ...)
#     is NOT mounted — it stays project-local under this dir.
_CODEX_DESCRIPTOR = PluginDescriptor(
    command=("codex",),
    bindings=(
        Binding("binary", HostSrcOrigin.BINARY, _BINARY_BOX_DEST, BindKind.FILE, BindScope.AGENT_CRITICAL, ro=True),
    ),
    mode={"start": (), "continue": ("resume", "--last")},
    operations={"exec": Operation(("exec",))},
    safe_bypass=SafeBypass(Channel.FLAG, flag=("--dangerously-bypass-approvals-and-sandbox",), setting_key=""),
    settings=(SettingArg("model", Channel.FLAG, flag=("--model",)),),
    container_env={},
    cred_files=(
        CredFileSpec(".codex/auth.json",   ".codex/auth.json",   cadence=Cadence.SYNC,      mtime_gate=True, filtered=False),
        CredFileSpec(".codex/config.toml", ".codex/config.toml", cadence=Cadence.SEED_ONCE,                  filtered=False),
    ),
    host_prep=False,
    init_dirs=(".codex",),
)


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

    @property
    def default_entrypoint(self) -> str | None:
        """Codex binary as container entrypoint."""
        return "codex"

    def detect(self) -> AgentInstall | None:
        """Detect the Codex installation, resolving the REAL native binary.

        ⚑ This shim -> native-binary resolution is the one genuinely
        codex-specific piece and the part that MUST be E2E-verified on a real
        codex install — the precise vendored hoist location and target triple
        are documented for codex 0.140.0 but not confirmed on the dev box (codex
        is not installed here; the unit tests mock the npm root + a fake vendored
        tree).

        ``which codex`` resolves the npm ``@openai/codex`` *Node SHIM*
        (``/usr/local/bin/codex`` -> ``bin/codex.js``), NOT a binary we can bind
        standalone into the box.  Binding the shim would require node in-box.  So
        we resolve the underlying native Rust ELF that the shim vendors:

        1. Find the npm global ``node_modules`` root via ``npm root -g``.
        2. Under it, locate the per-platform package
           ``@openai/codex-<os>-<arch>`` and its vendored binary at
           ``vendor/<triple>/bin/codex`` (checking the hoisted + nested layouts,
           then a glob fallback).
        3. Return an :class:`AgentInstall` pointing ``binary`` at that real ELF.
           The descriptor's BINARY binding uses ``install.binary``, so the
           static-pie musl ELF binds into the box and runs with no node.

        Returns ``None`` (never crashes) when npm/codex is not found.
        """
        npm_root = _npm_root_global()
        if npm_root is None:
            logger.debug("npm global root unavailable; codex not detected")
            return None

        binary = _resolve_vendored_binary(npm_root)
        if binary is None:
            logger.debug("codex vendored binary not found under %s", npm_root)
            return None

        logger.debug("Detected codex native binary: %s", binary)
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

    def generate_crab_config(self) -> CrabConfig:
        """Return default Codex crab configuration."""
        from kanibako.crabs import CrabConfig as _CrabConfig

        return _CrabConfig(
            name=self.display_name,
            shell="standard",
            state={"model": "gpt-5.5"},
        )

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Codex runtime settings.

        Only ``model`` (freeform; OpenAI adds models regularly).  There is no
        ``access`` setting: codex has no persisted safe-bypass default (the
        descriptor's ``safe_bypass.setting_key`` is ""), so safe-bypass is a
        per-launch -A/-S toggle only.
        """
        return [
            TargetSetting(
                key="model",
                description="Model to use",
                default="gpt-5.5",
            ),
        ]

    def resource_mappings(self) -> list[ResourceMapping]:
        """Declare Codex resource sharing scopes.

        Codex's live mutating state under ``~/.codex`` is per-project and is NOT
        core-mounted (PROJECT scope is documentation/correctness; the ``.codex``
        dir itself is created via the descriptor's ``init_dirs``).  We anchor
        these via ``base=".codex"`` so they root at the project home under
        ``.codex`` rather than the config dir.

        * ``auth.json`` is handled by the credsync engine (SYNC), not listed
          here as a shared resource.
        * ``sessions/`` + the sqlite/WAL state files are project-local.
        """
        return [
            ResourceMapping("sessions", ResourceScope.PROJECT, "Per-project session logs (sessions/YYYY/MM/DD/*.jsonl)", base=".codex"),
            ResourceMapping("state.sqlite", ResourceScope.PROJECT, "Project-local sqlite/WAL state (state_*/logs_*/goals_*/memories_*)", base=".codex"),
        ]
