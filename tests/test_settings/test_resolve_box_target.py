"""Tests for resolve_box_target() — the path-or-name box selector (W1 Phase D).

resolve_box_target resolves a ``--box`` value that is EITHER a box NAME or a
path, with box-NAME precedence in ambiguous cases (§Design 8).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kanibako.project import registry_store
from kanibako.errors import AmbiguousNameError, DerivedBoxNameError, ProjectError
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    establish_standalone,
    load_primary_boxes,
    resolve_any_project,
    DesignationRoute,
    designation_route,
    resolve_box_target,
    resolve_project,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_standalone(std, tmp_home, leaf: str = "sa"):
    """Create + register a standalone box under tmp_home; return (name, root).

    No ``name`` argument: a standalone box is named from its root, never by the caller.
    """
    root = tmp_home / leaf
    root.mkdir()
    (root / "box_data").mkdir()
    box_name, *_ = establish_standalone(std, root, enable_vault=True)
    return box_name, root


def _make_member(std, config, tmp_home, workset: str, member: str) -> Path:
    """Create a NAMED workset with one materialized member box; return its workspace."""
    from kanibako.settings.paths import WorksetSpec, resolve_workset_project
    from kanibako.project.workset import add_project, create_workset

    ws = create_workset(workset, tmp_home / "worksets" / workset, std)
    source = tmp_home / f"{workset}-src"
    source.mkdir()
    add_project(ws, member, source)
    # Materialize it, so the membership lands in the workset's per-workset
    # ``boxes:`` registry -- exactly what a first ``start`` does.
    resolve_workset_project(
        WorksetSpec.from_workset(ws), member, std, config, initialize=True,
    )
    return Path(ws.workspaces_dir) / member


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

    def test_a_primary_box_outranks_a_registered_standalone_of_the_same_name(
        self, std, config, tmp_home, caplog,
    ):
        """Spec order at the ``--box`` door (system-design § Detection & import):
        the PRIMARY box wins over a registered standalone holding the same name,
        and the shadow is announced.  This door used to read the ``standalone``
        section FIRST, which inverted that precedence."""
        from kanibako.project import registry_store

        primary_ws = tmp_home / "solo_box"
        primary_ws.mkdir()
        resolve_project(std, config, project_dir=str(primary_ws), initialize=True)

        sa_root = tmp_home / "sa" / "solo_box"
        sa_root.mkdir(parents=True)
        (sa_root / "box_data").mkdir()
        (sa_root / "workset.yaml").write_text(
            "box:\n  image: ghcr.io/x:1\nworkset:\n  registry: null\n")
        registry_store.register_standalone(std.registry, "solo_box", sa_root)

        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, "solo_box")
        assert proj.mode is BoxMode.primary
        assert proj.project_path == primary_ws.resolve()
        shadowed = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "standalone" in r.getMessage()]
        assert len(shadowed) == 1, shadowed
        assert str(sa_root) in shadowed[0]


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
        # The container name is now the real kb-cluster-cluster2, not a hash.
        from kanibako.utils import container_name_for
        assert container_name_for(proj) == "kb-cluster-cluster2"


# ---------------------------------------------------------------------------
# NAME precedence (a box name wins over a same-named relative path, except a standalone's)
# ---------------------------------------------------------------------------

class TestNamePrecedence:
    def test_a_same_named_path_outranks_a_registered_standalone_name(
        self, std, config, tmp_home, monkeypatch, caplog,
    ):
        """system-design § Box designation & workset path space: "Registered standalone
        box names are checked _after_ primary workset boxes and paths; a warning sounds
        on collision."  So ``./<name>`` wins at the ``--box`` door, and the shadowed
        standalone is named."""
        box_name, sa_root = _make_standalone(std, tmp_home, leaf="elsewhere")
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        clash_dir = cwd / box_name
        clash_dir.mkdir()
        monkeypatch.chdir(cwd)

        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, box_name)
        assert proj.project_path == clash_dir.resolve()
        shadowed = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "standalone" in r.getMessage()]
        assert len(shadowed) == 1, shadowed
        assert str(sa_root) in shadowed[0]

    def test_the_positional_door_warns_when_a_path_shadows_a_standalone_name(
        self, std, config, tmp_home, monkeypatch, caplog,
    ):
        box_name, sa_root = _make_standalone(std, tmp_home, leaf="elsewhere")
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        (cwd / box_name).mkdir()
        monkeypatch.chdir(cwd)

        with caplog.at_level(logging.WARNING):
            proj = resolve_any_project(std, config, box_name)
        assert proj.project_path == (cwd / box_name).resolve()
        shadowed = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "standalone" in r.getMessage()]
        assert len(shadowed) == 1, shadowed
        assert str(sa_root) in shadowed[0]

    def test_a_path_shadowing_a_standalone_a_workset_also_names_still_warns(
        self, std, config, tmp_home, monkeypatch, caplog,
    ):
        """Standalone ``foo`` + workset ``foo`` + folder ``./foo``: at ``--box`` the
        folder wins and the shadowed standalone is named exactly once."""
        from kanibako.project.workset import create_workset

        box_name, sa_root = _make_standalone(std, tmp_home, leaf="elsewhere")
        create_workset(box_name, tmp_home / "worksets" / box_name, std)
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        (cwd / box_name).mkdir()
        monkeypatch.chdir(cwd)

        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, box_name)
        assert proj.project_path == (cwd / box_name).resolve()
        shadowed = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "standalone" in r.getMessage()]
        assert len(shadowed) == 1, shadowed
        assert str(sa_root) in shadowed[0]

    def test_a_standalone_name_with_no_same_named_path_still_resolves(
        self, std, config, tmp_home, monkeypatch, caplog,
    ):
        box_name, sa_root = _make_standalone(std, tmp_home, leaf="elsewhere")
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, box_name)
        assert proj.mode is BoxMode.standalone
        assert proj.metadata_path == sa_root.resolve()
        assert not [r for r in caplog.records if "shadowed" in r.getMessage()]

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
        # Register a standalone box whose stored NAME violates the allowlist
        # (e.g. an interior space) — simulates a box created before the rule.
        root = tmp_home / "legacy"
        root.mkdir()
        (root / "box_data").mkdir()
        bad_name = "bad name"  # contains whitespace -> non-conforming
        # Materialize the sparse standalone marker (the root workset.yaml registry null)
        # and register the bad name directly (bypassing the validating create
        # path, as a pre-existing box would be).  P8b/Option A: the name comes
        # from the registry key, not on-disk meta — the marker file only needs its
        # registry null, and it carries no ``workset.kuid`` so the registry key wins.
        from kanibako.settings.config import WORKSET_META_FILE
        (root / WORKSET_META_FILE).write_text("workset:\n  registry: null\n")
        registry_store.register_standalone(std.registry, bad_name, root)

        # A name that fails the box-name rule is a PATH designation, so the box is
        # addressed by its root.
        with caplog.at_level(logging.WARNING):
            proj = resolve_box_target(std, config, str(root))

        # It STILL resolves (not rejected) ...
        assert proj.mode is BoxMode.standalone
        assert proj.name == bad_name
        # ... but a warning was emitted flagging the non-conforming name.
        assert any(
            "does not meet the naming rules" in r.message for r in caplog.records
        )
        # Its name is not looked up: it is a relative path, and none exists.
        with pytest.raises(ProjectError, match="does not exist"):
            resolve_box_target(std, config, bad_name)

    def test_conforming_name_emits_no_warning(
        self, std, config, tmp_home, caplog,
    ):
        box_name, _root = _make_standalone(std, tmp_home, leaf="clean")
        with caplog.at_level(logging.WARNING):
            resolve_box_target(std, config, box_name)
        assert not any(
            "does not meet the naming rules" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# An AMBIGUOUS name is still a NAME: a same-named cwd folder must not shadow it
# ---------------------------------------------------------------------------

class TestAmbiguousNameNotShadowedByFolder:
    def test_ambiguous_member_name_with_same_named_folder_raises(
        self, std, config, tmp_home, monkeypatch,
    ):
        """A name that is a member of TWO worksets errors, even beside a folder.

        The defect this pins: ``alpha`` is registered in ``cluster-a`` AND
        ``cluster-b``, and cwd also holds a plain ``./alpha`` directory.
        ``resolve_box_target`` consults the NAME route for any real name token
        (that is what makes a name beat a same-named folder), so the ambiguity
        error is raised -- and then ``except ProjectError: pass`` discarded it,
        handing back ``./alpha``.  The CLI then reported the wrong box with rc=0.

        The discriminator is NOT "is it a folder" -- an unregistered name that IS
        a folder is still a path (asserted by
        test_unregistered_bare_folder_name_still_resolves_as_a_path).  It is
        "did the NAME route raise AMBIGUITY specifically".
        """
        _make_member(std, config, tmp_home, "cluster-a", "alpha")
        _make_member(std, config, tmp_home, "cluster-b", "alpha")

        cwd = tmp_home / "cwd"
        cwd.mkdir()
        folder = cwd / "alpha"
        folder.mkdir()
        monkeypatch.chdir(cwd)

        with pytest.raises(AmbiguousNameError) as excinfo:
            resolve_box_target(std, config, "alpha", initialize=False)
        # Actionable: it names BOTH candidates as <workset>/<name> plus the cure.
        # The qualified NAME, not the workspace path: workset.workspaces is
        # settable, so a registered path need not contain its workset name.
        message = str(excinfo.value)
        assert "Ambiguous box name" in message
        assert "cluster-a/alpha" in message
        assert "cluster-b/alpha" in message
        assert "<workset>/alpha" in message

    def test_unambiguous_member_name_with_same_named_folder_still_wins(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The control half of the pairing: ONE workset is not ambiguous.

        Mutation proof for the test above: exempting ``ProjectError`` broadly, or
        keying the exemption on "the token is a folder" instead of on the
        ambiguity TYPE, makes either this resolve raise or the folder-shadowing
        case return ``./alpha``.
        """
        workspace = _make_member(std, config, tmp_home, "solo", "gamma")

        cwd = tmp_home / "cwd"
        cwd.mkdir()
        folder = cwd / "gamma"
        folder.mkdir()
        monkeypatch.chdir(cwd)

        proj = resolve_box_target(std, config, "gamma", initialize=False)
        assert proj.mode is BoxMode.named
        assert proj.name == "gamma"
        assert proj.project_path == workspace.resolve()

    def test_ambiguous_member_name_raises_through_resolve_any_project(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The SECOND front door, which has its own swallowing handler.

        ``resolve_any_project`` swallows ``ProjectError`` to fall through to the
        path route, so it discarded the ambiguity error too.  Its name lookup is
        guarded by ``not Path(raw).exists()``, so the folder cannot shadow the
        name HERE -- the reachable form of this bug is the token with no folder
        at all, which must still be refused rather than path-ified.
        """
        _make_member(std, config, tmp_home, "cluster-a", "beta")
        _make_member(std, config, tmp_home, "cluster-b", "beta")

        cwd = tmp_home / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        with pytest.raises(AmbiguousNameError, match="Ambiguous box name"):
            resolve_any_project(std, config, "beta", initialize=False)

    def test_ambiguous_member_name_on_the_create_path_is_not_pathified(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The CREATE path (``initialize=True``) must refuse an ambiguous name too.

        ``resolve_any_project`` re-raises a swallowed ``ProjectError`` only
        ``if not initialize``, so on the create path an unknown bare token is
        deliberately path-ified into a new box.  That is right for an UNKNOWN
        name, but for an AMBIGUOUS one it yielded the strictly worse
        ``Project path '<cwd>/beta' does not exist`` -- which names a directory
        the user never asked for and hides the two boxes they must choose
        between.  Same handler narrowing as the read path, so it is pinned here.
        """
        _make_member(std, config, tmp_home, "cluster-a", "beta")
        _make_member(std, config, tmp_home, "cluster-b", "beta")

        cwd = tmp_home / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        with pytest.raises(AmbiguousNameError, match="Ambiguous box name"):
            resolve_any_project(std, config, "beta", initialize=True)
        # The create path must NOT have registered the box it used to path-ify.
        # Asserted on the MEMBERSHIP, the store ``register_primary_box_name``
        # writes to -- not on ``cwd / "beta"``, a directory this path never
        # created, so that assertion could not have failed.
        assert "beta" not in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        )

    def test_ambiguous_member_name_raises_through_resolve_project(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The THIRD front door: ``resolve_project``'s own swallowing handler.

        The last of the three ``except ProjectError:`` handlers that discarded the
        ambiguity error, and the one nothing covered.  Its lookup is guarded by
        ``not Path(raw).exists()``, so the reachable form is the bare token with no
        folder -- which must surface the actionable error rather than the strictly
        worse ``Project path '<cwd>/delta' does not exist``.
        """
        _make_member(std, config, tmp_home, "cluster-a", "delta")
        _make_member(std, config, tmp_home, "cluster-b", "delta")

        cwd = tmp_home / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        with pytest.raises(AmbiguousNameError) as excinfo:
            resolve_project(std, config, project_dir="delta")
        # Actionable, exactly like the other two front doors: qualified names.
        message = str(excinfo.value)
        assert "Ambiguous box name" in message
        assert "cluster-a/delta" in message
        assert "cluster-b/delta" in message
        assert "<workset>/delta" in message

    def test_unknown_name_through_resolve_project_still_pathifies(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The control: an UNKNOWN token is still path-resolved, not refused.

        The discriminator is the exception TYPE.  A token that matched nothing must
        keep falling through to the path route and end in the path-naming
        ``ProjectError`` -- never in ``AmbiguousNameError``.
        """
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        with pytest.raises(ProjectError) as excinfo:
            resolve_project(std, config, project_dir="nosuchbox")
        assert not isinstance(excinfo.value, AmbiguousNameError)
        assert "nosuchbox" in str(excinfo.value)

    def test_unregistered_folder_name_still_resolves_through_resolve_any_project(
        self, std, config, tmp_home, monkeypatch,
    ):
        """The other control: an UNKNOWN name that is a folder is still a path.

        Proves the narrowing above did not widen into refusing every name miss --
        the behavior ``resolve_any_project`` exists to provide for a bare token
        that is simply a directory.
        """
        cwd = tmp_home / "cwd"
        cwd.mkdir()
        folder = cwd / "justafolder"
        folder.mkdir()
        monkeypatch.chdir(cwd)

        proj = resolve_any_project(std, config, "justafolder", initialize=False)
        assert proj.project_path == folder.resolve()


# ---------------------------------------------------------------------------
# Designation kind decides the route (system-design § Detection & import)
# ---------------------------------------------------------------------------

class TestDesignationRoute:
    @pytest.mark.parametrize(
        ("value", "name_first", "route"),
        [
            (None, False, DesignationRoute.CWD),
            ("", True, DesignationRoute.CWD),
            ("absent-name", False, DesignationRoute.NAME),
            ("present", False, DesignationRoute.PATH),
            ("present", True, DesignationRoute.NAME),
            (".hidden", False, DesignationRoute.PATH),
            (".hidden", True, DesignationRoute.PATH),
            ("foo.", True, DesignationRoute.PATH),
            ("ws/proj", False, DesignationRoute.QUALIFIED),
            ("present/sub", False, DesignationRoute.PATH),
            (".", True, DesignationRoute.PATH),
            ("./gone", False, DesignationRoute.PATH),
            ("../x/y", False, DesignationRoute.PATH),
            ("a/b/c", False, DesignationRoute.PATH),
            ("ws/.gone", False, DesignationRoute.PATH),
            ("ws/", False, DesignationRoute.PATH),
            ("foo\0", False, DesignationRoute.INVALID),
        ],
    )
    def test_route(self, tmp_path, monkeypatch, value, name_first, route):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "present" / "sub").mkdir(parents=True)
        assert designation_route(value, name_first=name_first) is route

    def test_hidden_box_dir_resolves_as_a_path(self, std, config, tmp_home, monkeypatch):
        """``.hidden`` cannot be a box name, so it is the relative path, never a lookup."""
        monkeypatch.chdir(tmp_home)
        hidden = tmp_home / ".hidden"
        hidden.mkdir()
        resolve_project(std, config, project_dir=str(hidden), initialize=True,
                        name_override="hidden")

        proj = resolve_box_target(std, config, ".hidden")
        assert proj.project_path == hidden.resolve()

    def test_missing_hidden_path_fails_as_a_path(self, std, config, tmp_home, monkeypatch):
        """A missing PATH fails as a path; the registry is not consulted."""
        monkeypatch.chdir(tmp_home)
        with pytest.raises(ProjectError, match="does not exist") as excinfo:
            resolve_box_target(std, config, ".hidden", initialize=False)
        assert "Unknown project or workset" not in str(excinfo.value)

    def test_nul_designation_is_refused(self, std, config):
        with pytest.raises(ProjectError, match="neither a box name nor a path"):
            resolve_box_target(std, config, "foo\0bar")


# ---------------------------------------------------------------------------
# Per-kind namespaces: a box verb never loses a box name to a workset name
# ---------------------------------------------------------------------------

def _cli(argv, capsys):
    """``cli.main`` to completion; return ``(exit_code, stdout, stderr)``."""
    from kanibako import cli

    capsys.readouterr()
    try:
        cli.main(argv)
        code = 0
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


class TestBoxAndWorksetShareAName:
    """system-design § Cross-kind name semantics: box and workset names are PER-KIND
    namespaces, and noun-scoped commands consult only their own."""

    def _standalone_and_workset(self, std, tmp_home, capsys, name="foo"):
        sa_root = tmp_home / "sa" / name
        sa_root.mkdir(parents=True)
        (sa_root / "box_data").mkdir()
        (sa_root / "workset.yaml").write_text(
            "box:\n  image: ghcr.io/x:1\nworkset:\n  registry: null\n")
        registry_store.register_standalone(std.registry, name, sa_root)
        ws_root = tmp_home / "worksets" / name
        code, _out, err = _cli(["workset", "create", "--name", name, str(ws_root)], capsys)
        assert code == 0, err
        return sa_root, ws_root

    def test_a_box_verb_reaches_a_registered_standalone_a_workset_names_too(
        self, std, tmp_home, credentials_dir, capsys,
    ):
        sa_root, _ws_root = self._standalone_and_workset(std, tmp_home, capsys)
        code, out, err = _cli(["box", "info", "foo"], capsys)
        assert code == 0, err
        assert str(sa_root) in out, out

    def test_a_workset_verb_still_reaches_the_workset(
        self, std, tmp_home, credentials_dir, capsys,
    ):
        _sa_root, ws_root = self._standalone_and_workset(std, tmp_home, capsys)
        code, out, err = _cli(["workset", "info", "foo"], capsys)
        assert code == 0, err
        assert str(ws_root) in out, out

    def test_a_workset_name_alone_still_names_its_cure_at_a_box_verb(
        self, tmp_home, credentials_dir, capsys,
    ):
        code, _out, err = _cli(["workset", "create", "--name", "lone",
                                str(tmp_home / "worksets" / "lone")], capsys)
        assert code == 0, err
        code, _out, err = _cli(["box", "info", "lone"], capsys)
        assert code != 0
        assert "'lone' is a workset, not a single project box" in err, err


# ---------------------------------------------------------------------------
# A standalone box moved to a directory name with no ASCII spelling
# ---------------------------------------------------------------------------

class TestMovedToNonAsciiLeaf:
    def _moved(self, std, tmp_home):
        _, root = _make_standalone(std, tmp_home, leaf="movee")
        moved = root.rename(tmp_home / "東京")
        return root, moved

    def test_every_lookup_refuses_with_the_cure(self, std, config, tmp_home):
        from kanibako.commands.start import _resolve_existing_box

        _, moved = self._moved(std, tmp_home)
        registry_before = std.registry.read_bytes()
        for resolve in (lambda: resolve_box_target(std, config, str(moved)),
                        lambda: _resolve_existing_box(std, config, str(moved))):
            with pytest.raises(DerivedBoxNameError) as exc:
                resolve()
            assert "cannot spell in ASCII" in str(exc.value)
            assert "Rename the directory to an ASCII name (or move it back)." in str(exc.value)
        assert std.registry.read_bytes() == registry_before

    def test_moving_back_to_an_ascii_name_restores_the_box(self, std, config, tmp_home):
        _, moved = self._moved(std, tmp_home)
        back = moved.rename(tmp_home / "movee2")
        proj = resolve_box_target(std, config, str(back))
        assert proj.mode is BoxMode.standalone
        assert proj.name.endswith("_movee2")
