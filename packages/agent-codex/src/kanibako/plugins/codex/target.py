"""CodexTarget: OpenAI Codex CLI agent target implementation (descriptor-native).

The first target written after the plugin interface was generalized, so it
implements ONLY the irreducible surface — identity, ``detect``, the declarative
``descriptor``, ``check_auth``, the config.toml + persona seams and the two
declarative helpers.  Everything else (``build_cli_args`` / ``binary_mounts`` /
``refresh_credentials`` / ``writeback_credentials`` / ``transform_cred``) is
inherited from the concrete :class:`Target` defaults.

⚑ codex is the one CONFIG-FILE harness: endpoint and model reach the box through
``~/.codex/config.toml``, not env vars.  Most of what is unusual here follows
from that one fact.

⚑ E2E-GATED: both ``detect`` paths are best-effort against the documented codex
0.140.0 layout and MUST be verified on a real install; codex is absent from the
dev box, so the tests mock both.

Reference: ``llm-docs/kanibako/plugins/codex/target.py.md``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Mapping
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
    PersonaProbeOutcome,
    PersonaReadOutcome,
    PersonaSettings,
    PluginDescriptor,
    ProbeEvidence,
    Target,
    TargetSetting,
    http_probe,
    probe_outcome,
    probe_outcome_no_model,
)

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig
    from kanibako.vscode.vscode_config import CodexModelProvider

logger = get_logger("targets.codex")

# Timeout (seconds) for the best-effort ``npm root -g`` probe in ``detect``.
_NPM_ROOT_TIMEOUT = 10


# The declarative default-set — descriptor, behavior floor and env — is this
# plugin's shipped ``codex-defaults.yaml`` (P6c coalesce), read by the thin
# :mod:`kanibako.settings.agent_defaults` loader; that file documents each
# non-obvious field.  Core start.py assembles codex's launch argv / env /
# delivery mounts / credential lifecycle from the descriptor it builds.
# ⚑ The one CODE-RESOLVED value is the CRITICAL host binary path, runtime-PROBED
# in ``detect()`` (origin=binary); the box-side destination is fixed in the file.
_DEFAULTS_PACKAGE = "kanibako.plugins.codex"
_DEFAULTS_FILE = "codex-defaults.yaml"

_CODEX_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
# The declared BEHAVIOR floor (the file's `behavior:` section) — no default value
# is written in this module.
_CODEX_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)


# ⚑ E2E-GATED: the suffix/triple strings below match the documented 0.140.0
# packaging but must be confirmed against a real install.
def _platform_pkg_and_triple() -> tuple[str, str] | None:
    """Return (npm-platform-pkg-suffix, vendored-target-triple) for this host.

    ``None`` for an unrecognized OS/arch — detect then falls back to the glob
    search, and ultimately to "not installed".
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

    Best-effort: every failure mode (npm absent, timeout, nonzero, garbage
    output) answers ``None`` — codex detection NEVER crashes on this.
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
    ``vendor/<triple>/bin/codex``, which npm may have HOISTED to the top level or
    NESTED under the shim.  Both are checked, in that order, then a glob of any
    layout / any vendored triple so a packaging quirk still resolves.  Returns
    the first existing real binary path, or ``None``.
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

    The discriminator between a directly-bindable machine-code / SEA executable
    (a Rust native build OR a Node single-executable-application — both are ELF
    on Linux) and the npm ``@openai/codex`` Node *shim* (a ``#!node`` text
    script, NOT bindable standalone).  Any ``OSError`` (missing / unreadable /
    dir) -> ``False`` so detection never crashes.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def _resolve_path_executable() -> Path | None:
    """Resolve ``codex`` on ``$PATH`` to its real (symlink-followed) target.

    ⚑ A ``$PATH`` lookup is the right primitive HERE — codex's install location
    is genuinely user-chosen, with no fixed contract path like claude/goose have
    — and it is not the PATH-injection vector anchoring guards against for those
    agents, because the result is ELF-verified (read-only) before it is ever
    trusted or bound.  ``None`` if ``codex`` is absent or unresolvable; never
    raises.
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

    def default_category_binds(self) -> CategoryBindDefaults:
        """Declare codex's AGENT-scope ``@``-ref-sourced category binds.

        Read from ``codex-defaults.yaml`` (via the loader), and currently EMPTY:
        the former ``@system.instructions`` → ``~/.codex/AGENTS.md`` bind was
        retired in favour of the RO canon bind + the flattened FINAL file.
        """
        return load_category_binds(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)

    def default_envs(self) -> dict[str, str]:
        """Declare codex's AGENT-scope env defaults (spec §2d ``agent.codex.env.*``).

        Read from ``codex-defaults.yaml``'s ``env:`` section: the one variable
        ``KANIBAKO_DIRECTIVE_FINAL``, naming codex's native ``~/.codex/AGENTS.md``
        slot.  An ordinary settings key — overridable by the SAME key in a nearer
        file, and refused when a second scope names the same variable.
        """
        return load_envs(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)

    @property
    def default_entrypoint(self) -> str | None:
        """Codex binary as container entrypoint."""
        return "codex"

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether codex has a recorded session to resume under the box home.

        ``continue`` mode builds ``codex resume --last``, which replays the newest
        rollout ``.jsonl`` under ``<home>/.codex/sessions/`` (kanibako sets no
        ``CODEX_HOME``, so codex's ``~/.codex`` default stands).  That is
        workdir-AGNOSTIC, so — unlike claude's per-project transcript dir — this
        checks the WHOLE store, recursively to cover the date nesting.

        On a FRESH box the store is absent/empty, so ``resume --last`` is DOOMED;
        ``False`` lets start.py launch a new session instead (the launch-time
        crash-and-retry net was removed).  Any stat/glob error ⇒ ``False`` too, a
        fresh start being always safe.
        """
        sessions = home / ".codex" / "sessions"
        try:
            if not sessions.is_dir():
                return False
            return next(sessions.rglob("*.jsonl"), None) is not None
        except OSError:
            return False

    def deliver_panel_permissions(
        self, *, config_root: Path, access: str,
    ) -> bool:
        """Mirror the box's CASCADE-resolved ``access`` TIER into the managed
        ``approval_policy``/``sandbox_mode`` root keys of the box's in-box
        ``~/.codex/config.toml``.

        The panel spawns its own in-box codex without kanibako's launch flags, so
        this parity is the ONLY way it sees the box's tier.  The SOLE writer of
        those two keys (the directive-hook write below is hook/trust/provider
        only).  ``approval_policy`` is TIER-gated (``full`` → ``"never"``,
        ``editing`` → ``"on-request"``, ``restricted`` → removed while it still
        equals a value WE manage, preserving a user-chosen one); ``sandbox_mode``
        is a BOX INVARIANT forced to ``"danger-full-access"`` ALWAYS, independent
        of *access*.  ⚑ That invariant is why the panel's middle tier rides the
        APPROVAL axis while the CLI's rides ``-s workspace-write``: writing
        workspace-write here is the configuration that hangs the app-server.  See
        :func:`kanibako.vscode.vscode_config.seed_codex_approval`.
        """
        from kanibako.vscode.vscode_config import seed_codex_approval

        return seed_codex_approval(
            config_root / ".codex" / "config.toml", access=access,
        )

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed the managed codex config.toml: the instruction-delivery
        ``[[hooks.SessionStart]]`` group, its pre-computed trust hash, the
        directory trust, and (for a codex persona) the *model_provider* region.

        ⚑ NEVER the approval/sandbox keys — those belong to
        :meth:`deliver_panel_permissions` alone, so no managed key has two writers
        (*access* is accepted per the seam contract but unused here).  The
        box-side literals codex keys its trust entries on (the in-box config path
        and workdir) derive from
        :data:`~kanibako.settings.settings_resolve.GUEST_HOME`; promote a seam
        parameter instead if either ever becomes configurable.
        """
        from kanibako.settings.settings_resolve import GUEST_HOME
        from kanibako.vscode.vscode_config import seed_codex_config

        return seed_codex_config(
            config_root / ".codex" / "config.toml",
            box_config_path=f"{GUEST_HOME}/.codex/config.toml",
            codex_cwd=f"{GUEST_HOME}/workspace",
            model_provider=model_provider,
        )

    def reattach_config_notice(self) -> str | None:
        """Warn that codex config changes apply only after a restart.

        codex's ``config.toml`` is a RECONCILED PROJECTION (D1): the launch seams
        re-materialise it only on start of a STOPPED box, and rewriting it under
        the panel's already-running app-server is unsafe.
        """
        return (
            "Note: this box is already running; codex config changes (model / "
            "provider / approvals) take effect only after restarting the box "
            "('kanibako stop' then 'kanibako start')."
        )

    @property
    def setup_entrypoint(self) -> str | None:
        """``codex login`` is codex's interactive in-box login.

        Run by ``start.py`` IN THE BOX when :meth:`check_auth` fails, so the user
        can complete the ChatGPT/OAuth flow before launch continues.
        """
        return "codex"

    @property
    def setup_args(self) -> list[str]:
        return ["login"]

    def should_run_setup(self, output: str) -> bool:
        # Launch-time ground truth that ``codex login`` did NOT produce a bootable
        # auth state.  Case-insensitive across codex's known login-needed signals,
        # so a phrasing change in any ONE of them still trips the detector.
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
        package > runtime package manager), so npm — the one path that would
        require node on the host — is the last resort.

        **PRIMARY:** ``codex`` on ``$PATH`` (symlinks followed) when it is an ELF
        — a Rust native build OR a Node single-executable application, both
        directly bindable, no node in-box.  **FALLBACK:** the native binary the
        npm ``@openai/codex`` Node *shim* vendors, reached through ``npm root -g``;
        the descriptor's BINARY binding uses ``install.binary``, so that
        static-pie musl ELF binds in and runs with no node either.  ``None``
        (never a crash) when neither is found.

        ⚑ Both paths are E2E-gated against a REAL codex install, which the dev box
        does not have: the unit tests mock ``$PATH``, the npm root and a fake
        vendored tree, so they cannot prove either resolution.
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

        ``True`` (do not block launch) when ``~/.codex/auth.json`` is present and
        non-empty, OR ``OPENAI_API_KEY`` is set — and on any stat error too,
        which is "cannot tell", not "no".  Matches goose's lenient style.
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

    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome:
        """Extract persona values from a rendered codex ``config.toml``.

        The store renders a codex persona in the SAME ``[model_providers.<id>]``
        shape kanibako emits at launch, so this parses the inverse: ``base_url``
        → endpoint, ``env_key`` → auth_env (codex configs SELF-NAME the bearer
        var), plus the top-level ``model``.  Table selection: the ``model_provider``
        key when it names a present table, else the single table when exactly one
        exists.  A codex config carries NO env block, so ``env``/``env_dropped``
        stay empty — unlike claude, whose persona env rides the config.

        FAIL-SOFT (base-class contract): every reject below returns an outcome
        NAMING its cause and the file.  Pure stdlib ``tomllib`` read; never
        touches the token.
        """
        import tomllib

        cfg = config_dir / "config.toml"
        try:
            raw = cfg.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg} is absent or unreadable ({exc})"
            ))
        try:
            data = tomllib.loads(raw)
        except ValueError as exc:
            # ValueError covers tomllib.TOMLDecodeError.
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg} is not valid TOML ({exc})"
            ))
        providers = data.get("model_providers")
        if not isinstance(providers, dict) or not providers:
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg} declares no "
                f"[model_providers.<id>] table"
            ))
        selected = data.get("model_provider")
        if isinstance(selected, str) and selected in providers:
            table = providers[selected]
        elif len(providers) == 1:
            table = next(iter(providers.values()))
        else:
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg} declares {len(providers)} "
                f"[model_providers.<id>] tables and no 'model_provider' "
                f"selecting one of them"
            ))
        if not isinstance(table, dict):
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg}: the selected model_providers "
                f"entry is not a table"
            ))
        base_url = table.get("base_url")
        env_key = table.get("env_key")
        if not isinstance(base_url, str) or not base_url:
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg}: the selected model_providers "
                f"table names no 'base_url'"
            ))
        if not isinstance(env_key, str) or not env_key:
            return PersonaReadOutcome(None, (
                f"codex persona config {cfg}: the selected model_providers "
                f"table names no 'env_key'"
            ))
        model = data.get("model")
        if not isinstance(model, str) or not model:
            model = None
        return PersonaReadOutcome(
            PersonaSettings(endpoint=base_url, model=model, auth_env=env_key),
            None,
        )

    def verify_persona(
        self,
        endpoint: str,
        token_path: Path | None,
        model: str | None,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 5.0,
    ) -> PersonaProbeOutcome:
        """Minimal OpenAI ``/responses`` ack against a persona endpoint.

        A genuine few-token completion on the RESPONSES wire — the only wire
        current codex speaks (``wire_api = "responses"``).  Per the base contract:
        2xx → ``PASS``, 401/403 → ``REJECTED``, unreachable/ambiguous →
        ``INCONCLUSIVE``.

        ⚑ *endpoint* is the provider ``base_url``, which by codex's convention
        ALREADY carries the ``/v1``-style prefix this appends ``/responses`` to —
        NOT an origin-only host like goose's ``OPENAI_HOST``.

        ⚑ **A PRESENT-null *token_path* (2026-08-17 ruling) is still PROBED, with
        the ``Authorization`` header OMITTED** — that persona declares the
        endpoint keyless, so the request goes out bare and the server decides.

        ⚑ **A persona that names no *model* is still PROBED, with the ``model``
        key OMITTED from the body** — an endpoint may serve exactly one model or
        apply its own, and declining to probe would let a DEAD token reach the
        box.  The answer is read through
        :func:`~kanibako.targets.base.probe_outcome_no_model` so a "model
        required" reply is silent rather than a permanent warning.

        ⚑ NEVER substitute a placeholder credential or model id to make either
        call go through: a hardwired-auth or hardwired-model server can REJECT one
        it does not serve, and a false ``REJECTED`` is a hard error that would
        refuse a working box.  (The codex LAUNCH gate is stricter than this probe
        — see the llm-doc — because this is also called straight off the store on
        the CREATE path.)

        ⚑ *env* is accepted and DELIBERATELY UNUSED.  It exists on the contract for a
        harness whose runtime rewrites the model from an env var before the wire
        (claude's ``ANTHROPIC_DEFAULT_<TIER>_MODEL``); codex names its model in
        ``config.toml`` and sends that id verbatim, so what is configured is already
        what goes out and there is nothing here to resolve.

        The one ``NOT_APPLICABLE`` decided here is a CONFIGURED (non-``None``)
        token file that is unreadable or empty.  The token is read transiently for
        this request only; never logged or persisted.
        """
        headers: dict[str, str] = {}
        if token_path is not None:
            try:
                token = token_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                return PersonaProbeOutcome.not_applicable(
                    f"the token file ({token_path}) could not be read"
                )
            if not token:
                return PersonaProbeOutcome.not_applicable(
                    f"the token file ({token_path}) is empty"
                )
            headers["Authorization"] = f"Bearer {token}"
        body: dict = {"input": "ping", "max_output_tokens": 16}
        if model:
            body["model"] = model
        sent = ProbeEvidence(endpoint=endpoint, model=model, token_path=token_path)
        response = http_probe(
            endpoint.rstrip("/") + "/responses",
            headers=headers,
            body=body,
            timeout=timeout,
        )
        if model:
            return probe_outcome(response, sent)
        return probe_outcome_no_model(response, sent)

    def generate_agent_config(self) -> AgentConfig:
        """Return default Codex agent configuration.

        ⚑ ``state`` is intentionally EMPTY (the FILE-PURITY invariant): the agent
        settings file holds USER INTENT only, and defaults come from the
        descriptor floor.  Seeding one into the file would pin every install ABOVE
        the floor, where a later change to the default can never reach it.
        """
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(
            name=self.display_name,
            state={},
        )

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare Codex runtime settings: ``model`` and the persona ``endpoint``.

        ⚑ ``endpoint`` is declared here ONLY to make it a first-class SETTABLE,
        cascade-resolved behavior key (``config set`` / ``--effective``): a codex
        endpoint is delivered via the ``~/.codex/config.toml``
        ``[model_providers.<id>]`` block, NOT an env var, so it is deliberately not
        a descriptor ``SettingArg``.

        The permission tier is NOT a setting descriptor either: it rides the
        uniform ``access`` key, persisted + cascade-resolved, default permissive;
        ``-A``/``-S`` override per launch.
        """
        return list(_CODEX_BEHAVIOR)
