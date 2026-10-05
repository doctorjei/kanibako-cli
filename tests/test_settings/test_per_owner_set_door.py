"""Keyspec §0 "Per-owner resources" at the set door: a per-owner value set from a scope
that contains its owner must reach that owner's identity, or nothing is written.

DESIGN § 6 Cases 1, 3 and 4, each refused value beside its cure, in both reference
spellings (``@x`` and ``{x}``): both grammars are live until braced step 5.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

from kanibako.project.workset import create_workset, default_workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_keys import KEY_OWNERS, ConfigLevel


def _forms(template: str) -> tuple[str, str]:
    """*template* in the old spelling and the new: ``<x>`` becomes ``@x`` and ``{x}``."""
    return (re.sub(r"<([a-z_.]+)>", r"@\1", template), re.sub(r"<([a-z_.]+)>", r"{\1}", template))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _system_set(key: str, value: str, std) -> str:
    return set_config_value(
        key, value, config_path=std.config_file, system_settings_path=std.settings,
        cascade_system_path=std.settings, command_scope=ConfigLevel.system,
        agents_root=std.agents, std=std,
    )


def _workset_set(key: str, value: str, ws, std) -> str:
    return set_config_value(
        key, value, config_path=ws.root / WORKSET_META_FILE,
        cascade_system_path=std.settings, command_scope=ConfigLevel.workset, std=std, ws=ws,
    )


@pytest.fixture
def seeded(std):
    """The system file already holds a value, so "nothing was written" is a hash, not absence."""
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text(yaml.safe_dump({"box": {"shell": "bash"}}))
    return std


@pytest.fixture
def ws(tmp_path, std):
    return create_workset("ownws", tmp_path / "ws", std)


def _refused(message: str, key: str, value: str, file: Path, cure: str) -> None:
    assert message.startswith("Error: nothing was written: "), message
    assert f"{key} = {value!r}" in message, message
    assert str(file) in message, message
    assert f"Spell the identity into the value: {cure!r}" in message, message


class TestCase1WorksetBoxesFromTheSystem:
    KEY = "workset.boxes"

    @pytest.mark.parametrize("value", ["/srv/kb", *_forms("<system.state>/kb")])
    def test_a_value_naming_no_workset_is_refused_and_nothing_is_written(self, value, seeded):
        before = _digest(seeded.settings)
        message = _system_set(self.KEY, value, seeded)
        _refused(message, self.KEY, value, seeded.settings, f"{value}/{{meta.workset.path}}")
        assert "system scope" in message and "working-set identity" in message, message
        assert _digest(seeded.settings) == before

    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.name>"))
    def test_the_workset_name_is_refused_because_standalone_shares_it(self, value, seeded):
        before = _digest(seeded.settings)
        assert _system_set(self.KEY, value, seeded).startswith("Error: nothing was written")
        assert _digest(seeded.settings) == before

    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.path>"))
    def test_the_cure_is_written(self, value, seeded):
        assert _system_set(self.KEY, value, seeded) == f"Set {self.KEY}={value}"
        assert yaml.safe_load(seeded.settings.read_text())["workset"]["boxes"] == value


class TestCase3MailboxesFromTheSystem:
    KEY = "workset.channels.mailboxes"

    @pytest.mark.parametrize("value", ["/srv/mb", *_forms("/srv/mb/<meta.workset.path>")])
    def test_a_value_naming_no_partition_is_refused_and_nothing_is_written(self, value, seeded):
        before = _digest(seeded.settings)
        message = _system_set(self.KEY, value, seeded)
        _refused(message, self.KEY, value, seeded.settings, f"{value}/{{meta.workset.name}}")
        assert "every channel partition" in message, message
        assert _digest(seeded.settings) == before

    @pytest.mark.parametrize("value", _forms("/srv/mb/<meta.workset.name>"))
    def test_the_cure_is_written(self, value, seeded):
        assert _system_set(self.KEY, value, seeded) == f"Set {self.KEY}={value}"


class TestCase4Sharing:
    """Entries are YAML-only (launch, D6b); at this door Case 4 is a shared key, which is
    accepted, and a box-owned key, which is not."""

    def test_a_shared_key_is_written(self, seeded):
        assert KEY_OWNERS["workset.template"] == "shared"
        assert _system_set("workset.template", "/srv/tmpl", seeded) == "Set workset.template=/srv/tmpl"

    @pytest.mark.parametrize("value", ["/srv/c", *_forms("/srv/c/<meta.workset.path>"),
                                       *_forms("/srv/c/<meta.box.name>")])
    def test_a_box_key_naming_no_box_is_refused_and_nothing_is_written(self, value, seeded):
        before = _digest(seeded.settings)
        message = _system_set("box.canon", value, seeded)
        _refused(message, "box.canon", value, seeded.settings,
                 f"{value}/{{meta.workset.path}}/{{meta.box.name}}")
        assert _digest(seeded.settings) == before

    @pytest.mark.parametrize("value", _forms("/srv/c/<meta.workset.path>/<meta.box.name>"))
    def test_the_cure_is_written(self, value, seeded):
        assert _system_set("box.canon", value, seeded) == f"Set box.canon={value}"


class TestTheWorksetDoor:
    @pytest.mark.parametrize("value", ["/srv/c", *_forms("<workset.boxes>/c")])
    def test_a_box_key_naming_no_box_is_refused_and_nothing_is_written(self, value, ws, std):
        file = ws.root / WORKSET_META_FILE
        before = _digest(file)
        message = _workset_set("box.canon", value, ws, std)
        _refused(message, "box.canon", value, file, f"{value}/{{meta.workset.path}}/{{meta.box.name}}")
        assert "workset scope" in message, message
        assert _digest(file) == before

    @pytest.mark.parametrize("value", _forms("<workset.boxes>/<meta.box.name>/c"))
    def test_the_cure_is_written(self, value, ws, std):
        assert _workset_set("box.canon", value, ws, std) == f"Set box.canon={value}"

    @pytest.mark.parametrize("value", _forms("<workset.template>/<meta.box.name>"))
    def test_an_anchor_is_reached_through_a_value_the_file_stores(self, value, ws, std):
        assert _workset_set("workset.template", "{meta.workset.path}/t", ws, std).startswith("Set ")
        assert _workset_set("box.canon", value, ws, std) == f"Set box.canon={value}"

    def test_a_workset_value_is_its_own_level(self, ws, std):
        assert _workset_set("workset.boxes", "/srv/kb", ws, std) == "Set workset.boxes=/srv/kb"
        assert _workset_set("workset.channels.mailboxes", "/srv/mb", ws, std).startswith("Set ")

    def test_a_pref_the_allowlist_refuses_gets_its_own_refusal(self, ws, std):
        file = ws.root / WORKSET_META_FILE
        before = _digest(file)
        message = _workset_set("pref.box.canon", "/srv/pc", ws, std)
        assert message.startswith("Error:") and "pref.box.canon" in message, message
        assert "Spell the identity" not in message, message
        assert _digest(file) == before


def test_a_box_value_at_the_box_door_is_its_own_level(tmp_path, std):
    box = tmp_path / "box.yaml"
    message = set_config_value(
        "box.canon", "/srv/c", config_path=box, cascade_system_path=std.settings,
        cascade_box_path=box, command_scope=ConfigLevel.box,
    )
    assert message == "Set box.canon=/srv/c", message


class TestTheCureChainsAtTheSystemDoor:
    """A system value built on a stored, anchored system ``workset.boxes``: the set-time probe
    forgives the referent's ``meta.workset.*`` refs as it forgives a direct one (keyspec §0
    defaults-down), and still refuses a referent that dangles."""

    @pytest.mark.parametrize("ref", ["@workset.boxes", "{workset.boxes}"])
    @pytest.mark.parametrize("boxes", _forms("/srv/kb/<meta.workset.path>"))
    def test_a_value_on_the_stored_cure_is_written(self, boxes, ref, seeded):
        assert _system_set("workset.boxes", boxes, seeded) == f"Set workset.boxes={boxes}"
        value = f"{ref}/lg"
        assert _system_set("workset.logs", value, seeded) == f"Set workset.logs={value}"

    @pytest.mark.parametrize("boxes", [*_forms("<meta.workset.path>/<meta.workset.nope>"),
                                       *_forms("<meta.workset.path>/<config.nope>")])
    def test_a_stored_referent_that_dangles_is_still_refused(self, boxes, seeded):
        seeded.settings.write_text(yaml.safe_dump({"workset": {"boxes": boxes}}))
        before = _digest(seeded.settings)
        message = _system_set("workset.logs", "{workset.boxes}/lg", seeded)
        assert message.startswith("Error:") and "dangling @-reference" in message, message
        assert _digest(seeded.settings) == before


_CYCLE = "/s/<meta.workset.path>/<meta.box.name>/<box.canon>"
_UNKNOWN_VAR = "/s/<meta.workset.path>/<meta.box.name>/{$NOPE_UNKNOWN}"


class TestOnlyTheFloorsBlindnessIsForgiven:
    """The set-time probe forgives a ref the command's files cannot see by construction, and
    nothing else on the chain: a cycle or an unknown variable the edit does not fix stands
    (keyspec §2a)."""

    @pytest.mark.parametrize("value", _forms("/c/<meta.workset.path>/<meta.box.name>"))
    def test_the_primary_workset_takes_the_cure(self, value, std):
        assert _workset_set("box.canon", value, default_workset(std), std) == f"Set box.canon={value}"

    @pytest.mark.parametrize("stored", [*_forms(_CYCLE), *_forms(_UNKNOWN_VAR)])
    def test_a_stored_defect_is_refused_at_the_system_door(self, stored, seeded):
        seeded.settings.write_text(yaml.safe_dump({"box": {"shell": stored}}))
        before = _digest(seeded.settings)
        assert _system_set("box.canon", "{box.shell}", seeded).startswith("Error:")
        assert _digest(seeded.settings) == before

    @pytest.mark.parametrize("stored", [*_forms(_CYCLE), *_forms(_UNKNOWN_VAR)])
    def test_a_stored_defect_is_refused_at_the_workset_door(self, stored, ws, std):
        file = ws.root / WORKSET_META_FILE
        doc = (yaml.safe_load(file.read_text()) if file.exists() else None) or {}
        file.write_text(yaml.safe_dump(doc | {"box": {"shell": stored}}))
        before = _digest(file)
        assert _workset_set("box.canon", "{box.shell}", ws, std).startswith("Error:")
        assert _digest(file) == before
