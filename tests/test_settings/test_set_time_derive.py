"""Keyspec §2a: set time derives the keys the launch derives after its expand (the
``meta.box.agent.*`` mirror, the computed ``*_active`` auth keys) from the command's cascade,
while a reference to nothing is still refused."""

from __future__ import annotations

import pytest
import yaml

from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_keys import ConfigLevel


def _write(path, doc):
    path.write_text(yaml.safe_dump(doc))
    return path


def _box_set(tmp_path, key, value, *, agent_doc=None):
    ssp = _write(tmp_path / "settings.yaml", {})
    box = tmp_path / "box.yaml"
    if not box.exists():
        _write(box, {})
    kw = {}
    if agent_doc is not None:
        kw["cascade_agent_path"] = _write(tmp_path / "agent.yaml", agent_doc)
    return set_config_value(
        key, value, config_path=box, cascade_system_path=ssp, cascade_box_path=box,
        cascade_agent_name="claude", command_scope=ConfigLevel.box, **kw,
    )


def _stored(path, *keys):
    node = yaml.safe_load(path.read_text())
    for key in keys:
        node = (node or {}).get(key)
    return node


class TestDerivedReferentsResolve:
    def test_a_mirror_ref_derives_once_the_agent_tier_is_present(self, tmp_path):
        """The derivation, given an agent tier through ``cascade_agent_path``; ``box set``
        threads no agent tier yet, so through the CLI a mirror ref is still refused."""
        msg = _box_set(
            tmp_path, "box.env.FOO", "{meta.box.agent.canon}/x",
            agent_doc={"agent": {"claude": {"canon": "/c"}}},
        )
        assert not msg.startswith("Error:"), msg
        assert _stored(tmp_path / "box.yaml", "box", "env", "FOO") == "{meta.box.agent.canon}/x"

    def test_a_box_auth_ref_is_accepted(self, tmp_path):
        msg = _box_set(tmp_path, "box.env.FOO", "{meta.box.auth.global_active}")
        assert not msg.startswith("Error:"), msg
        assert _stored(tmp_path / "box.yaml", "box", "env", "FOO") == "{meta.box.auth.global_active}"

    def test_a_workset_auth_ref_is_accepted_at_workset_scope(self, tmp_path):
        ssp = _write(tmp_path / "settings.yaml", {})
        ws = _write(tmp_path / "workset.yaml", {})
        msg = set_config_value(
            "workset.env.FOO", "{meta.workset.auth.global_active}", config_path=ws,
            cascade_system_path=ssp, cascade_workset_path=ws,
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert _stored(ws, "workset", "env", "FOO") == "{meta.workset.auth.global_active}"

    def test_a_workset_auth_ref_is_accepted_at_system_scope(self, tmp_path):
        ssp = _write(tmp_path / "settings.yaml", {})
        msg = set_config_value(
            "system.env.FOO", "{meta.workset.auth.global_active}",
            config_path=tmp_path / "kanibako.cfg", system_settings_path=ssp,
            cascade_system_path=ssp, command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        assert _stored(ssp, "system", "env", "FOO") == "{meta.workset.auth.global_active}"


class TestDanglingRefsAreStillRefused:
    @pytest.mark.parametrize("value", ["{meta.box.agent.nope}", "{box.nope}/x"])
    def test_a_ref_to_no_key_is_refused(self, tmp_path, value):
        msg = _box_set(
            tmp_path, "box.env.FOO", value,
            agent_doc={"agent": {"claude": {"canon": "/c"}}},
        )
        assert msg.startswith("Error:") and "dangling" in msg, msg
        assert _stored(tmp_path / "box.yaml", "box") is None

    def test_an_absent_auth_input_still_dangles_when_the_value_names_it(self, tmp_path):
        # The auth derivation reads ``system.auth.share_allowed`` as absent; the value's
        # own direct ref to it in the same pass is still the edited value's defect.
        msg = _box_set(
            tmp_path, "box.env.FOO",
            "{meta.box.auth.global_active}/{system.auth.share_allowed}",
        )
        assert msg.startswith("Error:") and "system.auth.share_allowed" in msg, msg
