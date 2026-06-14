"""Direct unit tests for the lifecycle engine (commands/box/_lifecycle.py).

These exercise the shared transactional routine that backs the redesigned
remap / move / convert commands (Phase 1 — no CLI wiring yet).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.commands.box import _lifecycle as lc
from kanibako.commands.box._lifecycle import (
    BARE_INTO_WS,
    INPLACE,
    UNCHANGED,
    ProjectState,
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.config import load_config, read_project_meta
from kanibako.errors import ProjectError, WorksetError
from kanibako.names import read_names
from kanibako.paths import (
    ProjectMode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.workset import (
    add_project,
    create_workset,
    list_worksets,
    load_workset,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    """Loaded config + std + temp home."""
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std, tmp_home


def _make_default(env, name="proj", contents="hello"):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    resolve_project(std, config, project_dir=str(project_dir), initialize=True)
    return project_dir


def _make_standalone(env, name="sa", contents="hi"):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    resolve_standalone_project(
        std, config, project_dir=str(project_dir), initialize=True,
    )
    return project_dir


def _make_workset(env, ws_name="ws", root_name="ws_root"):
    config, std, tmp_home = env
    return create_workset(ws_name, tmp_home / root_name, std)


def _conf_yes():
    return lambda: True


# ---------------------------------------------------------------------------
# resolve_lifecycle_target
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_default(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.owner == "default"
        assert state.mode == ProjectMode.default
        assert state.workspace_path == pdir.resolve()
        assert state.metadata_path.is_dir()
        assert not state.is_external
        assert state.ws is None

    def test_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.owner == "standalone"
        assert state.mode == ProjectMode.standalone
        assert state.metadata_path == (pdir / ".kanibako").resolve() or state.metadata_path.is_dir()
        assert not state.is_external

    def test_workset_internal(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        # internal source: a path inside the workset workspaces dir.
        internal_src = ws.workspaces_dir / "wp"
        internal_src.mkdir(parents=True)
        add_project(ws, "wp", internal_src, std)
        state = resolve_lifecycle_target(str(internal_src), std, config)
        assert state.owner == "workset:ws"
        assert state.mode == ProjectMode.workset
        assert not state.is_external
        assert state.ws is not None and state.ws.name == "ws"

    def test_workset_external_connected(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "external_repo"
        external.mkdir()
        add_project(ws, "ext", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        assert state.owner == "workset:ws"
        assert state.is_external
        assert state.workspace_path == external.resolve()

    def test_bare_project_name(self, env):
        """A bare REGISTERED project name resolves (cwd is elsewhere)."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="namedproj")
        # cwd is tmp_path/project (set by tmp_home), not pdir; resolve by name.
        state = resolve_lifecycle_target("namedproj", std, config)
        assert state.owner == "default"
        assert state.workspace_path == pdir.resolve()

    def test_bare_workset_name_errors(self, env):
        """A bare workset name is rejected (a workset is not a single box)."""
        config, std, tmp_home = env
        _make_workset(env, ws_name="lcw", root_name="lcw_root")
        with pytest.raises(WorksetError, match="is a workset"):
            resolve_lifecycle_target("lcw", std, config)


# ---------------------------------------------------------------------------
# In-place convert each direction
# ---------------------------------------------------------------------------

class TestConvertInPlace:
    def test_default_to_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.standalone
        assert (pdir / ".kanibako" / "project.yaml").is_file()
        meta = read_project_meta(pdir / ".kanibako" / "project.yaml")
        assert meta["mode"] == "standalone"
        # default-mode name unregistered.
        assert str(pdir) not in read_names(std.data_path)["projects"].values()

    def test_standalone_to_default(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.default
        assert new.metadata_path.is_dir()
        assert new.metadata_path.parent == std.boxes
        meta = read_project_meta(new.metadata_path / "project.yaml")
        assert meta["mode"] == "default"
        # old in-tree metadata gone.
        assert not (pdir / ".kanibako").exists()
        # name registered.
        assert str(pdir) in read_names(std.data_path)["projects"].values()

    def test_default_to_workset_external(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.workset
        assert new.is_external  # in-place → workspace stays outside ws → external
        # workspace still where it was.
        assert pdir.is_dir() and (pdir / "file.txt").is_file()
        # workset registration + external markers.
        ws2 = load_workset(ws.root)
        assert any(p.name == "proj" for p in ws2.projects)
        meta = read_project_meta(ws.projects_dir / "proj" / "project.yaml")
        assert meta["mode"] == "workset"
        assert meta["workspace"] == str(pdir.resolve())
        # old default name unregistered.
        assert str(pdir) not in read_names(std.data_path)["projects"].values()

    def test_standalone_to_workset_external(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.workset
        assert new.is_external
        assert pdir.is_dir()
        assert not (pdir / ".kanibako").exists()

    def test_workset_to_default(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "ext_repo"
        external.mkdir()
        (external / "file.txt").write_text("data")
        add_project(ws, "ep", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.default
        # external dir preserved (NEVER deleted).
        assert external.is_dir() and (external / "file.txt").is_file()
        # workset registration removed.
        ws2 = load_workset(ws.root)
        assert not any(p.name == "ep" for p in ws2.projects)
        # connected.yaml cleared.
        from kanibako.workset import _load_connected
        assert str(external.resolve()) not in _load_connected(std)

    def test_workset_to_standalone(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "ext_sa"
        external.mkdir()
        add_project(ws, "es", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.standalone
        assert external.is_dir()
        assert (external / ".kanibako" / "project.yaml").is_file()


# ---------------------------------------------------------------------------
# workset -> workset re-root
# ---------------------------------------------------------------------------

class TestWorksetToWorkset:
    def test_external_reroot_inplace(self, env):
        """External ws->ws repoints redirect/override; no file move needed."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        create_workset("wsb", tmp_home / "wsb_root", std)
        external = tmp_home / "ext_reroot"
        external.mkdir()
        (external / "f.txt").write_text("x")
        add_project(ws_a, "p", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wsb"
        assert new.is_external
        assert external.is_dir()
        # wsa no longer owns it; wsb does.
        assert not any(p.name == "p" for p in load_workset(ws_a.root).projects)
        assert any(p.name == "p" for p in load_workset(tmp_home / "wsb_root").projects)
        # connected.yaml points at wsb now.
        from kanibako.workset import _load_connected
        entry = _load_connected(std)[str(external.resolve())]
        assert entry["workset"] == "wsb"

    def test_internal_inplace_refused(self, env):
        """Internal-workspace ws->ws in-place raises the stubborn error."""
        config, std, tmp_home = env
        ws_a = create_workset("ia", tmp_home / "ia_root", std)
        create_workset("ib", tmp_home / "ib_root", std)
        internal = ws_a.workspaces_dir / "wp"
        internal.mkdir(parents=True)
        add_project(ws_a, "wp", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        with pytest.raises(ProjectError, match="Stubbornly refusing"):
            execute_lifecycle(
                state, TargetSpec(location=INPLACE, ownership="ib"),
                std, config, confirm=_conf_yes(),
            )


# ---------------------------------------------------------------------------
# move within same owner
# ---------------------------------------------------------------------------

class TestMoveSameOwner:
    def test_move_default(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env, contents="movecontent")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "newhome"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.default
        assert dest.is_dir()
        assert (dest / "file.txt").read_text() == "movecontent"
        assert not pdir.exists()
        # records updated.
        meta = read_project_meta(new.metadata_path / "project.yaml")
        assert meta["workspace"] == str(dest.resolve())
        # hash recomputed.
        from kanibako.utils import project_hash
        assert meta["project_hash"] == project_hash(str(dest.resolve()))
        # names.yaml updated.
        assert str(dest) in read_names(std.data_path)["projects"].values()
        assert str(pdir) not in read_names(std.data_path)["projects"].values()


# ---------------------------------------------------------------------------
# move + convert combo equals convert + move (same end state)
# ---------------------------------------------------------------------------

class TestCombo:
    def test_move_and_convert_to_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env, contents="combo")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "combo_dest"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.standalone
        assert dest.is_dir()
        assert (dest / "file.txt").read_text() == "combo"
        assert (dest / ".kanibako" / "project.yaml").is_file()
        assert not pdir.exists()

    def test_bare_into_workset(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env, contents="bare")
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=BARE_INTO_WS, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == ProjectMode.workset
        assert not new.is_external  # landed inside ws
        landed = ws.workspaces_dir / "proj"
        assert landed.is_dir() and (landed / "file.txt").read_text() == "bare"
        assert not pdir.exists()
        # recorded workspace is the in-tree dir.
        meta = read_project_meta(ws.projects_dir / "proj" / "project.yaml")
        assert meta["workspace"] == str(landed.resolve())


# ---------------------------------------------------------------------------
# bare --move only valid with workset target
# ---------------------------------------------------------------------------

class TestValidation:
    def test_bare_into_ws_requires_workset(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="bare"):
            execute_lifecycle(
                state, TargetSpec(location=BARE_INTO_WS, ownership="standalone"),
                std, config, confirm=_conf_yes(),
            )

    def test_dest_occupied(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "occupied"
        dest.mkdir()
        with pytest.raises(ProjectError, match="already exists"):
            execute_lifecycle(
                state, TargetSpec(location=dest), std, config, confirm=_conf_yes(),
            )

    def test_membership_guard(self, env):
        """Refuse landing files inside a non-member workset."""
        config, std, tmp_home = env
        other = create_workset("other", tmp_home / "other_root", std)
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = other.workspaces_dir / "intruder"
        with pytest.raises(ProjectError, match="not .* a member"):
            execute_lifecycle(
                state, TargetSpec(location=dest, ownership=UNCHANGED),
                std, config, confirm=_conf_yes(),
            )

    def test_confirm_false_aborts(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="Aborted"):
            execute_lifecycle(
                state, TargetSpec(ownership="standalone"),
                std, config, confirm=lambda: False,
            )
        # nothing changed.
        assert read_project_meta(state.metadata_path / "project.yaml")["mode"] == "default"


# ---------------------------------------------------------------------------
# Failure injection -> unwind
# ---------------------------------------------------------------------------

class TestUnwind:
    def test_ownership_failure_restores_state(self, env, monkeypatch):
        """If the ownership step blows up mid-move, original state is restored."""
        config, std, tmp_home = env
        pdir = _make_default(env, contents="unwind")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "unwind_dest"

        names_before = dict(read_names(std.data_path)["projects"])
        meta_before = read_project_meta(state.metadata_path / "project.yaml")

        # Force the standalone ownership step to raise AFTER file move + name
        # work has begun.
        orig = lc._to_standalone

        def boom(*a, **kw):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(lc, "_to_standalone", boom)

        with pytest.raises(RuntimeError, match="injected"):
            execute_lifecycle(
                state, TargetSpec(location=dest, ownership="standalone"),
                std, config, confirm=_conf_yes(),
            )

        # Files restored: dest copy removed, original intact.
        assert not dest.exists()
        assert pdir.is_dir() and (pdir / "file.txt").read_text() == "unwind"
        # Names + metadata unchanged.
        assert dict(read_names(std.data_path)["projects"]) == names_before
        assert read_project_meta(state.metadata_path / "project.yaml") == meta_before

    def test_workset_failure_unwinds_registration(self, env, monkeypatch):
        """A failure after add_project unwinds the workset registration."""
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env, contents="wsunwind")
        state = resolve_lifecycle_target(str(pdir), std, config)

        # Patch write_project_meta inside _lifecycle to raise after add_project
        # + copytree have run.
        real_write = lc.write_project_meta
        calls = {"n": 0}

        def flaky_write(*a, **kw):
            calls["n"] += 1
            raise RuntimeError("write failed")

        monkeypatch.setattr(lc, "write_project_meta", flaky_write)

        with pytest.raises(RuntimeError, match="write failed"):
            execute_lifecycle(
                state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
            )

        # workset registration unwound.
        ws2 = load_workset(ws.root)
        assert not any(p.name == "proj" for p in ws2.projects)
        # original default project intact.
        assert str(pdir) in read_names(std.data_path)["projects"].values()
        assert read_project_meta(state.metadata_path / "project.yaml")["mode"] == "default"
