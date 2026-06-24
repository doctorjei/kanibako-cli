"""Tests for kanibako.box_seed -- the uniform per-box seed-once dispatcher."""

from __future__ import annotations

from pathlib import Path

from kanibako import box_seed, registry_store
from kanibako.paths import BoxMode, ProjectGroup, ProjectPaths
from kanibako.workset import add_project, create_workset, load_workset


def _make_proj(mode: BoxMode, name: str, group: ProjectGroup | None) -> ProjectPaths:
    """Build a minimal ProjectPaths carrying only the dispatch-relevant fields."""
    dummy = Path("/nonexistent")
    return ProjectPaths(
        project_path=dummy,
        project_hash="0" * 16,
        metadata_path=dummy,
        shell_path=dummy,
        vault_ro_path=dummy,
        vault_rw_path=dummy,
        mode=mode,
        name=name,
        group=group,
    )


def _primary_group(std) -> ProjectGroup:
    return ProjectGroup(
        name="default",
        root=std.data_path,
        is_default=True,
        local_shared_base=std.data_path,
    )


# ---------------------------------------------------------------------------
# PRIMARY → registry seeded.projects
# ---------------------------------------------------------------------------


def test_primary_routes_to_projects_domain(std, tmp_home):
    proj = _make_proj(BoxMode.primary, "myapp", _primary_group(std))

    assert box_seed.box_is_seeded(proj, std) is False
    box_seed.mark_box_seeded(proj, std)
    assert box_seed.box_is_seeded(proj, std) is True

    # Landed in projects domain ONLY.
    assert registry_store.is_box_seeded(std.data_path, "projects", "myapp") is True
    assert registry_store.is_box_seeded(std.data_path, "standalone", "myapp") is False


# ---------------------------------------------------------------------------
# STANDALONE → registry seeded.standalone
# ---------------------------------------------------------------------------


def test_standalone_routes_to_standalone_domain(std, tmp_home):
    proj = _make_proj(BoxMode.standalone, "abc_box", None)

    assert box_seed.box_is_seeded(proj, std) is False
    box_seed.mark_box_seeded(proj, std)
    assert box_seed.box_is_seeded(proj, std) is True

    # Landed in standalone domain ONLY.
    assert registry_store.is_box_seeded(std.data_path, "standalone", "abc_box") is True
    assert registry_store.is_box_seeded(std.data_path, "projects", "abc_box") is False


# ---------------------------------------------------------------------------
# NAMED → WorksetProject.seeded
# ---------------------------------------------------------------------------


def test_named_routes_to_workset_project(std, tmp_home):
    ws = create_workset("myset", tmp_home / "worksets" / "myset", std)
    add_project(ws, "projname", tmp_home / "project")

    group = ProjectGroup(
        name=ws.name,
        root=ws.root,
        is_default=False,
        local_shared_base=ws.root,
    )
    proj = _make_proj(BoxMode.named, "projname", group)

    assert box_seed.box_is_seeded(proj, std) is False
    box_seed.mark_box_seeded(proj, std)
    assert box_seed.box_is_seeded(proj, std) is True

    # Landed on the WorksetProject, NOT in either registry domain.
    assert load_workset(ws.root).projects[0].seeded is True
    assert registry_store.is_box_seeded(std.data_path, "projects", "projname") is False
    assert (
        registry_store.is_box_seeded(std.data_path, "standalone", "projname") is False
    )


def test_named_missing_group_is_defensive(std, tmp_home):
    """A named box with no group reads unseeded and mark is a no-op."""
    proj = _make_proj(BoxMode.named, "orphan", None)
    assert box_seed.box_is_seeded(proj, std) is False
    box_seed.mark_box_seeded(proj, std)  # must not raise
    assert box_seed.box_is_seeded(proj, std) is False


def test_marking_one_mode_leaves_others_empty(std, tmp_home):
    """Marking the primary box does not touch the standalone domain or worksets."""
    ws = create_workset("myset", tmp_home / "worksets" / "myset", std)
    add_project(ws, "shared", tmp_home / "project")

    primary = _make_proj(BoxMode.primary, "shared", _primary_group(std))
    box_seed.mark_box_seeded(primary, std)

    # Standalone domain untouched; the same-named workset project unseeded.
    assert registry_store.is_box_seeded(std.data_path, "standalone", "shared") is False
    assert load_workset(ws.root).projects[0].seeded is False
