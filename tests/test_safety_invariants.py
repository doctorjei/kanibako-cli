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
    validate_socket_path,
)
from kanibako.paths import (
    ProjectMode,
    detect_project_mode,
    load_std_paths,
)
from kanibako.config import load_config
from kanibako.targets.base import Mount
from kanibako.targets.claude import ClaudeTarget
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
    cfg_file = cfg_dir / "kanibako.toml"
    cfg_file.write_text('[kanibako]\ncontainer_image = "kanibako-base"\n')
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
    def test_subdirectory_name_does_not_trigger_decentralized(
        self, config_file, tmp_home, dirname,
    ):
        """A subdirectory named '{dirname}' should not trigger decentralized mode."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / dirname).mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        # Should fall through to account_centric (default), NOT decentralized.
        # Exception: .kanibako is a legitimate marker.
        if dirname == ".kanibako":
            assert result.mode is ProjectMode.decentralized
        else:
            assert result.mode is not ProjectMode.decentralized

    def test_ancestor_named_kanibako_no_false_positive(
        self, config_file, tmp_home,
    ):
        """A project inside a directory named 'kanibako' should not detect as decentralized."""
        config = load_config(config_file)
        std = load_std_paths(config)
        # Simulate: ~/workspaces/kanibako/src/ — running from src/
        workspaces = tmp_home / "workspaces"
        kanibako_dir = workspaces / "kanibako"
        src_dir = kanibako_dir / "src"
        src_dir.mkdir(parents=True)

        result = detect_project_mode(src_dir.resolve(), std, config)
        assert result.mode is not ProjectMode.decentralized

    def test_dotless_kanibako_with_toml_is_valid(
        self, config_file, tmp_home,
    ):
        """A kanibako/ dir WITH project.toml IS a valid decentralized marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / "kanibako").mkdir()
        (project_dir / "kanibako" / "project.toml").write_text("")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized

    def test_dot_kanibako_marker_without_toml_is_valid(
        self, config_file, tmp_home,
    ):
        """.kanibako/ is always a valid marker (no project.toml required)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / ".kanibako").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized


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

class TestBinaryMountContract:
    """Binary mounts must only include sources that exist on the host."""

    def test_binary_mounts_all_exist(self, tmp_path):
        """When sources exist, both mounts are returned."""
        t = ClaudeTarget()
        from kanibako.targets.base import AgentInstall

        install_dir = tmp_path / "share" / "claude"
        install_dir.mkdir(parents=True)
        binary = tmp_path / "bin" / "claude"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"binary")

        install = AgentInstall(name="claude", binary=binary, install_dir=install_dir)
        mounts = t.binary_mounts(install)

        assert len(mounts) == 2
        for m in mounts:
            assert m.source.exists(), f"Mount source does not exist: {m.source}"

    def test_binary_mounts_missing_excluded(self, tmp_path):
        """When sources don't exist, mounts are not returned."""
        t = ClaudeTarget()
        from kanibako.targets.base import AgentInstall

        install = AgentInstall(
            name="claude",
            binary=tmp_path / "missing" / "claude",
            install_dir=tmp_path / "missing" / "share",
        )
        mounts = t.binary_mounts(install)

        assert len(mounts) == 0

    def test_partial_missing_only_existing_returned(self, tmp_path):
        """When only install_dir exists, only that mount is returned."""
        t = ClaudeTarget()
        from kanibako.targets.base import AgentInstall

        install_dir = tmp_path / "share" / "claude"
        install_dir.mkdir(parents=True)

        install = AgentInstall(
            name="claude",
            binary=tmp_path / "missing" / "claude",
            install_dir=install_dir,
        )
        mounts = t.binary_mounts(install)

        assert len(mounts) == 1
        assert mounts[0].source == install_dir
        assert mounts[0].source.exists()


# ── Contract tests: CLI args invariants ───────────────────────────────

class TestCLIArgsContract:
    """CLI args must include expected flags for common scenarios."""

    def test_existing_project_gets_continue(self):
        """An existing (non-new) project must get --continue."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
        )
        assert "--continue" in args

    def test_new_project_skips_continue(self):
        """A new project must NOT get --continue."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=True,
            extra_args=[],
        )
        assert "--continue" not in args

    def test_default_includes_dangerous_skip(self):
        """Default (non-safe) mode must include --dangerously-skip-permissions."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
        )
        assert "--dangerously-skip-permissions" in args

    def test_safe_mode_excludes_dangerous_skip(self):
        """Safe mode must NOT include --dangerously-skip-permissions."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=True,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
        )
        assert "--dangerously-skip-permissions" not in args

    def test_resume_mode_includes_resume_flag(self):
        """Resume mode must include --resume."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False,
            resume_mode=True,
            new_session=False,
            is_new_project=False,
            extra_args=[],
        )
        assert "--resume" in args
        assert "--continue" not in args

    def test_extra_args_with_resume_skips_continue(self):
        """Passing --resume in extra_args must skip --continue."""
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=["--resume"],
        )
        assert "--continue" not in args
        assert "--resume" in args
