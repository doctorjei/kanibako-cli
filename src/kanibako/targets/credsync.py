"""Agent-agnostic credential-sync engine (LIVE — wired into start.py).

This module centralizes the per-plugin credential lifecycle that previously lived
in each plugin's ``init_home`` / ``refresh_credentials`` / ``writeback_credentials``
hooks.  It is driven entirely by a :class:`~kanibako.targets.base.PluginDescriptor`
(its ``init_dirs`` and ``cred_files``) plus the plugin's
:meth:`~kanibako.targets.base.Target.transform_cred` hook for the divergent
filter/merge payload of ``filtered`` specs.

Three primitives map onto the three lifecycle phases:

* :func:`seed_cred_files`      — project init (replaces the credential part of ``init_home``)
* :func:`refresh_cred_files`   — pre-launch host->project sync (replaces ``refresh_credentials``)
* :func:`writeback_cred_files` — post-session project->host sync (replaces ``writeback_credentials``)

The engine owns the data-driven mtime gating / directory creation / chmod; the
per-file transform (claude ``claudeAiOauth`` merge + ``.claude.json`` allowlist,
goose ``config.yaml`` allowlist) stays a plugin hook reached via ``transform_cred``.

NOTE: This engine is LIVE.  ``commands/start.py`` calls :func:`seed_cred_files`
(new boxes), :func:`refresh_cred_files` (reattach + pre-launch), and
:func:`writeback_cred_files` (post-session) for every descriptor-bearing target;
the per-plugin ``init_home`` / ``refresh_credentials`` / ``writeback_credentials``
primitives are bypassed on the descriptor path.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.log import get_logger
from kanibako.targets.base import Cadence

if TYPE_CHECKING:
    from kanibako.targets.base import PluginDescriptor, Target

logger = get_logger("credsync")


def _chmod_600(path: Path) -> None:
    """Set 0600 (owner read/write) permissions on *path*, best-effort."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.debug("credsync: chmod 0600 failed for %s: %s", path, exc)


def _copy_dir(src: Path, dst: Path) -> None:
    """Recursively copy directory *src* into *dst*, merging into an existing dst.

    Used for ``is_dir`` cred specs (e.g. goose ``custom_providers/``).  A
    directory has no single mtime to gate on, so dir specs are NOT mtime-gated:
    the source tree is the authority and is mirrored wholesale on every sync.
    Each copied file is chmod 0600 (these dirs hold config that may reference
    secrets, so we keep the same conservative perms as the file path).
    """
    shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
    for child in dst.rglob("*"):
        if child.is_file():
            _chmod_600(child)


def seed_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    host_home: Path,
    project_home: Path,
    group_auth: bool,
) -> None:
    """Seed credential/config files into a freshly-created project home.

    Replaces the credential portion of each plugin's ``init_home``.  Creates
    the descriptor's ``init_dirs`` and lays down the initial in-copy of every
    ``cred_files`` spec (BOTH cadences get their initial in-copy at seed time;
    cadence only governs later refresh/writeback).

    Filtered specs are routed through ``target.transform_cred(..., "in")`` so the
    plugin can filter/merge or write a default when no source is available.
    Unfiltered specs are a wholesale copy + 0600 when a host source exists and
    *group_auth* is set; otherwise nothing is seeded (e.g. goose secrets under
    distinct auth).
    """
    for d in descriptor.init_dirs:
        (project_home / d).mkdir(parents=True, exist_ok=True)

    for spec in descriptor.cred_files:
        dst = project_home / spec.home_rel
        src = host_home / spec.host_rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if spec.is_dir:
            # Directory spec (e.g. goose custom_providers/): recursive copy when
            # a host source dir exists under group auth.  Never filtered.
            if group_auth and src.is_dir():
                _copy_dir(src, dst)
            continue

        src_ok = group_auth and src.is_file()

        if spec.filtered:
            target.transform_cred(spec, src if src_ok else None, dst, "in")
            if dst.is_file():
                _chmod_600(dst)
        elif src_ok:
            shutil.copy2(str(src), str(dst))
            _chmod_600(dst)
        # else: unfiltered + no source -> nothing to seed.


def refresh_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    host_home: Path,
    project_home: Path,
    group_auth: bool,
) -> None:
    """Pre-launch host->project sync of SYNC-cadence credential files.

    Replaces ``refresh_credentials``.  No-op when *group_auth* is False.
    ``SEED_ONCE`` specs are never refreshed.  For ``SYNC`` specs the host file
    must exist and (when ``mtime_gate``) be strictly newer than the project
    file; filtered specs route through ``transform_cred(..., "in")``, unfiltered
    specs are a wholesale copy + 0600.
    """
    if not group_auth:
        return

    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = host_home / spec.host_rel
        dst = project_home / spec.home_rel
        if spec.is_dir:
            # Directory spec: mirror the host dir into the box (no mtime gate;
            # see _copy_dir).  Skip silently when the host dir is absent.
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                _copy_dir(src, dst)
            continue
        if not src.is_file():
            continue
        if (
            spec.mtime_gate
            and dst.is_file()
            and src.stat().st_mtime <= dst.stat().st_mtime
        ):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if spec.filtered:
            target.transform_cred(spec, src, dst, "in")
            if dst.is_file():
                _chmod_600(dst)
        else:
            shutil.copy2(str(src), str(dst))
            _chmod_600(dst)


def writeback_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    host_home: Path,
    project_home: Path,
    group_auth: bool,
) -> None:
    """Post-session project->host sync of SYNC-cadence credential files.

    Replaces ``writeback_credentials``.  No-op when *group_auth* is False.
    ``SEED_ONCE`` specs are never written back.  Direction is REVERSED: the
    project file is the source and the host file the destination.  The project
    file must exist and (when ``mtime_gate``) be strictly newer than the host
    file; filtered specs route through ``transform_cred(..., "out")``, unfiltered
    specs are a wholesale copy (NO host chmod — writeback is wholesale, matching
    ``cp_if_newer``).
    """
    if not group_auth:
        return

    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = project_home / spec.home_rel
        dst = host_home / spec.host_rel
        if spec.is_dir:
            # Directory spec: mirror the box dir back to the host (no mtime
            # gate; see _copy_dir).  Skip silently when the box dir is absent.
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                _copy_dir(src, dst)
            continue
        if not src.is_file():
            continue
        if (
            spec.mtime_gate
            and dst.is_file()
            and src.stat().st_mtime <= dst.stat().st_mtime
        ):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if spec.filtered:
            target.transform_cred(spec, src, dst, "out")
        else:
            shutil.copy2(str(src), str(dst))
