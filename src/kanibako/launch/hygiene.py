"""Shell directory cleanup: remove waste files, compress old conversation logs.

⚑ The shell dir is the box HOME, so it is box-writable: every read, write, and removal
below it goes through :mod:`kanibako.boxfs` with the shell dir as the root, and a link
the box planted, at a leaf or a parent, is never followed.  A linked path is skipped
with a warning naming it; hygiene never stops a start.
"""

from __future__ import annotations

import gzip
import stat as _st
import time
from collections.abc import Callable
from pathlib import Path

from kanibako import boxfs
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
  agent_critical_dests: list[tuple[str, str]] | None = None,
) -> list[str]:
  """Remove stale/waste files from a persistent shell directory.

  Returns a list of human-readable action strings describing what was
  (or would be, in dry_run mode) cleaned up.  The list is empty when
  there is nothing to do.

  ``agent_critical_dests`` is an optional list of ``(rel, kind)`` pairs
  (``rel`` shell_dir-relative, ``kind`` in {"file","dir"}) naming the
  AGENT_CRITICAL delivery bind mountpoints of the installed plugins.  When
  provided, any STALE EMPTY stub at one of those dests is reaped (see
  :func:`_reap_stale_agent_mountpoints`).  Defaults to None → that step is a
  no-op, so callers that do not pass it (and existing tests) are unaffected.
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

  # 5. Reap stale EMPTY agent-critical bind mountpoints (0-byte decoys).
  actions.extend(_reap_stale_agent_mountpoints(shell_dir, agent_critical_dests, dry_run, logger))

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
    if not _is_box_dir(target, shell_dir):
      continue
    freed = _dir_size(target, shell_dir)
    if freed == 0:
      continue
    desc = f"{'[dry-run] ' if dry_run else ''}Removed {rel}/ contents ({_fmt_size(freed)})"
    if not dry_run:
      _remove_dir_contents(target, shell_dir)
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
    if not _is_box_dir(target, shell_dir):
      continue
    freed = _dir_size(target, shell_dir)
    if freed == 0:
      continue
    desc = f"{'[dry-run] ' if dry_run else ''}Removed {rel}/ ({_fmt_size(freed)})"
    if not dry_run:
      _remove_tree(target, shell_dir)
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
      size = boxfs.stat(candidate, shell_dir).st_size
    except OSError:
      continue

    if size < min_size:
      continue

    rel_path = candidate.relative_to(shell_dir)
    desc = (
      f"{'[dry-run] ' if dry_run else ''}Removed duplicate binary {rel_path} ({_fmt_size(size)})"
    )
    if not dry_run:
      try:
        boxfs.unlink(candidate, shell_dir)
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
    claude_file = d / "claude"
    link = boxfs.first_symlink(claude_file, shell_dir)
    if link == claude_file:
      continue  # a link named ``claude`` is not a stray copy: skipped quietly
    if link is not None:
      boxfs.no_symlink_or_warn(claude_file, shell_dir)
      continue
    try:
      mode = boxfs.stat(claude_file, shell_dir).st_mode
    except OSError:
      continue
    if _st.S_ISREG(mode):
      candidates.append(claude_file)

  return candidates


def _reap_stale_agent_mountpoints(
  shell_dir: Path,
  agent_critical_dests: list[tuple[str, str]] | None,
  dry_run: bool,
  logger: object,
) -> list[str]:
  """Reap stale EMPTY agent-critical bind mountpoints (0-byte decoys).

  A prior AGENT launch pre-creates the box-side mountpoint for each
  AGENT_CRITICAL delivery bind on the HOST under the persistent shell dir —
  a 0-byte file for a file bind, an empty dir for a dir bind.  The ro bind
  overlays them at runtime, but the stub PERSISTS on disk after the container
  exits.  A subsequent no-agent ``shell`` launch never overlays them, so the
  bare stub shows through as a misleading "decoy" (e.g. a 0-byte, non-exec
  ``~/.local/bin/claude`` plus an empty ``~/.local/share/claude/``).  Reap
  those stubs while they are still EMPTY.

  CONSERVATIVE — never touch real data.  For each ``(rel, kind)``:

  * SYMLINK          -> skip (out of scope; not a stub we created).
  * ``kind=="file"`` -> reap ONLY a regular file of size 0.
  * ``kind=="dir"``  -> reap ONLY a directory with no entries.
  * anything else (missing / non-empty dir / non-zero file / other kind)
    -> left untouched.

  Safe on EVERY launch: an agent ``start`` re-precreates + overlays its own
  stubs AFTER this hygiene sweep runs, so a reaped stub is simply recreated.
  When ``agent_critical_dests`` is None/empty this is a no-op.

  Scope note: reap targets are rooted under *shell_dir* (the box HOME), which
  is correct for every current AGENT_CRITICAL dest (all under ``~/.local``).
  A future AGENT_CRITICAL bind whose ``box_dest`` is under
  ``/home/agent/workspace/`` would be pre-created under the PROJECT dir instead
  (see ``container._guest_dest_to_host``), so it would silently not be reaped
  here — a SAFE failure (never mis-reaps); route ``rel`` through the same
  ``_guest_dest_to_host`` contract if such a dest is ever added.
  """
  actions: list[str] = []
  if not agent_critical_dests:
    return actions

  for rel, kind in agent_critical_dests:
    target = shell_dir / rel

    # Destructive op, so nothing is followed: every check and the removal go
    # through boxfs rooted at shell_dir.  A ``..`` in a descriptor's box_dest is
    # never acted on (box_dests come from trusted installed plugins, so this is
    # belt-and-suspenders); a link AT the stub is not one we made, and is skipped
    # quietly; a linked parent is skipped with a warning.
    try:
      link = boxfs.first_symlink(target, shell_dir)
    except ValueError:
      continue
    if link == target:
      continue
    if link is not None:
      boxfs.no_symlink_or_warn(target, shell_dir)
      continue
    try:
      st = boxfs.stat(target, shell_dir)
    except OSError:
      continue

    if kind == "file":
      if not _st.S_ISREG(st.st_mode) or st.st_size != 0:
        continue
    elif kind == "dir":
      if not _st.S_ISDIR(st.st_mode):
        continue
      try:
        if boxfs.listdir(target, shell_dir):
          continue
      except OSError:
        continue
    else:
      # Unknown kind — leave untouched.
      continue

    desc = f"{'[dry-run] ' if dry_run else ''}Reaped stale agent mountpoint {rel} ({kind})"
    if not dry_run:
      try:
        if kind == "file":
          boxfs.unlink(target, shell_dir)
        else:
          # ``rmdir`` is the emptiness test: an entry the box made since
          # the check stays.
          boxfs.rmdir(target, shell_dir)
      except OSError:
        continue
    actions.append(desc)

  return actions


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

  # ``projects/*/conversation_logs/*.jsonl``, listed and opened through boxfs.
  for log_file in _conversation_logs(projects_dir, shell_dir):
    try:
      st = boxfs.stat(log_file, shell_dir)
    except OSError:
      continue
    if not _st.S_ISREG(st.st_mode) or st.st_mtime >= cutoff:
      continue

    original_size = st.st_size
    if original_size == 0:
      continue

    rel_path = log_file.relative_to(shell_dir)
    gz_path = log_file.with_suffix(log_file.suffix + ".gz")

    # A link the box planted at the ``.gz`` is never written through: warned, skipped.
    if not boxfs.no_symlink_or_warn(gz_path, shell_dir):
      continue

    if not dry_run:
      try:
        compressed_size = _gzip_file(log_file, gz_path, shell_dir)
      except boxfs.SymlinkRefusedError as exc:  # planted after the check
        get_logger("hygiene").warning("skipped %s: %s", gz_path, exc)
        continue
      except OSError:
        continue
    else:
      compressed_size = original_size  # estimate unavailable in dry-run

    desc = f"{'[dry-run] ' if dry_run else ''}Compressed {rel_path} ({_fmt_size(original_size)}"
    if not dry_run:
      desc += f" -> {_fmt_size(compressed_size)}"
    desc += ")"
    actions.append(desc)

  return actions


def _conversation_logs(projects_dir: Path, root: Path) -> list[Path]:
  """The ``*/conversation_logs/*.jsonl`` entries under *projects_dir*, sorted.

  A linked ``projects``, project, or ``conversation_logs`` dir, or a linked log, is
  skipped with a warning.
  """
  logs: list[Path] = []
  for project in _box_listdir(projects_dir, root):
    logs_dir = projects_dir / project / "conversation_logs"
    for name in _box_listdir(logs_dir, root):
      log_file = logs_dir / name
      if name.endswith(".jsonl") and boxfs.no_symlink_or_warn(log_file, root):
        logs.append(log_file)
  return logs


def _gzip_file(src: Path, dst: Path, root: Path) -> int:
  """Compress *src* to *dst* with gzip, remove *src*, and return *dst*'s size.

  Both are under the box tree *root*, read and written through boxfs: *dst* is
  replaced atomically, keeping *src*'s mode and times.  *src* is read whole.
  """
  data, st = boxfs.read_bytes_with_stat(src, root)
  packed = gzip.compress(data, mtime=int(st.st_mtime))
  boxfs.write_bytes(
    dst,
    root,
    packed,
    mode=_st.S_IMODE(st.st_mode),
    times=(st.st_atime_ns, st.st_mtime_ns),
  )
  boxfs.unlink(src, root)
  return len(packed)


def _is_box_dir(path: Path, root: Path) -> bool:
  """True when *path* is a directory reached with no link; a link is warned."""
  if not boxfs.no_symlink_or_warn(path, root):
    return False
  try:
    return _st.S_ISDIR(boxfs.stat(path, root).st_mode)
  except OSError:
    return False


def _box_listdir(path: Path, root: Path) -> list[str]:
  """:func:`boxfs.listdir`, sorted; ``[]`` when *path* is missing or not a
  directory, and a warning too when it is linked."""
  if not boxfs.no_symlink_or_warn(path, root):
    return []
  try:
    return sorted(boxfs.listdir(path, root))
  except OSError:
    return []


def _no_raise(remove: Callable[[], None], path: Path) -> None:
  """Run *remove*; an error is logged, never raised (hygiene never stops a start)."""
  try:
    remove()
  except OSError as exc:
    get_logger("hygiene").warning("hygiene could not remove %s: %s", path, exc)


def _remove_dir_contents(d: Path, root: Path) -> None:
  """Remove all entries inside *d* without removing *d* itself; a read-only
  directory in the tree is widened first (``force``)."""
  _no_raise(lambda: boxfs.rmtree_contents(d, root, force=True), d)


def _remove_tree(d: Path, root: Path) -> None:
  """Remove the directory *d* and everything in it (``force``, as above)."""
  _no_raise(lambda: boxfs.rmtree(d, root, missing_ok=True, force=True), d)


def _dir_size(d: Path, root: Path) -> int:
  """Total size of the regular files under *d*; links are not followed or counted.

  Every entry is listed and stat'ed through boxfs; an entry that cannot be read is
  left out of the total.
  """
  total = 0
  pending = [d]
  while pending:
    current = pending.pop()
    try:
      names = boxfs.listdir(current, root)
    except OSError:
      continue
    for name in names:
      entry = current / name
      try:
        st = boxfs.stat(entry, root)
      except OSError:  # a link (SymlinkRefusedError) included
        continue
      if _st.S_ISDIR(st.st_mode):
        pending.append(entry)
      elif _st.S_ISREG(st.st_mode):
        total += st.st_size
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
