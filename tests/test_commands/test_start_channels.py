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

from kanibako import channels as _ch
from kanibako.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.commands.start import (
    _channel_default_categories,
    _emit_category_mounts,
    _resolve_launch_snapshot,
    _seed_channel_files,
)
from kanibako.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.workset import add_project, create_workset


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
    table as the narrow ``extra_default_categories``) and emit the reconciled
    non-agent MOUNT winners via ``_emit_category_mounts`` — exactly the live launch
    channel path (start.py:1317-1318), no separate channel resolver.
    """
    _seed_channel_files(std, proj)
    _snapshot, reconciled, _warnings = _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name="general",
        system_settings_path=None,
        project_toml=None,
        workset_path=None,
        agent_cfg_path=None,
        desc=None,
        install=None,
        target=None,
        agent_cfg=None,
        include_base_families=False,
        extra_default_categories=_channel_default_categories(std, proj),
        group_auth=proj.group_auth,
    )
    mounts = _emit_category_mounts(reconciled, label="channel")
    return {
        m.destination: (str(m.source), m.options) for m in mounts
    }, mounts


# The five system-scope guest dests + own inbox (every mode).
_SYSTEM_DESTS = {
    "/home/agent/channels/commons",
    "/home/agent/channels/chat",
    "/home/agent/channels/share",
    "/home/agent/channels/mailboxes",
    "/home/agent/channels/inbox",
}
_WORKSET_DESTS = {
    "/home/agent/channels/workset/commons",
    "/home/agent/channels/workset/chat",
    "/home/agent/channels/workset/share",
}


class TestPrimaryChannelMounts:
    def test_exact_mount_set(self, primary_proj, std):
        by_dest, mounts = _build(std, primary_proj)
        # System + workset-local (primary gets both).
        assert set(by_dest) == _SYSTEM_DESTS | _WORKSET_DESTS
        # System sources resolve to the channels skeleton.
        assert by_dest["/home/agent/channels/commons"][0] == str(std.channels_commons)
        assert by_dest["/home/agent/channels/chat"][0] == str(std.channels_chat)
        assert by_dest["/home/agent/channels/share"][0] == str(std.channels_share)
        assert (
            by_dest["/home/agent/channels/mailboxes"][0]
            == str(std.channels_mailboxes)
        )
        # Workset-local sources hang off @workset.meta.root/channels.
        wch = _ch.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert by_dest["/home/agent/channels/workset/commons"][0] == str(wch.commons)
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
        assert by_dest["/home/agent/channels/workset/commons"][0] == str(
            wroot / "commons"
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
    """The raw default_categories dict (pre-resolution) per mode."""

    def test_primary_keys(self, primary_proj, std):
        cats = _channel_default_categories(std, primary_proj)
        assert set(cats) == {
            "box.bindings.rw.global_commons",
            "box.bindings.rw.global_chat",
            "box.bindings.rw.global_share",
            "box.bindings.rw.mailboxes",
            "box.bindings.rw.inbox",
            "box.bindings.rw.workset_commons",
            "box.bindings.rw.workset_chat",
            "box.bindings.rw.workset_share",
        }

    def test_standalone_keys_omit_workset(self, standalone_proj, std):
        cats = _channel_default_categories(std, standalone_proj)
        assert set(cats) == {
            "box.bindings.rw.global_commons",
            "box.bindings.rw.global_chat",
            "box.bindings.rw.global_share",
            "box.bindings.rw.mailboxes",
            "box.bindings.rw.inbox",
        }
