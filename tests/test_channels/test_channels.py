"""Tests for the pure channel path-resolution + partition-addressing helpers.

Covers ``kanibako.channels.channels`` (Phase 6 sub-step 6a): the workset-name token
derivation, the workset-local channel roots (PRIMARY/NAMED only), the SYSTEM
per-workset partition roots (``mailboxes/<ws>``, ``share/<ws>``) for each of the
three ws-name tokens, and the per-box partition addresses
(``meta.box.{inbox,share_global,share_workset}``) per mode.

These are pure derivations — no directories are created, no mounts touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.channels import channels
from kanibako.channels.channels import (
    WS_TOKEN_PRIMARY,
    WS_TOKEN_STANDALONE,
)
from kanibako.settings.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.project.workset import add_project, create_workset


# ---------------------------------------------------------------------------
# Fixtures: one resolved proj per mode.
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
    proj = resolve_workset_project(
        WorksetSpec.from_workset(ws), "cool-app", std, config, initialize=True,
    )
    return proj


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(
        std, config, str(project_dir), initialize=True,
    )


# ---------------------------------------------------------------------------
# workset_name_token
# ---------------------------------------------------------------------------

class TestWorksetNameToken:
    def test_primary(self, primary_proj):
        assert channels.workset_name_token(primary_proj) == WS_TOKEN_PRIMARY

    def test_named_uses_workset_name(self, named_proj):
        assert channels.workset_name_token(named_proj) == "my-set"

    def test_standalone(self, standalone_proj):
        assert channels.workset_name_token(standalone_proj) == WS_TOKEN_STANDALONE


# ---------------------------------------------------------------------------
# ws_name single-source drift guard (P6b, spec §1A meta.runtime.ws_name)
# ---------------------------------------------------------------------------

class TestWsNameSingleSourceDriftGuard:
    """P6b: the channel partition token and the runtime-layer ``meta.runtime.
    ws_name`` are SINGLE-SOURCED — ``start.py`` threads
    ``channels.workset_name_token(proj)`` straight into ``meta_runtime_floor`` as
    ``ws_name`` (the SAME token also keys the channel partition, so the two cannot
    drift). This pins the channels derivation to the meta-runtime definition +
    the ``meta.workset.name`` anchor, so a change to either without the other goes
    RED. NON-vacuous: it exercises the real per-mode ``workset_name_token``
    derivation off ``proj`` and asserts the exact expected token per mode.
    """

    def _guard(self, proj, std):
        from kanibako.settings.settings_launch import meta_runtime_floor

        token = channels.workset_name_token(proj)
        mode = proj.mode.value
        ws_root = (
            None if mode == "primary" else str(channels.workset_root(proj, std))
        )
        floor = meta_runtime_floor(
            mode=mode, ws_name=token, ws_root_literal=ws_root,
        )
        # The token channels derived IS meta.runtime.ws_name (single source) …
        assert floor["meta.runtime.ws_name"] == token
        # … and meta.workset.name anchors into it (not a divergeable literal).
        assert floor["meta.workset.name"] == "{meta.runtime.ws_name}"
        return token

    def test_primary(self, primary_proj, std):
        assert self._guard(primary_proj, std) == WS_TOKEN_PRIMARY

    def test_named(self, named_proj, std):
        assert self._guard(named_proj, std) == "my-set"

    def test_standalone(self, standalone_proj, std):
        assert self._guard(standalone_proj, std) == WS_TOKEN_STANDALONE


# ---------------------------------------------------------------------------
# workset_root
# ---------------------------------------------------------------------------

class TestWorksetRoot:
    def test_primary_roots_at_primary_workset(self, primary_proj, std):
        assert channels.workset_root(primary_proj, std) == std.primary_workset

    def test_named_roots_at_group_root(self, named_proj, std):
        assert named_proj.group is not None
        assert channels.workset_root(named_proj, std) == named_proj.group.root

    def test_standalone_roots_at_project_dir(self, standalone_proj, std):
        # Drift H: the workset root is the project ROOT (metadata_path), NOT the
        # workspace subdir (project_path = <root>/workspace).
        assert (
            channels.workset_root(standalone_proj, std)
            == standalone_proj.metadata_path
        )
        assert (
            standalone_proj.project_path
            == standalone_proj.metadata_path / "workspace"
        )


# ---------------------------------------------------------------------------
# system_partition — for each of the three ws-name tokens.
# ---------------------------------------------------------------------------

class TestSystemPartition:
    def test_primary_token(self, std):
        part = channels.system_partition(std, WS_TOKEN_PRIMARY)
        assert part.mailboxes == std.channels_mailboxes / WS_TOKEN_PRIMARY
        assert part.share == std.channels_share / WS_TOKEN_PRIMARY

    def test_named_token(self, std):
        part = channels.system_partition(std, "my-set")
        assert part.mailboxes == std.channels_mailboxes / "my-set"
        assert part.share == std.channels_share / "my-set"

    def test_standalone_token(self, std):
        part = channels.system_partition(std, WS_TOKEN_STANDALONE)
        assert part.mailboxes == std.channels_mailboxes / WS_TOKEN_STANDALONE
        assert part.share == std.channels_share / WS_TOKEN_STANDALONE

    def test_partition_under_channels_skeleton(self, std):
        """The partition roots hang off the resolved system channels dirs."""
        part = channels.system_partition(std, WS_TOKEN_PRIMARY)
        assert part.mailboxes.parent == std.channels_mailboxes
        assert part.share.parent == std.channels_share
        assert std.channels_mailboxes == std.channels / "mailboxes"
        assert std.channels_share == std.channels / "share"


# ---------------------------------------------------------------------------
# workset_channel_paths — PRIMARY/NAMED only; standalone is None.
# ---------------------------------------------------------------------------

class TestWorksetChannelPaths:
    def test_primary_rooted_at_primary_workset(self, primary_proj, std):
        wch = channels.workset_channel_paths(primary_proj, std)
        assert wch is not None
        root = std.primary_workset / "channels"
        assert wch.root == root
        assert wch.common == root / "common"
        assert wch.chat == root / "chat"
        assert wch.chat_general == root / "chat" / "general.md"
        assert wch.chat_broadcast == root / "chat" / "broadcast.md"
        assert wch.share == root / "share"

    def test_named_rooted_at_workset_root(self, named_proj, std):
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert named_proj.group is not None
        root = named_proj.group.root / "channels"
        assert wch.root == root
        assert wch.common == root / "common"
        assert wch.share == root / "share"

    def test_standalone_has_no_workset_channels(self, standalone_proj, std):
        assert channels.workset_channel_paths(standalone_proj, std) is None

    def test_named_channelroot_repoint_honored(self, named_proj, std):
        """B2 (§3.3): the workset-local roots compose from the RESOLVED
        ``workset.channelroot`` — a ``workset: {channelroot: …}`` repoint in
        the workset's workset.yaml is honored (default unchanged, pinned by
        the sibling tests above)."""
        from kanibako.settings.config_io import dump_doc, load_doc

        assert named_proj.group is not None
        settings = named_proj.group.root / "workset.yaml"
        data = load_doc(settings)
        data.setdefault("workset", {})["channelroot"] = "@meta.workset.path/comms"
        dump_doc(settings, data)

        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        root = named_proj.group.root / "comms"
        assert wch.root == root
        assert wch.chat == root / "chat"
        assert wch.share == root / "share"

    def test_primary_channelroot_repoint_honored(self, primary_proj, std):
        """PRIMARY honors a repoint from the primary workset's workset.yaml."""
        from kanibako.settings.config_io import dump_doc, load_doc

        settings = std.primary_workset / "workset.yaml"
        data = load_doc(settings) if settings.is_file() else {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("workset", {})["channelroot"] = "/srv/comms"
        std.primary_workset.mkdir(parents=True, exist_ok=True)
        dump_doc(settings, data)

        wch = channels.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert wch.root == Path("/srv/comms")
        assert wch.common == Path("/srv/comms") / "common"

    def test_has_workset_channels_flag(
        self, primary_proj, named_proj, standalone_proj
    ):
        assert channels.has_workset_channels(primary_proj) is True
        assert channels.has_workset_channels(named_proj) is True
        assert channels.has_workset_channels(standalone_proj) is False


class TestNullChannelroot:
    """``workset.channelroot: null`` answers ``None`` — and the FLOOR ROWS STAY.

    ⚑ The three ``~/channels/workset/*`` binds are STANDARD, so their floor entries
    must be emitted whatever the root resolves to: the entry is what the §2a warning
    and ``box show --effective`` read, and the bind itself is omitted by the collapse
    off the ``@workset.channels.*`` ref.  Dropping the row would report a standard
    bind as never having existed.
    """

    _WS_DESTS = (
        "/home/agent/channels/workset/common",
        "/home/agent/channels/workset/chat",
        "/home/agent/channels/workset/share",
    )

    @staticmethod
    def _null_channelroot(root):
        from kanibako.settings.config_io import dump_doc

        settings = root / "workset.yaml"
        data = {}
        settings.parent.mkdir(parents=True, exist_ok=True)
        data["workset"] = {"channelroot": None}
        dump_doc(settings, data)

    def test_a_null_channelroot_answers_no_workset_channels(self, primary_proj, std):
        self._null_channelroot(std.primary_workset)
        assert channels.workset_channel_paths(primary_proj, std) is None

    def test_the_three_workset_rows_stay_in_the_floor(self, primary_proj, std):
        from kanibako.settings import core_defaults
        from kanibako.settings.settings_expand import _is_whole_value_ref

        self._null_channelroot(std.primary_workset)
        table = core_defaults.channel_default_categories(std, primary_proj)
        assert all(d in _ws_dests(table) for d in self._WS_DESTS), _ws_dests(table)
        # …and every one of them names the nulled key, so the collapse can omit it.
        for dest, value in _ws_dests(table).items():
            if dest in self._WS_DESTS:
                ref = _is_whole_value_ref(value[0])
                assert ref is not None and ref.startswith("workset.channels."), (dest, value)

    def test_a_standalone_box_still_omits_the_three_rows(self, standalone_proj, std):
        from kanibako.settings import core_defaults

        table = core_defaults.channel_default_categories(std, standalone_proj)
        assert not [d for d in _ws_dests(table) if d in self._WS_DESTS]

    def test_the_launch_omits_the_three_binds_and_names_the_key_and_the_file(
        self, primary_proj, std, caplog,
    ):
        """⚑ §2a ON THE REAL LAUNCH PATH: nulling ``workset.channelroot`` leaves each of
        the three rows naming a ``<None>`` source, so each is omitted — and ONE warning
        names the key that did it and the file it was written in.
        """
        from kanibako.commands.start import _resolve_launch_snapshot
        from kanibako.settings.settings_launch import (
            reset_none_warnings,
            snapshot_category_entries,
        )
        from kanibako.targets.shell import ShellTarget

        ws_yaml = std.primary_workset / "workset.yaml"
        self._null_channelroot(std.primary_workset)
        reset_none_warnings()  # one warning per process, not per resolve
        caplog.set_level("WARNING", logger="kanibako.settings.settings_launch")
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=primary_proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=ShellTarget(), agent_cfg=None,
            cli_level=None,
        )
        mounted = {
            entry.box_dest for entry in snapshot_category_entries(
                snapshot, active_agent="claude", box_ctx=None,
            )
            if entry.category.startswith("bindings")
        }
        assert not [d for d in self._WS_DESTS if d in mounted], sorted(mounted)
        warned = [
            record.getMessage() for record in caplog.records
            if record.name == "kanibako.settings.settings_launch"
            and "workset.channelroot" in record.getMessage()
        ]
        assert len(warned) == 1, warned
        assert str(ws_yaml) in warned[0]
        # Every omitted bind is named, so one warning answers for all three.
        for dest in self._WS_DESTS:
            assert dest in warned[0], (dest, warned[0])

    def test_a_null_root_with_the_three_entries_nulled_is_silent(
        self, primary_proj, std, caplog,
    ):
        """⚑ §2a, THE OTHER HALF: a bind is omitted by setting its entry AND its source
        to ``<None>`` (spec §2a, STANDARD binds), so a null root whose three entries are
        nulled with it says nothing at all.  A root is not a lone-null bind: with no bind
        lone-null there is nothing for the root's message to be about, and it has no
        list of binds to name."""
        from kanibako.commands.start import _resolve_launch_snapshot
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.settings_launch import (
            reset_none_warnings,
            snapshot_category_entries,
        )
        from kanibako.targets.shell import ShellTarget

        ws_yaml = std.primary_workset / "workset.yaml"
        ws_yaml.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(ws_yaml, {
            "workset": {"channelroot": None},
            "box": {"bindings": {"rw": {dest: None for dest in self._WS_DESTS}}},
        })
        reset_none_warnings()  # one warning per process, not per resolve
        caplog.set_level("WARNING", logger="kanibako.settings.settings_launch")
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=primary_proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=ShellTarget(), agent_cfg=None,
            cli_level=None,
        )
        mounted = {
            entry.box_dest for entry in snapshot_category_entries(
                snapshot, active_agent="claude", box_ctx=None,
            )
            if entry.category.startswith("bindings")
        }
        assert not [d for d in self._WS_DESTS if d in mounted], sorted(mounted)
        assert [record.getMessage() for record in caplog.records
                if record.name == "kanibako.settings.settings_launch"] == []


def _ws_dests(table):
    """Every destination in a channel floor table."""
    return {dest: value for arm in table.values() for dest, value in arm.items()}


# ---------------------------------------------------------------------------
# box_channel_addresses — meta.box.{inbox,share_global,share_workset}.
# ---------------------------------------------------------------------------

class TestBoxChannelAddresses:
    def test_primary_addresses(self, primary_proj, std):
        addr = channels.box_channel_addresses(primary_proj, std)
        name = primary_proj.name
        assert addr.ws_token == WS_TOKEN_PRIMARY
        assert addr.box_name == name
        assert addr.inbox == std.channels_mailboxes / WS_TOKEN_PRIMARY / name
        assert addr.share_global == std.channels_share / WS_TOKEN_PRIMARY / name
        assert addr.share_workset == std.primary_workset / "channels" / "share" / name

    def test_named_addresses(self, named_proj, std):
        addr = channels.box_channel_addresses(named_proj, std)
        name = named_proj.name
        assert named_proj.group is not None
        ws_root = named_proj.group.root
        assert addr.ws_token == "my-set"
        assert addr.inbox == std.channels_mailboxes / "my-set" / name
        assert addr.share_global == std.channels_share / "my-set" / name
        assert addr.share_workset == ws_root / "channels" / "share" / name

    def test_standalone_addresses_share_workset_none(self, standalone_proj, std):
        addr = channels.box_channel_addresses(standalone_proj, std)
        name = standalone_proj.name
        assert addr.ws_token == WS_TOKEN_STANDALONE
        assert addr.inbox == std.channels_mailboxes / WS_TOKEN_STANDALONE / name
        assert (
            addr.share_global == std.channels_share / WS_TOKEN_STANDALONE / name
        )
        # Standalone has no workset-local channels → no own workset share.
        assert addr.share_workset is None

    def test_inbox_is_own_mailbox_subdir(self, primary_proj, std):
        """The own-inbox path == mailboxes/<ws>/<self> (the (C)-stable alias)."""
        addr = channels.box_channel_addresses(primary_proj, std)
        part = channels.system_partition(std, WS_TOKEN_PRIMARY)
        assert addr.inbox == part.mailboxes / primary_proj.name


# ---------------------------------------------------------------------------
# Purity: deriving addresses creates no directories.
# ---------------------------------------------------------------------------

class TestPurity:
    def test_no_dirs_created(self, primary_proj, std):
        addr = channels.box_channel_addresses(primary_proj, std)
        wch = channels.workset_channel_paths(primary_proj, std)
        assert wch is not None
        # None of the derived channel dirs are created by the helpers.
        assert not addr.inbox.exists()
        assert not addr.share_global.exists()
        assert not wch.common.exists()
        assert not wch.chat.exists()
