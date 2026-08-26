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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
class WorksetPartition:
    """``workset.channels.{mailboxes,share_global}`` — the ALL-PROJECTS partition roots.

    ⚑ NOT :class:`SystemPartition`, which is the same pair of paths reached WITHOUT the
    keys: that one is the raw ``(std, ws_token)`` primitive the relocation path needs,
    and it is also this pair's DEFAULT.  These two are what the keyspace answers, so a
    repoint shows up here and not there.
    """

    ws_token: str
    mailboxes: Path
    share_global: Path


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

    The RAW-TOKEN primitive for move/convert relocation, which needs BOTH the OLD and
    the NEW partition and works from a pair of ``ProjectState``s rather than a resolved
    ``ProjectPaths``.

    ⚑⚑ IT IS THE DEFAULT, NOT THE KEY.  :func:`box_channel_addresses` routes through
    ``workset.channels.{mailboxes,share_global}`` and this does not — it has no workset
    root to read a repoint from.  So a relocation between two worksets, either of which
    repoints ``mailboxes``, moves the DEFAULT partition dir rather than the repointed
    one.  Closing that needs the caller (``commands/box/_lifecycle.py``) to hand over
    each side's workset root; it is a KNOWN GAP, not an oversight, and it is not
    reachable without a repoint.
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


#: The chat log that names NO KEY.  ``general.md`` is the default log every box writes
#: to, and the keyspace declares nothing for it — so it is the ONE leaf still joined by
#: hand, and it is joined onto the RESOLVED chat dir, never onto a re-derived one.
#: ⚑ Its sibling ``broadcast.md`` IS a key (``workset.channels.broadcast`` /
#: ``system.channels.broadcast``) and must never be joined like this.
#: ⚑ PUBLIC because the launch's chat-log seeder needs the SYSTEM scope's copy of the
#: same name, and two spellings of a non-key is exactly how a non-key starts to drift.
CHAT_GENERAL_LEAF = "general.md"


def _channels_repoint(
    workset_settings: Mapping[str, Any] | None, leaf: str
) -> str | None:
    """Return the RAW ``workset.channels.<leaf>`` repoint from an already-loaded doc.

    ⚑ The file slot comes from ``config_keys._KEY_ROUTES`` — the same table
    ``config set`` writes through — so the slot this reads and the slot the CLI writes
    cannot drift into two places.  Absent, empty and unreadable all mean "not
    repointed", which is what makes the key's DEFAULT the value in the common case.
    """
    from kanibako.settings.config_keys import _KEY_ROUTES

    sections, slot = _KEY_ROUTES[f"workset.channels.{leaf}"]
    node: object = workset_settings
    for section in sections:
        if not isinstance(node, Mapping):
            return None
        node = node.get(section)
    if not isinstance(node, Mapping):
        return None
    value = node.get(slot)
    return str(value) if value else None


def _channel_key(
    ws_root: Path, workset_settings: Mapping[str, Any] | None, leaf: str, default: Path
) -> Path:
    """Resolve ``workset.channels.<leaf>``: its stored repoint, else *default*.

    ⚑ THE DEFAULT IS THE CALLER'S because these defaults hang off the resolved
    ``workset.channelroot`` (or the system partition), and that is the one thing
    ``resolve_workset_dir_key`` cannot supply — it anchors at the workset root.
    Everything a repoint can contain (``@``-refs, ``$XDG_*``, ``~``, the relative
    anchor, and the refusal that names the key) stays that ONE pre-snapshot route's
    business; this adds no second grammar.
    """
    from kanibako.settings.workset_dirkeys import resolve_workset_dir_key

    repoint = _channels_repoint(workset_settings, leaf)
    if repoint is None:
        return default
    return resolve_workset_dir_key(ws_root, repoint, leaf, key=f"channels.{leaf}")


def workset_channel_paths(
    proj: ProjectPaths, std: StandardPaths
) -> WorksetChannels | None:
    """Derive the WORKSET-local channel roots for *proj*; ``None`` for standalone.

    ⚑⚑ EVERY LEAF IS RESOLVED THROUGH ITS OWN DECLARED KEY, never joined onto the root
    (R-35, "fix the CODE").  Joining looked harmless because the joins ARE the spec's
    defaults, but it made the keys inert: ``chat`` was a split carrier (the bind
    followed the override while the chat-log seeder followed the join), and
    ``broadcast`` had no consumer at all.  A closed keyspace that accepts a key and
    then ignores it is worse than one that refuses it.
    """
    if not has_workset_channels(proj):
        return None
    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workset_channelroot,
    )

    ws_root = workset_root(proj, std)
    doc = load_workset_settings_doc(ws_root)
    root = resolve_workset_channelroot(ws_root, doc)
    chat = _channel_key(ws_root, doc, "chat", root / "chat")
    return WorksetChannels(
        root=root,
        common=_channel_key(ws_root, doc, "common", root / "common"),
        chat=chat,
        chat_general=chat / CHAT_GENERAL_LEAF,
        chat_broadcast=_channel_key(
            ws_root, doc, "broadcast", chat / "broadcast.md",
        ),
        share=_channel_key(ws_root, doc, "share", root / "share"),
    )


def workset_partition_paths(
    proj: ProjectPaths, std: StandardPaths
) -> WorksetPartition:
    """Derive ``workset.channels.{mailboxes,share_global}`` — ALL PROJECTS, every mode.

    ⚑ NOT gated on :func:`has_workset_channels` (D-M9): these two keys aggregate at the
    SYSTEM scope partitioned by workset name, so a standalone box has them exactly as a
    primary one does.  Their default IS :func:`system_partition`, which is exactly why
    the un-keyed version looked correct: it produced the right value and obeyed no key.
    """
    from kanibako.project.workset import load_workset_settings_doc

    ws_token = workset_name_token(proj)
    ws_root = workset_root(proj, std)
    default = system_partition(std, ws_token)
    doc = load_workset_settings_doc(ws_root)
    return WorksetPartition(
        ws_token=ws_token,
        mailboxes=_channel_key(ws_root, doc, "mailboxes", default.mailboxes),
        share_global=_channel_key(ws_root, doc, "share_global", default.share),
    )


def box_channel_addresses(
    proj: ProjectPaths, std: StandardPaths
) -> BoxChannelAddresses:
    """Derive this box's own partition addresses (``meta.box.*``) for *proj*.

    ``inbox`` / ``share_global`` always resolve (system-scope, every mode);
    ``share_workset`` is ``None`` for standalone.  ⚑ RAISES on a nameless box —
    callers on the launch path resolve the name first.

    ⚑ ALL THREE ADDRESSES HANG OFF THE KEYS, which is the manifest's own spelling:
    ``@workset.channels.mailboxes/@meta.box.name``,
    ``@workset.channels.share_global/@meta.box.name``,
    ``@workset.channels.share/@meta.box.name``.  Reading the partition off
    :func:`system_partition` here is what let a user repoint ``mailboxes``, watch
    ``config get`` read the new value back, and still have their inbox mounted at the
    old one.
    """
    if not proj.name:
        raise ValueError(
            "box has no name; cannot derive its channel partition addresses."
        )
    part = workset_partition_paths(proj, std)
    wch = workset_channel_paths(proj, std)
    return BoxChannelAddresses(
        ws_token=part.ws_token,
        box_name=proj.name,
        inbox=part.mailboxes / proj.name,
        share_global=part.share_global / proj.name,
        share_workset=(wch.share / proj.name) if wch is not None else None,
    )
