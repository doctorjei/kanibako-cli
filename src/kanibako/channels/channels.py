"""Channel path resolution + per-instance partition addressing (PURE helpers).

Derives the host-side channel paths — the system-scope and workset-scope roots
plus this box's own mailbox/share addresses inside them — from an already-resolved
:class:`~kanibako.settings.paths.ProjectPaths` (``proj``) +
:class:`~kanibako.settings.paths.StandardPaths` (``std``).  **Pure derivation only:**
it computes paths, it creates no directories, no binds and no files.

The two scopes, the A8 derivation of the workset token/root, the callers, and the
aspirational-permissions stance are in ``llm-docs/kanibako/channels/channels.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle (paths.py would import this later)
    from kanibako.settings.paths import ProjectPaths, StandardPaths


# Reserved workset-name tokens for the system-scope partition key (a named
# workset may not use either — Phase 5 reserves them at create time, 5e).
WS_TOKEN_PRIMARY = "__PRIMARY__"
WS_TOKEN_STANDALONE = "__STANDALONE__"


@dataclass(frozen=True)
class SystemPartition:
    """The per-workset SYSTEM-scope partition roots — the PARENTS of each box's own subdir."""

    ws_token: str
    mailboxes: Path
    share: Path


@dataclass(frozen=True)
class WorksetChannels:
    """The workset-local channel roots under ``@workset.channelroot`` (PRIMARY/NAMED only)."""

    root: Path
    common: Path
    chat: Path
    chat_general: Path
    chat_broadcast: Path
    share: Path


@dataclass(frozen=True)
class BoxChannelAddresses:
    """This box's own partition ADDRESSES (TARGET §2c ``meta.box.*``)."""

    ws_token: str
    box_name: str
    inbox: Path
    share_global: Path
    share_workset: Path | None


@dataclass(frozen=True)
class OwnPartition:
    """This box's OWN system-scope partition dirs (mailbox + share_global)."""

    ws_token: str
    box_name: str
    mailbox: Path
    share_global: Path


def own_partition_dirs(
    std: StandardPaths, ws_token: str, box_name: str
) -> OwnPartition:
    """Derive a box's OWN system-scope partition dirs from ``(ws_token, box)``.

    The raw-token primitive behind :func:`box_channel_addresses`: the move/convert
    relocation needs BOTH the OLD and the NEW partition, and works from a pair of
    ``ProjectState``s rather than a resolved ``ProjectPaths``.
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

    Derived from ``proj.mode`` + ``proj.group`` (A8), not read off a dedicated field.
    """
    # Lazy import keeps this module free of an import cycle with paths.py.
    from kanibako.settings.paths import BoxMode

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
    """Return ``@meta.workset.path`` for *proj* (PRIMARY/NAMED/STANDALONE roots)."""
    from kanibako.settings.paths import BoxMode

    if proj.mode is BoxMode.primary:
        return std.primary_workset
    if proj.mode is BoxMode.standalone:
        # For standalone, metadata_path IS the root; the workspace is a subdir
        # under it, so project_path is NOT the workset root.
        return proj.metadata_path
    if proj.group is None:
        raise ValueError(
            "NAMED box is missing its workset group; cannot derive the workset "
            "root."
        )
    return proj.group.root


def has_workset_channels(proj: ProjectPaths) -> bool:
    """True iff *proj* gets workset-local channels (PRIMARY/NAMED, not standalone).

    ⚑ Standalone omits ``~/channels/workset/*`` but STILL has a system-scope
    partition — never reuse this predicate to gate that one (A10, D-M9).
    """
    from kanibako.settings.paths import BoxMode

    return proj.mode is not BoxMode.standalone


def system_partition(std: StandardPaths, ws_token: str) -> SystemPartition:
    """Derive the SYSTEM-scope ``mailboxes/<ws>`` + ``share/<ws>`` partition roots.

    ⚑ Applies to EVERY mode — do NOT gate this off the workset-local channels
    (D-M9): standalone still has a ``__STANDALONE__`` partition.
    """
    return SystemPartition(
        ws_token=ws_token,
        mailboxes=std.channels_mailboxes / ws_token,
        share=std.channels_share / ws_token,
    )


def workset_channel_paths(
    proj: ProjectPaths, std: StandardPaths
) -> WorksetChannels | None:
    """Derive the WORKSET-local channel roots for *proj*; ``None`` for standalone.

    ⚑ Rooted at the RESOLVED ``workset.channelroot``, never a hard-coded join: a
    repoint in the workset's workset.yaml is honored (§3.3 — real and USED).
    """
    if not has_workset_channels(proj):
        return None
    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workset_channelroot,
    )

    ws_root = workset_root(proj, std)
    root = resolve_workset_channelroot(ws_root, load_workset_settings_doc(ws_root))
    chat = root / "chat"
    return WorksetChannels(
        root=root,
        common=root / "common",
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
    ``share_workset`` is ``None`` for standalone.  ⚑ RAISES on a nameless box —
    callers on the launch path resolve the name first.
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
