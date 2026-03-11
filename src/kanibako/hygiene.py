"""Shell directory cleanup: remove waste files, compress old conversation logs."""

from __future__ import annotations

import gzip
import os
import shutil
import time
from pathlib import Path

from kanibako.log import get_logger

# Directories whose *contents* are always safe to delete.
_WASTE_DIRS = (
    ".claude/telemetry",
    ".claude/debug",
)

# Subdirectories under .cache/ that are safe to purge.
# We intentionally keep pip, uv, npm, etc. — only remove known-waste dirs.
_CACHE_WASTE_DIRS = (
    ".cache/claude",
    ".cache/sentry",
    ".cache/@anthropic",
)

# Files older than this many days get compressed (conversation logs).
_COMPRESS_AGE_DAYS = 7


def cleanup_shell_dir(
    shell_dir: Path,
    dry_run: bool = False,
) -> list[str]:
    """Remove stale/waste files from a persistent shell directory.

    Returns a list of human-readable action strings describing what was
    (or would be, in dry_run mode) cleaned up.  The list is empty when
    there is nothing to do.
    """
    logger = get_logger("hygiene")
    actions: list[str] = []

    if not shell_dir.is_dir():
        return actions

    # 1. Delete known waste directories.
    actions.extend(_clean_waste_dirs(shell_dir, dry_run, logger))

    # 2. Delete .cache waste subdirectories.
    actions.extend(_clean_cache_waste(shell_dir, dry_run, logger))

    # 3. Remove duplicate claude binaries outside .local/.
    actions.extend(_clean_duplicate_binaries(shell_dir, dry_run, logger))

    # 4. Compress old conversation logs.
    actions.extend(_compress_old_logs(shell_dir, dry_run, logger))

    if actions:
        total = len(actions)
        prefix = "[dry-run] " if dry_run else ""
        logger.info("%sHygiene: %d action(s) taken", prefix, total)

    return actions


def _clean_waste_dirs(
    shell_dir: Path,
    dry_run: bool,
    logger: object,
) -> list[str]:
    """Delete contents of known waste directories."""
    actions: list[str] = []
    for rel in _WASTE_DIRS:
        target = shell_dir / rel
        if not target.is_dir():
            continue
        freed = _dir_size(target)
        if freed == 0:
            continue
        desc = (
            f"{'[dry-run] ' if dry_run else ''}"
            f"Removed {rel}/ contents ({_fmt_size(freed)})"
        )
        if not dry_run:
            _remove_dir_contents(target)
        actions.append(desc)
    return actions


def _clean_cache_waste(
    shell_dir: Path,
    dry_run: bool,
    logger: object,
) -> list[str]:
    """Delete waste subdirectories under .cache/."""
    actions: list[str] = []
    for rel in _CACHE_WASTE_DIRS:
        target = shell_dir / rel
        if not target.is_dir():
            continue
        freed = _dir_size(target)
        if freed == 0:
            continue
        desc = (
            f"{'[dry-run] ' if dry_run else ''}"
            f"Removed {rel}/ ({_fmt_size(freed)})"
        )
        if not dry_run:
            shutil.rmtree(target, ignore_errors=True)
        actions.append(desc)
    return actions


def _clean_duplicate_binaries(
    shell_dir: Path,
    dry_run: bool,
    logger: object,
) -> list[str]:
    """Remove claude binary copies outside .local/.

    The legitimate binary lives at .local/bin/claude (bind-mounted from
    host).  Anything else that looks like a large claude binary is waste
    — typically 200+ MB copies left by install scripts or updates.
    """
    actions: list[str] = []
    # Minimum size to consider: 100 MB (real binary is ~227 MB).
    min_size = 100 * 1024 * 1024

    for candidate in _find_claude_binaries(shell_dir):
        # Skip the legitimate location.
        try:
            rel = candidate.relative_to(shell_dir / ".local")
            # It's under .local — leave it alone.
            _ = rel
            continue
        except ValueError:
            pass

        try:
            size = candidate.stat().st_size
        except OSError:
            continue

        if size < min_size:
            continue

        rel_path = candidate.relative_to(shell_dir)
        desc = (
            f"{'[dry-run] ' if dry_run else ''}"
            f"Removed duplicate binary {rel_path} ({_fmt_size(size)})"
        )
        if not dry_run:
            try:
                candidate.unlink()
            except OSError:
                continue
        actions.append(desc)

    return actions


def _find_claude_binaries(shell_dir: Path) -> list[Path]:
    """Find files named 'claude' that look like binaries in shell_dir.

    Only walks directories that are likely to contain stray copies:
    the top level and a few common subdirectories.  Does NOT recurse
    the entire tree (that would be too slow).
    """
    candidates: list[Path] = []

    # Check top-level and common locations for stray binaries.
    search_dirs = [
        shell_dir,
        shell_dir / ".claude" / "bin",
        shell_dir / "bin",
        shell_dir / ".bin",
        shell_dir / ".npm" / "_npx",
    ]
    for d in search_dirs:
        if not d.is_dir():
            continue
        claude_file = d / "claude"
        if claude_file.is_file() and not claude_file.is_symlink():
            candidates.append(claude_file)

    return candidates


def _compress_old_logs(
    shell_dir: Path,
    dry_run: bool,
    logger: object,
) -> list[str]:
    """Gzip conversation logs older than _COMPRESS_AGE_DAYS.

    Looks for .jsonl files under .claude/projects/*/conversation_logs/.
    """
    actions: list[str] = []
    cutoff = time.time() - (_COMPRESS_AGE_DAYS * 86400)

    projects_dir = shell_dir / ".claude" / "projects"
    if not projects_dir.is_dir():
        return actions

    # Glob for conversation log files.
    for log_file in projects_dir.glob("*/conversation_logs/*.jsonl"):
        if not log_file.is_file():
            continue
        # Already compressed?
        if log_file.suffix == ".gz":
            continue

        try:
            mtime = log_file.stat().st_mtime
        except OSError:
            continue

        if mtime >= cutoff:
            continue

        original_size = log_file.stat().st_size
        if original_size == 0:
            continue

        rel_path = log_file.relative_to(shell_dir)
        gz_path = log_file.with_suffix(log_file.suffix + ".gz")

        if not dry_run:
            try:
                _gzip_file(log_file, gz_path)
                compressed_size = gz_path.stat().st_size
            except OSError:
                continue
        else:
            compressed_size = original_size  # estimate unavailable in dry-run

        desc = (
            f"{'[dry-run] ' if dry_run else ''}"
            f"Compressed {rel_path} ({_fmt_size(original_size)}"
        )
        if not dry_run:
            desc += f" -> {_fmt_size(compressed_size)}"
        desc += ")"
        actions.append(desc)

    return actions


def _gzip_file(src: Path, dst: Path) -> None:
    """Compress *src* to *dst* with gzip and remove the original."""
    with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    # Preserve modification time on the compressed file.
    stat = src.stat()
    os.utime(dst, (stat.st_atime, stat.st_mtime))
    src.unlink()


def _remove_dir_contents(d: Path) -> None:
    """Remove all entries inside *d* without removing *d* itself."""
    for entry in d.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def _dir_size(d: Path) -> int:
    """Return total size of all files under *d* (non-recursive symlink-safe)."""
    total = 0
    try:
        for entry in d.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _fmt_size(nbytes: int) -> str:
    """Format byte count as a human-readable string."""
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    elif nbytes < 1024 * 1024 * 1024:
        return f"{nbytes / (1024 * 1024):.1f} MB"
    else:
        return f"{nbytes / (1024 * 1024 * 1024):.1f} GB"
