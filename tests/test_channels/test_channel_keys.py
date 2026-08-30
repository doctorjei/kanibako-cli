"""The ``workset.channels.*`` family measured as KEYS, not as path joins.

``tests/test_channels/test_channels.py`` pins what the six leaves resolve to when
nothing is repointed.  This file asks the question a CLOSED keyspace makes mandatory
and that file does not ask: **does setting the key change anything?**

⚑ The defect this file was written against (measured 2026-08-25) had three shapes, and
only the first is visible from a default-value test:

* ``chat`` was a SPLIT CARRIER — the bind followed the key while
  ``commands/start.py._seed_channel_files`` kept joining ``<channelroot>/chat``, so a
  repoint mounted one directory and seeded the chat logs into another.  The bible tells
  every agent its logs live at ``~/channels/workset/chat``; the override emptied the
  directory canon points at.
* ``share`` was the same split, latent — bind at the override, ``meta.box.share_workset``
  at the un-overridden join.
* ``broadcast`` / ``mailboxes`` / ``share_global`` had NO CONSUMER AT ALL.  ``config
  set`` succeeded, the value persisted, ``config get`` read it back, and nothing
  whatsoever changed.  ``mailboxes`` was the sharpest of the three: a user could
  believe they had repointed their own inbox and had not.

R-35 is already ratified on this ("fix the CODE") — the key SET was corrected then, the
FORMULAS were not.

Indent note: 4 spaces, matching every sibling in ``tests/test_channels/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.channels import channels
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.config_keys import _KEY_ROUTES
from kanibako.settings.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.settings.settings_keyspace import DECLARED_WORKSET_CHANNEL_LEAVES


# ---------------------------------------------------------------------------
# Fixtures: one resolved proj per mode.  ⚑ Declared locally, which is the house
# pattern for these three — ``test_channels.py``, ``test_launch/test_templates.py``
# and ``test_commands/test_start_channels.py`` each carry their own.  Importing a
# sibling module's fixture instead trips ``F811`` on every parameter that uses it.
# ---------------------------------------------------------------------------

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
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(std, config, str(project_dir), initialize=True)


def _repoint(ws_root: Path, key: str, value: str) -> None:
    """Store *value* at *key* in the workset's ``workset.yaml``.

    ⚑ Through ``_KEY_ROUTES`` + ``write_nested_key`` — the exact tail of
    ``config_interface.set_config_value``, so this writes where the CLI writes.  A test
    that placed the value by hand would prove only that its own guess was readable.
    """
    sections, leaf = _KEY_ROUTES[key]
    write_nested_key(ws_root / WORKSET_META_FILE, sections, leaf, value)


class TestTheRepointReaderIsTheCliRoute:
    """Anti-vacuity for every case below: the helper writes the CLI's own slot."""

    @pytest.mark.parametrize("leaf", sorted(DECLARED_WORKSET_CHANNEL_LEAVES))
    def test_every_declared_leaf_is_a_routed_key(self, leaf):
        assert _KEY_ROUTES[f"workset.channels.{leaf}"] == (
            ("workset", "channels"), leaf,
        )


class TestWorksetLocalLeafRepoints:
    """The four workset-LOCAL keys (spec §2c) — repoint each, read the derivation."""

    def test_channelroot_repoint_moves_the_whole_family(self, named_proj, std):
        """The pre-existing behaviour, kept honest: the leaves follow their root."""
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channelroot", "@meta.workset.path/comms")
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.root == ws_root / "comms"
        assert wch.common == ws_root / "comms" / "common"
        assert wch.chat == ws_root / "comms" / "chat"
        assert wch.share == ws_root / "comms" / "share"

    def test_common_repoint_is_honoured(self, named_proj, std):
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.common", str(ws_root / "elsewhere-common"))
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.common == ws_root / "elsewhere-common"
        # The siblings are UNMOVED — a leaf is a leaf, not a second root.
        assert wch.chat == wch.root / "chat"

    def test_chat_repoint_is_honoured(self, named_proj, std):
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.chat", str(ws_root / "talk"))
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.chat == ws_root / "talk"
        assert wch.chat_general == ws_root / "talk" / "general.md"
        # ⚑ broadcast DEFAULTS off the chat key (``@workset.channels.chat/broadcast.md``),
        # so moving chat moves it — that is the manifest's own formula, not a join.
        assert wch.chat_broadcast == ws_root / "talk" / "broadcast.md"

    def test_share_repoint_is_honoured(self, named_proj, std):
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.share", str(ws_root / "outbox"))
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.share == ws_root / "outbox"

    def test_share_repoint_reaches_meta_box_share_workset(self, named_proj, std):
        """The LATENT half of the split: the address, not just the bind."""
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.share", str(ws_root / "outbox"))
        addr = channels.box_channel_addresses(named_proj, std)
        assert addr.share_workset == ws_root / "outbox" / named_proj.name

    def test_broadcast_repoint_is_honoured(self, named_proj, std):
        """``broadcast`` names a FILE, and had no consumer at all before R-35's repair."""
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.broadcast", str(ws_root / "shout.md"))
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.chat_broadcast == ws_root / "shout.md"
        # ⚑ general.md is NOT a declared key, so it stays inside the chat dir.
        assert wch.chat_general == wch.chat / "general.md"

    def test_an_unresolvable_repoint_is_REFUSED_BY_NAME(self, named_proj, std):
        """A key that cannot be resolved is an error naming the key, never a fallback.

        ⚑ The refusal is the pre-snapshot route's, not a second one — this asserts the
        key SPELLING reaches it (``workset.channels.chat``, not ``workset.chat``), which
        is the half a per-key wrapper can get wrong.
        """
        from kanibako.settings.settings_resolve import SettingsError

        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.chat", "@config.registry/nope")
        with pytest.raises(SettingsError) as exc:
            channels.workset_channel_paths(named_proj, std)
        assert "workset.channels.chat" in str(exc.value)
        assert "@config.registry" in str(exc.value)

    def test_a_bare_relative_repoint_is_REFUSED_naming_both_readings(
        self, named_proj, std,
    ):
        """[R147]: the six channel leaves route through the same one resolver, so the
        ambiguity refusal reaches them by the same seam the token refusal above does.

        ⚑ INVERTED, NOT DELETED: this used to assert ``wch.chat == ws_root / "talk"``
        under the heading "the ONE pre-snapshot grammar, unchanged".  The grammar IS
        still one — the root-relative reading just has to be SPELLED now.
        """
        from kanibako.settings.settings_resolve import SettingsError

        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.chat", "talk")
        with pytest.raises(SettingsError) as exc:
            channels.workset_channel_paths(named_proj, std)
        message = str(exc.value)
        assert "workset.channels.chat" in message
        assert str(ws_root / "talk") in message
        assert str(Path.cwd() / "talk") in message

    def test_the_root_relative_reading_stays_expressible(self, named_proj, std):
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.chat", "@meta.workset.path/talk")
        wch = channels.workset_channel_paths(named_proj, std)
        assert wch is not None
        assert wch.chat == ws_root / "talk"


class TestThePrimaryWorksetIsNotSpecial:
    """⚑ The default-mode box reads the SAME keys, out of the primary workset's file.

    Worth its own case because the primary workset is synthesized rather than created
    by the user, so it is easy to assume it has no ``workset.yaml`` to repoint from —
    and it is the workset almost every box is in.
    """

    def test_a_chat_repoint_in_the_primary_workset_is_honoured(self, primary_proj, std):
        ws_root = channels.workset_root(primary_proj, std)
        assert ws_root == std.primary_workset
        _repoint(ws_root, "workset.channels.chat", str(ws_root / "talk"))
        wch = channels.workset_channel_paths(primary_proj, std)
        assert wch is not None
        assert wch.chat == ws_root / "talk"

    def test_a_mailboxes_repoint_in_the_primary_workset_is_honoured(
        self, primary_proj, std,
    ):
        ws_root = channels.workset_root(primary_proj, std)
        _repoint(ws_root, "workset.channels.mailboxes", str(ws_root / "mail"))
        addr = channels.box_channel_addresses(primary_proj, std)
        assert addr.inbox == ws_root / "mail" / primary_proj.name


class TestAllProjectsPartitionRepoints:
    """``mailboxes`` / ``share_global`` — ALL PROJECTS, every mode (spec §2c)."""

    def test_mailboxes_default_is_the_system_partition(self, named_proj, std):
        part = channels.workset_partition_paths(named_proj, std)
        assert part.mailboxes == std.channels_mailboxes / "my-set"
        assert part.share_global == std.channels_share / "my-set"

    def test_mailboxes_repoint_moves_the_boxs_own_inbox(self, named_proj, std, tmp_home):
        """⚑ THE SHARPEST OF THE THREE: a user could believe this worked, and it did not."""
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.mailboxes", str(tmp_home / "mail"))
        part = channels.workset_partition_paths(named_proj, std)
        assert part.mailboxes == tmp_home / "mail"
        addr = channels.box_channel_addresses(named_proj, std)
        assert addr.inbox == tmp_home / "mail" / named_proj.name

    def test_share_global_repoint_moves_the_boxs_own_share(
        self, named_proj, std, tmp_home,
    ):
        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.share_global", str(tmp_home / "pub"))
        addr = channels.box_channel_addresses(named_proj, std)
        assert addr.share_global == tmp_home / "pub" / named_proj.name

    def test_a_standalone_box_has_the_partition_keys_too(self, standalone_proj, std):
        """⚑ D-M9: standalone omits the workset-LOCAL channels and keeps the partition."""
        assert channels.workset_channel_paths(standalone_proj, std) is None
        part = channels.workset_partition_paths(standalone_proj, std)
        assert part.mailboxes == (
            std.channels_mailboxes / channels.WS_TOKEN_STANDALONE
        )

    def test_a_standalone_box_honours_a_mailboxes_repoint(
        self, standalone_proj, std, tmp_home,
    ):
        _repoint(
            channels.workset_root(standalone_proj, std),
            "workset.channels.mailboxes",
            str(tmp_home / "solo-mail"),
        )
        addr = channels.box_channel_addresses(standalone_proj, std)
        assert addr.inbox == tmp_home / "solo-mail" / standalone_proj.name


class TestTheChatSeederFollowsTheKey:
    """The MEASURED user-visible defect: seeded logs landed outside the mount."""

    def test_the_logs_are_seeded_into_the_repointed_chat_dir(self, named_proj, std):
        from kanibako.commands.start import _seed_channel_files

        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.chat", str(ws_root / "talk"))
        _seed_channel_files(std, named_proj)

        assert (ws_root / "talk" / "general.md").is_file()
        assert (ws_root / "talk" / "broadcast.md").is_file()
        # ⚑ AND NOWHERE ELSE.  The old code seeded the un-overridden join, which is
        # mounted nowhere — so the guest's ~/channels/workset/chat was empty and the
        # host grew a stray directory the user never asked for.
        assert not (ws_root / "channels" / "chat").exists()

    def test_a_broadcast_repoint_is_the_file_that_gets_created(self, named_proj, std):
        from kanibako.commands.start import _seed_channel_files

        ws_root = named_proj.group.root
        _repoint(ws_root, "workset.channels.broadcast", str(ws_root / "shout.md"))
        _seed_channel_files(std, named_proj)

        assert (ws_root / "shout.md").is_file()
        assert not (ws_root / "channels" / "chat" / "broadcast.md").exists()


class TestTheFloorCarriesTheWholeFamily:
    """A key nothing installs is not resolvable, so ``@workset.channels.x`` would dangle."""

    def test_the_launch_floor_installs_channelroot_and_all_six_leaves(
        self, named_proj, std,
    ):
        from kanibako.commands.start import _workset_channel_floor_values
        from kanibako.settings.settings_launch import workset_anchor_floor

        channelroot, leaves = _workset_channel_floor_values(std, named_proj)
        floor = workset_anchor_floor(
            mode="named", channelroot=channelroot, workset_channels=leaves,
        )
        assert floor["workset.channelroot"] == str(
            channels.workset_channel_paths(named_proj, std).root
        )
        for leaf in DECLARED_WORKSET_CHANNEL_LEAVES:
            assert f"workset.channels.{leaf}" in floor, leaf

    def test_a_standalone_launch_still_installs_the_partition_leaves(
        self, standalone_proj, std,
    ):
        """⚑ standalone: the four LOCAL leaves are ``<None>``, the two partition keys are not."""
        from kanibako.commands.start import _workset_channel_floor_values
        from kanibako.settings.settings_launch import workset_anchor_floor

        channelroot, leaves = _workset_channel_floor_values(std, standalone_proj)
        assert channelroot is None
        floor = workset_anchor_floor(
            mode="standalone", channelroot=channelroot, workset_channels=leaves,
        )
        assert "workset.channelroot" not in floor
        assert floor["workset.channels.mailboxes"] == str(
            channels.workset_partition_paths(standalone_proj, std).mailboxes
        )
        assert "workset.channels.chat" not in floor
