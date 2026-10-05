"""A ``--null`` the LAUNCH refuses is refused at ``set`` too (spec §2a, CLASS S1).

``--null`` writes a present ``None`` and every reader spells it ``null``.  The ``system:``
and ``config:`` path tiers REFUSE that spelling by name (``config._refuse_null_paths``), as
does ``project.workset.resolve_workset_boxes`` for ``workset.boxes`` ([R177]) — so this door
refuses a ``--null`` at any of those keys BEFORE the write, and the user learns it at the
door they typed rather than at the next launch.

⚑ ONE CARRIER, CALLED — the anti-drift half of this file is
``TestTheRefusalCarriesTheLaunchsOwnReason``: a copied predicate at the set door is the
duplicate-renderer defect one layer down and drifts the first time a reader gains or loses a
key, so the membership is derived (``config.refuses_null_path_key``) and the wording is
built by the reader's own builder (``config.null_path_keys_error``).

⚑ AND WHAT IS **NOT** HERE IS THE POINT OF THE OTHER HALF.  A null is a VALUE at
``system.agent`` ("no default agent", §2b), at ``workset.workspaces`` ("no workspace dir",
§2c) and at every ``env.<VAR>`` leaf (the §2h suppression idiom).  Those readers do not
refuse a null, so this door does not either — see ``TestWhatStaysLenient``, which is the
half that keeps the cure from being "refuse a null at any path key".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project.workset import (
    create_workset,
    load_workset_settings_doc,
    resolve_workset_boxes,
)
from kanibako.settings.bootstrap import BOXES_PATH, SYSTEM_PATH_DEFAULTS
from kanibako.settings.config import (
    BOX_META_FILE,
    WORKSET_META_FILE,
    null_path_keys_error,
    refuses_null_path_key,
    system_path_set_values,
)
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.messages import (
    ERR_CONFIG_NULL_PATH_REASON,
    ERR_CONFIG_NULL_PATH_SET_HEAD,
)
from kanibako.settings.paths import BoxMode, _early_scope, load_system_config
from tests.support.filenames import CONFIG_FILENAME

#: THE TWO SHAPES A ``--null`` LANDS AS, as the stored YAML spells them.  ``system.canon`` is
#: the plain leaf; ``system.channels.common`` is an entry of a MAPPING — the file holds
#: ``channels: {common: null}``, which is why the one carrier has to name the key the way
#: the reader's walk spells it (``_flatten_leaves`` flattens the nested table to
#: ``system.channels.common``) rather than the leaf the writer was handed.
_SYSTEM_LEAF = "system.canon"
_SYSTEM_MAPPED = "system.channels.common"
_WORKSET_BOXES = f"workset.{BOXES_PATH}"

def _files(tmp_path: Path) -> dict:
    """The four settings files the scopes write, each in a plausible tier directory."""
    ws = tmp_path / "ws"
    box = tmp_path / "ws" / "boxes" / "b"
    ws.mkdir(parents=True)
    box.mkdir(parents=True)
    return {
        "config": tmp_path / CONFIG_FILENAME,
        "system": tmp_path / "settings.yaml",
        "workset": ws / WORKSET_META_FILE,
        "box": box / BOX_META_FILE,
        "agents": tmp_path / "agents",
    }


def _set(key: str, value, files: dict, scope: ConfigLevel) -> str:
    """Drive ``set_config_value`` with the threading the matching noun command uses."""
    if scope is ConfigLevel.system:
        return set_config_value(
            key, value, config_path=files["config"],
            system_settings_path=files["system"], cascade_system_path=files["system"],
            command_scope=scope, agents_root=files["agents"],
        )
    if scope is ConfigLevel.workset:
        return set_config_value(
            key, value, config_path=files["workset"],
            cascade_system_path=files["system"], cascade_workset_path=files["workset"],
            command_scope=scope,
        )
    return set_config_value(
        key, value, config_path=files["box"],
        cascade_system_path=files["system"], cascade_workset_path=files["workset"],
        cascade_box_path=files["box"], command_scope=scope,
    )


def _assert_refused_without_writing(message: str, key: str, files: dict, scope: ConfigLevel) -> None:
    """A refusal, carrying the launch's reason, that left the destination file untouched."""
    assert message.startswith("Error:"), message
    assert key in message, message
    assert ERR_CONFIG_NULL_PATH_REASON in message, message
    # ⚑ REFUSED BEFORE THE WRITE. The settings file is not merely free of the null — on a
    # fresh store it does not EXIST, so there is nothing to go and undo.
    destination = files["system"] if scope is ConfigLevel.system else files[scope.value]
    assert not destination.exists(), (
        f"{destination} was written by a refused set: {message}"
    )


@pytest.fixture
def workset(std, tmp_home):
    """A real workset, so the workset-tier reader has a root and a file to read."""
    return create_workset("nullws", tmp_home / "nullws", std)


class TestTheLaunchRefusesWhatSetAccepted:
    """Each shape the defect arrived in: ``set`` wrote it, the launch refused it."""

    def test_a_system_leaf_null_is_refused_and_nothing_is_written(self, tmp_path):
        files = _files(tmp_path)
        _assert_refused_without_writing(
            _set(_SYSTEM_LEAF, None, files, ConfigLevel.system),
            _SYSTEM_LEAF, files, ConfigLevel.system,
        )

    def test_a_system_mapping_entry_null_is_refused_and_nothing_is_written(self, tmp_path):
        files = _files(tmp_path)
        _assert_refused_without_writing(
            _set(_SYSTEM_MAPPED, None, files, ConfigLevel.system),
            _SYSTEM_MAPPED, files, ConfigLevel.system,
        )

    def test_a_null_workset_boxes_is_refused_and_nothing_is_written(self, tmp_path):
        files = _files(tmp_path)
        _assert_refused_without_writing(
            _set(_WORKSET_BOXES, None, files, ConfigLevel.workset),
            _WORKSET_BOXES, files, ConfigLevel.workset,
        )

    def test_the_refusal_names_the_key_and_never_claims_a_file_holds_it(self, tmp_path):
        # ⚑ THE LEAD IS THE DOOR'S, AND IT MAY NOT NAME A FILE. Nothing was written and on
        # a fresh store the settings file does not exist, so a lead reading "<path> sets
        # these path keys to null" would be a false statement about the user's own file.
        files = _files(tmp_path)
        message = _set(_SYSTEM_LEAF, None, files, ConfigLevel.system)
        assert _SYSTEM_LEAF in message, message
        assert str(files["system"]) not in message, message
        assert not files["system"].exists()


class TestTheLaunchStillRefusesTheStoredValue:
    """The other half of the round trip, driven through the launch's OWN readers.

    A ``set``-time refusal is only the same rule if the launch still refuses the stored
    value — so these drive the readers the launch path tier and the box store resolve
    through, with no container anywhere.
    """

    def test_the_system_path_tier_reader_refuses_a_stored_null(self, std, tmp_path):
        files = _files(tmp_path)
        files["system"].parent.mkdir(parents=True, exist_ok=True)
        files["system"].write_text("system:\n  canon: null\n")
        with pytest.raises(Exception) as caught:
            system_path_set_values(files["system"])
        assert ERR_CONFIG_NULL_PATH_REASON in str(caught.value)
        assert _SYSTEM_LEAF in str(caught.value)

    def test_the_whole_path_tier_refuses_it_on_the_launch_path(self, std, config_file, tmp_home):
        # ⚑ THE REAL LAUNCH ENTRY, not the reader alone: ``load_std_paths`` is what every
        # launch command runs first, so this is the launch-time half of the round trip.
        from kanibako.settings.config_io import dump_doc

        settings = std.settings
        dump_doc(settings, {"system": {"channels": {"common": None}}})
        with pytest.raises(Exception) as caught:
            load_system_config(config_file, data_home=std.data_home, home=tmp_home)
        assert ERR_CONFIG_NULL_PATH_REASON in str(caught.value)
        assert _SYSTEM_MAPPED in str(caught.value)

    def test_the_box_store_resolver_refuses_a_stored_null(self, std, workset):
        from kanibako.settings.config_io import dump_doc

        dump_doc(workset.settings_path, {"workset": {BOXES_PATH: None}})
        with pytest.raises(Exception) as caught:
            resolve_workset_boxes(
                workset.root, load_workset_settings_doc(workset.root),
                early=_early_scope(std, BoxMode.named, workset.name),
            )
        assert ERR_CONFIG_NULL_PATH_REASON in str(caught.value)
        assert _WORKSET_BOXES in str(caught.value)


class TestTheRefusalCarriesTheLaunchsOwnReason:
    """⚑ ONE CARRIER. The set door and the launch door must not be able to drift."""

    def test_the_set_message_is_the_carriers_own(self, tmp_path):
        files = _files(tmp_path)
        message = _set(_SYSTEM_LEAF, None, files, ConfigLevel.system)
        assert message == "Error: " + null_path_keys_error(
            files["system"], (_SYSTEM_LEAF,),
            head=ERR_CONFIG_NULL_PATH_SET_HEAD,
            cure=(
                f"{ERR_CONFIG_NULL_PATH_REASON} Nothing was written: to use {_SYSTEM_LEAF}'s "
                f"default, run 'reset {_SYSTEM_LEAF}', or set the path you mean."
            ),
        )

    def test_the_set_message_and_the_launch_message_share_the_reason(self, tmp_path):
        # ⚑ THE ANTI-DRIFT PIN. The REASON is one sentence with one home and the two doors
        # must not drift apart on it.  If a reader's wording changes and the door's does
        # not, this reds.  The LEAD and the CUE after it are per-door by contract and are
        # NOT compared here.
        files = _files(tmp_path)
        files["system"].write_text("system:\n  canon: null\n")
        with pytest.raises(Exception) as caught:
            system_path_set_values(files["system"])
        at_launch = str(caught.value)
        at_set = _set(_SYSTEM_LEAF, None, files, ConfigLevel.system)

        def lead_of(message: str) -> str:
            return message.partition(ERR_CONFIG_NULL_PATH_REASON)[0]

        for door, message in (("launch", at_launch), ("set", at_set)):
            assert ERR_CONFIG_NULL_PATH_REASON in message, f"{door}: {message}"
            # ⚑ EXACTLY ONCE, so neither door grows a second, competing reason sentence.
            assert message.count(ERR_CONFIG_NULL_PATH_REASON) == 1, f"{door}: {message}"
            # ⚑ BOTH NAME THE KEY — the set door's lead replaces the file with the keys.
            assert _SYSTEM_LEAF in lead_of(message), f"{door}: {message}"
        # ⚑ THE LEAD IS PER-DOOR BY CONTRACT: the launch found the lines already in a file
        # and this door wrote none, so the two leads must NOT be one shared sentence.
        assert lead_of(at_launch) != lead_of(at_set)

    def test_the_membership_is_the_readers_own(self):
        # ⚑ DERIVED (P13), NOT A LIST BESIDE THEM: every key of the two path tables the
        # readers refuse, plus the one workset leaf whose reader does.
        assert all(refuses_null_path_key(key) for key in SYSTEM_PATH_DEFAULTS)
        assert refuses_null_path_key(_WORKSET_BOXES)

    def test_the_cure_names_the_door_that_wrote_nothing(self, tmp_path):
        # ⚑ The read-time cure says "delete those lines"; at a door that wrote none that is
        # a false statement about the user's own file, so the door passes its own.
        files = _files(tmp_path)
        message = _set(_SYSTEM_LEAF, None, files, ConfigLevel.system)
        assert "Nothing was written" in message
        assert f"reset {_SYSTEM_LEAF}" in message
        assert "Delete those lines" not in message


class TestTheWholeMembershipRefuses:
    """The sweep, derived from the tables the readers are built on (P13)."""

    def test_the_corpus_is_not_empty(self):
        # ⚑ NON-VACUITY. A parametrized sweep over an empty list is green and proves
        # nothing; this is the case that reds if the derivation stops finding keys.
        assert len(SYSTEM_PATH_DEFAULTS) > 10
        assert {_SYSTEM_LEAF, _SYSTEM_MAPPED} <= set(SYSTEM_PATH_DEFAULTS)

    @pytest.mark.parametrize("key", sorted(SYSTEM_PATH_DEFAULTS))
    def test_every_system_path_key_refuses_a_null(self, key, tmp_path):
        files = _files(tmp_path)
        _assert_refused_without_writing(
            _set(key, None, files, ConfigLevel.system), key, files, ConfigLevel.system,
        )

    def test_the_workset_box_store_key_refuses_a_null(self, tmp_path):
        files = _files(tmp_path)
        _assert_refused_without_writing(
            _set(_WORKSET_BOXES, None, files, ConfigLevel.workset),
            _WORKSET_BOXES, files, ConfigLevel.workset,
        )

    @pytest.mark.parametrize("key", sorted(SYSTEM_PATH_DEFAULTS))
    def test_and_the_launch_reader_refuses_every_one_of_them(self, key, tmp_path):
        # ⚑ THE ROUND TRIP, one key at a time: the membership is only worth having if each
        # member really is a key the reader refuses.  This is what a membership list
        # hand-written beside the readers would silently rot into.
        from kanibako.settings.config_io import dump_doc

        files = _files(tmp_path)
        head = files["system"]
        parts = key.partition("system.")[2].split(".")
        table = {parts[0]: {parts[1]: None}} if len(parts) == 2 else {parts[0]: None}
        dump_doc(head, {"system": table})
        with pytest.raises(Exception) as caught:
            system_path_set_values(head)
        assert key in str(caught.value), caught.value


class TestWhatStaysLenient:
    """⚑ THE OTHER HALF. A null that MEANS something, and every value that is not a null."""

    @pytest.mark.parametrize("key,scope_name", [
        ("system.agent", "system"),          # "no default agent" (spec §2b)
        ("workset.workspaces", "workset"),    # "no workspace dir" (spec §2c)
        ("workset.logs", "workset"),          # "no logs dir"
        ("workset.canon", "workset"),         # another workset dir key, not a member
        ("workset.channels.chat", "workset"), # ...and so is every other workset dir key
        ("box.canon", "box"),
        ("box.env.FOO", "box"),               # the §2h suppression idiom
    ])
    def test_a_null_that_means_something_is_still_written(self, key, scope_name, tmp_path):
        files = _files(tmp_path)
        scope = ConfigLevel[scope_name]
        message = _set(key, None, files, scope)
        assert not message.startswith("Error:"), message
        assert f"Set {key}=null" in message, message
        destination = files["system"] if scope is ConfigLevel.system else files[scope.value]
        assert destination.exists() and "null" in destination.read_text()

    @pytest.mark.parametrize("key,value", [
        ("system.canon", "/abs/canon"),  # a legal path still lands
        ("system.canon", ""),             # an empty value is not this rule's business
        ("system.canon", "@system.cache"),  # an '@'-ref still lands
    ])
    def test_a_non_null_value_is_untouched(self, key, value, tmp_path):
        files = _files(tmp_path)
        message = _set(key, value, files, ConfigLevel.system)
        assert not message.startswith("Error:"), message
        assert files["system"].exists()

    def test_a_bare_relative_still_gets_r147_not_this_refusal(self, tmp_path):
        # ⚑ ORDER, PINNED. The value is a shape the launch refuses too, so the door must
        # answer with [R147]'s two-readings message and NOT with the null refusal — the two
        # name different files' worth of truth, and a user told "a null path key has no
        # meaning" about the string 'comms' would be told something false.
        files = _files(tmp_path)
        message = _set("system.canon", "comms", files, ConfigLevel.system)
        assert message.startswith("Error:"), message
        assert "BARE RELATIVE" in message
        assert ERR_CONFIG_NULL_PATH_REASON not in message
