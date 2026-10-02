"""Tests for resolve_box_target() — the path-or-name box selector (W1 Phase D).

resolve_box_target resolves a ``--box`` value that is EITHER a box NAME or a
path, with box-NAME precedence in ambiguous cases (§Design 8).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kanibako.project import registry_store
from kanibako.errors import ProjectError
from kanibako.settings.paths import (
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
        # refuse-invent raises an honest error rather than path-ifying to a
        # nonexistent cwd-relative dir + minting a phantom kanibako-<hash>.
        with pytest.raises(ProjectError):
            resolve_box_target(std, config, "no-such-box")

    def test_unknown_bare_name_does_not_mint_phantom_box(
        self, std, config, tmp_home, monkeypatch,
    ):
        """Refuse-invent (BUG-B #2): a bogus bare name never resolves to an
        UNREGISTERED/empty-name box (which would hash to kanibako-<hash>).

        Mutation proof: dropping the ``if not initialize: raise`` guard in
        ``resolve_any_project`` makes this NOT raise (it path-ifies to
        ``cwd/no-such-box`` and, since that dir is absent, surfaces a different
        error or a hash box), reddening the assertion on the message.
        """
        cwd = tmp_home / "somewhere"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        # The honest name-based error, NOT the path-ified "Project path
        # '<cwd>/no-such-box' does not exist" that the fall-through produced.
        with pytest.raises(ProjectError, match="Unknown project or workset"):
            resolve_box_target(std, config, "no-such-box", initialize=False)


# ---------------------------------------------------------------------------
# Workset-MEMBER box resolves from OUTSIDE its workset (BUG-B end-to-end)
# ---------------------------------------------------------------------------

class TestWorksetMemberFromOutside:
    def test_member_box_name_resolves_from_outside(
        self, std, config, tmp_home, monkeypatch,
    ):
        """`stop <name>`/`start <name>` reach a workset member from ~ (BUG-B).

        Builds a NAMED workset with an internal member box, materializes it
        (writing the per-workset ``boxes:`` entry the launch path records), then
        resolves the bare member name from a cwd OUTSIDE the workset.
        """
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.project.workset import add_project, create_workset

        ws_root = tmp_home / "worksets" / "cluster"
        ws = create_workset("cluster", ws_root, std)
        source = tmp_home / "cluster2-src"
        source.mkdir()
        add_project(ws, "cluster2", source)
        # Materialize the box (initialize=True) so its membership is recorded in
        # the workset's per-workset ``boxes:`` registry — exactly what a first
        # `start` does.
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "cluster2", std, config,
            initialize=True,
        )

        # cwd OUTSIDE the workset tree.
        outside = tmp_home / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        proj = resolve_box_target(std, config, "cluster2", initialize=False)
        assert proj.mode is BoxMode.named
        assert proj.name == "cluster2"
        # The container name is now the real kanibako-cluster2, not a hash.
        from kanibako.utils import container_name_for
        assert container_name_for(proj) == "kanibako-cluster2"


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

    def test_workset_member_name_wins_over_folder(
        self, std, config, tmp_home, monkeypatch,
    ):
        """A registered workset-member box name wins over a same-named folder in cwd.

        Regression: bare-name resolution in resolve_any_project is skipped when
        the token matches an existing path (e.g. ./myproj), but a registered box
        name should still be found first (README: "box name (precedence) or path").
        """
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.project.workset import add_project, create_workset

        ws_root = tmp_home / "worksets" / "cluster"
        ws = create_workset("cluster", ws_root, std)
        source = tmp_home / "cluster-src"
        source.mkdir()
        add_project(ws, "myproj", source)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "myproj", std, config,
            initialize=True,
        )

        # A plain directory of the same name exists in cwd.
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        clash_dir = cwd / "myproj"
        clash_dir.mkdir()
        monkeypatch.chdir(cwd)

        # The box NAME resolves to the registered workset member, NOT ./myproj.
        proj = resolve_box_target(std, config, "myproj", initialize=False)
        assert proj.mode is BoxMode.named
        assert proj.name == "myproj"

    def test_dot_from_member_workspace_is_the_path_not_a_name(
        self, std, config, tmp_home, monkeypatch,
    ):
        """``--box .`` inside a member workspace resolves to THAT member.

        Pairs with test_workset_member_name_wins_over_folder: the name route
        must not swallow path syntax.  ``.`` has no ``/``, so a bare "try
        resolve_name first" would send it to resolve_name, whose first step
        accepts any directory under the workspaces dir — and ``.`` from a
        member workspace is one.  Mutation proof: dropping the ``.``/``..``
        guard in resolve_box_target makes this raise WorksetError
        ("Inside workset ... but not in a specific project workspace").
        """
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.project.workset import add_project, create_workset

        ws_root = tmp_home / "worksets" / "cluster"
        ws = create_workset("cluster", ws_root, std)
        source = tmp_home / "cluster-src"
        source.mkdir()
        add_project(ws, "alpha", source)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "alpha", std, config,
            initialize=True,
        )
        member_workspace = Path(ws.workspaces_dir) / "alpha"

        # cwd IS the member workspace -- the `stop .` / `box info .` case.
        monkeypatch.chdir(member_workspace)

        proj = resolve_box_target(std, config, ".", initialize=False)
        assert proj.mode is BoxMode.named
        assert proj.name == "alpha"
        assert proj.project_path == member_workspace.resolve()

    def test_unregistered_bare_folder_name_still_resolves_as_a_path(
        self, std, config, tmp_home, monkeypatch,
    ):
        """A bare token that is NOT a registered box but IS a folder -> the folder.

        The other half of the pairing: excluding ``.``/``..`` from the name
        route must not send EVERY bare token down it — an unregistered name is
        still a path, and the name route must fall through to path resolution.
        """
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        folder = cwd / "justafolder"
        folder.mkdir()
        monkeypatch.chdir(cwd)

        proj = resolve_box_target(std, config, "justafolder", initialize=False)
        assert proj.project_path == folder.resolve()


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
        # Materialize the sparse standalone marker (box_data/ + workset.yaml)
        # and register the bad name directly (bypassing the validating create
        # path, as a pre-existing box would be).  P8b/Option A: the name comes
        # from the registry key, not on-disk meta — the marker file only needs to
        # exist, and it carries no ``workset.kuid`` so the registry key wins.
        from kanibako.settings.config import WORKSET_META_FILE
        (root / WORKSET_META_FILE).write_text("")
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
