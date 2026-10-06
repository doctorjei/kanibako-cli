"""Keyspec §0 "Per-owner resources" at the launch: a per-owner key or bind entry whose
winning value a CONTAINING settings file supplies must reach its owner's identity.

DESIGN § 6 Case 4 rows 3–4 are refused at start, naming the file and the entry; sharing
spelled with a shared category key is accepted; an undeclared ``agent.<agent>.*`` entry
is not policed (keyspec §0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import box_workset_settings_paths, resolve_project
from kanibako.settings.settings_launch import snapshot_leaf
from kanibako.settings.settings_resolve import SettingsError
from kanibako.targets.shell import ShellTarget


def _launch(std, proj):
    """The start path's own resolve (``commands.start._resolve_launch_snapshot``)."""
    from kanibako.commands.start import _resolve_launch_snapshot

    snapshot, _ = _resolve_launch_snapshot(
        std=std, proj=proj, agent_name="claude", system_settings_path=std.settings,
        agent_cfg_path=None, desc=None, install=None, target=ShellTarget(),
        agent_cfg=None, deliver_creds=True, cli_level=None,
    )
    return snapshot


def _merge_into(path: Path, table: dict) -> Path:
    """Deep-merge *table* into the settings file at *path*; return the file."""
    doc = load_doc(path) if path.exists() else {}

    def merge(into: dict, top: dict) -> None:
        for key, value in top.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            else:
                into[key] = value

    merge(doc, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, doc)
    return path


@pytest.fixture
def box(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


def _refusal(std, proj) -> str:
    with pytest.raises(SettingsError) as excinfo:
        _launch(std, proj)
    return str(excinfo.value)


class TestCaseFourAtStart:
    """DESIGN § 6 Case 4, written in the system file."""

    def test_a_new_box_destination_written_from_above_is_refused(self, std, box, tmp_path):
        """Row 3: ``box.bindings.rw`` at an undeclared dest is box-owned (its category)."""
        src = str(tmp_path / "srv" / "data")
        system = _merge_into(std.settings, {"box": {"bindings": {"rw": {"/data": [src]}}}})
        message = _refusal(std, box)
        assert f"box.bindings.rw[/data] is set to {src!r} in {system}" in message
        assert "names no box identity" in message
        assert "system.bindings.rw" in message  # sharing on purpose has a shared key

    def test_a_declared_per_box_entry_overridden_from_above_is_refused(
        self, std, box, tmp_path,
    ):
        """Row 4: the declared ``~/workspace`` entry is box-owned, wherever it is written."""
        src = str(tmp_path / "srv" / "ws")
        system = _merge_into(
            std.settings, {"box": {"bindings": {"rw": {"~/workspace": [src]}}}},
        )
        message = _refusal(std, box)
        assert "box.bindings.rw[" in message and "/workspace] is set to" in message
        assert str(system) in message
        assert f"{src}/{{meta.workset.path}}/{{meta.box.name}}" in message

    @pytest.mark.parametrize("spelling", [
        "{meta.workset.path}/{meta.box.name}", "@meta.workset.path/@meta.box.name",
    ], ids=["new-form", "old-form"])
    def test_the_anchored_cure_is_accepted(self, std, box, tmp_path, spelling):
        src = f"{tmp_path}/srv/ws/{spelling}"
        _merge_into(std.settings, {"box": {"bindings": {"rw": {"/data": [src]}}}})
        snapshot = _launch(std, box)
        assert snapshot_leaf(snapshot, "box.bindings.rw") is not None

    def test_system_bindings_sharing_is_accepted(self, std, box, tmp_path):
        """Row 2's twin for ``rw``: a shared category key names one resource on purpose."""
        src = str(tmp_path / "srv" / "data")
        _merge_into(std.settings, {"system": {"bindings": {"rw": {"/data": [src]}}}})
        snapshot = _launch(std, box)
        assert snapshot_leaf(snapshot, "system.bindings.rw")["/data"].src == src

    def test_an_undeclared_agent_entry_from_the_system_file_is_accepted(
        self, std, box, tmp_path,
    ):
        """Keyspec §0: an undeclared entry under ``agent.<agent>.*`` is not policed."""
        src = str(tmp_path / "srv" / "ref")
        _merge_into(
            std.settings, {"agent": {"claude": {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        snapshot = _launch(std, box)
        assert snapshot_leaf(snapshot, "agent.claude.bindings.ro")["/ref"].src == src


class TestWhoseValueIsJudged:
    def test_a_workset_file_entry_in_the_box_category_is_refused(self, std, box, tmp_path):
        """The workset contains the box: one literal source for every box in it."""
        _, workset = box_workset_settings_paths(box)
        src = str(tmp_path / "srv" / "data")
        _merge_into(workset, {"box": {"bindings": {"ro": {"/data": [src]}}}})
        message = _refusal(std, box)
        assert f"box.bindings.ro[/data] is set to {src!r} in {workset}" in message

    def test_a_workset_file_entry_in_its_own_category_is_accepted(self, std, box, tmp_path):
        _, workset = box_workset_settings_paths(box)
        src = str(tmp_path / "srv" / "data")
        _merge_into(workset, {"workset": {"bindings": {"rw": {"/data": [src]}}}})
        _launch(std, box)

    def test_a_workset_category_entry_from_the_system_file_is_refused(
        self, std, box, tmp_path,
    ):
        src = str(tmp_path / "srv" / "data")
        system = _merge_into(
            std.settings, {"workset": {"bindings": {"rw": {"/data": [src]}}}},
        )
        message = _refusal(std, box)
        assert f"workset.bindings.rw[/data] is set to {src!r} in {system}" in message
        assert "names no working-set identity" in message

    def test_the_box_s_own_entry_is_accepted(self, std, box, tmp_path):
        box_file, _ = box_workset_settings_paths(box)
        src = str(tmp_path / "srv" / "data")
        _merge_into(box_file, {"box": {"bindings": {"rw": {"/data": [src]}}}})
        _launch(std, box)

    @pytest.mark.parametrize("shadow", [["own"], None], ids=["own-source", "unbound"])
    def test_a_system_entry_the_box_file_shadows_is_not_judged(
        self, std, box, tmp_path, shadow,
    ):
        box_file, _ = box_workset_settings_paths(box)
        _merge_into(std.settings, {"box": {"bindings": {"rw": {"/data": ["/srv/data"]}}}})
        own = None if shadow is None else [str(tmp_path / "own")]
        _merge_into(box_file, {"box": {"bindings": {"rw": {"/data": own}}}})
        _launch(std, box)

    def test_a_per_owner_key_from_the_system_file_is_refused(self, std, box, tmp_path):
        """A KEY, not an entry: ``box.canon`` is box-owned."""
        system = _merge_into(std.settings, {"box": {"canon": "/srv/canon"}})
        message = _refusal(std, box)
        assert f"box.canon is set to '/srv/canon' in {system}" in message
        assert "/srv/canon/{meta.workset.path}/{meta.box.name}" in message

    def test_a_per_owner_key_from_the_workset_file_is_refused(self, std, box):
        _, workset = box_workset_settings_paths(box)
        _merge_into(workset, {"box": {"canon": "/srv/canon"}})
        assert f"box.canon is set to '/srv/canon' in {workset}" in _refusal(std, box)

    def test_a_per_owner_key_built_on_a_per_owner_key_is_accepted(self, std, box):
        """``{workset.boxes}`` reaches workset identity (a per-owner key is an anchor)."""
        _merge_into(std.settings, {"box": {"canon": "{workset.boxes}/{meta.box.name}/c"}})
        _launch(std, box)

    def test_the_box_s_own_key_is_accepted(self, std, box):
        box_file, _ = box_workset_settings_paths(box)
        _merge_into(box_file, {"box": {"canon": "/srv/canon"}})
        _launch(std, box)

    def test_a_shared_key_from_the_system_file_is_accepted(self, std, box):
        """Case 4 row 1's class: ``box.image`` is shared."""
        _merge_into(std.settings, {"box": {"image": "example.org/rig:1"}})
        _launch(std, box)
