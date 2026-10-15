"""Tests for CodexTarget (descriptor-native)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.plugins.codex import CodexTarget
from kanibako.settings.settings_resolve import UNSET
from kanibako.targets import assembly
from kanibako.targets.base import (
    BindKind,
    BindScope,
    Cadence,
    Channel,
    HostSrcOrigin,
    PluginDescriptor,
)

from conftest import make_vendored_tree


class TestProperties:
    def test_name(self):
        assert CodexTarget().name == "codex"

    def test_display_name(self):
        assert CodexTarget().display_name == "OpenAI Codex CLI"

    def test_config_dir_name_default(self):
        """name='codex' -> default config dir '.codex' (no override needed)."""
        assert CodexTarget().config_dir_name == ".codex"

    def test_default_entrypoint(self):
        assert CodexTarget().default_entrypoint == "codex"

    def test_credential_check_path_is_the_declared_cred_file(self, tmp_path: Path):
        """The agent report checks the file codex declares in ``cred_files``."""
        assert CodexTarget().credential_check_path(tmp_path) == tmp_path / ".codex" / "auth.json"

    def test_reattach_config_notice_warns_restart(self):
        """codex's config.toml is a reconciled projection re-materialized only on
        start, so a reattach-to-running notice tells the user to restart for
        config changes to apply (base default is None; codex overrides)."""
        notice = CodexTarget().reattach_config_notice()
        assert notice is not None
        assert "restart" in notice.lower()


class TestHasResumableSession:
    """codex decides continue-vs-fresh UP FRONT off its rollout store (the
    launch-time crash-and-retry net was removed).  ``continue`` = ``codex resume
    --last`` replays the newest recorded session; the store is
    ``<home>/.codex/sessions/<year>/<MM>/<DD>/rollout-*.jsonl`` (CODEX_HOME defaults
    to ~/.codex; kanibako sets none).  ``resume --last`` is workdir-agnostic, so the
    WHOLE store is checked.  Verified against openai/codex codex-rs/rollout/src.
    """

    def _sessions(self, home: Path) -> Path:
        d = home / ".codex" / "sessions"
        d.mkdir(parents=True)
        return d

    def test_false_when_sessions_dir_missing(self, tmp_path: Path):
        # Fresh box, no rollout store -> resume --last is doomed -> launch fresh.
        assert CodexTarget().has_resumable_session(tmp_path) is False

    def test_false_when_sessions_dir_empty(self, tmp_path: Path):
        # Dir exists (e.g. init) but no rollout recorded -> nothing to resume.
        self._sessions(tmp_path)
        assert CodexTarget().has_resumable_session(tmp_path) is False

    def test_true_when_dated_rollout_present(self, tmp_path: Path):
        # Real layout: sessions/<year>/<MM>/<DD>/rollout-<ts>-<uuid>.jsonl.
        d = self._sessions(tmp_path) / "2026" / "07" / "14"
        d.mkdir(parents=True)
        (d / "rollout-2026-07-14T10-00-00-abc.jsonl").write_text("{}\n")
        assert CodexTarget().has_resumable_session(tmp_path) is True

    def test_false_when_only_unrelated_files(self, tmp_path: Path):
        # Non-rollout files (no *.jsonl) -> nothing to resume.
        d = self._sessions(tmp_path)
        (d / "notes.txt").write_text("hi")
        assert CodexTarget().has_resumable_session(tmp_path) is False

    def test_false_on_oserror_launches_fresh(self, tmp_path: Path, monkeypatch):
        # Tolerant: a stat/glob error -> False (a fresh start is always safe).
        self._sessions(tmp_path)

        def _raise(self, *a, **k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "rglob", _raise)
        assert CodexTarget().has_resumable_session(tmp_path) is False


def _write_exe(path: Path, data: bytes) -> Path:
    """Write *data* to *path*, mark it executable, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755)
    return path


class TestDetect:
    """detect() prefers a standalone binary on PATH; npm is the LAST resort.

    Preference order (recorded host-binary principle): machine-code-compiled
    executable > self-contained/contained package (SEA) > runtime-dependent
    package managers (npm/pip), last.  On Linux all three collapse to the ELF
    discriminator — a directly-bindable ELF on PATH wins; a non-ELF Node shim
    falls through to the npm-vendored native binary.
    """

    def _patch_path_codex(self, monkeypatch, path: Path | None):
        """Mock `shutil.which('codex')` to return *path* (or None = absent)."""
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(
            codex_mod.shutil, "which",
            lambda name: (str(path) if path is not None else None) if name == "codex" else None,
        )

    def _patch_npm_root(self, monkeypatch, root: Path | None):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod, "_npm_root_global", lambda: root)

    def _force_linux_x64(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(
            codex_mod, "_platform_pkg_and_triple",
            lambda: ("codex-linux-x64", "x86_64-unknown-linux-musl"),
        )

    # --- PRIMARY: standalone executable on PATH -------------------------------

    def test_primary_standalone_elf_on_path(self, tmp_path: Path, monkeypatch):
        """PATH `codex` -> a real-looking ELF -> bind THAT file directly.

        npm is also wired up (and would resolve), proving the ELF wins.
        """
        standalone = _write_exe(tmp_path / "bin" / "codex", b"\x7fELF native codex\n")
        self._patch_path_codex(monkeypatch, standalone)
        # npm path is available too -> must NOT be used (standalone wins).
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        make_vendored_tree(npm_root, nested=False)
        self._force_linux_x64(monkeypatch)
        self._patch_npm_root(monkeypatch, npm_root)

        result = CodexTarget().detect()
        assert result is not None
        assert result.name == "codex"
        assert result.binary == standalone
        assert result.install_dir == standalone.parent
        assert result.launcher is None

    def test_primary_symlinked_standalone_resolves_to_real_target(self, tmp_path: Path, monkeypatch):
        """A symlinked standalone ELF resolves (Path.resolve) to its real target."""
        real = _write_exe(tmp_path / "opt" / "codex-0.140" / "codex", b"\x7fELF native codex\n")
        link = tmp_path / "bin" / "codex"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real)
        self._patch_path_codex(monkeypatch, link)
        # No npm fallback available -> only the PATH-resolved real binary can win.
        self._patch_npm_root(monkeypatch, None)

        result = CodexTarget().detect()
        assert result is not None
        assert result.binary == real.resolve()
        assert result.install_dir == real.resolve().parent

    # --- FALLBACK: npm-vendored native binary --------------------------------

    def test_fallback_path_codex_is_node_shim(self, tmp_path: Path, monkeypatch):
        """PATH `codex` -> a `#!node` JS shim (non-ELF) -> fall back to npm vendored."""
        shim = _write_exe(tmp_path / "bin" / "codex", b"#!/usr/bin/env node\nrequire('./codex.js')\n")
        self._patch_path_codex(monkeypatch, shim)
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        binary = make_vendored_tree(npm_root, nested=False)
        self._force_linux_x64(monkeypatch)
        self._patch_npm_root(monkeypatch, npm_root)

        result = CodexTarget().detect()
        assert result is not None
        assert result.binary == binary  # the vendored native ELF, not the shim
        assert result.install_dir == binary.parent

    def test_fallback_no_path_codex(self, tmp_path: Path, monkeypatch):
        """PATH `codex` absent -> npm fallback resolves the vendored binary."""
        self._patch_path_codex(monkeypatch, None)
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        binary = make_vendored_tree(npm_root, nested=False)
        self._force_linux_x64(monkeypatch)
        self._patch_npm_root(monkeypatch, npm_root)

        result = CodexTarget().detect()
        assert result is not None
        assert result.binary == binary

    def test_fallback_found_nested(self, tmp_path: Path, monkeypatch):
        """Nested layout: <root>/@openai/codex/node_modules/@openai/codex-...."""
        self._patch_path_codex(monkeypatch, None)
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        binary = make_vendored_tree(npm_root, nested=True)
        self._force_linux_x64(monkeypatch)
        self._patch_npm_root(monkeypatch, npm_root)

        result = CodexTarget().detect()
        assert result is not None
        assert result.binary == binary

    def test_fallback_found_via_glob(self, tmp_path: Path, monkeypatch):
        """Unrecognized platform map -> glob fallback still finds the binary."""
        self._patch_path_codex(monkeypatch, None)
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        # Materialize an arbitrary-triple vendored binary the direct map misses.
        binary = make_vendored_tree(
            npm_root, suffix="codex-linux-arm64",
            triple="aarch64-unknown-linux-musl", nested=False,
        )
        import kanibako.plugins.codex.target as codex_mod
        # Force the direct map to return None so only the glob can resolve it.
        monkeypatch.setattr(codex_mod, "_platform_pkg_and_triple", lambda: None)
        self._patch_npm_root(monkeypatch, npm_root)

        result = CodexTarget().detect()
        assert result is not None
        assert result.binary == binary

    # --- Neither path resolves ------------------------------------------------

    def test_not_found_no_path_no_npm_root(self, monkeypatch):
        """No standalone on PATH and no npm global root -> None, never crashes."""
        self._patch_path_codex(monkeypatch, None)
        self._patch_npm_root(monkeypatch, None)
        assert CodexTarget().detect() is None

    def test_not_found_shim_and_no_vendored_binary(self, tmp_path: Path, monkeypatch):
        """PATH shim (non-ELF) + npm root present but no codex package -> None."""
        shim = _write_exe(tmp_path / "bin" / "codex", b"#!/usr/bin/env node\n")
        self._patch_path_codex(monkeypatch, shim)
        npm_root = tmp_path / "node_modules"
        npm_root.mkdir()
        self._force_linux_x64(monkeypatch)
        self._patch_npm_root(monkeypatch, npm_root)
        assert CodexTarget().detect() is None


class TestIsElf:
    """_is_elf discriminates a bindable native/SEA executable from a Node shim."""

    def test_true_for_elf_magic(self, tmp_path: Path):
        import kanibako.plugins.codex.target as codex_mod
        p = _write_exe(tmp_path / "codex", b"\x7fELF\x02\x01\x01\x00rest")
        assert codex_mod._is_elf(p) is True

    def test_false_for_node_shim(self, tmp_path: Path):
        import kanibako.plugins.codex.target as codex_mod
        p = _write_exe(tmp_path / "codex", b"#!/usr/bin/env node\n")
        assert codex_mod._is_elf(p) is False

    def test_false_for_missing_path(self, tmp_path: Path):
        import kanibako.plugins.codex.target as codex_mod
        assert codex_mod._is_elf(tmp_path / "nope") is False

    def test_false_for_directory(self, tmp_path: Path):
        import kanibako.plugins.codex.target as codex_mod
        assert codex_mod._is_elf(tmp_path) is False

    def test_false_for_short_file(self, tmp_path: Path):
        import kanibako.plugins.codex.target as codex_mod
        p = _write_exe(tmp_path / "codex", b"\x7fEL")  # only 3 bytes
        assert codex_mod._is_elf(p) is False


class TestResolvePathExecutable:
    """_resolve_path_executable follows symlinks and tolerates absence."""

    def test_none_when_absent(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod.shutil, "which", lambda name: None)
        assert codex_mod._resolve_path_executable() is None

    def test_resolves_symlink_to_real_target(self, tmp_path: Path, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        real = _write_exe(tmp_path / "real" / "codex", b"\x7fELF")
        link = tmp_path / "bin" / "codex"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real)
        monkeypatch.setattr(codex_mod.shutil, "which", lambda name: str(link))
        assert codex_mod._resolve_path_executable() == real.resolve()


class TestNpmRootGlobal:
    """_npm_root_global tolerates every npm failure mode."""

    def test_success(self, tmp_path: Path, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod

        class _R:
            returncode = 0
            stdout = str(tmp_path) + "\n"

        monkeypatch.setattr(codex_mod.subprocess, "run", lambda *a, **k: _R())
        assert codex_mod._npm_root_global() == tmp_path

    def test_npm_missing(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod

        def _boom(*a, **k):
            raise FileNotFoundError("npm")

        monkeypatch.setattr(codex_mod.subprocess, "run", _boom)
        assert codex_mod._npm_root_global() is None

    def test_nonzero(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod

        class _R:
            returncode = 1
            stdout = "/whatever\n"

        monkeypatch.setattr(codex_mod.subprocess, "run", lambda *a, **k: _R())
        assert codex_mod._npm_root_global() is None

    def test_empty_output(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod

        class _R:
            returncode = 0
            stdout = "\n"

        monkeypatch.setattr(codex_mod.subprocess, "run", lambda *a, **k: _R())
        assert codex_mod._npm_root_global() is None

    def test_nonexistent_dir(self, tmp_path: Path, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod

        class _R:
            returncode = 0
            stdout = str(tmp_path / "nope") + "\n"

        monkeypatch.setattr(codex_mod.subprocess, "run", lambda *a, **k: _R())
        assert codex_mod._npm_root_global() is None


class TestPlatformMap:
    def test_linux_x64(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(codex_mod.platform, "machine", lambda: "x86_64")
        assert codex_mod._platform_pkg_and_triple() == (
            "codex-linux-x64", "x86_64-unknown-linux-musl",
        )

    def test_linux_arm64(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(codex_mod.platform, "machine", lambda: "aarch64")
        assert codex_mod._platform_pkg_and_triple() == (
            "codex-linux-arm64", "aarch64-unknown-linux-musl",
        )

    def test_darwin_arm64(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(codex_mod.platform, "machine", lambda: "arm64")
        assert codex_mod._platform_pkg_and_triple() == (
            "codex-darwin-arm64", "aarch64-apple-darwin",
        )

    def test_unknown_returns_none(self, monkeypatch):
        import kanibako.plugins.codex.target as codex_mod
        monkeypatch.setattr(codex_mod.platform, "system", lambda: "Plan9")
        monkeypatch.setattr(codex_mod.platform, "machine", lambda: "weird")
        assert codex_mod._platform_pkg_and_triple() is None


class TestCheckAuth:
    def test_true_when_auth_json_present(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (fake_host / ".codex" / "auth.json").write_text('{"api_key": "x"}')
        assert CodexTarget().check_auth() is True

    def test_false_when_auth_json_empty_and_no_env(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (fake_host / ".codex" / "auth.json").write_text("")  # empty
        assert CodexTarget().check_auth() is False

    def test_false_when_absent_and_no_env(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert CodexTarget().check_auth() is False

    def test_true_when_openai_api_key_set(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # No auth.json file at all.
        assert CodexTarget().check_auth() is True

    def test_box_keywords_are_ignored(self, fake_host: Path, monkeypatch, tmp_path: Path):
        """A box store and a delivered key do not pass a failing host check."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        box = tmp_path / "box"
        (box / ".codex").mkdir(parents=True)
        (box / ".codex" / "auth.json").write_text('{"api_key": "x"}')
        target = CodexTarget()
        assert target.check_auth(
            box_home=box, delivered=frozenset({"OPENAI_API_KEY"}),
        ) is False


class TestSetupCommand:
    """In-box setup command (``codex login``) declared via the base hooks."""

    def test_setup_entrypoint_is_codex_login(self):
        t = CodexTarget()
        assert t.setup_entrypoint == "codex"
        assert t.setup_args == ["login"]


class TestShouldRunSetup:
    """Post-launch matcher: launch logs prove the login did NOT take."""

    def test_true_on_not_logged_in(self):
        assert CodexTarget().should_run_setup("Error: Not logged in.")

    def test_true_on_login_hint(self):
        assert CodexTarget().should_run_setup("Please run 'codex login' first.")

    def test_true_on_authentication_failed(self):
        assert CodexTarget().should_run_setup("Authentication failed (401).")

    def test_true_on_401_unauthorized(self):
        assert CodexTarget().should_run_setup("HTTP 401 Unauthorized")

    def test_case_insensitive(self):
        assert CodexTarget().should_run_setup("NOT LOGGED IN")

    def test_false_on_unrelated_output(self):
        assert not CodexTarget().should_run_setup("codex session ready")

    def test_false_on_empty_output(self):
        assert not CodexTarget().should_run_setup("")


class TestGenerateAgentConfig:
    def test_defaults(self):
        # FILE PURITY: the generated agent settings file carries USER INTENT
        # only, so state is EMPTY — ``model`` comes from the descriptor floor
        # (setting_descriptors -> EMPTY, i.e. codex's own built-in default),
        # never from a seed.
        config = CodexTarget().generate_agent_config()
        assert config.state == {}

    def test_the_description_is_a_DECLARED_KEY_not_a_file_field(self):
        """D8b: ``AgentConfig`` has no ``name``; the description is ``agent.codex.label``.

        ⚑ NON-VACUITY: the string is the one spec §2d states, so a plugin that stopped
        declaring it would read the all-agents backstop, the agent's own name.
        """
        import dataclasses

        from kanibako.settings.agent_config import AgentConfig

        assert "name" not in {f.name for f in dataclasses.fields(AgentConfig)}
        label = next(
            d for d in CodexTarget().setting_descriptors() if d.key == "label"
        )
        assert label.default == "Codex CLI"

    def test_model_default_comes_from_the_descriptor_floor(self):
        # ... and the plugin floor sets none: spec §2d ``agent.codex.model`` has no
        # plugin default and inherits ``agent.default.model`` (<None> = codex's
        # built-in default).  The ALL-AGENTS form of this rule is pinned in
        # ``tests/test_targets/test_agent_behavior_defaults.py``.
        t = CodexTarget()
        assert "model" not in t.generate_agent_config().state
        model = next(d for d in t.setting_descriptors() if d.key == "model")
        assert model.default is UNSET


class TestSettingDescriptors:
    def test_model_and_endpoint(self):
        settings = CodexTarget().setting_descriptors()
        keys = [s.key for s in settings]
        # model + endpoint (persona: the alternate model-provider base-URL, a
        # first-class settable/cascade-resolved key; delivered via config.toml, not
        # an env — see the descriptor persona.endpoint_delivery: config_file).
        # ⚑ ``label`` LEADS THE TABLE (D8b): the agent's description is a declared §2d
        # key now, and the file order is the order ``config`` lists the agent's settings in.
        assert keys == ["label", "model", "endpoint"]
        endpoint = next(s for s in settings if s.key == "endpoint")
        assert endpoint.default is UNSET
        # ``access`` (R-41's permission TIER) is NOT a declared TargetSetting —
        # it is the agent-scope enum key routed verbatim
        # (access_realization.setting_key), redeemed + validated at launch.  The retired
        # ``auto_approve`` spelling must not reappear here either.
        assert "access" not in keys
        assert "auto_approve" not in keys

    def test_persona_wiring_declared(self):
        # INC 2: codex declares config-file endpoint delivery + a dynamic token var
        # (empty → the configured secret_path key doubles as the provider env_key).
        spec = CodexTarget().descriptor.persona
        assert spec is not None
        assert spec.endpoint_delivery == "config_file"
        assert spec.token_var == ""
        assert spec.wire_api == "responses"
        # No model veto: an unset model launches with none, and codex uses its own
        # default.
        assert not hasattr(spec, "model_required")


class TestDefaultShares:
    """Part 3b: the resource_mappings abstraction was deleted (all PROJECT —
    those dirs live in the box home bind).  codex declares no agent chares."""

    def test_no_default_shares(self):
        assert CodexTarget().default_common() == {}


class TestDescriptor:
    """The declarative PluginDescriptor that puts codex on the generic launch path."""

    def test_is_plugin_descriptor(self):
        assert isinstance(CodexTarget().descriptor, PluginDescriptor)

    def test_command(self):
        assert CodexTarget().descriptor.command == ("codex",)

    def test_vscode_extension_id(self):
        # FF-4: the official OpenAI "Codex" extension id is `openai.chatgpt`
        # (NOT `openai.codex`); auto-installed on `kanibako code` attach.
        assert CodexTarget().descriptor.vscode_extension == "openai.chatgpt"

    def test_binary_binding(self):
        d = CodexTarget().descriptor
        bindings = {b.key: b for b in d.bindings}
        # ``managed_pointer`` is the instruction-delivery kickoff-loader SEED
        # (delivered RO to ~/.config/kanibako/kickoff.md) added alongside the
        # binary bind.
        assert set(bindings) == {"binary", "managed_pointer"}
        binary = bindings["binary"]
        assert binary.origin == HostSrcOrigin.BINARY
        assert binary.box_dest == "/home/agent/.local/bin/codex"
        assert binary.kind == BindKind.FILE
        assert binary.scope == BindScope.AGENT_CRITICAL
        assert binary.ro is True

    def test_mode(self):
        """codex 0.140.0: bare new session / `resume --last` continue."""
        d = CodexTarget().descriptor
        assert d.mode["start"] == ()
        assert d.mode["continue"] == ("resume", "--last")
        # No dedicated resume PICKER mode.
        assert "resume" not in d.mode

    def test_operations_exec(self):
        d = CodexTarget().descriptor
        assert "exec" in d.operations
        assert d.operations["exec"].fragment == ("exec",)

    def test_access_realization_access_rows(self):
        """The three codex rows (R-41), verified against codex-cli 0.158.0.

        ⚑ The approval half rides ``-c approval_policy=...``, never ``-a``:
        ``codex exec`` rejects ``-a`` and shares this argv tail, while every
        path accepts ``-c`` (its value is TOML, hence the quotes).
        """
        ar = CodexTarget().descriptor.access_realization
        assert ar is not None
        assert ar.channel == Channel.FLAG
        assert ar.env_var == ""
        assert ar.full.flag == ("--dangerously-bypass-approvals-and-sandbox",)
        assert ar.editing.flag == (
            "-c", 'approval_policy="on-request"', "-s", "workspace-write",
        )
        assert ar.restricted.flag == (
            "-c", 'approval_policy="on-request"', "-s", "read-only",
        )
        for tier in ("restricted", "editing", "full"):
            assert "-a" not in ar.row(tier).flag
            assert "--ask-for-approval" not in ar.row(tier).flag
        assert ar.rendered_tiers() == ("restricted", "editing", "full")
        # Never emitted (D-7) — and NOT because they are absent or undocumented.
        # Verified on 0.141.0 (2026-08-02): neither is listed in ``codex --help``
        # or ``codex exec --help``, yet BOTH parse (``--yolo`` top-level,
        # ``--full-auto`` on ``exec``), and OpenAI's docs reference ``--yolo`` in
        # ordinary prose usage. We emit
        # ``--dangerously-bypass-approvals-and-sandbox`` because it is the
        # explicit, self-describing spelling that IS in ``--help`` with an
        # unambiguous description — the emitted argv says what it does.
        for tier in ("restricted", "editing", "full"):
            assert "--full-auto" not in ar.row(tier).flag
            assert "--yolo" not in ar.row(tier).flag
        # codex persists the tier uniformly ([Jei:2026-06-27] collapse decision carried
        # over to ``access``): redeemed via setting_key="access".
        assert ar.setting_key == "access"

    def test_settings_model_flag(self):
        d = CodexTarget().descriptor
        settings = {s.setting_key: s for s in d.settings}
        assert set(settings) == {"model"}
        assert settings["model"].channel == Channel.FLAG
        assert settings["model"].flag == ("--model",)

    def test_declared_env_is_the_directive_final_slot(self):
        # codex declares ONE variable: the instruction-delivery FINAL slot the
        # box-start flattener writes codex's flattened per-agent guide to (codex
        # reads ~/.codex/AGENTS.md natively).  It is a DECLARED KEY
        # (agent.codex.env.<VAR>, spec §2d) reaching the box through the settings
        # channel, so a user can override it by name; {$GUEST_HOME} is expanded by
        # the loader.
        assert CodexTarget().default_envs() == {
            "agent.codex.env.KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.codex/AGENTS.md",
        }

    def test_cred_files(self):
        # The host config.toml IMPORT (SEED_ONCE) was removed in 1.6.0; only the
        # synced auth.json remains.
        d = CodexTarget().descriptor
        specs = {s.home_rel: s for s in d.cred_files}
        assert set(specs) == {".codex/auth.json"}

        auth = specs[".codex/auth.json"]
        assert auth.host_rel == ".codex/auth.json"
        assert auth.cadence == Cadence.SYNC
        assert auth.mtime_gate is True
        assert auth.filtered is False

    def test_host_prep_and_init_dirs(self):
        d = CodexTarget().descriptor
        assert d.host_prep is False
        assert d.init_dirs == (".codex",)


class TestDescriptorAssembly:
    """Integration: codex's argv/env assembled from the descriptor via assembly.*."""

    def _argv(self, *, resume_mode=False, access="full", state=None, extra_args=None, op=None):
        d = CodexTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=resume_mode,
            new_session=False,
            is_new_project=False,
            extra_args=extra_args or [],
            available_modes=d.mode.keys(),
        )
        # B5: fragments are passed in (the live caller reads them off the
        # launch snapshot's meta.agent.<a>.{mode,exec}); the descriptor is the
        # fixture's fragment source here.
        return assembly.assemble_argv(
            d,
            mode_fragment=d.mode[mode_key],
            access=access,
            setting_values=state or {},
            op_fragment=d.operations[op].fragment if op is not None else None,
            extra_args=extra_args or [],
        )

    def test_default_argv_is_continue(self):
        """Default launch resolves to continue-last -> ['resume', '--last'].

        The ``restricted`` tier, so the bypass flag does not appear.
        """
        argv = self._argv(access="restricted")
        assert argv == ["resume", "--last", "-c", 'approval_policy="on-request"', "-s", "read-only"]

    def test_start_mode_is_empty(self):
        d = CodexTarget().descriptor
        assert d.mode["start"] == ()
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="restricted",
            setting_values={}, op_fragment=None, extra_args=[],
        )
        assert argv == ["-c", 'approval_policy="on-request"', "-s", "read-only"]

    def test_new_session_forced_is_start(self):
        d = CodexTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=False, new_session=True, is_new_project=False,
            extra_args=[], available_modes=d.mode.keys(),
        )
        assert mode_key == "start"
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode[mode_key], access="restricted",
            setting_values={}, op_fragment=None, extra_args=[],
        )
        assert argv == ["-c", 'approval_policy="on-request"', "-s", "read-only"]

    def test_full_tier_adds_bypass_flag(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="full",
            setting_values={}, op_fragment=None, extra_args=[],
        )
        assert argv == ["--dangerously-bypass-approvals-and-sandbox"]

    def test_editing_tier_uses_the_sandbox_middle_step(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="editing",
            setting_values={}, op_fragment=None, extra_args=[],
        )
        assert argv == [
            "-c", 'approval_policy="on-request"', "-s", "workspace-write",
        ]
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv

    def test_editing_tier_survives_the_exec_path(self):
        """⚑ The exec constraint, pinned: `codex exec` REJECTS `-a`, so the
        approval half rides `-c`.  This asserts the op path (which shares the
        same tail) emits nothing codex exec would reject."""
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=None, access="editing", setting_values={},
            op_fragment=d.operations["exec"].fragment, extra_args=["do it"],
        )
        assert argv == [
            "exec", "-c", 'approval_policy="on-request"', "-s", "workspace-write",
            "do it",
        ]
        assert "-a" not in argv and "--ask-for-approval" not in argv

    def test_restricted_tier_no_bypass_flag(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="restricted",
            setting_values={}, op_fragment=None, extra_args=[],
        )
        assert argv == ["-c", 'approval_policy="on-request"', "-s", "read-only"]
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv

    _TIER_FLAGS = {
        "restricted": ["-c", 'approval_policy="on-request"', "-s", "read-only"],
        "editing": ["-c", 'approval_policy="on-request"', "-s", "workspace-write"],
        "full": ["--dangerously-bypass-approvals-and-sandbox"],
    }

    @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
    @pytest.mark.parametrize("path", ["interactive", "resume", "exec"])
    def test_each_tier_reaches_every_path(self, tier, path):
        """Every tier × every launch path: the row follows the path's fragment,
        and no path ever carries ``-a`` (``codex exec`` rejects it)."""
        d = CodexTarget().descriptor
        mode, op = {
            "interactive": (d.mode["start"], None),
            "resume": (d.mode["continue"], None),
            "exec": (None, d.operations["exec"].fragment),
        }[path]
        argv = assembly.assemble_argv(
            d, mode_fragment=mode, access=tier, setting_values={},
            op_fragment=op, extra_args=["go"] if path == "exec" else [],
        )
        head = {"interactive": [], "resume": ["resume", "--last"], "exec": ["exec"]}[path]
        tail = ["go"] if path == "exec" else []
        assert argv == head + self._TIER_FLAGS[tier] + tail
        assert "-a" not in argv and "--ask-for-approval" not in argv

    def test_model_flag_from_settings(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="restricted",
            setting_values={"model": "some-model-id"}, op_fragment=None, extra_args=[],
        )
        assert argv == [
            "-c", 'approval_policy="on-request"', "-s", "read-only",
            "--model", "some-model-id",
        ]

    def test_exec_op_argv(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_fragment=d.mode["start"], access="restricted",
            setting_values={}, op_fragment=d.operations["exec"].fragment, extra_args=["do the thing"],
        )
        assert argv == ["exec", "-c", 'approval_policy="on-request"', "-s", "read-only", "do the thing"]

    def test_the_assembly_emits_nothing_for_codex(self):
        d = CodexTarget().descriptor
        env = assembly.assemble_env(d, access="full", setting_values={"model": "gpt-5.5"})
        # codex realizes NOTHING on the ENV channel: model is a FLAG (argv) and so
        # is the bypass.  Its one variable — the instruction-delivery FINAL slot —
        # is a DECLARED KEY carried by the settings channel, not assembled here.
        assert env == {}
        assert CodexTarget().default_envs() == {
            "agent.codex.env.KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.codex/AGENTS.md",
        }


class TestDeliverySeams:
    """T1 seams: CodexTarget's panel-permission (sandbox invariant) and
    directive-hook (managed config.toml, hook/trust/provider only) deliveries.
    Region/trust content is proven at the emitter level in
    ``tests/test_commands/test_code_config.py``; here we pin the DISPATCH: the
    right file, the right split (no key with two writers), the GUEST_HOME-derived
    trust literals, and the provider threading."""

    def _config(self, config_root: Path) -> Path:
        return config_root / ".codex" / "config.toml"

    def _provider(self):
        from kanibako.vscode.vscode_config import CodexModelProvider
        return CodexModelProvider(
            provider_id="navigator", name="navigator",
            base_url="https://api.example/v1", wire_api="chat",
            env_key="NAVIGATOR_API_KEY", model="gemma-4-31b-it",
        )

    @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
    def test_panel_permissions_writes_only_the_sandbox_invariant(self, tmp_path, tier):
        """Every tier writes the same file: ``sandbox_mode`` (the box invariant)
        and NO ``approval_policy`` — the tier reaches the CLI as ``-c`` instead."""
        assert CodexTarget().deliver_panel_permissions(
            config_root=tmp_path, access=tier,
        ) is True
        import tomllib
        data = tomllib.loads(self._config(tmp_path).read_text())
        assert data == {"sandbox_mode": "danger-full-access"}

    @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
    @pytest.mark.parametrize("policy", ["on-request", "never", "untrusted"])
    def test_panel_permissions_keeps_a_user_approval_byte_identical(
        self, tmp_path, tier, policy,
    ):
        """A user's own ``approval_policy`` survives a start byte-identical at
        every tier, including a value kanibako used to write."""
        cfg = self._config(tmp_path)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        before = (
            f'sandbox_mode = "danger-full-access"\napproval_policy = "{policy}"\n'
        ).encode()
        cfg.write_bytes(before)
        assert CodexTarget().deliver_panel_permissions(
            config_root=tmp_path, access=tier,
        ) is False
        assert cfg.read_bytes() == before

    def test_panel_permissions_forces_the_sandbox_and_keeps_the_approval_line(
        self, tmp_path,
    ):
        """A user ``sandbox_mode`` is overwritten (box invariant); the user's
        ``approval_policy`` line is untouched."""
        cfg = self._config(tmp_path)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('sandbox_mode = "read-only"\napproval_policy = "never"\n')
        assert CodexTarget().deliver_panel_permissions(
            config_root=tmp_path, access="restricted",
        ) is True
        assert cfg.read_text() == (
            'sandbox_mode = "danger-full-access"\napproval_policy = "never"\n'
        )

    @pytest.mark.parametrize("where", ["leaf", "parent"])
    @pytest.mark.parametrize("to", ["outside", "inside"])
    def test_panel_permissions_never_follows_a_symlink(self, tmp_path, where, to, caplog):
        """No-follow patch: a box ``config.toml`` (leaf) or ``.codex`` (parent) that is
        a symlink -- out of the box or inside it -- is skipped with a warning naming
        the path; the file it names is unchanged."""
        import logging
        root = tmp_path / "home"
        root.mkdir()
        held = tmp_path / "outside" if to == "outside" else root / "elsewhere"
        held.mkdir()
        sentinel = held / "config.toml"
        sentinel.write_text('sandbox_mode = "read-only"\n')
        before = sentinel.read_bytes()
        cfg = self._config(root)
        if where == "leaf":
            cfg.parent.mkdir()
            cfg.symlink_to(sentinel)
        else:
            cfg.parent.symlink_to(held)
        with caplog.at_level(logging.WARNING):
            assert CodexTarget().deliver_panel_permissions(
                config_root=root, access="full",
            ) is False
        assert sentinel.read_bytes() == before
        assert str(cfg) in caplog.text and "symlink" in caplog.text

    def test_directive_hook_writes_hook_trust_never_approval(self, tmp_path):
        import tomllib
        from kanibako.settings.settings_resolve import GUEST_HOME
        assert CodexTarget().deliver_directive_hook(
            config_root=tmp_path, access="full",
        ) is True
        data = tomllib.loads(self._config(tmp_path).read_text())
        # hook + GUEST_HOME-derived trust literals (directive group 0, the four
        # charter start scripts, then one per PostToolUse/Stop group):
        assert data["hooks"]["SessionStart"]
        cfg = f"{GUEST_HOME}/.codex/config.toml"
        state_keys = list(data["hooks"]["state"])
        assert state_keys == [
            *(f"{cfg}:session_start:{i}:0" for i in range(5)),
            f"{cfg}:post_tool_use:0:0",
            f"{cfg}:post_tool_use:1:0",
            f"{cfg}:stop:0:0",
        ]
        assert (
            data["projects"][f"{GUEST_HOME}/workspace"]["trust_level"] == "trusted"
        )
        # the split: the directive write carries neither root key; sandbox_mode
        # belongs solely to deliver_panel_permissions.
        assert "approval_policy" not in data
        assert "sandbox_mode" not in data

    def test_directive_hook_threads_model_provider(self, tmp_path):
        import tomllib
        assert CodexTarget().deliver_directive_hook(
            config_root=tmp_path, access="restricted",
            model_provider=self._provider(),
        ) is True
        data = tomllib.loads(self._config(tmp_path).read_text())
        assert data["model_provider"] == "navigator"
        assert data["model"] == "gemma-4-31b-it"
        assert data["model_providers"]["navigator"]["env_key"] == (
            "NAVIGATOR_API_KEY"
        )

    def test_seam_composition_is_stable(self, tmp_path):
        """Panel then directive (core call order), twice: second launch is a
        byte-level no-op — no writer fights the other."""
        t = CodexTarget()
        t.deliver_panel_permissions(config_root=tmp_path, access="full")
        t.deliver_directive_hook(config_root=tmp_path, access="full")
        before = self._config(tmp_path).read_bytes()
        assert t.deliver_panel_permissions(
            config_root=tmp_path, access="full",
        ) is False
        assert t.deliver_directive_hook(
            config_root=tmp_path, access="full",
        ) is False
        assert self._config(tmp_path).read_bytes() == before

    def test_directive_hook_delivers_the_canon_hooks(self, tmp_path):
        """The directive write carries claude's canon hooks: the directive group, the
        four charter start scripts (each cascading to ``pid-add.sh``), the mail
        monitor and ``edited.sh``, and ``stop.sh`` — one trust entry each, keyed on
        the BOX config path.  NO SessionEnd: codex fires it per thread in one shared
        PID, so ``end.sh``'s pid-rm would clear a live panel's marker."""
        import tomllib
        from kanibako.settings.settings_resolve import GUEST_HOME
        from kanibako.vscode.vscode_config import _CODEX_MANAGED_HOOKS
        assert CodexTarget().deliver_directive_hook(
            config_root=tmp_path, access="restricted",
        ) is True
        data = tomllib.loads(self._config(tmp_path).read_text())
        commands = [
            g["hooks"][0]["command"]
            for event in ("SessionStart", "PostToolUse", "Stop")
            for g in data["hooks"][event]
        ]
        assert commands == [hook.command for hook in _CODEX_MANAGED_HOOKS]
        assert "SessionEnd" not in data["hooks"]
        cfg = f"{GUEST_HOME}/.codex/config.toml"
        assert set(data["hooks"]["state"]) == {
            *(f"{cfg}:session_start:{i}:0" for i in range(5)),
            f"{cfg}:post_tool_use:0:0",
            f"{cfg}:post_tool_use:1:0",
            f"{cfg}:stop:0:0",
        }
