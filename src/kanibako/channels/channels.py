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
    from kanibako.settings.paths import BoxMode, ProjectPaths, StandardPaths
    from kanibako.settings.workset_dirkeys import EarlyScope


# Reserved workset-name tokens for the system-scope partition key (a named
# workset may not use either — Phase 5 reserves them at create time, 5e).
WS_TOKEN_PRIMARY = "__PRIMARY__"
WS_TOKEN_STANDALONE = "__STANDALONE__"


@dataclass(frozen=True)
class SystemPartition:
    """The per-workset SYSTEM-scope partition roots — the PARENTS of each box's own subdir."""

    ws_token: str
    # ⚑ ``None`` on a null system key — why, is ``paths._resolve_system_path_keys``; WHAT
    # a null workset-local key means is the workset-local seam's, not this primitive.
    mailboxes: Path | None
    share: Path | None


@dataclass(frozen=True)
class WorksetChannels:
    """The workset-local channel roots under ``@workset.channelroot`` (PRIMARY/NAMED only).

    ⚑ ``None`` on a leaf whose own key is null, the two INDEPENDENTLY.  ⚑ TYPE ONLY —
    what a null leaf DOES is the workset-local seam's; this exists so a ``None`` reaches
    ``settings_launch._workset_channel_floor_values`` instead of ``str()``'d.
    """

    root: Path
    common: Path | None
    chat: Path | None
    chat_general: Path | None
    chat_broadcast: Path | None
    share: Path | None


@dataclass(frozen=True)
class WorksetPartition:
    """``workset.channels.{mailboxes,share_global}`` — the ALL-PROJECTS partition roots.

    ⚑ NOT :class:`SystemPartition`, which is the same pair of paths reached WITHOUT the
    keys: that one is the raw ``(std, ws_token)`` primitive the relocation path needs,
    and it is also this pair's DEFAULT.  These two are what the keyspace answers, so a
    repoint shows up here and not there.
    """

    ws_token: str
    # ⚑ ``None`` on the terms :class:`SystemPartition` states, carried one step: the
    # default is the system partition, so a null system key nulls the workset-local key.
    mailboxes: Path | None
    share_global: Path | None


@dataclass(frozen=True)
class BoxChannelAddresses:
    """This box's own partition ADDRESSES (TARGET §2c ``meta.box.*``)."""

    ws_token: str
    box_name: str
    # ⚑ ``None`` on :class:`SystemPartition`'s terms; a null inbox is what omits the
    # ``~/channels/inbox`` bind, which sources ``meta.box.inbox``.
    inbox: Path | None
    share_global: Path | None
    share_workset: Path | None


@dataclass(frozen=True)
class OwnPartition:
    """This box's OWN partition dirs (mailbox + share_global), reached WITHOUT a ``proj``.

    ⚑ The same two directories :class:`BoxChannelAddresses` calls ``inbox`` and
    ``share_global``, and resolved through the same keys — the difference is the INPUT
    (raw token + workset root, for the relocation path), never the answer.  They were
    two answers once; see :func:`own_partition_dirs`.
    """

    ws_token: str
    box_name: str
    # ⚑ ``None`` on :class:`BoxChannelAddresses`' terms.
    mailbox: Path | None
    share_global: Path | None


def own_partition_dirs(
    std: StandardPaths, ws_token: str, box_name: str, *, ws_root: Path
) -> OwnPartition:
    """Derive a box's OWN partition dirs from ``(ws_token, ws_root, box)``.

    The RAW-INPUT primitive for move/convert relocation, which needs BOTH the OLD and
    the NEW partition and works from a pair of ``ProjectState``s rather than a resolved
    ``ProjectPaths``.

    ⚑⚑ *ws_root* IS REQUIRED, and that is the whole repair.  This used to take
    ``(std, ws_token)`` alone, which is exactly enough to build the partition's DEFAULT
    and not enough to read ``workset.channels.{mailboxes,share_global}`` — so a
    relocation moved the default directory while :func:`box_channel_addresses`, which
    HAS routed through those keys since R-35, mounted the box's inbox at the repointed
    one.  A ``box move`` out of a workset that repoints ``mailboxes`` therefore moved an
    empty directory and left every message the box had received stranded at an address
    no longer registered to it.  An OPTIONAL root would have reproduced that silently
    for any caller who omitted it, so the caller is made to answer.

    ⚑⚑ A NULL PARTITION ARM **PROPAGATES** HERE, naming no error.  ⚑ MERGE DECISION —
    the `channelnull` lane REFUSED this arm (``SettingsError``), and that refusal is
    correct for a workset-local key the user explicitly nulled.  ⚑ It is NOT correct
    in this tree: ``workset.channels.{mailboxes,share_global}`` DEFAULT to
    :func:`system_partition`, and a null SYSTEM key is now a declared value (spec §2a),
    so a legal configuration reaches here as a null arm.  Refusing it would reject
    what the spec says to OMIT.  Ⓣ :func:`partition_key_paths` collapses "present
    ``<None>``" and "absent, defaulting to a null system key" into the same ``None``,
    so the two cannot be told apart here without changing that function's shape —
    which neither lane's grant named.  ⛔ If the refusal is wanted for the EXPLICIT
    case, that is a follow-up: thread the defaulted-vs-declared bit out of
    ``_channel_key``.  ⭐ ``_lifecycle._relocate_channel_partition`` already takes the
    matching arm (it skips a null src/dst), so omit-and-skip is the coherent whole.
    """
    part = partition_key_paths(std, ws_token, ws_root)
    return OwnPartition(
        ws_token=ws_token,
        box_name=box_name,
        mailbox=None if part.mailboxes is None else part.mailboxes / box_name,
        share_global=(
            None if part.share_global is None else part.share_global / box_name
        ),
    )


def workset_name_token(proj: ProjectPaths) -> str:
    """Return the workset-name token for *proj* (the system partition key).

    Derived from ``proj.mode`` + ``proj.group`` (A8), not read off a dedicated field.
    """
    return workset_token(
        proj.mode, proj.group.name if proj.group is not None else None,
    )


def workset_token(mode: BoxMode, group_name: str | None) -> str:
    """The workset-name token for a box of *mode* in the workset named *group_name*.

    The ONE carrier of the token rule; :func:`workset_name_token` is its
    ``ProjectPaths`` adapter, and a box-less resolve (a working set with no box)
    calls it directly.  *group_name* is read only for ``named``.
    """
    # Lazy import keeps this module free of an import cycle with paths.py.
    from kanibako.settings.paths import BoxMode

    if mode is BoxMode.primary:
        return WS_TOKEN_PRIMARY
    if mode is BoxMode.standalone:
        return WS_TOKEN_STANDALONE
    # NAMED: the partition key is the named workset's name.
    if not group_name:
        raise ValueError(
            "NAMED box is missing its workset group/name; cannot derive the "
            "channel partition token."
        )
    return group_name


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

    ⚑⚑ IT IS THE DEFAULT, NOT THE ANSWER.  Its only PRODUCTION caller is
    :func:`partition_key_paths`, which hands it in as the value
    ``workset.channels.{mailboxes,share_global}`` fall back to; the tests that call it
    directly are pinning that default and nothing else.  Reaching for it anywhere on a
    live path is how a repoint gets ignored — that was the whole of the relocation
    defect — so a caller wanting "the box's partition" wants that function.
    """
    return SystemPartition(
        ws_token=ws_token,
        mailboxes=None if std.channels_mailboxes is None
        else std.channels_mailboxes / ws_token,
        share=None if std.channels_share is None
        else std.channels_share / ws_token,
    )


#: The chat log that names NO KEY.  ``general.md`` is the default log every box writes
#: to, and the keyspace declares nothing for it — so it is the ONE leaf still joined by
#: hand, and it is joined onto the RESOLVED chat dir, never onto a re-derived one.
#: ⚑ Its sibling ``broadcast.md`` IS a key (``workset.channels.broadcast`` /
#: ``system.channels.broadcast``) and must never be joined like this.
#: ⚑ PUBLIC because the launch's chat-log seeder needs the SYSTEM scope's copy of the
#: same name, and two spellings of a non-key is exactly how a non-key starts to drift.
CHAT_GENERAL_LEAF = "general.md"


def _channel_key(
    ws_root: Path, workset_settings: Mapping[str, Any] | None, leaf: str,
    default: Path | None, *, standalone: bool | None, early: EarlyScope,
) -> Path | None:
    """Resolve ``workset.channels.<leaf>``: its stored repoint (``early_repoint``), else *default*.

    *standalone* is the box mode the leaf is read in, as ``resolve_workset_dir_key`` takes it:
    ``False`` for the workset-local leaves, ``None`` for the two partition leaves, read in
    every mode.

    ⚑ THE DEFAULT IS THE CALLER'S because these defaults hang off the resolved
    ``workset.channelroot`` (or the system partition), and that is the one thing
    ``resolve_workset_dir_key`` cannot supply — it anchors at the workset root.
    Everything a repoint can contain (``@``-refs, ``$XDG_*``, ``~``, the relative
    anchor, and the refusal that names the key) stays that ONE pre-snapshot route's
    business; this adds no second grammar.

    ⚑ A PRESENT ``<None>`` ANSWERS ``None`` (spec §2a: a null arm is a declared value,
    so the dependent bind is OMITTED); only an ABSENT key takes *default*, and a
    ``None`` default is how a caller whose OWN key is null passes that on.
    """
    from kanibako.settings.settings_resolve import UNSET
    from kanibako.settings.workset_dirkeys import early_repoint, resolve_workset_dir_key

    repoint, where = early_repoint(ws_root, workset_settings, f"channels.{leaf}", early=early)
    if repoint is UNSET:
        return default
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        ws_root, str(repoint), leaf, key=f"channels.{leaf}", where=where,
        standalone=standalone, workset_settings=workset_settings, early=early,
    )


def workset_channel_paths(
    proj: ProjectPaths, std: StandardPaths
) -> WorksetChannels | None:
    """Derive the WORKSET-local channel roots for *proj*; ``None`` for standalone, or for a
    null ``workset.channelroot``.

    ⚑⚑ EVERY LEAF IS RESOLVED THROUGH ITS OWN DECLARED KEY, never joined onto the root
    (R-35, "fix the CODE").  Joining looked harmless because the joins ARE the spec's
    defaults, but it made the keys inert: ``chat`` was a split carrier (the bind
    followed the override while the chat-log seeder followed the join), and
    ``broadcast`` had no consumer at all.  A closed keyspace that accepts a key and
    then ignores it is worse than one that refuses it.
    """
    from kanibako.settings.workset_dirkeys import EarlyScope

    if not has_workset_channels(proj):
        return None
    return workset_channels_at(
        workset_root(proj, std), early=EarlyScope(std.early_system, workset_name_token(proj)),
    )


def workset_channels_at(
    ws_root: Path, *, early: EarlyScope,
) -> WorksetChannels | None:
    """Derive the WORKSET-local channel roots of *ws_root*; ``None`` for a nulled
    ``workset.channelroot``.

    The ONE carrier of the derivation; :func:`workset_channel_paths` is its
    ``ProjectPaths`` adapter and adds the standalone gate.  A box-less resolve (a
    working set, which is never standalone) calls it directly.
    """
    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workset_channelroot,
    )

    doc = load_workset_settings_doc(ws_root)
    root = resolve_workset_channelroot(ws_root, doc, early=early)
    if root is None:
        return None
    chat = _channel_key(ws_root, doc, "chat", root / "chat", standalone=False, early=early)
    return WorksetChannels(
        root=root,
        common=_channel_key(
            ws_root, doc, "common", root / "common", standalone=False, early=early,
        ),
        chat=chat,
        chat_general=None if chat is None else chat / CHAT_GENERAL_LEAF,
        # ⚑ ``broadcast`` DEFAULTS OFF ``chat`` (keyspec §2c: the row is
        # ``@workset.channels.chat/broadcast.md``), so a null ``chat`` makes the
        # whole value null — §0: an embedded reference to a present ``<None>``
        # makes the whole value ``<None>``.  Only an EXPLICIT ``broadcast``
        # repoint still resolves, because a stored value replaces the formula.
        chat_broadcast=_channel_key(
            ws_root, doc, "broadcast",
            None if chat is None else chat / "broadcast.md",
            standalone=False, early=early,
        ),
        share=_channel_key(ws_root, doc, "share", root / "share", standalone=False, early=early),
    )


def partition_key_paths(
    std: StandardPaths, ws_token: str, ws_root: Path
) -> WorksetPartition:
    """Resolve ``workset.channels.{mailboxes,share_global}`` from ``(token, ws_root)``.

    ⚑ THE ONE PLACE THE TWO PARTITION KEYS ARE READ.  Both entry points land here:
    :func:`workset_partition_paths` for a resolved ``ProjectPaths``, and
    :func:`own_partition_dirs` for the relocation path, which holds a pair of
    ``ProjectState``s and no ``ProjectPaths`` at all.  Those two used to answer the
    same question differently — one through the keys, one through
    :func:`system_partition` — which is how a repoint could be honored at launch and
    ignored by a ``box move``.

    ⚑ RAISES (``SettingsError``, naming the key) on a repoint that cannot resolve, like
    every other pre-snapshot key read.  A best-effort caller catches it; it is not
    softened here, because a silent fallback to the default is the failure this
    function exists to end.

    """
    from kanibako.project.workset import load_workset_settings_doc
    from kanibako.settings.workset_dirkeys import EarlyScope

    default = system_partition(std, ws_token)
    doc = load_workset_settings_doc(ws_root)
    early = EarlyScope(std.early_system, ws_token)
    return WorksetPartition(
        ws_token=ws_token,
        mailboxes=_channel_key(
            ws_root, doc, "mailboxes", default.mailboxes, standalone=None, early=early,
        ),
        share_global=_channel_key(
            ws_root, doc, "share_global", default.share, standalone=None, early=early,
        ),
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
    return partition_key_paths(
        std, workset_name_token(proj), workset_root(proj, std),
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
        inbox=None if part.mailboxes is None else part.mailboxes / proj.name,
        share_global=(
            None if part.share_global is None else part.share_global / proj.name
        ),
        share_workset=(
            None if wch is None or wch.share is None else wch.share / proj.name
        ),
    )
