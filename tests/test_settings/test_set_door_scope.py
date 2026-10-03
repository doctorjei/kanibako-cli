"""Two doors, one rule about WHERE a value may be WRITTEN (spec §0).

**Row 1 — a BOX STORE value the launch cannot use.** ``workset.boxes`` is the one key the
launch's box-root assertion reads for every rooted box key: it continues past a value only
when that value is a NON-EMPTY ``str`` not ending in ``/``. So ``workset set ws1
workset.boxes=`` used to answer ``Set workset.boxes=`` and store ``''`` — a file the very
next launch refuses, with the user learning it there rather than at the door they typed. The
membership is :func:`config.refuses_box_store_value` and the wording is
:func:`config.null_path_keys_error`, the same carrier and the same one-reason discipline the
``--null`` door builds on, one value class apart.

⚑ THE LAUNCH'S TEST IS REPRODUCED VERBATIM, NOT WIDENED — see
``TestTheLaunchsOwnTestIsTheWholeRule``: a whitespace-only value is a NON-EMPTY string, the
launch takes it, and so must this door. A ``strip()`` here would be a set door refusing
something the launch accepts, which is the worse error of the two.

**Row 2 — a fully spelled key of a CONTAINED scope.** Spec §0: "a scope's settings file may
hold keys of the scopes it CONTAINS ... which serve as OVERRIDABLE defaults", and a scope "may
NOT write a setting in a containing (higher) level". The set-time resolution probe judges a
candidate value against the COMMAND's cascade, so a referent living in the contained scope is
absent from that snapshot by construction — and ``system set workset.canon=@workset.channelroot/x``
was refused as a "dangling @-reference (no such config key in the keyspace)" for a key that is
declared and that every launch resolves. :func:`_floor_blind_default` forgives exactly that
blindness and nothing else.

⚑ SO THE ASYMMETRY IS THE POINT, and both halves are pinned: ``TestAnUpwardWriteStaysRefused``
holds the containing-level write still refused with its UNCHANGED message, and
``TestWhatStaysRefused`` holds every defect that is not a referent this floor cannot see — an
undeclared referent, a cycle, a malformed ref, a ``@config.*``/scopeless ref.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project.workset import create_workset
from kanibako.settings.bootstrap import BOXES_PATH
from kanibako.settings.config import (
    BOX_META_FILE,
    WORKSET_META_FILE,
    null_path_keys_error,
    refuses_box_store_value,
    refuses_null_path_key,
)
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.messages import (
    ERR_BOX_STORE_EMPTY_REASON,
    ERR_BOX_STORE_SET_HEAD,
    ERR_BOX_STORE_TRAILING_REASON,
    ERR_CONFIG_NULL_PATH_REASON,
    ERR_CONFIG_NULL_PATH_SET_HEAD,
)
from tests.support.filenames import CONFIG_FILENAME

_BOXES = f"workset.{BOXES_PATH}"
#: A contained-scope key and a ref INTO the contained scope — the shape §0 says a system
#: file may hold, and the shape the base refused as a dangling @-reference.
_CONTAINED_KEY = "workset.canon"
_CONTAINED_REF = "@workset.channelroot/x"


def _files(tmp_path: Path) -> dict:
    """The settings files the scopes write, each in a plausible tier directory."""
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


@pytest.fixture
def ws_files(tmp_path, std):
    """:func:`_files` over a REAL working set, so the workset tier holds its own anchor.

    ⚑ WITHOUT ONE, ``@workset.channelroot`` dangles at the WORKSET door too and the
    "unchanged at its own door" half of Row 2 would be testing a missing anchor rather
    than the door's behaviour. ``create_workset`` refuses a root that already exists, so
    the tiers are laid down around it, not before it.
    """
    files = {
        "config": tmp_path / CONFIG_FILENAME,
        "system": tmp_path / "settings.yaml",
        "agents": tmp_path / "agents",
        "std": std,
    }
    ws = create_workset("setdoorws", tmp_path / "ws", std)
    box = ws.root / "boxes" / "b"
    box.mkdir(parents=True)
    return files | {"ws": ws, "workset": ws.root / WORKSET_META_FILE,
                    "box": box / BOX_META_FILE}


def _set(key: str, value, files: dict, scope: ConfigLevel, *, std=None, ws=None) -> str:
    """Drive ``set_config_value`` with the threading the matching noun command uses.

    ⚑ ``std``/``ws`` are the real ones on purpose: the set-time probe builds the COMMAND's
    target from them, and a caller that threads neither gets a target-LESS snapshot — so an
    under-threaded helper would report a ref as dangling that the launch resolves.
    """
    if scope is ConfigLevel.system:
        return set_config_value(
            key, value, config_path=files["config"],
            system_settings_path=files["system"], cascade_system_path=files["system"],
            command_scope=scope, agents_root=files["agents"], std=std,
        )
    if scope is ConfigLevel.workset:
        return set_config_value(
            key, value, config_path=files["workset"],
            cascade_system_path=files["system"], cascade_workset_path=files["workset"],
            command_scope=scope, std=std, ws=ws,
        )
    return set_config_value(
        key, value, config_path=files["box"],
        cascade_system_path=files["system"], cascade_workset_path=files["workset"],
        cascade_box_path=files["box"], command_scope=scope,
    )


# --------------------------------------------------------------------------- #
# Row 1 — the box store at a value the launch refuses                        #
# --------------------------------------------------------------------------- #

class TestTheLaunchRefusesWhatSetAccepted:
    """The defect's two shapes: ``set`` wrote the value, the launch refused it."""

    def test_an_empty_box_store_is_refused_and_nothing_is_written(self, tmp_path):
        files = _files(tmp_path)
        message = _set(_BOXES, "", files, ConfigLevel.workset)
        assert message.startswith("Error:"), message
        assert _BOXES in message, message
        assert ERR_BOX_STORE_SET_HEAD % _BOXES in message, message
        assert ERR_BOX_STORE_EMPTY_REASON in message, message
        assert "Nothing was written" in message, message
        # ⚑ REFUSED BEFORE THE WRITE — on a fresh store the file does not EXIST, so there
        # is no ``''`` anywhere to go and undo.
        assert not files["workset"].exists(), (
            f"{files['workset']} was written by a refused set: {message}"
        )

    def test_a_trailing_separator_box_store_is_refused_with_the_launchs_own_clause(
        self, tmp_path,
    ):
        files = _files(tmp_path)
        message = _set(_BOXES, "/tmp/boxes/", files, ConfigLevel.workset)
        assert message.startswith("Error:"), message
        assert ERR_BOX_STORE_TRAILING_REASON in message, message
        assert not files["workset"].exists(), message

    def test_the_same_refusal_holds_at_the_system_door(self, tmp_path):
        """⚑ ONE RULE, EVERY DOOR: ``system set workset.boxes=`` is the same write, so the
        launch's refusal cannot be reachable at one noun and not another."""
        files = _files(tmp_path)
        message = _set(_BOXES, "", files, ConfigLevel.system)
        assert message.startswith("Error:"), message
        assert ERR_BOX_STORE_EMPTY_REASON in message, message
        assert not files["system"].exists(), message

    def test_a_valid_box_store_is_still_accepted_and_written(self, tmp_path):
        files = _files(tmp_path)
        message = _set(_BOXES, "/tmp/boxstore", files, ConfigLevel.workset)
        assert not message.startswith("Error:"), message
        assert files["workset"].exists(), "a valid store was not written"
        assert "boxstore" in files["workset"].read_text()


class TestTheLaunchsOwnTestIsTheWholeRule:
    """🛑 The membership is the launch's test, so it is pinned in BOTH directions."""

    @pytest.mark.parametrize("value", [
        "",                       # the defect this file exists for
        "/tmp/boxstore/",         # a trailing separator: the final segment resolved to nothing
        0,                        # a non-str value: the launch's test refuses it too
        None,                     # the --null door's shape — same predicate, other message
    ])
    def test_the_launch_refuses_these(self, value):
        assert refuses_box_store_value(_BOXES, value) is True

    @pytest.mark.parametrize("value", [
        "/tmp/boxstore",
        "/tmp/box store",         # a space inside is not an empty value
        " ",                      # ⚑ WHITESPACE IS NOT EMPTY — the launch takes it
        "\t",
        "~",
    ])
    def test_the_launch_takes_these(self, value):
        assert refuses_box_store_value(_BOXES, value) is False

    @pytest.mark.parametrize("key", [
        "system.canon",           # a path key, but not THE box store
        "workset.workspaces",     # a workset dir key whose null MEANS "no dir" (§2c)
        "workset.logs",
        "box.image",
    ])
    def test_no_other_key_is_in_the_membership(self, key):
        assert refuses_box_store_value(key, "") is False

    def test_a_whitespace_box_store_is_not_refused_by_this_door(self, tmp_path):
        """⚑ A ``strip()`` here would be WORSE THAN BASE: the launch accepts a non-empty
        string, so a set door that refused one would turn a working launch into a dead one."""
        files = _files(tmp_path)
        message = _set(_BOXES, " ", files, ConfigLevel.workset)
        assert ERR_BOX_STORE_SET_HEAD not in message, message


class TestTheRefusalCarriesTheLaunchsOwnWords:
    """One reason, one home — the anti-drift half, the way ``--null``'s door is built."""

    def test_both_value_classes_build_through_the_one_carrier(self):
        """The empty-value door calls ``config.null_path_keys_error``, the same builder
        ``_refuse_null_paths`` raises through, so a second copy of the text cannot exist."""
        built = null_path_keys_error(
            Path("/nowhere/settings.yaml"), (_BOXES,),
            head=ERR_BOX_STORE_SET_HEAD, cure=ERR_BOX_STORE_EMPTY_REASON,
        )
        assert built is not None
        assert built == ERR_BOX_STORE_SET_HEAD % _BOXES + ERR_BOX_STORE_EMPTY_REASON

    def test_the_null_membership_still_answers_for_the_null_door(self):
        """The two doors are one value class apart, so neither borrows the other's message."""
        assert refuses_null_path_key(_BOXES) is True
        assert refuses_box_store_value(_BOXES, None) is True

    def test_the_null_door_keeps_its_own_lead(self, tmp_path):
        files = _files(tmp_path)
        message = _set(_BOXES, None, files, ConfigLevel.workset)
        assert ERR_CONFIG_NULL_PATH_SET_HEAD % _BOXES in message, message
        assert ERR_CONFIG_NULL_PATH_REASON in message, message
        assert ERR_BOX_STORE_SET_HEAD % _BOXES not in message, message
        assert not files["workset"].exists(), message

    def test_the_lead_never_claims_a_file_holds_the_value(self, tmp_path):
        """Nothing was written, so naming a file would be a false statement about the
        user's own store — the reason the ``--null`` door has a lead of its own."""
        files = _files(tmp_path)
        message = _set(_BOXES, "", files, ConfigLevel.workset)
        assert str(files["workset"]) not in message, message


# --------------------------------------------------------------------------- #
# Row 2 — a fully spelled key of a CONTAINED scope                          #
# --------------------------------------------------------------------------- #

class TestAContainedScopeKeyIsAcceptedAtTheContainingDoor:
    """Spec §0's directional rule: a file may hold the keys of the scopes it CONTAINS."""

    def test_a_contained_scope_key_is_accepted_and_written_to_the_system_file(
        self, tmp_path, std,
    ):
        files = _files(tmp_path)
        message = _set(_CONTAINED_KEY, _CONTAINED_REF, files, ConfigLevel.system, std=std)
        assert not message.startswith("Error:"), message
        assert files["system"].exists(), "the downward default was not written"
        assert "canon" in files["system"].read_text()

    def test_the_same_key_at_its_own_door_is_unchanged(self, ws_files):
        """The contained scope's own door probes against the store that holds the referent,
        so it was never blind and must not have been taught anything new."""
        message = _set(_CONTAINED_KEY, _CONTAINED_REF, ws_files, ConfigLevel.workset,
                       std=ws_files["std"], ws=ws_files["ws"])
        assert not message.startswith("Error:"), message

    def test_a_dangling_ref_at_the_keys_own_door_is_still_refused(self, ws_files):
        """The cure is keyed on the CONTAINMENT RELATION, so it cannot fire where the floor
        DOES hold the referent — the contained scope's own door is not exempt."""
        message = _set(_CONTAINED_KEY, "@workset.nonexistent", ws_files,
                       ConfigLevel.workset, std=ws_files["std"], ws=ws_files["ws"])
        assert message.startswith("Error:"), message
        assert "dangling @-reference" in message, message

    def test_a_contained_key_with_a_bare_value_is_unaffected(self, tmp_path, std):
        """The cure is about a ref this floor cannot see; a plain value never went near it."""
        files = _files(tmp_path)
        message = _set(_CONTAINED_KEY, "/srv/canon", files, ConfigLevel.system, std=std)
        assert not message.startswith("Error:"), message


class TestAnUpwardWriteStaysRefused:
    """The other half of §0's rule, and the message is UNCHANGED text."""

    def test_a_workset_door_still_refuses_a_system_key(self, tmp_path):
        files = _files(tmp_path)
        message = _set("system.canon", "/tmp/canon", files, ConfigLevel.workset)
        assert message.startswith("Error:"), message
        assert "writing upward is refused" in message, message
        assert "Set it at the system scope instead." in message, message
        assert not files["workset"].exists(), message

    def test_a_system_door_still_refuses_a_meta_key(self, tmp_path):
        files = _files(tmp_path)
        message = _set("meta.box.name", "b", files, ConfigLevel.system)
        assert message.startswith("Error:"), message
        assert not files["system"].exists(), message

    def test_a_ref_the_floor_does_hold_is_judged_normally(self, tmp_path, std):
        """The blindness is a property of the ABSENCE, not a blanket pass: at the system
        door the ``@system.*`` referent IS in the floor, so the probe judges it as always."""
        files = _files(tmp_path)
        message = _set(_CONTAINED_KEY, "@system.canon", files, ConfigLevel.system, std=std)
        assert not message.startswith("Error:"), message


class TestWhatStaysRefused:
    """🛑 A RELAXED refusal is the real risk, so every non-blindness defect is pinned here."""

    @pytest.mark.parametrize("value", [
        "@workset.nonexistent",             # undeclared referent: a real dangling ref
        "@workset.canon",                   # self-reference: a cycle, not a missing referent
        "@workset.channelroot/@workset.nope",   # one declared and one not
        "@config.nonexistent",              # the Layer-1 foundation, not a cascade scope
        "@",                                # malformed
    ])
    def test_these_are_still_refused_at_the_system_door(self, tmp_path, std, value):
        files = _files(tmp_path)
        message = _set(_CONTAINED_KEY, value, files, ConfigLevel.system, std=std)
        assert message.startswith("Error:"), f"{value!r} was ACCEPTED: {message}"
        assert not files["system"].exists(), f"{value!r} was WRITTEN: {message}"

    def test_an_escaped_ref_is_not_a_ref(self, tmp_path, std):
        r"""``\@workset.nonexistent`` is a literal, so the floor's blindness must not open.

        ⚑ This is a GUARD, not a new behaviour: the value never reaches the probe, because
        the ``[R147]`` bare-relative refusal takes it first. What is pinned is that the
        blindness rule did not open behind that guard.
        """
        files = _files(tmp_path)
        message = _set(_CONTAINED_KEY, r"\@workset.nonexistent", files,
                       ConfigLevel.system, std=std)
        assert "dangling @-reference" not in message, message
        assert not files["system"].exists(), message
