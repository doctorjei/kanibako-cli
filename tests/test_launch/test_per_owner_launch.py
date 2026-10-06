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
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.paths import (
    WorksetSpec,
    box_workset_settings_paths,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
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


@pytest.fixture
def named_box(std, config, tmp_home):
    ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
    source = tmp_home / "original-project"
    source.mkdir()
    add_project(ws, "cool-app", source)
    return resolve_workset_project(
        WorksetSpec.from_workset(ws), "cool-app", std, config, initialize=True,
    )


@pytest.fixture
def standalone_box(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(std, config, str(project_dir), initialize=True)


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
        assert "does not reach box identity" in message
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
        assert "does not reach working-set identity" in message

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

    def test_a_whole_arm_reset_shadows_every_entry_below_it(self, std, box):
        """``box.bindings.rw: null`` in the box file drops the arm (``settings_merge``), so
        the system file's entry is not what the box inherits."""
        box_file, _ = box_workset_settings_paths(box)
        _merge_into(std.settings, {"box": {"bindings": {"rw": {"/data": ["/srv/data"]}}}})
        _merge_into(box_file, {"box": {"bindings": {"rw": None}}})
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


class TestTheWorksetFileByMode:
    """A workset file is judged in the launching box's mode (keyspec §0 anchors)."""

    _ENTRY = "/srv/data/{meta.workset.path}"

    def test_a_workset_anchor_alone_reaches_a_standalone_box(self, std, standalone_box):
        """Keyspec §0: in standalone a workset anchor alone suffices."""
        _, workset = box_workset_settings_paths(standalone_box)
        _merge_into(workset, {"box": {"bindings": {"rw": {"/data": [self._ENTRY]}}}})
        _launch(std, standalone_box)

    def test_a_workset_anchor_alone_does_not_reach_a_named_box(self, std, named_box):
        _, workset = box_workset_settings_paths(named_box)
        _merge_into(workset, {"box": {"bindings": {"rw": {"/data": [self._ENTRY]}}}})
        message = _refusal(std, named_box)
        assert f"box.bindings.rw[/data] is set to {self._ENTRY!r} in {workset}" in message

    def test_a_literal_in_a_standalone_workset_file_is_refused(self, std, standalone_box):
        _, workset = box_workset_settings_paths(standalone_box)
        _merge_into(workset, {"box": {"canon": "/x"}})
        assert f"box.canon is set to '/x' in {workset}" in _refusal(std, standalone_box)


def _descriptor(owner: str):
    """An in-test plugin descriptor declaring ONE ``/ref`` bind row owned by *owner*."""
    from kanibako.targets.base import (
        BindKind, BindScope, Binding, HostSrcOrigin, PluginDescriptor,
    )

    row = Binding(
        key="ref", origin=HostSrcOrigin.LITERAL, box_dest="/ref", kind=BindKind.DIR,
        scope=BindScope.AGENT, literal_src=Path("/srv/plugin-ref"), owner=owner,
    )
    return PluginDescriptor(command=("claude",), bindings=(row,), mode={"start": ()})


def _launch_with(std, proj, desc):
    from kanibako.commands.start import _resolve_launch_snapshot

    snapshot, _ = _resolve_launch_snapshot(
        std=std, proj=proj, agent_name="claude", system_settings_path=std.settings,
        agent_cfg_path=None, desc=desc, install=None, target=ShellTarget(),
        agent_cfg=None, deliver_creds=True, cli_level=None,
    )
    return snapshot


_SHIPPED = [
    ("kanibako.plugins.claude", "claude-defaults.yaml"),
    ("kanibako.plugins.codex", "codex-defaults.yaml"),
    ("kanibako.plugins.goose", "goose-defaults.yaml"),
]


class TestDeclaredPluginBindRows:
    """Keyspec §0: a DECLARED ``agent.<agent>.*`` bind entry is judged by its row's owner."""

    @pytest.mark.parametrize("node", ["claude", "default"])
    def test_a_per_box_plugin_row_written_from_above_is_refused(
        self, std, box, tmp_path, node,
    ):
        src = str(tmp_path / "srv" / "ref")
        system = _merge_into(
            std.settings, {"agent": {node: {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        with pytest.raises(SettingsError) as excinfo:
            _launch_with(std, box, _descriptor("box"))
        message = str(excinfo.value)
        assert f"bindings.ro[/ref] is set to {src!r} in {system}" in message
        assert "does not reach box identity" in message
        assert f"{src}/{{meta.workset.path}}/{{meta.box.name}}" in message

    def test_a_per_agent_plugin_row_names_the_active_agent_in_its_cure(
        self, std, box, tmp_path,
    ):
        src = str(tmp_path / "srv" / "ref")
        _merge_into(
            std.settings, {"agent": {"default": {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        with pytest.raises(SettingsError) as excinfo:
            _launch_with(std, box, _descriptor("agent"))
        assert f"{src}/{{meta.agent.claude.name}}" in str(excinfo.value)

    @pytest.mark.parametrize("anchors", [
        "", "/{meta.workset.path}", "/{meta.workset.name}", "/{meta.box.name}",
        "/{meta.workset.path}/{meta.box.name}",
    ], ids=["none", "ws-path", "ws-name", "box-name", "box"])
    def test_a_per_agent_row_lacking_the_agent_says_different_agents_share_it(
        self, std, box, tmp_path, anchors,
    ):
        src = f"{tmp_path / 'srv' / 'ref'}{anchors}"
        _merge_into(
            std.settings, {"agent": {"default": {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        with pytest.raises(SettingsError) as excinfo:
            _launch_with(std, box, _descriptor("agent"))
        message = str(excinfo.value)
        assert "which would give different agents one shared path" in message, message
        assert "Different agents would share it." in message, message
        assert "Every agent" not in message and "of each working set" not in message, message
        assert f"{src}/{{meta.agent.claude.name}}" in message, message

    def test_a_per_box_plugin_row_in_the_box_s_own_file_is_accepted(
        self, std, box, tmp_path,
    ):
        box_file, _ = box_workset_settings_paths(box)
        src = str(tmp_path / "srv" / "ref")
        _merge_into(box_file, {"agent": {"claude": {"bindings": {"ro": {"/ref": [src]}}}}})
        _launch_with(std, box, _descriptor("box"))

    def test_a_shared_plugin_row_written_from_above_is_accepted(self, std, box, tmp_path):
        src = str(tmp_path / "srv" / "ref")
        _merge_into(
            std.settings, {"agent": {"claude": {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        snapshot = _launch_with(std, box, _descriptor("shared"))
        assert snapshot_leaf(snapshot, "agent.claude.bindings.ro")["/ref"].src == src

    def test_another_agent_s_entry_is_not_judged_by_the_active_descriptor(
        self, std, box, tmp_path,
    ):
        src = str(tmp_path / "srv" / "ref")
        _merge_into(
            std.settings, {"agent": {"goose": {"bindings": {"ro": {"/ref": [src]}}}}},
        )
        _launch_with(std, box, _descriptor("box"))

    @pytest.mark.parametrize(("package", "filename"), _SHIPPED)
    def test_every_shipped_plugin_row_is_shared(self, package, filename):
        from kanibako.settings.agent_defaults import load_descriptor

        desc = load_descriptor(package, filename)
        assert desc.bindings
        assert {b.owner for b in desc.bindings} == {"shared"}

    def test_a_shipped_plugin_row_written_from_above_is_accepted(self, std, box, tmp_path):
        from kanibako.settings.agent_defaults import load_descriptor

        desc = load_descriptor(*_SHIPPED[0])
        src = str(tmp_path / "srv" / "share")
        dest = desc.bindings[0].box_dest
        _merge_into(std.settings, {"agent": {"claude": {"bindings": {"ro": {dest: [src]}}}}})
        _launch_with(std, box, desc)
