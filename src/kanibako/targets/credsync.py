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
    from kanibako.settings_launch import AuthSource
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
    source_root: Path | None,
    project_home: Path,
) -> None:
    """Seed credential/config files into a freshly-created project home.

    Replaces the credential portion of each plugin's ``init_home``.  Creates
    the descriptor's ``init_dirs`` and lays down the initial in-copy of every
    ``cred_files`` spec (BOTH cadences get their initial in-copy at seed time;
    cadence only governs later refresh/writeback).

    *source_root* is the SELECTED credential SOURCE root (auth-level design step
    4): the host home for the GLOBAL tier, the box's per-agent WORKSET source dir
    for the WORKSET tier, or ``None`` for the private/BOX tier (no source — today's
    distinct auth). Each spec's ``host_rel`` joins under it (the layout mirrors the
    in-guest ``home_rel``). ``None`` degenerates to "no source": unfiltered specs
    seed nothing, filtered specs get ``src=None`` (the plugin writes its default).

    Filtered specs are routed through ``target.transform_cred(..., "in")`` so the
    plugin can filter/merge or write a default when no source is available.
    Unfiltered specs are a wholesale copy + 0600 when a source file exists.
    """
    for d in descriptor.init_dirs:
        (project_home / d).mkdir(parents=True, exist_ok=True)

    for spec in descriptor.cred_files:
        dst = project_home / spec.home_rel
        src = source_root / spec.host_rel if source_root is not None else None
        dst.parent.mkdir(parents=True, exist_ok=True)

        if spec.is_dir:
            # Directory spec (e.g. goose custom_providers/): recursive copy when
            # a source dir exists (a private box has no source).  Never filtered.
            if src is not None and src.is_dir():
                _copy_dir(src, dst)
            continue

        src_ok = src is not None and src.is_file()

        if spec.filtered:
            target.transform_cred(spec, src if src_ok else None, dst, "in")
            if dst.is_file():
                _chmod_600(dst)
        elif src_ok:
            assert src is not None  # src_ok implies src present.
            shutil.copy2(str(src), str(dst))
            _chmod_600(dst)
        # else: unfiltered + no source -> nothing to seed.


def refresh_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    source_root: Path | None,
    project_home: Path,
) -> None:
    """Pre-launch source->project sync of SYNC-cadence credential files.

    Replaces ``refresh_credentials``.  No-op when *source_root* is ``None`` (the
    private/BOX tier — today's distinct auth).  ``SEED_ONCE`` specs are never
    refreshed.  For ``SYNC`` specs the source file (under *source_root* — the host
    home for GLOBAL, the workset source dir for WORKSET) must exist and (when
    ``mtime_gate``) be strictly newer than the project file; filtered specs route
    through ``transform_cred(..., "in")``, unfiltered specs are a wholesale copy +
    0600.
    """
    if source_root is None:
        return

    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = source_root / spec.host_rel
        dst = project_home / spec.home_rel
        if spec.is_dir:
            # Directory spec: mirror the source dir into the box (no mtime gate;
            # see _copy_dir).  Skip silently when the source dir is absent.
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
    source_root: Path | None,
    project_home: Path,
) -> None:
    """Post-session project->source sync of SYNC-cadence credential files.

    Replaces ``writeback_credentials``.  No-op when *source_root* is ``None`` (the
    private/BOX tier).  ``SEED_ONCE`` specs are never written back.  Direction is
    REVERSED: the project file is the source and the file under *source_root* (the
    host home for GLOBAL, the workset source dir for WORKSET) the destination.  The
    project file must exist and (when ``mtime_gate``) be strictly newer than the
    dest; filtered specs route through ``transform_cred(..., "out")``, unfiltered
    specs are a wholesale copy (NO dest chmod — writeback is wholesale, matching
    ``cp_if_newer``).
    """
    if source_root is None:
        return

    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = project_home / spec.home_rel
        dst = source_root / spec.host_rel
        if spec.is_dir:
            # Directory spec: mirror the box dir back to the source (no mtime
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


# --------------------------------------------------------------------------- #
# Tier orchestrators — the 3-tier SHARING dispatch (auth-level design step 4)  #
# --------------------------------------------------------------------------- #
#
# The credsync PRIMITIVES above are the ONE uniform op ("sync a cred dir against a
# source root"). These orchestrators apply that op at the SELECTED tier per the
# resolved :class:`~kanibako.settings_launch.AuthSource` (precedence workset >
# global > box):
#
#   * GLOBAL tier  → source_root = the host home (host_rel). One hop.
#   * WORKSET tier → source_root = the box's per-agent workset source dir
#     (box.auth.workset_path; layout mirrors home_rel). PLUS, when global_sync is
#     on, the SAME primitive runs a SECOND hop between the workset dir and global —
#     the workset dir is just another sync client of global (design SYNC). Natural
#     ordering: refresh TOP-DOWN (global → workset → box), writeback BOTTOM-UP
#     (box → workset → global).
#   * BOX tier     → source_root = None → the primitives no-op (private/distinct).
#
# The workset dir is synced against global by treating it as ANOTHER "project
# home" (its layout mirrors home_rel — the SAME layout the box uses), with global
# (host home) as the source root — no new mechanism, the same primitive.


def selected_source_root(
    auth: AuthSource, *, host_home: Path
) -> Path | None:
    """The credential SOURCE root the box syncs its OWN cred files against.

    Per the resolved tier (precedence workset>global): the workset per-agent
    source dir for ``"workset"``, the host home for ``"global"``, ``None`` for the
    private ``"box"`` tier (the primitives no-op). The workset source is
    ``auth.workset_source`` (``box.auth.workset_path`` — already resolved).
    """
    if auth.tier == "workset" and auth.workset_source is not None:
        return Path(auth.workset_source)
    if auth.tier == "global":
        return host_home
    return None


def _sync_workset_dir_from_global(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
) -> None:
    """TOP-DOWN hop: refresh the WORKSET auth dir FROM global (design SYNC).

    Runs only for the WORKSET tier with ``global_sync`` on. Treats the workset
    source dir as another sync client of global (host home) via the SAME
    ``refresh_cred_files`` primitive — the workset dir's layout mirrors ``home_rel``
    (the same layout a box uses), so the primitive applies unchanged.
    """
    if (
        auth.tier != "workset"
        or not auth.global_sync
        or auth.workset_source is None
    ):
        return
    refresh_cred_files(
        descriptor, target,
        source_root=host_home,
        project_home=Path(auth.workset_source),
    )


def _sync_workset_dir_to_global(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
) -> None:
    """BOTTOM-UP hop: write the WORKSET auth dir back TO global (design SYNC).

    Runs only for the WORKSET tier with ``global_sync`` on. The mirror of
    :func:`_sync_workset_dir_from_global` via ``writeback_cred_files`` (project =
    the workset dir, dest = global/host home).
    """
    if (
        auth.tier != "workset"
        or not auth.global_sync
        or auth.workset_source is None
    ):
        return
    writeback_cred_files(
        descriptor, target,
        source_root=host_home,
        project_home=Path(auth.workset_source),
    )


def _create_workset_source_dirs(
    descriptor: PluginDescriptor, *, auth: AuthSource
) -> None:
    """Create the WORKSET credential SOURCE dirs the plugin owns (design step 5).

    Because the plugin OWNS the in-guest cred LAYOUT (``home_rel`` / ``init_dirs``),
    it also creates the matching WORKSET sync-source directories in that layout:
    the per-agent workset auth dir (``box.auth.workset_path`` = the source root),
    its ``init_dirs`` substructure, and each cred file's parent dir. The GLOBAL
    source is NOT created (it is the user's real host login location). No-op unless
    the WORKSET tier is actually selected.

    ⚑ Gated on ``auth.tier == "workset"`` (NOT merely on ``workset_source is not
    None``): a non-workset box (standalone/global/private) must NEVER mkdir a
    workset source dir. The resolver already scrubs ``workset_source`` to None off
    the workset tier, but this tier gate is the primary guard — belt-and-braces so
    a stray resolved path can never mkdir against the host root.
    """
    if auth.tier != "workset" or auth.workset_source is None:
        return
    root = Path(auth.workset_source)
    root.mkdir(parents=True, exist_ok=True)
    for d in descriptor.init_dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for spec in descriptor.cred_files:
        (root / spec.host_rel).parent.mkdir(parents=True, exist_ok=True)


def seed_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
) -> None:
    """Seed a fresh box's creds from the SELECTED tier source (design step 4/5).

    Plugin-owned WORKSET source-dir creation runs first (design step 5 — the
    plugin owns the layout, so it creates the workset source dirs mirroring
    ``home_rel``). Then TOP-DOWN: refresh the workset dir from global (when the
    workset tier is global-synced), then seed the box FROM the selected source root
    (workset dir / host home / None). ``init_dirs`` in the box home are always
    created (even for the private tier, which seeds no cred content).
    """
    _create_workset_source_dirs(descriptor, auth=auth)
    _sync_workset_dir_from_global(
        descriptor, target, auth=auth, host_home=host_home
    )
    seed_cred_files(
        descriptor, target,
        source_root=selected_source_root(auth, host_home=host_home),
        project_home=project_home,
    )


def refresh_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
) -> None:
    """Pre-launch refresh from the SELECTED tier source (design step 4).

    TOP-DOWN: refresh the workset dir from global first (when global-synced), then
    refresh the box FROM the selected source root. A private box (tier ``"box"``)
    is a no-op (source_root None). The workset source dirs are ensured present
    (idempotent) so a box that adopted the workset tier after creation still finds
    its source layout.
    """
    _create_workset_source_dirs(descriptor, auth=auth)
    _sync_workset_dir_from_global(
        descriptor, target, auth=auth, host_home=host_home
    )
    refresh_cred_files(
        descriptor, target,
        source_root=selected_source_root(auth, host_home=host_home),
        project_home=project_home,
    )


def writeback_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
) -> None:
    """Post-session writeback to the SELECTED tier source (design step 4).

    BOTTOM-UP: write the box back TO the selected source root first, then (for the
    global-synced workset tier) write the workset dir back UP to global. A private
    box is a no-op.
    """
    writeback_cred_files(
        descriptor, target,
        source_root=selected_source_root(auth, host_home=host_home),
        project_home=project_home,
    )
    _sync_workset_dir_to_global(
        descriptor, target, auth=auth, host_home=host_home
    )
