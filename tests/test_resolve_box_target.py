"""Tests for resolve_box_target() — the path-or-name box selector (W1 Phase D).

resolve_box_target resolves a ``--box`` value that is EITHER a box NAME or a
path, with box-NAME precedence in ambiguous cases (§Design 8).
"""

from __future__ import annotations

import logging

import pytest

from kanibako import registry_store
from kanibako.errors import ProjectError
from kanibako.paths import (
    BoxMode,
    establish_standalone,
    resolve_box_target,
    resolve_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_standalone(std, tmp_home, leaf: str = "sa", name: str = ""):
    """Create + register a standalone box under tmp_home; return (name, root)."""
    root = tmp_home / leaf
    root.mkdir()
    (root / "box_data").mkdir()
    box_name, *_ = establish_standalone(
        std, root, enable_vault=True, name=name,
    )
    return box_name, root


# ---------------------------------------------------------------------------
# NAME resolution
# ---------------------------------------------------------------------------

class TestResolveByName:
    def test_standalone_box_name_resolves(self, std, config, tmp_home):
        box_name, root = _make_standalone(std, tmp_home)
        proj = resolve_box_target(std, config, box_name)
        assert proj.mode is BoxMode.standalone
        assert proj.metadata_path == root.resolve()

    def test_standalone_name_is_case_folded(self, std, config, tmp_home):
        box_name, root = _make_standalone(std, tmp_home)
        proj = resolve_box_target(std, config, box_name.upper())
        assert proj.mode is BoxMode.standalone
        assert proj.metadata_path == root.resolve()

    def test_registered_default_project_name_resolves(
        self, std, config, tmp_home,
    ):
        # Create a default-mode project on disk; resolve_project auto-registers
        # the basename "myapp" in the projects registry section.
        proj_root = tmp_home / "myapp"
        proj_root.mkdir()
        resolve_project(std, config, project_dir=str(proj_root), initialize=True)

        proj = resolve_box_target(std, config, "myapp")
        assert proj.project_path == proj_root.resolve()


# ---------------------------------------------------------------------------
# PATH resolution
# ---------------------------------------------------------------------------

class TestResolveByPath:
    def test_default_project_path_resolves(self, std, config, tmp_home):
        proj_root = tmp_home / "viapath"
        proj_root.mkdir()
        resolve_project(std, config, project_dir=str(proj_root), initialize=True)

        proj = resolve_box_target(std, config, str(proj_root))
        assert proj.project_path == proj_root.resolve()

    def test_standalone_path_resolves(self, std, config, tmp_home):
        _box_name, root = _make_standalone(std, tmp_home, leaf="onpath")
        proj = resolve_box_target(std, config, str(root))
        assert proj.mode is BoxMode.standalone

    def test_none_resolves_cwd(self, std, config, project_dir, monkeypatch):
        # cwd is an initialized default project.
        resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        monkeypatch.chdir(project_dir)
        proj = resolve_box_target(std, config, None)
        assert proj.project_path == project_dir.resolve()

    def test_unknown_name_that_is_not_a_path_errors(self, std, config):
        # A bare token that matches no name and is not an existing path:
        # falls through to path resolution and fails like positional `project`.
        with pytest.raises(ProjectError):
            resolve_box_target(std, config, "no-such-box")


# ---------------------------------------------------------------------------
# NAME precedence (name wins over a same-named relative path)
# ---------------------------------------------------------------------------

class TestNamePrecedence:
    def test_name_wins_over_relative_path(
        self, std, config, tmp_home, monkeypatch,
    ):
        # A standalone box named e.g. "ab2c3_clash" lives elsewhere...
        box_name, sa_root = _make_standalone(std, tmp_home, leaf="elsewhere")

        # ...and a DIFFERENT directory of the same basename exists in cwd.
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        clash_dir = cwd / box_name
        clash_dir.mkdir()
        monkeypatch.chdir(cwd)

        # Bare token == the box name: NAME precedence -> the standalone box,
        # NOT the relative ./<box_name> directory.
        proj = resolve_box_target(std, config, box_name)
        assert proj.mode is BoxMode.standalone
        assert proj.metadata_path == sa_root.resolve()


# ---------------------------------------------------------------------------
# Pre-existing non-conforming name: resolves but FLAGGED, not rejected
# ---------------------------------------------------------------------------

class TestNonConformingNameFlagged:
    def test_nonconforming_name_resolves_with_warning(
        self, std, config, tmp_home, caplog,
    ):
        # Register a standalone box whose stored NAME violates the blocklist
        # (e.g. an interior space) — simulates a box created before the rule.
        root = tmp_home / "legacy"
        root.mkdir()
        (root / "box_data").mkdir()
        bad_name = "bad name"  # contains whitespace -> non-conforming
        # Write meta with the bad name + register it directly (bypassing the
        # validating create path, as a pre-existing box would be).
        from kanibako.config import write_project_meta, BOX_META_FILE
        from kanibako.paths import _standalone_box_paths
        from kanibako.utils import project_hash
        shell_p, vro, vrw = _standalone_box_paths(root)
        write_project_meta(
            root / BOX_META_FILE,
            mode="standalone",
            workspace=str(root / "workspace"),
            shell=str(shell_p), vault_ro=str(vro), vault_rw=str(vrw),
            enable_vault=True,
            metadata=str(root / "box_data"),
            project_hash=project_hash(str(root.resolve())),
            name=bad_name,
        )
        registry_store.register_standalone(std.registry, bad_name, root)

        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, bad_name)

        # It STILL resolves (not rejected) ...
        assert proj.mode is BoxMode.standalone
        # ... but a warning was emitted flagging the non-conforming name.
        assert any(
            "does not meet the naming rules" in r.message for r in caplog.records
        )

    def test_conforming_name_emits_no_warning(
        self, std, config, tmp_home, caplog,
    ):
        box_name, _root = _make_standalone(std, tmp_home, leaf="clean")
        with caplog.at_level(logging.WARNING):
            resolve_box_target(std, config, box_name)
        assert not any(
            "does not meet the naming rules" in r.message for r in caplog.records
        )
