"""The ``system.*`` path tier measured as KEYS — the SYSTEM twins of ``workset.channels.*``.

``tests/test_channels/test_channel_keys.py`` asks of the WORKSET family the question a
closed keyspace makes mandatory: *does the key answer?*  This file asks it of the SYSTEM
family, which ``config_keys.py`` declares in the same breath (*"the SYSTEM twins of
``workset.channels.*``"*) and which had the same shape of hole.

⚑ The defect this file was written against (measured 2026-08-26):

* ``system.channels.broadcast`` was DECLARED, carried a manifest default, resolved into
  ``StandardPaths.channels_broadcast`` — and reached NO floor.  ``@system.channels.
  broadcast`` was ``__MISSING__`` in every launch snapshot, so a ``box.bindings.rw``
  entry whose source is that key collapsed to ``None`` and the bind was DROPPED, with
  no message and no non-zero exit.  Its four siblings mounted.
* ``commands/workset_cmd._print_effective_shares`` — the SECOND carrier of the same
  tier, named as such by the comment above the launch's own copy of the map (then in
  ``commands/start``; the builder is now ``settings_launch.resolve_inputs``) — carried
  NONE of the five.  So a workset binding sourcing
  ``@system.channels.chat`` mounted correctly at launch and VANISHED from
  ``workset share list --effective``: the display lying about what a launch does, which
  is the exact divergence the two comments warned each other about.

Both carriers now read ``settings/paths.system_path_floor``, so "both must carry the
same keys" is a property of there being one map, not of two hand lists agreeing.

Indent note: 4 spaces, matching every sibling in ``tests/test_channels/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_io import dump_doc, load_doc, write_nested_key
from kanibako.settings.config_keys import _KEY_ROUTES
from kanibako.settings.paths import (
    _floor_field,
    box_workset_settings_paths,
    load_std_paths,
    resolve_project,
    system_path_floor,
)
from kanibako.settings.settings_keyspace import DECLARED_SYSTEM_CHANNEL_LEAVES
from kanibako.targets.shell import ShellTarget
from kanibako.settings.bootstrap import SYSTEM_PATH_DEFAULTS


@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


def nulled_std(std, key: str):
    """*std* re-read with ``key`` stored null, through the production writer and reader.

    ⚑ NOT a hand-built ``StandardPaths``: the null goes into the settings FILE through
    ``write_nested_key`` and comes back out through ``load_std_paths`` — the same pair a
    launch uses — so the readers below are handed what a user who set the key gets.
    """
    sections, slot = _KEY_ROUTES[key]
    write_nested_key(std.settings, sections, slot, None)
    reloaded = load_std_paths()
    assert getattr(reloaded, _floor_field(key)) is None, key
    return reloaded


def _paths_of(*values):
    """Every ``Path`` among *values*, flattened out of dataclasses and dicts."""
    from dataclasses import fields, is_dataclass

    out = []
    for value in values:
        if isinstance(value, dict):
            out.extend(_paths_of(*value.values()))
        elif is_dataclass(value):
            out.extend(_paths_of(*(getattr(value, f.name) for f in fields(value))))
        elif isinstance(value, (list, tuple)):
            out.extend(_paths_of(*value))
        elif isinstance(value, Path):
            out.append(value)
    return out


def _snapshot(std, proj):
    """The REAL launch snapshot — the same call the live launch makes."""
    from kanibako.commands.start import _resolve_launch_snapshot

    return _resolve_launch_snapshot(
        std=std, proj=proj, agent_name="claude",
        system_settings_path=None, agent_cfg_path=None,
        desc=None, install=None, target=ShellTarget(), agent_cfg=None,
        cli_level=None,
    )


class TestTheTierBuilderIsDerivedNotListed:
    """Anti-vacuity: the builder must follow the DECLARED family, not a hand list."""

    def test_the_channel_leaves_come_from_the_declared_family(self, std):
        floor = system_path_floor(std)
        assert {
            key[len("system.channels."):] for key in floor
            if key.startswith("system.channels.")
        } == set(DECLARED_SYSTEM_CHANNEL_LEAVES)

    def test_every_leaf_is_the_resolved_standard_path(self, std):
        floor = system_path_floor(std)
        assert floor["system.channels.common"] == str(std.channels_common)
        assert floor["system.channels.chat"] == str(std.channels_chat)
        assert floor["system.channels.share"] == str(std.channels_share)
        assert floor["system.channels.mailboxes"] == str(std.channels_mailboxes)
        assert floor["system.channels.broadcast"] == str(std.channels_broadcast)
        assert floor["system.channelroot"] == str(std.channels)
        assert floor["system.template"] == str(std.template)
        assert floor["system.canon"] == str(std.canon)

    def test_the_whole_declared_system_table_reaches_the_floor(self, std):
        """⚑⚑ INVERTED 2026-08-28.  It used to pin the three keys as UNINSTALLED.

        ``system.{backup,cache,runtime}`` were declared, carried manifest defaults and
        were CLI-settable, and no floor installed them — while the SET-time tier
        (``config_interface._path_tier_split``) resolved all eleven.  So ``config set``
        ACCEPTED a binding sourced at ``@system.cache`` and the launch snapshot answered
        ``__MISSING__``, dropping it with no message and rc 0: the
        ``system.channels.broadcast`` shape, one omission over.  ``[R143]`` settles that
        this is a defect rather than a stated omission — *"if it has a default value,
        yes, thay value should be placed in the keystore"* — so ``system_path_floor``
        derives from :data:`SYSTEM_PATH_DEFAULTS` ENTIRE and the floor went 8 → 11.

        ⚑ THE OLD CASE IS NOT DELETED, IT IS TURNED OVER: the widening is loud in the
        same place the omission was.  Consumers checked in that same change, both of
        them and both taking the whole map — ``settings_launch.resolve_inputs``
        (the three are SCALARS, so they take the last-wins arm of
        ``_merge_default_categories``, claim no category destination and provoke no
        origin refusal; being declared, ``_refuse_undeclared_snapshot`` stays silent)
        and ``commands/workset_cmd._print_effective_shares`` (folds the map into a
        resolve floor and prints collapsed BINDINGS, never the floor, so ``--effective``
        gains no row from the widening).

        ⚑ RESERVED IS UNTOUCHED.  Nothing in kanibako READS the three, and nothing here
        gives them a reader: reserved is about consumers, this floor is about the
        keystore, and a reserved key still answers.
        """
        floor = system_path_floor(std)
        assert set(floor) == set(SYSTEM_PATH_DEFAULTS), (
            "the launch floor no longer covers the whole declared system.* table; "
            f"missing={sorted(set(SYSTEM_PATH_DEFAULTS) - set(floor))} "
            f"extra={sorted(set(floor) - set(SYSTEM_PATH_DEFAULTS))}"
        )
        # ⚑ EFFECT, not membership: the three newly floored keys hold the resolved
        # value, which is also what pins the derived field-name rule for them.
        assert floor["system.backup"] == str(std.backup)
        assert floor["system.cache"] == str(std.cache)
        assert floor["system.runtime"] == str(std.runtime)


class TestTheLaunchFloorCarriesTheWholeFamily:
    """A key no floor installs is not resolvable, so ``@system.channels.x`` dangles."""

    def test_the_launch_inputs_install_every_declared_leaf(self, primary_proj, std):
        from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs

        resolved_sys = resolve_inputs(
            subject=ResolveSubject.BOX, std=std, proj=primary_proj,
            agent_name="claude", system_path=std.settings,
        ).system_floor
        for leaf in DECLARED_SYSTEM_CHANNEL_LEAVES:
            assert f"system.channels.{leaf}" in resolved_sys, leaf

    @pytest.mark.parametrize("leaf", sorted(DECLARED_SYSTEM_CHANNEL_LEAVES))
    def test_the_snapshot_answers_every_declared_leaf(self, primary_proj, std, leaf):
        """⚑ Read off the REAL snapshot: ``broadcast`` used to be ``__MISSING__`` here."""
        from kanibako.settings.settings_launch import snapshot_leaf

        snapshot, _deliveries = _snapshot(std, primary_proj)
        assert snapshot_leaf(snapshot, f"system.channels.{leaf}") == str(
            getattr(std, f"channels_{leaf}")
        )

    def test_the_snapshot_answers_every_declared_system_path_key(self, primary_proj, std):
        """⚑⚑ THE WIDENING MEASURED AS AN EFFECT, not as a key-set equality.

        Read off the REAL snapshot, so it cannot pass by the floor builder agreeing
        with itself: before 2026-08-28 ``system.{backup,cache,runtime}`` were
        ``__MISSING__`` here while the other eight answered.  One snapshot, all
        eleven keys — the parametrized leaf case above rebuilds per leaf and this one
        deliberately does not.
        """
        from kanibako.settings.settings_launch import snapshot_leaf

        snapshot, _deliveries = _snapshot(std, primary_proj)
        missing = {
            key for key in SYSTEM_PATH_DEFAULTS
            if snapshot_leaf(snapshot, key) != str(getattr(std, _floor_field(key)))
        }
        assert not missing, (
            "declared system.* keys that the launch snapshot does not answer: "
            f"{sorted(missing)}"
        )

    def test_a_binding_sourced_at_broadcast_is_not_dropped(self, primary_proj, std):
        """⚑⚑ THE USER-VISIBLE DEFECT: the bind vanished, silently and with rc 0.

        ``config set`` took the key, ``config get`` read it back, and a binding that
        used it produced no mount, no warning and no error — the closed keyspace's
        worst failure mode, an accepted key that does nothing.
        """
        from kanibako.commands.start import _launch_bind_map

        box_path, _ws_path = box_workset_settings_paths(primary_proj)
        box_path.parent.mkdir(parents=True, exist_ok=True)
        # ⚑ MERGED into whatever ``initialize=True`` wrote, not written over it: a
        # clobbered box tier could make this pass or fail for a reason that is not
        # the key.
        doc = load_doc(box_path) or {}
        doc.setdefault("box", {}).setdefault("bindings", {})["rw"] = {
            "/home/agent/bcast.md": ["@system.channels.broadcast"],
            # The CONTROL: a sibling leaf that already worked, so a green here cannot
            # come from the whole binding table failing to arrive.
            "/home/agent/chat": ["@system.channels.chat"],
        }
        dump_doc(box_path, doc)

        snapshot, _deliveries = _snapshot(std, primary_proj)
        binds = _launch_bind_map(snapshot)
        assert binds["/home/agent/chat"].src == str(std.channels_chat)
        assert "/home/agent/bcast.md" in binds, (
            "@system.channels.broadcast resolved to nothing, so the bind was dropped "
            "from the collapse entirely — no mount, no warning, rc 0"
        )
        assert binds["/home/agent/bcast.md"].src == str(std.channels_broadcast)


class TestTheEffectiveDisplayIsTheLaunchTier:
    """``workset share list --effective`` must show what a launch would mount."""

    def _ws_with_bindings(self, std, tmp_home, bindings):
        from kanibako.project.workset import create_workset

        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)
        doc_path = ws_root / WORKSET_META_FILE
        doc = load_doc(doc_path) or {}
        doc.setdefault("workset", {}).setdefault("bindings", {})["ro"] = bindings
        dump_doc(doc_path, doc)
        return ws_root

    def test_a_binding_through_a_system_channel_key_is_displayed(
        self, std, config, tmp_home, capsys,
    ):
        """⚑ MEASURED: every ``@system.channels.*`` row was silently omitted here.

        The launch mounts ``@system.channels.chat`` and always did; this display
        dropped the row, because its floor carried none of the five.  A user reading
        ``--effective`` to check their workset saw a binding they had configured simply
        not listed.
        """
        from kanibako.commands import workset_cmd

        self._ws_with_bindings(std, tmp_home, {
            "/home/agent/chat-ro": ["@system.channels.chat"],
            "/home/agent/bcast-ro": ["@system.channels.broadcast"],
            # The CONTROL rows: a literal and a key the old floor DID carry.
            "/home/agent/lit-ro": ["/tmp"],
            "/home/agent/root-ro": ["@system.channelroot"],
        })
        rc = workset_cmd.run_share_list(
            argparse.Namespace(workset="my-set", effective=True)
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert f"{std.channels_chat} -> /home/agent/chat-ro" in out
        assert f"{std.channels_broadcast} -> /home/agent/bcast-ro" in out
        assert f"{std.channels} -> /home/agent/root-ro" in out
        assert "/tmp -> /home/agent/lit-ro" in out


class TestANullSystemChannelKeyYieldsANullArm:
    """A null at a ``system.channels.*`` key is a DECLARED value, so the reader that
    partitions it answers ``None`` — it does not raise, and it does not divide.

    ⚑ THE SYSTEM ARM, which is the arm no test here reached.  ``test_channel_keys.py``
    nulls a ``workset.channels.*`` leaf and lands on ``_channel_key``'s three-state
    answer; this family nulls the SYSTEM key those leaves DEFAULT to, so the two
    readers that hang off ``std`` itself are the subject.  Each of them divides a
    possibly-null path by a box name, so an unguarded arm is a ``TypeError`` at exactly
    the null this tree admits.
    """

    @pytest.mark.parametrize("nulled, sibling", [
        ("system.channels.mailboxes", "share"),
        ("system.channels.share", "mailboxes"),
    ])
    def test_the_system_partition_arm_is_null(self, std, nulled, sibling):
        from kanibako.channels.channels import system_partition

        part = system_partition(nulled_std(std, nulled), "WS-abc")
        assert getattr(part, sibling) is not None, sibling
        null_arm = "mailboxes" if sibling == "share" else "share"
        assert getattr(part, null_arm) is None

    @pytest.mark.parametrize("nulled, live", [
        ("system.channels.mailboxes", "share_global"),
        ("system.channels.share", "mailbox"),
    ])
    def test_the_own_partition_arm_is_null(self, std, nulled, live):
        """``own_partition_dirs`` is the RAW-INPUT primitive ``box move`` relocates off.

        ⛔ A NULL ARM PROPAGATES HERE, naming no error: ``workset.channels.*`` DEFAULT to
        the system partition, so a null system key is a LEGAL configuration arriving at
        the relocation path.  It is the same ``None`` whether the user nulled the
        workset key or the system key it defaults to, so the reader cannot tell them
        apart — and refusing would reject what the spec says to OMIT.
        """
        from kanibako.channels.channels import (
            WS_TOKEN_PRIMARY,
            own_partition_dirs,
        )

        own = own_partition_dirs(
            nulled_std(std, nulled), WS_TOKEN_PRIMARY, "proj",
            ws_root=std.primary_workset,
        )
        assert getattr(own, live) is not None, live
        null_arm = "mailbox" if live == "share_global" else "share_global"
        assert getattr(own, null_arm) is None

    def test_the_own_inbox_address_is_null(self, std, primary_proj):
        """``meta.box.inbox`` is a LITERAL the identity floor builds from this address,
        so a ``"None"`` here reaches the expander as a four-character path — which for a
        MOUNT podman reads as a NAMED VOLUME."""
        from kanibako.channels.channels import box_channel_addresses

        addr = box_channel_addresses(primary_proj, nulled_std(std, "system.channels.mailboxes"))
        assert addr.inbox is None
        assert addr.share_global is not None

    @pytest.mark.parametrize("key", [
        "system.channels.common", "system.channels.chat",
        "system.channels.share", "system.channels.mailboxes",
    ])
    def test_no_reader_names_a_directory_None(self, std, primary_proj, key):
        """Every reader over a null system key, and every path any of them hands back.

        ``str()`` of a null used to be born here, and a bind carrying ``"None"`` is a
        directory named ``None`` — so this walks the resolved values rather than the one
        arm under test.
        """
        from kanibako.channels import channels as ch

        nulled = nulled_std(std, key)
        part = ch.system_partition(nulled, "WS-abc")
        own = ch.own_partition_dirs(
            nulled, ch.WS_TOKEN_PRIMARY, "proj", ws_root=std.primary_workset,
        )
        addr = ch.box_channel_addresses(primary_proj, nulled)
        wch = ch.workset_channel_paths(primary_proj, nulled)
        for path in _paths_of(part, own, addr, wch):
            assert "None" not in path.parts, f"{key} produced {path}"

    def test_no_directory_named_None_is_left_on_disk(self, std, primary_proj, tmp_home):
        """⚑ The filesystem half: a reader that merely avoided the crash by naming a
        directory ``None`` would pass the value assertions above."""
        from kanibako.channels import channels as ch

        nulled = nulled_std(std, "system.channels.mailboxes")
        ch.box_channel_addresses(primary_proj, nulled)
        ch.own_partition_dirs(
            nulled, ch.WS_TOKEN_PRIMARY, "proj", ws_root=std.primary_workset,
        )
        named = [p for p in tmp_home.rglob("None")]
        assert named == [], f"a None-named path was created: {named}"
