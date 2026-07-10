"""Safety invariant tests: boundary, negative, and contract checks.

These tests verify system-level constraints that unit tests with mocked
paths and runtimes tend to miss.  They encode real-world limits (AF_UNIX
path length), adversarial inputs (directory names that collide with
kanibako markers), and contracts (mount sources must exist, CLI args
must include expected flags).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kanibako.commands.start import (
    _UNIX_SOCKET_PATH_LIMIT,
    _validate_mounts,
    bounded_socket_name,
    validate_socket_path,
)
from kanibako.paths import (
    BoxMode,
    detect_project_mode,
    load_std_paths,
)
from kanibako.config import load_config
from kanibako.targets.base import Mount
from kanibako.plugins.claude import ClaudeTarget
from kanibako.utils import short_hash


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Minimal kanibako config for detection tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))

    cfg_dir = tmp_path / ".config" / "kanibako"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "kanibako_config.yaml"
    cfg_file.write_text('box:\n  image: "kanibako-oci"\n')
    return cfg_file


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ── Boundary tests: AF_UNIX socket path ──────────────────────────────

class TestSocketPathBoundary:
    """Socket path must stay under the AF_UNIX sun_path limit."""

    def test_short_hash_socket_under_limit(self):
        """Socket in /run/user/$UID/kanibako/ with short_hash stays under limit."""
        # Simulate a realistic path.
        long_hash = hashlib.sha256(b"/home/user/some/deep/project/path").hexdigest()
        shash = short_hash(long_hash)
        socket_path = Path(f"/run/user/1000/kanibako/{shash}.sock")
        assert len(str(socket_path)) < _UNIX_SOCKET_PATH_LIMIT

    def test_name_based_socket_under_limit(self):
        """Socket with project name stays under limit for typical names."""
        socket_path = Path("/run/user/1000/kanibako/my-long-project-name.sock")
        assert len(str(socket_path)) < _UNIX_SOCKET_PATH_LIMIT

    def test_metadata_path_socket_exceeds_limit(self):
        """Socket in metadata_path (old location) would exceed the limit."""
        long_hash = hashlib.sha256(b"/home/user/project").hexdigest()
        # This is the OLD location that caused the bug.
        socket_path = Path(f"/home/user/.local/share/kanibako/boxes/{long_hash}/helper.sock")
        assert len(str(socket_path)) >= _UNIX_SOCKET_PATH_LIMIT

    def test_validate_socket_path_raises_on_long_path(self):
        """validate_socket_path raises ValueError for paths at the limit."""
        long_path = Path("/tmp/" + "x" * 100 + ".sock")
        assert len(str(long_path)) >= _UNIX_SOCKET_PATH_LIMIT
        with pytest.raises(ValueError, match="Socket path too long"):
            validate_socket_path(long_path)

    def test_validate_socket_path_accepts_short_path(self):
        """validate_socket_path accepts paths under the limit."""
        short_path = Path("/run/user/1000/kanibako/abc123.sock")
        validate_socket_path(short_path)  # Should not raise.

    def test_worst_case_xdg_runtime_dir(self):
        """Even with a long XDG_RUNTIME_DIR, socket stays under limit."""
        # Some systems have longer runtime dirs.
        long_hash = hashlib.sha256(b"/very/deep/path").hexdigest()
        shash = short_hash(long_hash)
        # Simulate a long-ish runtime dir.
        socket_path = Path(f"/run/user/1000000/kanibako/{shash}.sock")
        assert len(str(socket_path)) < _UNIX_SOCKET_PATH_LIMIT

    def test_tmp_fallback_under_limit(self):
        """Fallback /tmp/kanibako-$UID/ path stays under limit."""
        long_hash = hashlib.sha256(b"/home/user/deep/project").hexdigest()
        shash = short_hash(long_hash)
        socket_path = Path(f"/tmp/kanibako-1000000/{shash}.sock")
        assert len(str(socket_path)) < _UNIX_SOCKET_PATH_LIMIT


class TestBoundedSocketName:
    """``bounded_socket_name`` keeps the socket under the AF_UNIX limit."""

    def test_short_name_verbatim(self):
        """A short combined identity is used verbatim as ``<identity>.sock``."""
        run_dir = Path("/run/user/1000/kanibako")
        assert bounded_socket_name("myproj", run_dir) == "myproj.sock"

    def test_combined_box_ws_identity_verbatim(self):
        """The host socket basename is ``<box>-<ws>.sock`` (FIX K)."""
        run_dir = Path("/run/user/1000/kanibako")
        assert (
            bounded_socket_name("myproj-__PRIMARY__", run_dir)
            == "myproj-__PRIMARY__.sock"
        )

    def test_same_box_distinct_ws_distinct_sockets(self):
        """A box name reused across worksets gets distinct sockets via the token."""
        run_dir = Path("/run/user/1000/kanibako")
        primary = bounded_socket_name("app-__PRIMARY__", run_dir)
        named = bounded_socket_name("app-myset", run_dir)
        assert primary != named

    def test_worst_case_standalone_name_under_limit(self):
        """A ``<kuid>_<32-char-leaf>`` name in a deep runtime dir fits."""
        # 5-char kuid + "_" + 32-char leaf = 38-char box name.
        box_name = "abcde_" + "x" * 32
        assert len(box_name) == 38
        # A plausibly deep XDG_RUNTIME_DIR.
        run_dir = Path("/run/user/4000000/kanibako")
        socket_path = run_dir / bounded_socket_name(box_name, run_dir)
        assert len(str(socket_path)) < _UNIX_SOCKET_PATH_LIMIT

    def test_very_long_name_falls_back_to_hash(self):
        """An over-long name is replaced by a bounded hash, still under limit."""
        run_dir = Path("/run/user/1000/kanibako")
        box_name = "z" * 200
        name = bounded_socket_name(box_name, run_dir)
        # Verbatim would be far over the limit; the fallback must be short.
        assert name != f"{box_name}.sock"
        assert name.endswith(".sock")
        assert len(name) == len("0123456789abcdef") + len(".sock")
        assert len(str(run_dir / name)) < _UNIX_SOCKET_PATH_LIMIT

    def test_deterministic(self):
        """Same box name yields the same socket name (so reattach finds it)."""
        run_dir = Path("/run/user/1000/kanibako")
        box_name = "q" * 200
        assert bounded_socket_name(box_name, run_dir) == bounded_socket_name(
            box_name, run_dir
        )

    def test_distinct_names_distinct_sockets(self):
        """Different over-long names get different bounded sockets."""
        run_dir = Path("/run/user/1000/kanibako")
        a = bounded_socket_name("a" * 200, run_dir)
        b = bounded_socket_name("b" * 200, run_dir)
        assert a != b

    def test_boundary_triggers_fallback(self):
        """When verbatim hits the limit exactly, the hash fallback engages."""
        run_dir = Path("/run/user/1000/kanibako")
        # Pick a name whose verbatim ``<name>.sock`` is exactly at the limit.
        prefix_len = len(str(run_dir)) + 1  # run_dir + "/"
        name_len = _UNIX_SOCKET_PATH_LIMIT - prefix_len - len(".sock")
        box_name = "n" * name_len
        verbatim = run_dir / f"{box_name}.sock"
        assert len(str(verbatim)) == _UNIX_SOCKET_PATH_LIMIT  # not < limit
        # Must therefore fall back, and the result must pass the guard.
        result = bounded_socket_name(box_name, run_dir)
        assert result != f"{box_name}.sock"
        assert len(str(run_dir / result)) < _UNIX_SOCKET_PATH_LIMIT


# ── Negative tests: detection false positives ─────────────────────────

class TestDetectionFalsePositives:
    """Project mode detection must not false-positive on common directory names."""

    COMMON_NAMES = [
        "kanibako",      # The project itself being named kanibako.
        "src",
        "build",
        "dist",
        "node_modules",
        ".git",
    ]

    @pytest.mark.parametrize("dirname", COMMON_NAMES)
    def test_subdirectory_name_does_not_trigger_standalone(
        self, config_file, tmp_home, dirname,
    ):
        """A subdirectory named '{dirname}' should not trigger standalone mode."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / dirname).mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        # Should fall through to local (default), NOT standalone.  A bare
        # directory (even ``.kanibako``) is not a marker on its own: a real
        # standalone settings.yaml is required.
        assert result.mode is not BoxMode.standalone

    def test_ancestor_named_kanibako_no_false_positive(
        self, config_file, tmp_home,
    ):
        """A project inside a directory named 'kanibako' should not detect as standalone."""
        config = load_config(config_file)
        std = load_std_paths(config)
        # Simulate: ~/workspaces/kanibako/src/ — running from src/
        workspaces = tmp_home / "workspaces"
        kanibako_dir = workspaces / "kanibako"
        src_dir = kanibako_dir / "src"
        src_dir.mkdir(parents=True)

        result = detect_project_mode(src_dir.resolve(), std, config)
        assert result.mode is not BoxMode.standalone

    def test_legacy_kanibako_dirs_are_not_markers(
        self, config_file, tmp_home,
    ):
        """The legacy ``.kanibako``/``kanibako`` dirs are no longer markers."""
        config = load_config(config_file)
        std = load_std_paths(config)
        for idx, dirname in enumerate((".kanibako", "kanibako")):
            project_dir = tmp_home / f"proj_{idx}"
            project_dir.mkdir()
            (project_dir / dirname).mkdir()
            (project_dir / dirname / "settings.yaml").write_text(
                'project:\n  mode: "standalone"\n'
            )
            result = detect_project_mode(project_dir.resolve(), std, config)
            assert result.mode is not BoxMode.standalone

    def test_box_data_marker_with_toml_is_valid(
        self, config_file, tmp_home,
    ):
        """box_data/ dir + a ROOT settings.yaml (drift I) is a valid marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / "box_data").mkdir()
        (project_dir / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.standalone

    def test_box_data_marker_without_toml_is_not_standalone(
        self, config_file, tmp_home,
    ):
        """A bare box_data/ (no metadata file) is NOT a marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / "box_data").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is not BoxMode.standalone


# ── Stale names.yaml safety ────────────────────────────────────────────

class TestStaleNameSafety:
    """Stale names.yaml entries pointing at $HOME must not trigger local detection."""

    def test_stale_home_entry_ignored_by_detection(self, config_file, tmp_home):
        """Stale entry at $HOME (no boxes dir) → detection falls through to default."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"  # $HOME set by fixture

        # Register a stale PRIMARY-membership entry pointing at $HOME (direct
        # write, bypassing the $HOME guard, to simulate a stale/legacy entry).
        from kanibako import workset_registry
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, None,
        )
        workset_registry.register_workset_box(prim_reg, "jjb", home.resolve())
        # Intentionally do NOT create boxes/jjb/

        # Run detection from a subdirectory of $HOME.
        project_dir = home / "myproject"
        project_dir.mkdir(parents=True, exist_ok=True)
        result = detect_project_mode(project_dir.resolve(), std, config)

        # Should fall through to the default mode at project_dir, NOT match $HOME.
        assert result.project_root == project_dir.resolve()
        assert result.mode is BoxMode.primary


# ── Contract tests: mount source validation ───────────────────────────

class TestMountValidation:
    """All mount sources must exist before being passed to the container runtime."""

    def test_validate_mounts_warns_on_missing_source(self, tmp_path, capsys):
        """_validate_mounts prints a warning for non-existent source."""
        import logging
        logger = logging.getLogger("test")

        mounts = [
            Mount(
                source=tmp_path / "nonexistent" / "file",
                destination="/home/agent/.local/bin/claude",
                options="ro",
            ),
        ]
        _validate_mounts(mounts, logger)
        captured = capsys.readouterr()
        assert "mount source does not exist" in captured.err

    def test_validate_mounts_silent_on_existing_source(self, tmp_path, capsys):
        """_validate_mounts is silent when all sources exist."""
        import logging
        logger = logging.getLogger("test")

        existing = tmp_path / "real_file"
        existing.touch()
        mounts = [
            Mount(source=existing, destination="/home/agent/file", options="ro"),
        ]
        _validate_mounts(mounts, logger)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_validate_mounts_handles_empty_list(self):
        """_validate_mounts handles empty mount list without error."""
        import logging
        logger = logging.getLogger("test")
        _validate_mounts([], logger)  # Should not raise.


# ── Contract tests: binary mount sources ──────────────────────────────
#
# Claude's delivery binds come from its descriptor (the legacy ``binary_mounts``
# hook was removed for the descriptor-only public release); core builds them via
# ``descriptor_mounts``.  The invariant under test is unchanged: an
# AGENT_CRITICAL source that does not exist on the host must NOT silently produce
# an empty/dangling bind — it raises ``BindingSourceError`` (the clean safe-fail
# start.py converts to an actionable error).


class TestBinaryMountContract:
    """Delivery binds: existing sources are delivered ro; a missing one safe-fails."""

    def _install(self, tmp_path, *, make_share=True, make_launcher=True):
        from kanibako.targets.base import AgentInstall

        install_dir = tmp_path / "share" / "claude"
        launcher = tmp_path / "bin" / "claude"
        if make_share:
            install_dir.mkdir(parents=True)
        if make_launcher:
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_bytes(b"binary")
        return AgentInstall(
            name="claude",
            binary=launcher,
            install_dir=install_dir,
            launcher=launcher,
        )

    def test_delivery_mounts_all_exist(self, tmp_path):
        """When sources exist, all ro delivery binds are returned.

        Two AGENT_CRITICAL delivery binds (share + launcher) plus the best-effort
        kickoff-loader SEED (its shipped source exists).
        """
        from kanibako.targets.assembly import descriptor_mounts

        t = ClaudeTarget()
        install = self._install(tmp_path)
        mounts = descriptor_mounts(t.descriptor, install)

        assert len(mounts) == 3
        for m in mounts:
            assert m.source.exists(), f"Mount source does not exist: {m.source}"
            assert m.options == "ro"

    def test_missing_critical_source_safe_fails(self, tmp_path):
        """A missing AGENT_CRITICAL source raises BindingSourceError (no dangling bind)."""
        from kanibako.targets.assembly import BindingSourceError, descriptor_mounts

        t = ClaudeTarget()
        install = self._install(tmp_path, make_launcher=False)
        with pytest.raises(BindingSourceError):
            descriptor_mounts(t.descriptor, install)


# ── Contract tests: CLI args invariants ───────────────────────────────
#
# Claude's launch argv is assembled from its descriptor via
# ``kanibako.targets.assembly`` (the legacy ``build_cli_args`` hook was removed
# for the descriptor-only public release).  ``_claude_argv`` mirrors start.py's
# descriptor argv assembly so these tests pin the same flag invariants.


def _claude_argv(*, safe_mode, resume_mode, new_session, is_new_project, extra_args):
    from kanibako.targets import assembly

    desc = ClaudeTarget().descriptor
    # Persisted auto_approve defaults True (PERMISSIVE) when unset; mirror the
    # launch reader's bool coercion (the setting_key is now "auto_approve").
    safe_off = assembly.effective_safe_mode_off(
        secure=safe_mode, autonomous=False, auto_approve=True,
    )
    mode_key = assembly.resolve_mode(
        resume_mode=resume_mode,
        new_session=new_session,
        is_new_project=is_new_project,
        extra_args=extra_args,
        available_modes=desc.mode.keys(),
    )
    return assembly.assemble_argv(
        desc,
        mode_key=mode_key,
        safe_mode_off=safe_off,
        setting_values={"model": "opus"},
        op=None,
        extra_args=extra_args,
    )


class TestCLIArgsContract:
    """CLI args must include expected flags for common scenarios."""

    def test_existing_project_gets_continue(self):
        """An existing (non-new) project must get --continue."""
        args = _claude_argv(
            safe_mode=False, resume_mode=False, new_session=False,
            is_new_project=False, extra_args=[],
        )
        assert "--continue" in args

    def test_new_project_skips_continue(self):
        """A new project must NOT get --continue."""
        args = _claude_argv(
            safe_mode=False, resume_mode=False, new_session=False,
            is_new_project=True, extra_args=[],
        )
        assert "--continue" not in args

    def test_default_includes_dangerous_skip(self):
        """Default (non-safe) mode must include --dangerously-skip-permissions."""
        args = _claude_argv(
            safe_mode=False, resume_mode=False, new_session=False,
            is_new_project=False, extra_args=[],
        )
        assert "--dangerously-skip-permissions" in args

    def test_safe_mode_excludes_dangerous_skip(self):
        """Safe mode must NOT include --dangerously-skip-permissions."""
        args = _claude_argv(
            safe_mode=True, resume_mode=False, new_session=False,
            is_new_project=False, extra_args=[],
        )
        assert "--dangerously-skip-permissions" not in args

    def test_extra_args_with_resume_skips_continue(self):
        """Passing --resume in extra_args must skip --continue."""
        args = _claude_argv(
            safe_mode=False, resume_mode=False, new_session=False,
            is_new_project=False, extra_args=["--resume"],
        )
        assert "--continue" not in args
        assert "--resume" in args
