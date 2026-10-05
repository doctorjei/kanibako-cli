"""Keyspec §0 "Per-owner resources": a per-owner value must reach its owner's identity.

DESIGN § 6 Cases 1–4, each refused value beside its cure, in both reference spellings
(``@x`` and ``{x}``): both grammars are live until braced step 5.
"""

from __future__ import annotations

import pytest

from kanibako.settings.config import reaches_identity
from kanibako.settings.config_keys import KEY_OWNERS
from kanibako.settings.kb_store import IDENTITY_ANCHORS
from kanibako.settings.paths import BoxMode

PRIMARY, NAMED, STANDALONE = BoxMode.primary, BoxMode.named, BoxMode.standalone
ALL_MODES = tuple(BoxMode)


def _stored(values: dict[str, str] | None = None):
    return (values or {}).get


def _reaches(value, owner, mode, *, key, stored=None):
    return reaches_identity(value, owner, mode, key=key, stored=stored or _stored())


def _forms(template: str) -> tuple[str, str]:
    """*template* in the old spelling and the new: ``<x>`` becomes ``@x`` and ``{x}``."""
    import re
    return (re.sub(r"<([a-z_.]+)>", r"@\1", template), re.sub(r"<([a-z_.]+)>", r"{\1}", template))


class TestCase1WorksetBoxesFromTheSystem:
    @pytest.mark.parametrize("mode", [PRIMARY, NAMED])
    @pytest.mark.parametrize("value", ["/srv/kb", *_forms("<config.data>/kb")])
    def test_a_value_naming_no_workset_is_refused(self, mode, value):
        assert not _reaches(value, "workset", mode, key="workset.boxes")

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.path>"))
    def test_the_cure_reaches_the_workset_path(self, mode, value):
        assert _reaches(value, "workset", mode, key="workset.boxes")

    @pytest.mark.parametrize("mode", [PRIMARY, NAMED])
    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.name>"))
    def test_the_workset_name_anchors_outside_standalone(self, mode, value):
        assert _reaches(value, "workset", mode, key="workset.boxes")


class TestCase2Standalone:
    @pytest.mark.parametrize("value", ["/srv/kb", *_forms("<config.data>/kb")])
    def test_a_value_naming_no_workset_is_refused(self, value):
        assert not _reaches(value, "workset", STANDALONE, key="workset.boxes")

    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.name>"))
    def test_the_workset_name_is_every_standalone_boxs(self, value):
        assert not _reaches(value, "workset", STANDALONE, key="workset.boxes")

    @pytest.mark.parametrize("value", _forms("/srv/kb/<meta.workset.path>"))
    def test_the_cure_reaches_the_workset_path(self, value):
        assert _reaches(value, "workset", STANDALONE, key="workset.boxes")


class TestCase3Mailboxes:
    KEY = "workset.channels.mailboxes"

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", ["/srv/mb", *_forms("/srv/mb/<meta.workset.path>")])
    def test_a_value_naming_no_partition_is_refused(self, mode, value):
        assert not _reaches(value, "partition", mode, key=self.KEY)

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", _forms("/srv/mb/<meta.workset.name>"))
    def test_the_cure_reaches_the_partition(self, mode, value):
        assert _reaches(value, "partition", mode, key=self.KEY)

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", _forms("<system.channels.mailboxes>/<meta.workset.name>"))
    def test_the_spec_default_reaches_the_partition(self, mode, value):
        assert _reaches(value, "partition", mode, key=self.KEY)


class TestCase4Sharing:
    KEY = "box.bindings.rw"

    def test_a_shared_key_has_no_identity_to_reach(self):
        assert KEY_OWNERS["workset.template"] == "shared"
        assert "shared" not in IDENTITY_ANCHORS
        with pytest.raises(KeyError):
            _reaches("/srv/tmpl", "shared", NAMED, key="workset.template")

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", ["/srv/data", "/srv/ws"])
    def test_a_box_entry_naming_no_box_is_refused(self, mode, value):
        assert not _reaches(value, "box", mode, key=self.KEY)

    @pytest.mark.parametrize("mode", [PRIMARY, NAMED])
    @pytest.mark.parametrize("value", _forms("/srv/ws/<meta.box.name>"))
    def test_the_box_name_alone_is_refused_outside_standalone(self, mode, value):
        assert not _reaches(value, "box", mode, key=self.KEY)

    @pytest.mark.parametrize("mode", [PRIMARY, NAMED])
    @pytest.mark.parametrize("value", _forms("/srv/ws/<meta.workset.name>/<meta.box.name>"))
    def test_the_cure_reaches_the_box(self, mode, value):
        assert _reaches(value, "box", mode, key=self.KEY)

    @pytest.mark.parametrize("value", [*_forms("/srv/ws/<meta.workset.name>/<meta.box.name>"),
                                       *_forms("/srv/ws/<meta.box.name>"),
                                       *_forms("/srv/ws/<meta.workset.path>"),
                                       *_forms("<workset.boxes>/x")])
    def test_standalone_the_box_name_or_a_workset_anchor_alone_reaches_the_box(self, value):
        assert _reaches(value, "box", STANDALONE, key=self.KEY)

    @pytest.mark.parametrize("value", ["/srv/x", *_forms("/srv/ws/<meta.workset.name>"),
                                       *_forms("<workset.channels.mailboxes>/x"),
                                       *_forms("/srv/ws/<meta.agent.claude.name>")])
    def test_standalone_a_partition_or_agent_anchor_alone_is_refused(self, value):
        assert not _reaches(value, "box", STANDALONE, key=self.KEY)

    @pytest.mark.parametrize(("key", "default"), [
        ("meta.box.inbox", "<workset.channels.mailboxes>/<meta.box.name>"),
        ("meta.box.share_global", "<workset.channels.share_global>/<meta.box.name>"),
        ("meta.box.workspace", "<workset.workspaces>"),
        ("meta.box.path", "<workset.boxes>"),
    ])
    def test_the_standalone_spec_defaults_reach_the_box(self, key, default):
        for value in _forms(default):
            assert _reaches(value, "box", STANDALONE, key=key), value


class TestAPerOwnerKeyIsAnAnchorOfItsLevel:
    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", _forms("<workset.boxes>/<meta.box.name>"))
    def test_workset_boxes_plus_the_box_name_reaches_box_identity(self, mode, value):
        assert _reaches(value, "box", mode, key="meta.box.path")

    @pytest.mark.parametrize("mode", [PRIMARY, NAMED])
    @pytest.mark.parametrize("value", _forms("<workset.boxes>/x"))
    def test_a_workset_anchor_alone_is_no_box_identity(self, mode, value):
        assert not _reaches(value, "box", mode, key="box.bindings.rw")

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("value", _forms("<workset.boxes>/x"))
    def test_a_key_is_no_anchor_for_itself(self, mode, value):
        assert not _reaches(value, "workset", mode, key="workset.boxes")

    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("level", ["workset", "partition", "box"])
    def test_every_per_owner_key_anchors_its_level(self, mode, level):
        keys = [k for k, owner in KEY_OWNERS.items() if owner == level]
        assert keys
        for key in keys:
            for value in _forms(f"<{key}>/x"):
                assert _reaches(value, level, mode, key="box.bindings.ro"), value

    @pytest.mark.parametrize("value", _forms("<x.via>/y"))
    def test_an_anchor_is_reached_through_a_stored_referent(self, value):
        stored = _stored({"x.via": "{meta.workset.path}/z"})
        assert _reaches(value, "workset", NAMED, key="workset.boxes", stored=stored)


class TestAgent:
    KEY = "agent.claude.caches"

    @pytest.mark.parametrize("value", ["/srv/c", *_forms("<config.agents>/c"),
                                       *_forms("<meta.agent.shell.path>/c")])
    def test_a_value_naming_no_agent_or_another_is_refused(self, value):
        assert not _reaches(value, "agent", NAMED, key=self.KEY)

    @pytest.mark.parametrize("value", [*_forms("<config.agents>/<meta.agent.claude.name>"),
                                       *_forms("<meta.agent.claude.path>/caches")])
    def test_the_agents_name_or_store_reaches_it(self, value):
        assert _reaches(value, "agent", NAMED, key=self.KEY)


@pytest.mark.parametrize("level", sorted(IDENTITY_ANCHORS))
def test_every_level_names_every_box_mode(level):
    assert set(IDENTITY_ANCHORS[level]) == {mode.value for mode in BoxMode}
