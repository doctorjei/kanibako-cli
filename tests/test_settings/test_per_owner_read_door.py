"""Keyspec §0 "Per-owner resources" at the read door: a per-owner early key the system file
holds with a value that reaches no owner identity is refused by every reader, naming the file.

The set door's judgment, so the two doors agree; in both reference spellings (``@x`` and
``{x}``), since both grammars are live until braced step 5.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import load_std_paths
from kanibako.settings.workset_dirkeys import (
    EarlyScope, EarlySystem, early_repoint, refuse_inherited_per_owner,
)

_FORMS = ("@", "{")


def _ref(form: str, name: str) -> str:
    return f"@{name}" if form == "@" else "{" + name + "}"


def _write_system(std, workset: dict, system: dict | None = None) -> None:
    doc: dict = {"workset": workset}
    if system is not None:
        doc["system"] = system
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text(yaml.safe_dump(doc))


def _scope(tier: dict[str, str | None], file: Path) -> EarlyScope:
    return EarlyScope(EarlySystem(tier=tier, file=file, system_paths={}), "kento")


class TestLoadStdPaths:
    def test_a_system_boxes_literal_is_refused_naming_the_file(self, std, config_file):
        _write_system(std, {"boxes": "/srv/kb"})
        with pytest.raises(ConfigError) as excinfo:
            load_std_paths(load_config(config_file))
        text = str(excinfo.value)
        assert text.startswith(f"workset.boxes is set to '/srv/kb' in {std.settings},"), text
        assert "does not reach working-set identity" in text
        # The cure order: the anchored system value, the reset-then-each-workset form, stop --all.
        cure = text.index("kanibako system set 'workset.boxes=/srv/kb/{meta.workset.path}'")
        reset = text.index("kanibako system reset workset.boxes")
        assert cure < reset < text.index("kanibako workset set <workset> 'workset.boxes=")
        assert "kanibako stop --all stops every running box even when a settings file is refused" in text

    @pytest.mark.parametrize("form", _FORMS)
    def test_the_anchored_cure_is_read(self, std, config_file, form):
        _write_system(std, {"boxes": "/srv/kb/" + _ref(form, "meta.workset.path")})
        load_std_paths(load_config(config_file))

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_value_built_on_a_refused_referent_is_refused(self, std, config_file, form):
        _write_system(std, {"boxes": "/srv/kb", "logs": _ref(form, "workset.boxes") + "/lg"})
        with pytest.raises(ConfigError, match=r"workset\.boxes is set to '/srv/kb'"):
            load_std_paths(load_config(config_file))

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_value_built_on_an_anchored_referent_is_read(self, std, config_file, form):
        _write_system(std, {
            "boxes": "/srv/kb/" + _ref(form, "meta.workset.path"),
            "logs": _ref(form, "workset.boxes") + "/lg",
        })
        load_std_paths(load_config(config_file))


class TestEarlyRepoint:
    def test_a_workset_own_value_is_not_refused(self, tmp_path):
        own = {"workset": {"boxes": "/srv/kb"}}
        scope = _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml")
        assert early_repoint(tmp_path, own, "boxes", early=scope) == (
            "/srv/kb", tmp_path / WORKSET_META_FILE,
        )

    def test_an_inherited_value_is_refused(self, tmp_path):
        scope = _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml")
        with pytest.raises(ConfigError, match=str(tmp_path / "settings.yaml")):
            early_repoint(tmp_path, None, "boxes", early=scope)

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_referent_the_workset_owns_anchors_an_inherited_value(self, tmp_path, form):
        own = {"workset": {"boxes": "/srv/kb"}}
        scope = _scope(
            {"workset.logs": _ref(form, "workset.boxes") + "/lg"}, tmp_path / "settings.yaml",
        )
        assert early_repoint(tmp_path, own, "logs", early=scope)[1] == tmp_path / "settings.yaml"

    def test_a_present_null_and_a_shared_key_are_not_judged(self, tmp_path):
        scope = _scope(
            {"workset.boxes": None, "workset.template": "/srv/tmpl"}, tmp_path / "settings.yaml",
        )
        assert early_repoint(tmp_path, None, "boxes", early=scope)[0] is None
        assert early_repoint(tmp_path, None, "template", early=scope)[0] == "/srv/tmpl"


class TestRefuseInheritedPerOwner:
    @pytest.mark.parametrize("form", _FORMS)
    def test_a_partition_literal_is_refused_and_its_cure_passes(self, tmp_path, form):
        settings = tmp_path / "settings.yaml"
        with pytest.raises(ConfigError) as excinfo:
            refuse_inherited_per_owner(
                tmp_path, _scope({"workset.channels.mailboxes": "/srv/mb"}, settings),
            )
        assert "does not reach partition identity" in str(excinfo.value)
        assert "'workset.channels.mailboxes=/srv/mb/{meta.workset.name}'" in str(excinfo.value)
        refuse_inherited_per_owner(tmp_path, _scope(
            {"workset.channels.mailboxes": "/srv/mb/" + _ref(form, "meta.workset.name")}, settings,
        ))

    def test_the_workset_own_file_shields_its_keys(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text(yaml.safe_dump({"workset": {"boxes": "/srv/kb"}}))
        refuse_inherited_per_owner(
            tmp_path, _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml"),
        )

    def test_every_inherited_per_owner_key_is_judged(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text(yaml.safe_dump({"workset": {"boxes": "/srv/kb"}}))
        scope = _scope(
            {"workset.boxes": "/srv/kb", "workset.vault_rw": "/srv/vault"}, tmp_path / "s.yaml",
        )
        with pytest.raises(ConfigError, match=r"workset\.vault_rw is set to '/srv/vault'"):
            refuse_inherited_per_owner(tmp_path, scope)

    def test_a_shared_key_is_not_refused(self, tmp_path):
        refuse_inherited_per_owner(
            tmp_path, _scope({"workset.template": "/srv/tmpl"}, tmp_path / "settings.yaml"),
        )
