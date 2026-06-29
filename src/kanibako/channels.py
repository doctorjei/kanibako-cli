"""Channel path resolution + per-instance partition addressing (PURE helpers).

Phase 6 (sub-step 6a) of the 1.6.0 config/settings revamp.  This module derives
the host-side channel paths from an already-resolved :class:`~kanibako.paths.
ProjectPaths` (``proj``) + :class:`~kanibako.paths.StandardPaths` (``std``).  It
is **pure derivation only** — it computes paths, it does NOT create directories,
touch launch mounts, or change any behavior.  The bind production / mkdir /
file-seeding live in sub-step 6b.

Two channel scopes (TARGET §1, §2c, §2f):

* **system scope** — five type roots under ``@system.channelroot`` (commons, chat,
  broadcast, mailboxes, share).  The instance-owned types (mailboxes, share) are
  *partitioned* by the workset-name token: ``mailboxes/<ws>`` and ``share/<ws>``
  where ``<ws>`` is ``__PRIMARY__`` | ``<named>`` | ``__STANDALONE__``.  These
  partition roots apply to EVERY mode (standalone included).
* **workset scope** — three type roots under ``@meta.workset.path/channels``
  (commons, chat, share).  These exist for the PRIMARY and NAMED modes ONLY;
  standalone has no workset-local channels (its ``workset.channelroot`` is
  ``<None>`` per the TARGET).

The per-instance partition ADDRESSES (``meta.box.{inbox,share_global,
share_workset}``) are this box's own mailbox/share dirs *within* the partitioned
roots — derived from the workset-name token + the box name.

⚑ The workset-name token and the workset root are NOT carried verbatim on
``ProjectPaths`` (the legacy comms block used only ``proj.name``).  Per the
design-review A8 resolution we derive them HERE from ``proj.mode`` + ``proj.
group`` rather than widening the resolver's public shape: PRIMARY roots at
``@system.primary_workset`` with token ``__PRIMARY__``; NAMED roots at
``proj.group.root`` with token ``proj.group.name``; STANDALONE roots at
``proj.metadata_path`` (the root; the workspace is a subdir) with token
``__STANDALONE__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle (paths.py would import this later)
    from kanibako.paths import ProjectPaths, StandardPaths


# Reserved workset-name tokens for the system-scope partition key.  A named
# workset may not use either (Phase 5 reserves them at create time, 5e); they
# stand for the PRIMARY and STANDALONE pseudo-worksets.
WS_TOKEN_PRIMARY = "__PRIMARY__"
WS_TOKEN_STANDALONE = "__STANDALONE__"


@dataclass(frozen=True)
class SystemPartition:
    """The per-workset SYSTEM-scope partition roots (keyed by the ws token).

    ``mailboxes`` == ``@system.channels.mailboxes/<ws>`` and ``share`` ==
    ``@system.channels.share/<ws>``.  These are the *parents* under which each
    box gets its own ``<box>`` subdir (see :func:`box_channel_addresses`).
    """

    ws_token: str
    mailboxes: Path
    share: Path


@dataclass(frozen=True)
class WorksetChannels:
    """The workset-local channel roots (PRIMARY/NAMED only).

    ``@workset.channelroot = @meta.workset.path/channels`` with ``commons``,
    ``chat`` (+ the reserved ``broadcast.md`` / default ``general.md`` files
    inside it), and ``share`` (per-box subdirs are ``meta.box.share_workset``).
    """

    root: Path
    commons: Path
    chat: Path
    chat_general: Path
    chat_broadcast: Path
    share: Path


@dataclass(frozen=True)
class BoxChannelAddresses:
    """This box's own partition ADDRESSES (TARGET §2c ``meta.box.*``).

    * ``inbox`` == ``@system.channels.mailboxes/<ws>/<box>`` — this box's own
      mailbox dir (also surfaced in-box at ``~/channels/inbox``).
    * ``share_global`` == ``@system.channels.share/<ws>/<box>`` — this box's own
      system-scope publication dir.
    * ``share_workset`` == ``@workset.channels.share/<box>`` — this box's own
      workset-scope publication dir; ``None`` for standalone (no workset-local
      channels).
    """

    ws_token: str
    box_name: str
    inbox: Path
    share_global: Path
    share_workset: Path | None


@dataclass(frozen=True)
class OwnPartition:
    """This box's OWN system-scope partition dirs (mailbox + share_global).

    ``mailbox`` == ``@system.channels.mailboxes/<ws>/<box>`` (this box's inbox
    source dir) and ``share_global`` == ``@system.channels.share/<ws>/<box>``
    (this box's system-scope publication dir).  Used by the move/convert
    relocation (6d), which addresses the partition by raw ``(ws_token, box)``
    rather than a fully-resolved :class:`ProjectPaths`.
    """

    ws_token: str
    box_name: str
    mailbox: Path
    share_global: Path


def own_partition_dirs(
    std: StandardPaths, ws_token: str, box_name: str
) -> OwnPartition:
    """Derive a box's OWN system-scope partition dirs from ``(ws_token, box)``.

    The lower-level primitive behind :func:`box_channel_addresses` — it takes the
    workset-name token + box name directly (no ``ProjectPaths``), so the lifecycle
    relocation can compute BOTH the OLD and the NEW partition for a box being
    moved/converted.  Mirrors :func:`system_partition` + the ``meta.box.{inbox,
    share_global}`` joins (TARGET §2c).
    """
    part = system_partition(std, ws_token)
    return OwnPartition(
        ws_token=ws_token,
        box_name=box_name,
        mailbox=part.mailboxes / box_name,
        share_global=part.share / box_name,
    )


def workset_name_token(proj: ProjectPaths) -> str:
    """Return the workset-name token for *proj* (the system partition key).

    ``__PRIMARY__`` for PRIMARY mode, the named workset's name for NAMED mode,
    ``__STANDALONE__`` for STANDALONE mode.  Derived from ``proj.mode`` +
    ``proj.group`` (A8) rather than read off a dedicated field.
    """
    # Lazy import keeps this module free of an import cycle with paths.py.
    from kanibako.paths import BoxMode

    if proj.mode is BoxMode.primary:
        return WS_TOKEN_PRIMARY
    if proj.mode is BoxMode.standalone:
        return WS_TOKEN_STANDALONE
    # NAMED: the partition key is the named workset's name.
    if proj.group is None or not proj.group.name:
        raise ValueError(
            "NAMED box is missing its workset group/name; cannot derive the "
            "channel partition token."
        )
    return proj.group.name


def workset_root(proj: ProjectPaths, std: StandardPaths) -> Path:
    """Return ``@meta.workset.path`` for *proj*.

    PRIMARY → ``@system.primary_workset``; NAMED → the named workset root
    (``proj.group.root``); STANDALONE → the project root itself
    (``proj.metadata_path``, which for standalone IS the root — the workspace is
    a ``workspace/`` subdir under it, so ``project_path`` is not the root).
    """
    from kanibako.paths import BoxMode

    if proj.mode is BoxMode.primary:
        return std.primary_workset
    if proj.mode is BoxMode.standalone:
        return proj.metadata_path
    if proj.group is None:
        raise ValueError(
            "NAMED box is missing its workset group; cannot derive the workset "
            "root."
        )
    return proj.group.root


def has_workset_channels(proj: ProjectPaths) -> bool:
    """True iff *proj* gets workset-local channels (PRIMARY/NAMED, not standalone).

    Standalone omits ``~/channels/workset/*`` (no workset-local channels); its
    system-scope partition (mailboxes/share_global) still applies (A10).
    """
    from kanibako.paths import BoxMode

    return proj.mode is not BoxMode.standalone


def system_partition(std: StandardPaths, ws_token: str) -> SystemPartition:
    """Derive the SYSTEM-scope per-workset partition roots for *ws_token*.

    ``mailboxes/<ws>`` and ``share/<ws>`` under the resolved system channels
    skeleton.  Applies to EVERY mode — do NOT gate this off the workset-local
    channels (D-M9): standalone still has a ``__STANDALONE__`` partition.
    """
    return SystemPartition(
        ws_token=ws_token,
        mailboxes=std.channels_mailboxes / ws_token,
        share=std.channels_share / ws_token,
    )


def workset_channel_paths(
    proj: ProjectPaths, std: StandardPaths
) -> WorksetChannels | None:
    """Derive the WORKSET-local channel roots for *proj* (PRIMARY/NAMED only).

    Returns ``None`` for standalone (no workset-local channels).  Rooted at
    ``@meta.workset.path/channels`` (A3 — a derived helper, NOT new fields on
    ``ProjectPaths``).
    """
    if not has_workset_channels(proj):
        return None
    root = workset_root(proj, std) / "channels"
    chat = root / "chat"
    return WorksetChannels(
        root=root,
        commons=root / "commons",
        chat=chat,
        chat_general=chat / "general.md",
        chat_broadcast=chat / "broadcast.md",
        share=root / "share",
    )


def box_channel_addresses(
    proj: ProjectPaths, std: StandardPaths
) -> BoxChannelAddresses:
    """Derive this box's own partition addresses (``meta.box.*``) for *proj*.

    ``inbox`` / ``share_global`` always resolve (system-scope, every mode);
    ``share_workset`` is ``None`` for standalone.  *proj.name* is the box name.
    """
    if not proj.name:
        raise ValueError(
            "box has no name; cannot derive its channel partition addresses."
        )
    ws_token = workset_name_token(proj)
    part = system_partition(std, ws_token)
    wch = workset_channel_paths(proj, std)
    return BoxChannelAddresses(
        ws_token=ws_token,
        box_name=proj.name,
        inbox=part.mailboxes / proj.name,
        share_global=part.share / proj.name,
        share_workset=(wch.share / proj.name) if wch is not None else None,
    )
