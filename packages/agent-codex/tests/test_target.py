"""Tests for CodexTarget (descriptor-native)."""

from __future__ import annotations

from pathlib import Path

from kanibako.plugins.codex import CodexTarget
from kanibako.targets import assembly
from kanibako.targets.base import (
    BindKind,
    BindScope,
    Cadence,
    Channel,
    HostSrcOrigin,
    PluginDescriptor,
    ResourceScope,
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


class TestGenerateCrabConfig:
    def test_defaults(self):
        config = CodexTarget().generate_crab_config()
        assert config.name == "OpenAI Codex CLI"
        assert config.shell == "standard"
        assert config.state["model"] == "gpt-5.5"


class TestSettingDescriptors:
    def test_only_model(self):
        settings = CodexTarget().setting_descriptors()
        keys = [s.key for s in settings]
        assert keys == ["model"]
        # No 'access' setting (codex has no persisted safe-bypass default).
        assert "access" not in keys


class TestResourceMappings:
    def test_project_scope_anchored_to_codex(self):
        mappings = CodexTarget().resource_mappings()
        assert len(mappings) >= 1
        for m in mappings:
            assert m.scope == ResourceScope.PROJECT
            assert m.base == ".codex"
        paths = {m.path for m in mappings}
        assert "sessions" in paths


class TestDescriptor:
    """The declarative PluginDescriptor that puts codex on the generic launch path."""

    def test_is_plugin_descriptor(self):
        assert isinstance(CodexTarget().descriptor, PluginDescriptor)

    def test_command(self):
        assert CodexTarget().descriptor.command == ("codex",)

    def test_binary_binding(self):
        d = CodexTarget().descriptor
        bindings = {b.key: b for b in d.bindings}
        assert set(bindings) == {"binary"}
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

    def test_safe_bypass_flag(self):
        sb = CodexTarget().descriptor.safe_bypass
        assert sb is not None
        assert sb.channel == Channel.FLAG
        assert sb.flag == ("--dangerously-bypass-approvals-and-sandbox",)
        assert sb.env_var == ""
        assert sb.setting_key == ""

    def test_settings_model_flag(self):
        d = CodexTarget().descriptor
        settings = {s.setting_key: s for s in d.settings}
        assert set(settings) == {"model"}
        assert settings["model"].channel == Channel.FLAG
        assert settings["model"].flag == ("--model",)

    def test_container_env_empty(self):
        assert CodexTarget().descriptor.container_env == {}

    def test_cred_files(self):
        d = CodexTarget().descriptor
        specs = {s.home_rel: s for s in d.cred_files}
        assert set(specs) == {".codex/auth.json", ".codex/config.toml"}

        auth = specs[".codex/auth.json"]
        assert auth.host_rel == ".codex/auth.json"
        assert auth.cadence == Cadence.SYNC
        assert auth.mtime_gate is True
        assert auth.filtered is False

        config = specs[".codex/config.toml"]
        assert config.host_rel == ".codex/config.toml"
        assert config.cadence == Cadence.SEED_ONCE
        assert config.filtered is False

    def test_host_prep_and_init_dirs(self):
        d = CodexTarget().descriptor
        assert d.host_prep is False
        assert d.init_dirs == (".codex",)


class TestInheritedDefaults:
    """codex implements NO legacy hooks -> step-3a concrete defaults apply."""

    def test_binary_mounts_default_empty(self):
        from kanibako.targets.base import AgentInstall
        install = AgentInstall(name="codex", binary=Path("/x"), install_dir=Path("/"))
        assert CodexTarget().binary_mounts(install) == []

    def test_build_cli_args_passthrough(self):
        out = CodexTarget().build_cli_args(
            safe_mode=True, resume_mode=False, new_session=False,
            is_new_project=False, extra_args=["--foo"],
        )
        assert out == ["--foo"]

    def test_init_home_noop(self, project_home: Path):
        # Inherited no-op: must not raise.
        assert CodexTarget().init_home(project_home) is None

    def test_refresh_writeback_noop(self, project_home: Path):
        assert CodexTarget().refresh_credentials(project_home) is None
        assert CodexTarget().writeback_credentials(project_home) is None


class TestDescriptorAssembly:
    """Integration: codex's argv/env assembled from the descriptor via assembly.*."""

    def _argv(self, *, resume_mode=False, safe_off=True, state=None, extra_args=None, op=None):
        d = CodexTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=resume_mode,
            new_session=False,
            is_new_project=False,
            extra_args=extra_args or [],
            available_modes=d.mode.keys(),
        )
        return assembly.assemble_argv(
            d,
            mode_key=mode_key,
            safe_mode_off=safe_off,
            setting_values=state or {},
            op=op,
            extra_args=extra_args or [],
        )

    def test_default_argv_is_continue(self):
        """Default launch resolves to continue-last -> ['resume', '--last'].

        safe_off=False so the bypass flag does not appear.
        """
        argv = self._argv(safe_off=False)
        assert argv == ["resume", "--last"]

    def test_start_mode_is_empty(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=False,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == []

    def test_new_session_forced_is_start(self):
        d = CodexTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=False, new_session=True, is_new_project=False,
            extra_args=[], available_modes=d.mode.keys(),
        )
        argv = assembly.assemble_argv(
            d, mode_key=mode_key, safe_mode_off=False,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == []

    def test_safe_off_adds_bypass_flag(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["--dangerously-bypass-approvals-and-sandbox"]

    def test_safe_on_no_bypass_flag(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=False,
            setting_values={}, op=None, extra_args=[],
        )
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv

    def test_model_flag_from_settings(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=False,
            setting_values={"model": "gpt-5.5"}, op=None, extra_args=[],
        )
        assert argv == ["--model", "gpt-5.5"]

    def test_exec_op_argv(self):
        d = CodexTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=False,
            setting_values={}, op="exec", extra_args=["do the thing"],
        )
        assert argv == ["exec", "do the thing"]

    def test_env_is_empty(self):
        d = CodexTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=True, setting_values={"model": "gpt-5.5"})
        # model is a FLAG (argv), not env; no container_env; bypass is a flag.
        assert env == {}
