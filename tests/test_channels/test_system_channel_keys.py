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
  tier, named as such by the comment above ``commands/start._launch_snapshot_inputs``'s
  ``resolved_sys`` — carried NONE of the five.  So a workset binding sourcing
  ``@system.channels.chat`` mounted correctly at launch and VANISHED from
  ``workset share list --effective``: the display lying about what a launch does, which
  is the exact divergence the two comments warned each other about.

Both carriers now read ``settings/paths.system_path_floor``, so "both must carry the
same keys" is a property of there being one map, not of two hand lists agreeing.

Indent note: 4 spaces, matching every sibling in ``tests/test_channels/``.
"""

from __future__ import annotations

import argparse

import pytest

from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import (
    box_workset_settings_paths,
    resolve_project,
    system_path_floor,
)
from kanibako.settings.paths_defaults import SYSTEM_PATH_DEFAULTS
from kanibako.settings.settings_keyspace import DECLARED_SYSTEM_CHANNEL_LEAVES
from kanibako.targets.no_agent import NoAgentTarget


@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


def _snapshot(std, proj):
    """The REAL launch snapshot — the same call the live launch makes."""
    from kanibako.commands.start import _resolve_launch_snapshot

    return _resolve_launch_snapshot(
        std=std, proj=proj, agent_name="claude",
        system_settings_path=None, agent_cfg_path=None,
        desc=None, install=None, target=NoAgentTarget(), agent_cfg=None,
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

    def test_the_three_uninstalled_system_keys_are_still_uninstalled(self, std):
        """⚑ A STATED SUBSET, not an accident — the same finding, one scope over.

        ``system.{backup,cache,runtime}`` are declared keys with manifest defaults that
        no floor has ever installed either.  This floor does not close that; the case
        is here so the omission is a measured fact rather than something a reader has
        to notice, and so widening the floor to cover them reds and gets said out loud.
        """
        floor = system_path_floor(std)
        uninstalled = set(SYSTEM_PATH_DEFAULTS) - set(floor)
        assert uninstalled == {"system.backup", "system.cache", "system.runtime"}


class TestTheLaunchFloorCarriesTheWholeFamily:
    """A key no floor installs is not resolvable, so ``@system.channels.x`` dangles."""

    def test_the_launch_inputs_install_every_declared_leaf(self, primary_proj, std):
        from kanibako.commands.start import _launch_snapshot_inputs

        resolved_sys = _launch_snapshot_inputs(
            std=std, proj=primary_proj, agent_name="claude",
        )[1]
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
