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
* the declarative helpers ``setting_descriptors`` / ``generate_crab_config`` /
  ``resource_mappings``.

Everything else (``build_cli_args`` / ``binary_mounts`` / ``init_home`` /
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

    @property
    def default_entrypoint(self) -> str | None:
        """Codex binary as container entrypoint."""
        return "codex"

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
