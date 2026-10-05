"""Per-mode channel MOUNT assertions for the 6b mount swap.

Pins the EXACT channel bind set emitted into a box for each mode (PRIMARY,
NAMED, STANDALONE) against TARGET §4.  These drive the behavior-sensitive
change: replacing the single legacy ``~/comms`` mount with the ``~/channels/``
channel tree.  The asserts are byte-level on the emitted ``Mount`` set:

* all modes get the five system channel binds + the own-inbox double-bind;
* primary/named additionally get the three ``~/channels/workset/*`` binds;
* STANDALONE OMITS ``~/channels/workset/*`` entirely;
* the own inbox is the SAME host dir bound at BOTH ``~/channels/inbox`` and
  ``~/channels/mailboxes/<ws>/<self>`` (the A2 double-bind).

These exercise the LIVE channel-mount path (block 7c: ``_seed_channel_files`` +
``_emit_category_mounts(_resolve_launch_snapshot(...))``) with real resolved
``proj``/``std`` objects, not the MagicMock launch fixture. The per-family
``_build_channel_mounts`` was retired in 7c (its second resolver route folded into
``build_launch_snapshot``); these tests pin the resolved channel mount set against
TARGET §4 over the single-route snapshot path.
"""

from __future__ import annotations

import pytest

from kanibako.channels import channels as _ch
from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.commands.start import (
    _channel_default_categories,
    _emit_category_mounts,
    _resolve_launch_snapshot,
    _seed_channel_files,
)
from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs
from tests.support.narrow_resolve import table_bind_dests
from kanibako.settings.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.config_keys import _KEY_ROUTES


# ---------------------------------------------------------------------------
# Fixtures: one resolved proj per mode (mirrors test_channels.py).
# ---------------------------------------------------------------------------

@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def named_proj(std, config, tmp_home):
    ws_root = tmp_home / "worksets" / "my-set"
    ws = create_workset("my-set", ws_root, std)
    source = tmp_home / "original-project"
    source.mkdir()
    add_project(ws, "cool-app", source)
    return resolve_workset_project(
        WorksetSpec.from_workset(ws), "cool-app", std, config, initialize=True,
    )


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(
        std, config, str(project_dir), initialize=True,
    )


def _build(std, proj):
    """Resolve the channel mounts as a {box_dest: (host_src, options)} map.

    Drives the LIVE single-route path (7c): seed the chat files, then resolve the
    channel default-category table through the committed ``build_launch_snapshot``
    pipeline (``_resolve_launch_snapshot`` with base-families OFF + the channel
    table as the narrow ``extra_default_categories``) and emit the resolve's OWN
    table winners via ``_emit_category_mounts``.

    ⚑ A NARROW resolve, so it emits from ``LaunchDeliveries.narrow_bindings`` —
    the seam the image and helper resolves take (cutover 6-R2/6-R3). The live
    launch carries the channel table as a BASE family and emits it from the
    collapse instead; what this isolates is the table's own contribution, which is
    identical either way and is the only thing these assertions name.
    """
    _seed_channel_files(std, proj)
    _table = _channel_default_categories(std, proj)
    _snapshot, deliveries = _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name="shell",
        system_settings_path=None,
        agent_cfg_path=None,
        desc=None,
        install=None,
        target=None,
        agent_cfg=None,
        include_base_families=False,
        extra_default_categories=_table,
        deliver_creds=True,
        narrow_bind_dests=table_bind_dests(_table),
        cli_level=None,
    )
    mounts = _emit_category_mounts(
        deliveries.narrow_bindings, label="channel",
        skip_if_absent=deliveries.agent_dests,
    )
    return {
        m.destination: (str(m.source), m.options) for m in mounts
    }, mounts


# The five system-scope guest dests + own inbox (every mode).
_SYSTEM_DESTS = {
    "/home/agent/channels/common",
    "/home/agent/channels/chat",
    "/home/agent/channels/share",
    "/home/agent/channels/mailboxes",
    "/home/agent/channels/inbox",
}
_WORKSET_DESTS = {
    "/home/agent/channels/workset/common",
    "/home/agent/channels/workset/chat",
    "/home/agent/channels/workset/share",
}


class TestPrimaryChannelMounts:
    def test_exact_mount_set(self, primary_proj, std):
        by_dest, mounts = _build(std, primary_proj)
        # System + workset-local (primary gets both).
        assert set(by_dest) == _SYSTEM_DESTS | _WORKSET_DESTS
        # System sources resolve to the channels skeleton.
        assert by_dest["/home/agent/channels/common"][0] == str(std.channels_common)
        assert by_dest["/home/agent/channels/chat"][0] == str(std.channels_chat)
        assert by_dest["/home/agent/channels/share"][0] == str(std.channels_share)
        assert (
            by_dest["/home/agent/channels/mailboxes"][0]
            == str(std.channels_mailboxes)
        )
        # Workset-local sources hang off @meta.workset.path/channels.
        wch = _ch.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert by_dest["/home/agent/channels/workset/common"][0] == str(wch.common)
        assert by_dest["/home/agent/channels/workset/chat"][0] == str(wch.chat)
        assert by_dest["/home/agent/channels/workset/share"][0] == str(wch.share)
        # Every channel bind is rw (Z,U) under option (A).
        assert all(opts == "Z,U" for _, opts in by_dest.values())

    def test_own_inbox_double_bind(self, primary_proj, std):
        """A2: inbox + mailboxes/<ws>/<self> are the SAME host dir, two dests."""
        by_dest, _ = _build(std, primary_proj)
        addr = _ch.box_channel_addresses(primary_proj, std)
        # ~/channels/inbox source == @system.channels.mailboxes/__PRIMARY__/<box>
        assert by_dest["/home/agent/channels/inbox"][0] == str(addr.inbox)
        expected = std.channels_mailboxes / WS_TOKEN_PRIMARY / primary_proj.name
        assert by_dest["/home/agent/channels/inbox"][0] == str(expected)
        # The mailboxes mount is the PARENT of the inbox source (overlay alias).
        assert by_dest["/home/agent/channels/mailboxes"][0] == str(
            std.channels_mailboxes
        )

    def test_l7_guarantee_creates_sources(self, primary_proj, std):
        """The rw L7 branch mkdir's every channel partition source."""
        by_dest, _ = _build(std, primary_proj)
        from pathlib import Path

        for src, _opts in by_dest.values():
            assert Path(src).is_dir(), src

    def test_chat_files_seeded(self, primary_proj, std):
        _build(std, primary_proj)
        assert (std.channels_chat / "general.md").is_file()
        assert (std.channels_chat / "broadcast.md").is_file()
        wch = _ch.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert wch.chat_general.is_file()
        assert wch.chat_broadcast.is_file()


class TestNamedChannelMounts:
    def test_exact_mount_set(self, named_proj, std):
        by_dest, _ = _build(std, named_proj)
        assert set(by_dest) == _SYSTEM_DESTS | _WORKSET_DESTS
        # Workset-local sources root at the NAMED workset root.
        assert named_proj.group is not None
        wroot = named_proj.group.root / "channels"
        assert by_dest["/home/agent/channels/workset/common"][0] == str(
            wroot / "common"
        )

    def test_inbox_partitioned_by_named_ws(self, named_proj, std):
        by_dest, _ = _build(std, named_proj)
        expected = std.channels_mailboxes / "my-set" / named_proj.name
        assert by_dest["/home/agent/channels/inbox"][0] == str(expected)


class TestStandaloneChannelMounts:
    def test_omits_workset_local(self, standalone_proj, std):
        """A10: standalone gets system channels + own inbox ONLY."""
        by_dest, _ = _build(std, standalone_proj)
        assert set(by_dest) == _SYSTEM_DESTS
        # No ~/channels/workset/* at all.
        assert not any(
            d.startswith("/home/agent/channels/workset") for d in by_dest
        )

    def test_inbox_partitioned_by_standalone_token(self, standalone_proj, std):
        by_dest, _ = _build(std, standalone_proj)
        expected = (
            std.channels_mailboxes / WS_TOKEN_STANDALONE / standalone_proj.name
        )
        assert by_dest["/home/agent/channels/inbox"][0] == str(expected)

    def test_no_workset_chat_seeded(self, standalone_proj, std):
        _build(std, standalone_proj)
        # System chat logs still seeded; no workset chat dir for standalone.
        assert (std.channels_chat / "general.md").is_file()
        assert _ch.workset_channel_paths(standalone_proj, std) is None


class TestChannelDefaultCategories:
    """The raw default_categories dict (pre-resolution) per mode.

    ⚑ The table is DEST-KEYED (R-3/R-5/R-10): ONE terminal ``box.bindings.rw`` key
    whose value is the whole ``{box_dest: (src,)}`` map. The per-channel ``key``
    from ``core-defaults.yaml`` is no longer a settings key segment at all, so the
    identity asserted here is the DESTINATION — R-11-normalized, hence
    ``/home/agent/...`` and not the file's authored ``~/...``.
    """

    def test_primary_keys(self, primary_proj, std):
        cats = _channel_default_categories(std, primary_proj)
        assert set(cats) == {"box.bindings.rw"}
        assert set(cats["box.bindings.rw"]) == {
            "/home/agent/channels/common",
            "/home/agent/channels/chat",
            "/home/agent/channels/share",
            "/home/agent/channels/mailboxes",
            "/home/agent/channels/inbox",
            "/home/agent/channels/workset/common",
            "/home/agent/channels/workset/chat",
            "/home/agent/channels/workset/share",
        }

    def test_standalone_keys_omit_workset(self, standalone_proj, std):
        """The one terminal key.  The standalone DEST set (no ``~/channels/workset/*``)
        is the ``channel-binds-standalone`` kinemata view's."""
        cats = _channel_default_categories(std, standalone_proj)
        assert set(cats) == {"box.bindings.rw"}


class TestNullWorksetChatIsNotSeeded:
    """A present ``<None>`` at ``workset.channels.chat`` seeds NOTHING (spec §2a).

    The seeder runs on every launch.  A null chat omits the ``~/channels/workset/chat``
    bind, so creating anything under the chat dir would write into a directory no
    mount points at.  The DIRECTORY is part of that: a tip that writes no file but
    still leaves an empty ``channels/chat/`` behind has still published a chat root
    the keyspace was told does not exist.
    """

    def test_nothing_is_created_under_a_null_chat(self, named_proj, std):
        ws_root = named_proj.group.root
        sections, leaf = _KEY_ROUTES["workset.channels.chat"]
        write_nested_key(ws_root / WORKSET_META_FILE, sections, leaf, None)
        _seed_channel_files(std, named_proj)
        # ``general.md`` names no key and ``broadcast`` is ``@workset.channels.chat``/
        # ``broadcast.md`` — an embedded ref, so §0 makes the whole value null with it.
        assert not (ws_root / "channels" / "chat").exists()
        # The SYSTEM-scope log is unconditional — only the workset arm is nulled.
        assert (std.channels_chat / "general.md").is_file()

    def test_an_absent_chat_still_seeds_both_logs(self, named_proj, std):
        _seed_channel_files(std, named_proj)
        wch = _ch.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.chat_general.is_file()
        assert wch.chat_broadcast.is_file()


class TestNullSystemChannelSeedsNothing:
    """The SYSTEM-scope arm of the seeder, which the workset-local case above does not reach.

    ``_seed_channel_files`` runs on EVERY launch and divides ``std.channels_chat`` by
    ``general.md`` to name the system log.  ``StandardPaths.channels_chat`` admits
    ``None`` — a null at a standard bind's source key is a declared value, so the bind
    is omitted (spec §2a) — so that division is a ``TypeError`` on exactly the null the
    tree admits, and the guard in the list build is what keeps the launch alive.
    """

    @staticmethod
    def _nulled(std, key):
        """*std* re-read with ``key`` stored null — written into the settings FILE by the
        product's own writer and read back by the product's own reader, so the seeder is
        handed what a user who set the key gets."""
        from kanibako.settings.paths import _floor_field, load_std_paths

        sections, slot = _KEY_ROUTES[key]
        write_nested_key(std.settings, sections, slot, None)
        reloaded = load_std_paths()
        assert getattr(reloaded, _floor_field(key)) is None, key
        return reloaded

    def test_a_null_system_chat_does_not_raise_and_seeds_nothing(self, named_proj, std):
        nulled = self._nulled(std, "system.channels.chat")
        # ``broadcast`` is ``@system.channels.chat/broadcast.md`` — an embedded ref, so
        # §0 makes the whole value null with it.  Both logs of the system arm are null.
        assert nulled.channels_broadcast is None
        _seed_channel_files(nulled, named_proj)
        # The workset arm is untouched by a SYSTEM key — it is the control.
        wch = _ch.workset_channel_paths(named_proj, nulled)
        assert wch is not None
        assert wch.chat_general.is_file()
        assert wch.chat_broadcast.is_file()

    def test_a_null_system_chat_leaves_no_path_named_None(self, named_proj, std, tmp_home):
        nulled = self._nulled(std, "system.channels.chat")
        _seed_channel_files(nulled, named_proj)
        named = sorted(p for p in tmp_home.rglob("None"))
        assert named == [], f"the seeder created a None-named path: {named}"

    def test_the_system_rotation_is_skipped_for_a_null_broadcast(self, named_proj, std, monkeypatch):
        """The rotation is the half of the seeder that a null reaches AFTER the skip.

        The general log is skipped, then ``broadcast`` — null with it, since its row is
        ``@system.channels.chat/broadcast.md`` — would be handed to the rotator, which
        stats a path no bind mounts.  Spied rather than asserted through a side effect,
        because the rotator's own threshold is not this test's subject.
        """
        nulled = self._nulled(std, "system.channels.chat")
        assert nulled.channels_broadcast is None
        rotated = []
        monkeypatch.setattr(
            "kanibako.commands.start._rotate_file", lambda path: rotated.append(path),
        )
        _seed_channel_files(nulled, named_proj)
        wch = _ch.workset_channel_paths(named_proj, nulled)
        assert wch is not None
        # The control: the workset arm is a real address and IS still rotated, so a
        # green here cannot come from the seeder not running at all.
        assert wch.chat_broadcast in rotated
        assert not [path for path in rotated if path is None]

    def test_an_absent_system_broadcast_is_still_rotated(self, named_proj, std, monkeypatch):
        """The other half: a key that is merely unset is not a null, so the rotate runs."""
        rotated = []
        monkeypatch.setattr(
            "kanibako.commands.start._rotate_file", lambda path: rotated.append(path),
        )
        _seed_channel_files(std, named_proj)
        assert std.channels_broadcast in rotated


class TestNullPartitionLeafOmitsItsBind:
    """A present ``<None>`` at ``workset.channels.{mailboxes,share_global}`` omits the
    bind that key is the source of (spec §2a / §2h).

    The inbox row's source is ``@meta.box.inbox``, and that key is a LITERAL the
    identity floor builds from ``box_channel_addresses`` — so the null has to survive
    the floor as a null.  The failure this pins is the one that made the widening
    unsafe to ship blind: ``str(None)`` reaches the expander as the four-character
    path ``"None"``, which for a MOUNT podman reads as a NAMED VOLUME.
    """

    @pytest.mark.parametrize("leaf", ["mailboxes", "share_global"])
    def test_the_partition_address_is_none(self, named_proj, std, leaf):
        ws_root = named_proj.group.root
        sections, name = _KEY_ROUTES[f"workset.channels.{leaf}"]
        write_nested_key(ws_root / WORKSET_META_FILE, sections, name, None)
        addr = _ch.box_channel_addresses(named_proj, std)
        assert getattr(addr, "inbox" if leaf == "mailboxes" else "share_global") is None

    def test_a_null_mailboxes_omits_the_inbox_bind(self, named_proj, std):
        ws_root = named_proj.group.root
        sections, name = _KEY_ROUTES["workset.channels.mailboxes"]
        write_nested_key(ws_root / WORKSET_META_FILE, sections, name, None)
        by_dest, _ = _build(std, named_proj)
        # ⚑ THE PIN: absent, NOT the four-character path "None".
        assert "/home/agent/channels/inbox" not in by_dest
        assert not any(src == "None" for src, _ in by_dest.values())

    def test_an_absent_partition_leaf_still_binds_the_inbox(self, named_proj, std):
        by_dest, _ = _build(std, named_proj)
        addr = _ch.box_channel_addresses(named_proj, std)
        assert by_dest["/home/agent/channels/inbox"][0] == str(addr.inbox)


def _workset_anchor(std, proj):
    """The ``workset_anchor`` floor fragment the LIVE launch path produces.

    Read BY NAME off :class:`~kanibako.settings.settings_launch.LaunchInputs`, so an
    added or removed field cannot shift which fragment this returns.
    """
    return resolve_inputs(
        subject=ResolveSubject.BOX, std=std, proj=proj, agent_name="shell",
        system_path=std.settings,
    ).workset_anchor


class TestWorksetChannelFloorLeaf:
    """⚑ Pins the PRODUCTION site of the derived ``workset.channels.<leaf>`` key.

    The key is never written literally: ``start.py`` builds a dict whose LEAF
    names the channel, and ``settings_launch.workset_anchor_floor`` f-strings that
    leaf into ``workset.channels.{leaf}``.  A rename of the channel type root that
    updates only the literal spellings leaves the floor installing the OLD key
    while ``core-defaults.yaml`` asks for the NEW ``{workset.channels.common}`` —
    the @-ref does not resolve and the workset common bind SILENTLY VANISHES (no
    error, no warning).

    Tests that pass their own ``workset_channels=`` dict into
    ``workset_anchor_floor`` (e.g. ``test_categories_live``) CANNOT catch this —
    they assert what the f-string does with a leaf they supplied themselves.  This
    drives ``settings_launch.resolve_inputs``, so the leaf comes from the real
    ``workset_channel_paths`` production site.
    """

    def test_primary_floor_installs_common_leaf(self, primary_proj, std):
        floor = _workset_anchor(std, primary_proj)
        wch = _ch.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert floor["workset.channels.common"] == str(wch.common)
        # The pre-rename spelling must be GONE from the produced floor.
        assert "workset.channels.commons" not in floor

    def test_named_floor_installs_common_leaf(self, named_proj, std):
        floor = _workset_anchor(std, named_proj)
        wch = _ch.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert floor["workset.channels.common"] == str(wch.common)
        assert "workset.channels.commons" not in floor

    def test_floor_leaves_match_the_bind_refs(self, primary_proj, std):
        """The floor's leaf set == the ``{workset.channels.*}`` refs the binds use.

        The two halves of the seam are declared in different files; this asserts
        they agree, so neither side can be renamed alone.
        """
        floor = _workset_anchor(std, primary_proj)
        installed = {k for k in floor if k.startswith("workset.channels.")}
        cats = _channel_default_categories(std, primary_proj)
        # Dest-keyed arms: the sources live INSIDE each arm's map, one level down,
        # and an entry is ``(src,)`` — the destination is the key, not element 1.
        referenced = {
            str(entry[0])[1:-1]
            for arm in cats.values()
            for entry in arm.values()
            if str(entry[0]).startswith("{workset.channels.")
        }
        assert referenced, "expected {workset.channels.*} routed binds"
        assert referenced <= installed, (
            f"bind @-refs with no floor key: {sorted(referenced - installed)}"
        )

    def test_standalone_has_only_the_all_projects_channel_keys(
        self, standalone_proj, std,
    ):
        """⚑ NOT "no channel keys" — the family splits per mode, and it always did.

        The four workset-LOCAL leaves are PRIMARY/NAMED (a standalone box has no
        ``~/channels/workset/*``), but ``mailboxes`` and ``share_global`` are ALL
        PROJECTS (spec §2c): they aggregate at the SYSTEM scope partitioned by workset
        name, and ``__STANDALONE__`` is a partition like any other.  This case asserted
        the whole family was absent for as long as no floor installed those two in ANY
        mode, so it read as a mode rule when it was really an outage.
        The four LOCAL leaves and the channel ROOT carry the ``<None>`` §2c declares for
        standalone — SUPPLIED as a present ``None``, not omitted ([R177]).
        """
        floor = _workset_anchor(std, standalone_proj)
        with_a_path = {
            k for k, v in floor.items()
            if k.startswith("workset.channels.") and v is not None
        }
        assert with_a_path == {
            "workset.channels.mailboxes", "workset.channels.share_global",
        }
        for key in (
            "workset.channelroot", "workset.channels.common", "workset.channels.chat",
            "workset.channels.broadcast", "workset.channels.share",
        ):
            assert key in floor and floor[key] is None, key
        assert floor["workset.channels.mailboxes"] == str(
            _ch.workset_partition_paths(standalone_proj, std).mailboxes
        )
