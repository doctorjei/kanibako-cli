"""Tests for kanibako.hygiene: shell directory cleanup and compression."""

from __future__ import annotations

import gzip
import os
import time

from kanibako.hygiene import (
    _CACHE_WASTE_DIRS,
    _COMPRESS_AGE_DAYS,
    _WASTE_DIRS,
    _clean_cache_waste,
    _clean_duplicate_binaries,
    _clean_waste_dirs,
    _compress_old_logs,
    _find_claude_binaries,
    _fmt_size,
    _gzip_file,
    cleanup_shell_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path, size=0, content=b"", mtime=None):
    """Create a file with optional size / content / mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if content:
        path.write_bytes(content)
    elif size:
        path.write_bytes(b"\x00" * size)
    else:
        path.touch()
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _old_mtime():
    """Return a timestamp older than _COMPRESS_AGE_DAYS."""
    return time.time() - (_COMPRESS_AGE_DAYS + 1) * 86400


def _recent_mtime():
    """Return a timestamp within _COMPRESS_AGE_DAYS."""
    return time.time() - (_COMPRESS_AGE_DAYS - 1) * 86400


# ---------------------------------------------------------------------------
# cleanup_shell_dir (top-level)
# ---------------------------------------------------------------------------

class TestCleanupShellDir:
    def test_empty_dir_no_actions(self, tmp_path):
        """An empty shell dir produces no actions."""
        actions = cleanup_shell_dir(tmp_path)
        assert actions == []

    def test_nonexistent_dir_no_actions(self, tmp_path):
        """A non-existent path produces no actions."""
        actions = cleanup_shell_dir(tmp_path / "nope")
        assert actions == []

    def test_combined_cleanup(self, tmp_path):
        """Multiple waste types are all cleaned in one call."""
        # Telemetry waste
        _make_file(tmp_path / ".claude" / "telemetry" / "event.json", size=100)
        # Cache waste
        _make_file(tmp_path / ".cache" / "claude" / "data.bin", size=100)
        # Old conversation log
        _make_file(
            tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl",
            content=b'{"msg":"hello"}\n',
            mtime=_old_mtime(),
        )

        actions = cleanup_shell_dir(tmp_path)
        assert len(actions) >= 3
        # Telemetry dir is now empty
        assert list((tmp_path / ".claude" / "telemetry").iterdir()) == []
        # Cache dir was removed
        assert not (tmp_path / ".cache" / "claude").exists()
        # Log was compressed
        assert (tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl.gz").exists()
        assert not (tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl").exists()

    def test_dry_run_no_modifications(self, tmp_path):
        """dry_run=True reports actions but does not modify anything."""
        _make_file(tmp_path / ".claude" / "telemetry" / "event.json", size=100)
        _make_file(tmp_path / ".cache" / "claude" / "data.bin", size=100)
        _make_file(
            tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl",
            content=b'{"msg":"hello"}\n',
            mtime=_old_mtime(),
        )

        actions = cleanup_shell_dir(tmp_path, dry_run=True)
        assert len(actions) >= 3
        for a in actions:
            assert "[dry-run]" in a

        # Nothing was actually removed.
        assert (tmp_path / ".claude" / "telemetry" / "event.json").exists()
        assert (tmp_path / ".cache" / "claude" / "data.bin").exists()
        assert (tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl").exists()


# ---------------------------------------------------------------------------
# _clean_waste_dirs
# ---------------------------------------------------------------------------

class TestCleanWasteDirs:
    def test_removes_telemetry_contents(self, tmp_path):
        _make_file(tmp_path / ".claude" / "telemetry" / "a.json", size=50)
        _make_file(tmp_path / ".claude" / "telemetry" / "b.json", size=50)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_waste_dirs(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "telemetry" in actions[0]
        # Directory still exists but is empty.
        assert (tmp_path / ".claude" / "telemetry").is_dir()
        assert list((tmp_path / ".claude" / "telemetry").iterdir()) == []

    def test_removes_debug_contents(self, tmp_path):
        _make_file(tmp_path / ".claude" / "debug" / "trace.log", size=200)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_waste_dirs(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "debug" in actions[0]
        assert list((tmp_path / ".claude" / "debug").iterdir()) == []

    def test_skips_missing_dirs(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        actions = _clean_waste_dirs(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_skips_empty_dirs(self, tmp_path):
        (tmp_path / ".claude" / "telemetry").mkdir(parents=True)
        import logging
        logger = logging.getLogger("test")
        actions = _clean_waste_dirs(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_removes_subdirs(self, tmp_path):
        """Nested directories inside waste dirs are fully removed."""
        _make_file(tmp_path / ".claude" / "telemetry" / "sub" / "deep" / "file.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_waste_dirs(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        # Subdir gone, parent preserved.
        assert (tmp_path / ".claude" / "telemetry").is_dir()
        assert list((tmp_path / ".claude" / "telemetry").iterdir()) == []

    def test_dry_run_preserves(self, tmp_path):
        _make_file(tmp_path / ".claude" / "telemetry" / "event.json", size=50)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_waste_dirs(tmp_path, dry_run=True, logger=logger)
        assert len(actions) == 1
        assert "[dry-run]" in actions[0]
        assert (tmp_path / ".claude" / "telemetry" / "event.json").exists()


# ---------------------------------------------------------------------------
# _clean_cache_waste
# ---------------------------------------------------------------------------

class TestCleanCacheWaste:
    def test_removes_claude_cache(self, tmp_path):
        _make_file(tmp_path / ".cache" / "claude" / "sessions" / "data.bin", size=300)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "claude" in actions[0]
        assert not (tmp_path / ".cache" / "claude").exists()

    def test_removes_sentry_cache(self, tmp_path):
        _make_file(tmp_path / ".cache" / "sentry" / "envelope.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "sentry" in actions[0]
        assert not (tmp_path / ".cache" / "sentry").exists()

    def test_removes_anthropic_cache(self, tmp_path):
        _make_file(tmp_path / ".cache" / "@anthropic" / "stuff.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "@anthropic" in actions[0]
        assert not (tmp_path / ".cache" / "@anthropic").exists()

    def test_preserves_pip_cache(self, tmp_path):
        """pip cache should NOT be removed."""
        _make_file(tmp_path / ".cache" / "pip" / "http" / "data.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert (tmp_path / ".cache" / "pip" / "http" / "data.bin").exists()

    def test_preserves_uv_cache(self, tmp_path):
        """uv cache should NOT be removed."""
        _make_file(tmp_path / ".cache" / "uv" / "data.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert (tmp_path / ".cache" / "uv" / "data.bin").exists()

    def test_preserves_npm_cache(self, tmp_path):
        """npm cache should NOT be removed."""
        _make_file(tmp_path / ".cache" / "npm" / "data.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert (tmp_path / ".cache" / "npm" / "data.bin").exists()

    def test_skips_missing_cache(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_dry_run_preserves(self, tmp_path):
        _make_file(tmp_path / ".cache" / "claude" / "data.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=True, logger=logger)
        assert len(actions) == 1
        assert "[dry-run]" in actions[0]
        assert (tmp_path / ".cache" / "claude" / "data.bin").exists()

    def test_multiple_waste_dirs(self, tmp_path):
        """All waste subdirs are cleaned in one pass."""
        _make_file(tmp_path / ".cache" / "claude" / "a.bin", size=100)
        _make_file(tmp_path / ".cache" / "sentry" / "b.bin", size=100)
        _make_file(tmp_path / ".cache" / "@anthropic" / "c.bin", size=100)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_cache_waste(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 3


# ---------------------------------------------------------------------------
# _clean_duplicate_binaries
# ---------------------------------------------------------------------------

class TestCleanDuplicateBinaries:
    def test_removes_toplevel_duplicate(self, tmp_path):
        """A large 'claude' binary at top level is removed."""
        _make_file(tmp_path / "claude", size=200 * 1024 * 1024)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "duplicate binary" in actions[0]
        assert not (tmp_path / "claude").exists()

    def test_preserves_local_bin(self, tmp_path):
        """The legitimate binary at .local/bin/claude is preserved."""
        _make_file(tmp_path / ".local" / "bin" / "claude", size=200 * 1024 * 1024)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert (tmp_path / ".local" / "bin" / "claude").exists()

    def test_preserves_small_files(self, tmp_path):
        """Small files named 'claude' are not removed (scripts, wrappers)."""
        _make_file(tmp_path / "claude", size=1024)  # 1KB — wrapper script
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert (tmp_path / "claude").exists()

    def test_removes_claude_bin_dir(self, tmp_path):
        """A duplicate in .claude/bin/ is removed."""
        _make_file(tmp_path / ".claude" / "bin" / "claude", size=200 * 1024 * 1024)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert not (tmp_path / ".claude" / "bin" / "claude").exists()

    def test_dry_run_preserves(self, tmp_path):
        _make_file(tmp_path / "claude", size=200 * 1024 * 1024)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=True, logger=logger)
        assert len(actions) == 1
        assert "[dry-run]" in actions[0]
        assert (tmp_path / "claude").exists()

    def test_symlinks_ignored(self, tmp_path):
        """Symlinks named 'claude' are not treated as duplicates."""
        real = _make_file(tmp_path / ".local" / "bin" / "claude", size=200 * 1024 * 1024)
        link = tmp_path / "claude"
        link.symlink_to(real)
        import logging
        logger = logging.getLogger("test")

        actions = _clean_duplicate_binaries(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert link.is_symlink()


# ---------------------------------------------------------------------------
# _find_claude_binaries
# ---------------------------------------------------------------------------

class TestFindClaudeBinaries:
    def test_finds_toplevel(self, tmp_path):
        _make_file(tmp_path / "claude", size=100)
        result = _find_claude_binaries(tmp_path)
        assert tmp_path / "claude" in result

    def test_finds_claude_bin(self, tmp_path):
        _make_file(tmp_path / ".claude" / "bin" / "claude", size=100)
        result = _find_claude_binaries(tmp_path)
        assert tmp_path / ".claude" / "bin" / "claude" in result

    def test_ignores_symlinks(self, tmp_path):
        real = _make_file(tmp_path / "real_claude", size=100)
        (tmp_path / "claude").symlink_to(real)
        result = _find_claude_binaries(tmp_path)
        # Symlink 'claude' should not appear.
        assert tmp_path / "claude" not in result

    def test_empty_dir(self, tmp_path):
        result = _find_claude_binaries(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# _compress_old_logs
# ---------------------------------------------------------------------------

class TestCompressOldLogs:
    def test_compresses_old_log(self, tmp_path):
        content = b'{"msg":"hello"}\n{"msg":"world"}\n'
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl"
        _make_file(log_file, content=content, mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 1
        assert "Compressed" in actions[0]
        assert not log_file.exists()

        gz_path = log_file.with_suffix(".jsonl.gz")
        assert gz_path.exists()
        with gzip.open(gz_path, "rb") as f:
            assert f.read() == content

    def test_preserves_recent_log(self, tmp_path):
        content = b'{"msg":"hello"}\n'
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl"
        _make_file(log_file, content=content, mtime=_recent_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert log_file.exists()

    def test_skips_empty_log(self, tmp_path):
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl"
        _make_file(log_file, mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_skips_already_compressed(self, tmp_path):
        """Files ending in .gz are not double-compressed."""
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl.gz"
        _make_file(log_file, content=b"compressed data", mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_multiple_projects(self, tmp_path):
        """Logs from multiple projects are all compressed."""
        for proj_name in ("proj-a", "proj-b"):
            log_file = tmp_path / ".claude" / "projects" / proj_name / "conversation_logs" / "old.jsonl"
            _make_file(log_file, content=b'{"data":1}\n', mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert len(actions) == 2

    def test_dry_run_preserves(self, tmp_path):
        content = b'{"msg":"hello"}\n'
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl"
        _make_file(log_file, content=content, mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=True, logger=logger)
        assert len(actions) == 1
        assert "[dry-run]" in actions[0]
        assert log_file.exists()
        assert not log_file.with_suffix(".jsonl.gz").exists()

    def test_no_projects_dir(self, tmp_path):
        """No .claude/projects/ directory — no crash, no actions."""
        import logging
        logger = logging.getLogger("test")
        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert actions == []

    def test_preserves_non_jsonl_files(self, tmp_path):
        """Non-.jsonl files in conversation_logs/ are left alone."""
        txt_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "notes.txt"
        _make_file(txt_file, content=b"notes", mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        actions = _compress_old_logs(tmp_path, dry_run=False, logger=logger)
        assert actions == []
        assert txt_file.exists()

    def test_compressed_content_matches_original(self, tmp_path):
        """Verify the gzipped content is identical to the original."""
        content = b'{"conversation_id":"abc","turn":1}\n' * 100
        log_file = tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "big.jsonl"
        _make_file(log_file, content=content, mtime=_old_mtime())
        import logging
        logger = logging.getLogger("test")

        _compress_old_logs(tmp_path, dry_run=False, logger=logger)

        gz_path = log_file.with_suffix(".jsonl.gz")
        with gzip.open(gz_path, "rb") as f:
            assert f.read() == content


# ---------------------------------------------------------------------------
# _gzip_file
# ---------------------------------------------------------------------------

class TestGzipFile:
    def test_compresses_and_removes_original(self, tmp_path):
        src = _make_file(tmp_path / "data.jsonl", content=b"line1\nline2\n")
        dst = tmp_path / "data.jsonl.gz"

        _gzip_file(src, dst)

        assert not src.exists()
        assert dst.exists()
        with gzip.open(dst, "rb") as f:
            assert f.read() == b"line1\nline2\n"

    def test_preserves_mtime(self, tmp_path):
        old_time = time.time() - 86400 * 30  # 30 days ago
        src = _make_file(tmp_path / "data.jsonl", content=b"test", mtime=old_time)
        dst = tmp_path / "data.jsonl.gz"

        _gzip_file(src, dst)

        gz_mtime = dst.stat().st_mtime
        assert abs(gz_mtime - old_time) < 2  # Within 2 seconds


# ---------------------------------------------------------------------------
# _fmt_size
# ---------------------------------------------------------------------------

class TestFmtSize:
    def test_bytes(self):
        assert _fmt_size(0) == "0 B"
        assert _fmt_size(512) == "512 B"
        assert _fmt_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert _fmt_size(1024) == "1.0 KB"
        assert _fmt_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert _fmt_size(1024 * 1024) == "1.0 MB"
        assert _fmt_size(227 * 1024 * 1024) == "227.0 MB"

    def test_gigabytes(self):
        assert _fmt_size(1024 * 1024 * 1024) == "1.0 GB"
        assert _fmt_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


# ---------------------------------------------------------------------------
# Constants consistency
# ---------------------------------------------------------------------------

class TestConstants:
    def test_waste_dirs_are_relative(self):
        """Waste dir patterns should be relative (no leading slash)."""
        for rel in _WASTE_DIRS:
            assert not rel.startswith("/")

    def test_cache_waste_dirs_are_relative(self):
        for rel in _CACHE_WASTE_DIRS:
            assert not rel.startswith("/")
            assert rel.startswith(".cache/")

    def test_compress_age_positive(self):
        assert _COMPRESS_AGE_DAYS > 0


# ---------------------------------------------------------------------------
# Integration: useful files are NOT deleted
# ---------------------------------------------------------------------------

class TestPreservation:
    """Verify that cleanup_shell_dir leaves useful files untouched."""

    def test_preserves_claude_config(self, tmp_path):
        """Claude config files are not affected."""
        cfg = _make_file(tmp_path / ".claude" / "settings.json", content=b'{"key":"val"}')
        cleanup_shell_dir(tmp_path)
        assert cfg.exists()

    def test_preserves_claude_projects_metadata(self, tmp_path):
        """Project metadata under .claude/projects/ is not touched."""
        meta = _make_file(tmp_path / ".claude" / "projects" / "proj" / "CLAUDE.md", content=b"# Guide")
        cleanup_shell_dir(tmp_path)
        assert meta.exists()

    def test_preserves_recent_conversation_logs(self, tmp_path):
        """Recent conversation logs are never deleted or compressed."""
        log_file = _make_file(
            tmp_path / ".claude" / "projects" / "proj" / "conversation_logs" / "chat.jsonl",
            content=b'{"msg":"recent"}\n',
            mtime=_recent_mtime(),
        )
        cleanup_shell_dir(tmp_path)
        assert log_file.exists()
        assert not log_file.with_suffix(".jsonl.gz").exists()

    def test_preserves_workspace_files(self, tmp_path):
        """Files in workspace/ are not touched."""
        src = _make_file(tmp_path / "workspace" / "main.py", content=b"print('hello')")
        cleanup_shell_dir(tmp_path)
        assert src.exists()

    def test_preserves_pip_cache(self, tmp_path):
        pip_data = _make_file(tmp_path / ".cache" / "pip" / "wheels" / "pkg.whl", size=5000)
        cleanup_shell_dir(tmp_path)
        assert pip_data.exists()

    def test_preserves_local_bin_claude(self, tmp_path):
        """The legitimate claude binary in .local/bin/ is preserved."""
        binary = _make_file(tmp_path / ".local" / "bin" / "claude", size=200 * 1024 * 1024)
        cleanup_shell_dir(tmp_path)
        assert binary.exists()

    def test_preserves_kanibako_dir(self, tmp_path):
        """The .kanibako/ directory is not touched."""
        sock_marker = _make_file(tmp_path / ".kanibako" / "helper.sock", size=10)
        cleanup_shell_dir(tmp_path)
        assert sock_marker.exists()

    def test_preserves_bashrc(self, tmp_path):
        """Shell config files are not touched."""
        bashrc = _make_file(tmp_path / ".bashrc", content=b"export PATH=$PATH")
        cleanup_shell_dir(tmp_path)
        assert bashrc.exists()
