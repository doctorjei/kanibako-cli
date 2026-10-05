"""The production sites in ``paths``, ``names``, ``box_resolve`` and ``settings_launch`` hand
their early readers ``std``'s record, under the partition name the read is for.

Every read of the system tier goes through :func:`workset_dirkeys.early_repoint`; the
``early_reads`` fixture wraps it in every module that holds it, refuses a call with no scope (that call would open the
system settings file again), and records the scope names it saw.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.project import names, registry_store, workset_registry
from kanibako.settings import paths
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import BoxMode, STANDALONE_META_DIR
from kanibako.settings.workset_dirkeys import EarlyScope


@pytest.fixture
def early_reads(monkeypatch) -> list[str]:
    """The scope names every early read is made under; a read with no scope raises."""
    from kanibako.settings import workset_dirkeys

    real = workset_dirkeys.early_repoint
    seen: list[str] = []

    def spy(workset_root, workset_settings, key, *, early=None):
        if early is None:
            raise AssertionError(f"workset.{key} under {workset_root} was read with no record")
        seen.append(early.workset_name)
        return real(workset_root, workset_settings, key, early=early)

    for module in list(sys.modules.values()):
        if getattr(module, "early_repoint", None) is real:
            monkeypatch.setattr(module, "early_repoint", spy)
    return seen


def _named_workset(std, root: Path, name: str) -> Path:
    """Register a NAMED workset *name* at *root*; returns its per-workset registry file."""
    root.mkdir(parents=True, exist_ok=True)
    section = registry_store.load_section(std.registry, "worksets")
    section[name] = str(root)
    registry_store.save_section(std.registry, "worksets", section)
    return workset_registry.resolve_workset_registry_path(
        root, None, early=EarlyScope(std.early_system, name))


class TestPrimarySites:
    def test_create_and_register_a_primary_box(self, std, config_file, tmp_home, early_reads):
        proj = paths.resolve_project(std, load_config(config_file), str(tmp_home / "project"),
                                     initialize=True)
        assert proj.mode is BoxMode.primary
        assert set(early_reads) == {WS_TOKEN_PRIMARY}

    def test_iter_projects(self, std, config_file, early_reads):
        std.boxes.mkdir(parents=True, exist_ok=True)
        paths.iter_projects(std, load_config(config_file))
        assert set(early_reads) == {WS_TOKEN_PRIMARY}

    def test_the_primary_name_api(self, std, tmp_home, early_reads):
        early = paths._early_scope(std, BoxMode.primary)
        name = paths.assign_primary_box_name(std.primary_workset, std.registry,
                                             tmp_home / "project", early=early)
        assert paths.primary_box_name_for_workspace(
            std.primary_workset, str(tmp_home / "project"), early=early) == name
        paths.unregister_primary_box_name(std.primary_workset, name, early=early)
        assert set(early_reads) == {WS_TOKEN_PRIMARY}


class TestNamedSites:
    def test_detection_and_lookups_use_the_workset_name(self, std, config_file, tmp_home,
                                                        early_reads):
        from kanibako.launch import box_resolve

        root = tmp_home / "wsroot"
        registry = _named_workset(std, root, "team")
        outside = tmp_home / "elsewhere"
        outside.mkdir()
        workset_registry.register_workset_box(registry, "ext", outside)

        assert paths.detect_project_mode(root, std, load_config(config_file)).mode is BoxMode.named
        assert box_resolve.find_connected_external_box(outside, std) is not None
        assert box_resolve._find_owning_box(outside, std, load_config(config_file)) is not None
        assert names.resolve_qualified_name(std.registry, "team/ext",
                                            early_system=std.early_system)[1] == "team"
        assert names.resolve_name(std.registry, "ext", early_system=std.early_system)[0] == \
            str(outside)
        assert "team" in early_reads
        assert set(early_reads) <= {"team", WS_TOKEN_PRIMARY}

    def test_the_ancestor_walk_scopes_a_candidate_by_its_leaf(self, std, config_file,
                                                             tmp_home, early_reads):
        """A candidate root is checked under the name it would import as."""
        root = tmp_home / "found"
        root.mkdir()
        paths.detect_project_mode(root, std, load_config(config_file))
        assert "found" in early_reads

    def test_a_workset_listing_loads_with_the_record(self, std, config_file, tmp_home,
                                                     early_reads):
        _named_workset(std, tmp_home / "wsroot", "team")
        rows = paths.iter_workset_projects(std, load_config(config_file))
        assert [ws.early_system for _, ws, _ in rows] == [std.early_system]


class TestStandaloneSites:
    def test_an_existing_standalone_box_resolves_under_the_partition(
        self, std, config_file, tmp_home, early_reads,
    ):
        root = tmp_home / "lone"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        (root / WORKSET_META_FILE).write_text("box: {}\n")
        proj = paths.resolve_standalone_project(std, load_config(config_file), str(root))
        paths.box_logs_location(std, proj)
        assert set(early_reads) == {WS_TOKEN_STANDALONE}


class TestLaunchInputs:
    def test_a_named_working_set_resolves_under_its_name(self, std, tmp_home, request):
        from kanibako.project.workset import create_workset
        from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs

        ws = create_workset("team", tmp_home / "worksets" / "team", std)
        early_reads = request.getfixturevalue("early_reads")
        resolve_inputs(subject=ResolveSubject.WORKSET, std=std, agent_name="",
                       system_path=None, ws=ws)
        assert "team" in early_reads
        assert set(early_reads) <= {"team", WS_TOKEN_PRIMARY}

    def test_a_named_box_logs_dir_resolves_under_its_workset(self, std, tmp_home, request):
        from kanibako.project.workset import create_workset

        ws = create_workset("team", tmp_home / "worksets" / "team", std)
        early_reads = request.getfixturevalue("early_reads")
        paths.box_logs_dir_for(std, BoxMode.named, ws.projects_dir / "b", ws.root,
                               workset_name=ws.name)
        assert set(early_reads) == {"team"}
