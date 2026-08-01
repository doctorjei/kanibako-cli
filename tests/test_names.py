"""Tests for kanibako.names (global name registry I/O + resolution) and name wiring.

Since the global ``projects:`` section retired (clean split, 2026-07-08), the
:mod:`kanibako.names` module owns ONLY the ``worksets`` name section; default-mode
(PRIMARY) box names live in the primary per-workset ``boxes:`` membership, whose
name API (``pick``/``assign``/``register``/``unregister``/reverse-lookup) lives in
:mod:`kanibako.settings.paths` (tested here + in ``test_paths.py``).
"""

from __future__ import annotations

import argparse

import pytest
from pathlib import Path

from kanibako.errors import ProjectError
from kanibako.names import (
    lookup_by_path,
    read_names,
    register_name,
    resolve_name,
    resolve_qualified_name,
    unregister_name,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """Return the resolved ``config.registry`` file path under a temp tree.

    The names API is path-based: every function takes ``std.registry`` (the
    ``{data_path}/global/registry.yaml`` file), not the data root.
    """
    dp = tmp_path / "data"
    dp.mkdir()
    return dp / "global" / "registry.yaml"


def _register_primary_box(
    primary_workset: Path, name: str, workspace: Path | str,
) -> None:
    """Register a PRIMARY box (name → workspace) in the primary membership."""
    from kanibako import workset_registry

    reg = workset_registry.resolve_workset_registry_path(primary_workset, None)
    workset_registry.register_workset_box(reg, name, Path(workspace))


# ---------------------------------------------------------------------------
# read_names
# ---------------------------------------------------------------------------

class TestReadNames:
    def test_empty_when_no_file(self, registry: Path) -> None:
        result = read_names(registry)
        assert result == {"worksets": {}}

    def test_round_trip(self, registry: Path) -> None:
        register_name(registry, "client", "/home/user/ws/client", section="worksets")
        result = read_names(registry)
        assert result["worksets"] == {"client": "/home/user/ws/client"}


# ---------------------------------------------------------------------------
# register_name (worksets)
# ---------------------------------------------------------------------------

class TestRegisterName:
    def test_register_workset(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws/root", section="worksets")
        names = read_names(registry)
        assert names["worksets"]["ws1"] == "/ws/root"

    def test_default_section_is_worksets(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws/root")
        assert read_names(registry)["worksets"]["ws1"] == "/ws/root"

    def test_duplicate_name(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws/root", section="worksets")
        with pytest.raises(ProjectError, match="already registered"):
            register_name(registry, "ws1", "/other/path", section="worksets")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        reg = tmp_path / "a" / "b" / "c" / "global" / "registry.yaml"
        register_name(reg, "x", "/x", section="worksets")
        assert reg.is_file()


# ---------------------------------------------------------------------------
# unregister_name (worksets)
# ---------------------------------------------------------------------------

class TestUnregisterName:
    def test_unregister_existing(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws1", section="worksets")
        assert unregister_name(registry, "ws1", section="worksets") is True
        assert "ws1" not in read_names(registry)["worksets"]

    def test_unregister_nonexistent(self, registry: Path) -> None:
        assert unregister_name(registry, "nope", section="worksets") is False


# ---------------------------------------------------------------------------
# resolve_name
# ---------------------------------------------------------------------------

class TestResolveName:
    def test_resolve_primary_box_via_membership(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A bare primary-box name resolves via the primary membership (step 2)."""
        primary = tmp_path / "primary_workset"
        ws = tmp_path / "myapp"
        ws.mkdir()
        _register_primary_box(primary, "myapp", ws)

        path, kind = resolve_name(registry, "myapp", primary_workset=primary)
        assert path == str(ws)
        assert kind == "project"

    def test_primary_step_skipped_without_primary_workset(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """No *primary_workset* → the primary membership is not consulted."""
        primary = tmp_path / "primary_workset"
        _register_primary_box(primary, "myapp", tmp_path / "myapp")
        with pytest.raises(ProjectError, match="Unknown project"):
            resolve_name(registry, "myapp")

    def test_resolve_workset(self, registry: Path) -> None:
        register_name(registry, "ws1", "/home/user/ws", section="worksets")
        path, kind = resolve_name(registry, "ws1")
        assert path == "/home/user/ws"
        assert kind == "workset"

    def test_primary_takes_precedence_over_workset(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A primary box (step 2) is found before a workset (step 3)."""
        primary = tmp_path / "primary_workset"
        ws = tmp_path / "proj"
        ws.mkdir()
        _register_primary_box(primary, "proj", ws)
        register_name(registry, "proj", "/ws", section="worksets")

        path, kind = resolve_name(registry, "proj", primary_workset=primary)
        assert kind == "project"
        assert path == str(ws)

    def test_shadowed_bare_name_returns_box_and_warns(
        self, registry: Path, tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cross-kind shadow (per-kind name policy): a bare name that is BOTH a
        primary box and a workset resolves to the BOX (step 2 precedes step 3)
        and emits a ONE-LINE warning naming the shadowed workset."""
        primary = tmp_path / "primary_workset"
        ws = tmp_path / "proj"
        ws.mkdir()
        _register_primary_box(primary, "proj", ws)
        register_name(registry, "proj", "/ws", section="worksets")

        with caplog.at_level("WARNING"):
            path, kind = resolve_name(registry, "proj", primary_workset=primary)
        assert (path, kind) == (str(ws), "project")
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, warnings
        assert "proj" in warnings[0] and "workset" in warnings[0]

    def test_unshadowed_primary_resolve_does_not_warn(
        self, registry: Path, tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A primary box with NO same-named workset resolves silently (the warn
        fires ONLY on a live collision)."""
        primary = tmp_path / "primary_workset"
        ws = tmp_path / "solo"
        ws.mkdir()
        _register_primary_box(primary, "solo", ws)

        with caplog.at_level("WARNING"):
            resolve_name(registry, "solo", primary_workset=primary)
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    def test_unknown_name_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown project"):
            resolve_name(registry, "nope")

    def test_cwd_context_finds_workset_project(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """When cwd is inside a workset, check its workspace dirs first."""
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        path, kind = resolve_name(
            registry, "api", cwd=ws_root / "workspaces" / "api"
        )
        assert path == str(ws_root / "workspaces" / "api")
        assert kind == "project"

    def test_cwd_context_falls_through_to_primary(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """cwd inside a workset but name matches a primary box instead."""
        primary = tmp_path / "primary_workset"
        other = tmp_path / "other" / "path"
        other.mkdir(parents=True)
        _register_primary_box(primary, "other", other)

        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces").mkdir()
        register_name(registry, "myws", str(ws_root), section="worksets")

        path, kind = resolve_name(
            registry, "other", cwd=ws_root / "workspaces",
            primary_workset=primary,
        )
        assert path == str(other)
        assert kind == "project"

    # -- Workset-MEMBER box fallback (BUG-B) --------------------------------

    def _register_ws_member(
        self, registry: Path, tmp_path: Path, ws_name: str, box_name: str,
    ) -> Path:
        """Register a NAMED workset + a member box in its per-workset registry.

        Returns the box's workspace path.
        """
        from kanibako import workset_registry

        ws_root = tmp_path / ws_name
        box_ws = ws_root / "workspaces" / box_name
        box_ws.mkdir(parents=True)
        register_name(registry, ws_name, str(ws_root), section="worksets")
        reg_path = workset_registry.resolve_workset_registry_path(ws_root, None)
        workset_registry.register_workset_box(reg_path, box_name, box_ws)
        return box_ws

    def test_workset_member_resolves_from_outside(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A workset-MEMBER box name resolves from OUTSIDE its workset (BUG-B).

        Mutation proof: deleting the step-4 workset-membership fallback in
        ``resolve_name`` makes this raise ``ProjectError`` instead of resolving.
        """
        box_ws = self._register_ws_member(registry, tmp_path, "myws", "cluster2")

        # cwd is OUTSIDE the workset (tmp_path is the parent of the ws root).
        path, kind = resolve_name(registry, "cluster2", cwd=tmp_path)
        assert kind == "project"
        assert Path(path).resolve() == box_ws.resolve()

    def test_workset_member_ambiguous_across_worksets_raises(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A name that is a member of TWO worksets is ambiguous from outside."""
        self._register_ws_member(registry, tmp_path, "ws1", "dup")
        self._register_ws_member(registry, tmp_path, "ws2", "dup")

        with pytest.raises(ProjectError, match="Ambiguous"):
            resolve_name(registry, "dup", cwd=tmp_path)

    def test_workset_member_unknown_still_raises(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A bare name that is no member of any workset still raises."""
        self._register_ws_member(registry, tmp_path, "myws", "cluster2")
        with pytest.raises(ProjectError, match="Unknown"):
            resolve_name(registry, "not-a-member", cwd=tmp_path)


# ---------------------------------------------------------------------------
# resolve_qualified_name
# ---------------------------------------------------------------------------

class TestResolveQualifiedName:
    def test_resolve_qualified(self, registry: Path, tmp_path: Path) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        path, ws_name = resolve_qualified_name(registry, "myws/api")
        assert path == str(ws_root / "workspaces" / "api")
        assert ws_name == "myws"

    def test_unknown_workset_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown workset"):
            resolve_qualified_name(registry, "nope/api")

    def test_unknown_project_in_workset_raises(
        self, registry: Path, tmp_path: Path
    ) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        with pytest.raises(ProjectError, match="not found in workset"):
            resolve_qualified_name(registry, "myws/nope")

    def test_not_qualified_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Not a qualified name"):
            resolve_qualified_name(registry, "bare-name")


# ---------------------------------------------------------------------------
# PRIMARY-box name API (paths) — the membership-domain replacement for the
# retired projects-section name operations.
# ---------------------------------------------------------------------------

class TestPrimaryBoxNameApi:
    def test_assign_registers_membership(self, registry: Path, tmp_path: Path) -> None:
        from kanibako.settings.paths import assign_primary_box_name, load_primary_boxes

        primary = tmp_path / "primary_workset"
        ws = tmp_path / "projects" / "myapp"
        name = assign_primary_box_name(primary, registry, str(ws))
        assert name == "myapp"
        assert load_primary_boxes(primary)[name] == str(ws)

    def test_collision_numbering(self, registry: Path, tmp_path: Path) -> None:
        from kanibako.settings.paths import assign_primary_box_name

        primary = tmp_path / "primary_workset"
        assert assign_primary_box_name(primary, registry, "/a/myapp") == "myapp"
        assert assign_primary_box_name(primary, registry, "/b/myapp") == "myapp2"

    def test_cross_domain_collision_with_workset(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """A WORKSET name prevents using the same PRIMARY box name (new domain)."""
        from kanibako.settings.paths import assign_primary_box_name

        register_name(registry, "myapp", "/ws", section="worksets")
        primary = tmp_path / "primary_workset"
        assert assign_primary_box_name(primary, registry, "/proj/myapp") == "myapp2"

    def test_register_refuses_workset_name_collision_unless_forced(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """Cross-kind (per-kind name policy, Jei 2026-07-08): an EXPLICIT primary
        box name that collides with a WORKSET name refuses UNLESS ``force`` — and
        the refusal teaches ``--force``.  With ``force=True`` it registers."""
        from kanibako.settings.paths import load_primary_boxes, register_primary_box_name

        register_name(registry, "myapp", "/ws", section="worksets")
        primary = tmp_path / "primary_workset"
        with pytest.raises(ProjectError, match="workset"):
            register_primary_box_name(primary, registry, "myapp", "/proj/myapp")

        # --force bypasses the CROSS-KIND refusal → the box registers.
        register_primary_box_name(
            primary, registry, "myapp", "/proj/myapp", force=True,
        )
        assert load_primary_boxes(primary)["myapp"] == "/proj/myapp"

    def test_force_never_bypasses_same_kind_primary_collision(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """SAME-kind (two primary boxes, one name) is UNCONDITIONAL — ``force``
        never bypasses it."""
        from kanibako.settings.paths import register_primary_box_name

        primary = tmp_path / "primary_workset"
        register_primary_box_name(primary, registry, "myapp", "/a/myapp")
        with pytest.raises(ProjectError, match="already registered"):
            register_primary_box_name(
                primary, registry, "myapp", "/b/myapp", force=True,
            )

    def test_pick_skips_existing_box_dir(
        self, registry: Path, tmp_path: Path
    ) -> None:
        from kanibako.settings.paths import pick_primary_box_name

        primary = tmp_path / "primary_workset"
        boxes = tmp_path / "boxes"
        (boxes / "myapp").mkdir(parents=True)  # half-built box, unregistered.
        name = pick_primary_box_name(primary, registry, "/x/myapp", boxes_dir=boxes)
        assert name == "myapp2"

    def test_if_absent_noop_on_identical(
        self, registry: Path, tmp_path: Path
    ) -> None:
        from kanibako.settings.paths import (
            register_primary_box_name,
            register_primary_box_name_if_absent,
        )

        primary = tmp_path / "primary_workset"
        register_primary_box_name(primary, registry, "myapp", "/p/myapp")
        # Recovery re-entry: same name → same path is a silent no-op.
        register_primary_box_name_if_absent(primary, registry, "myapp", "/p/myapp")

    def test_if_absent_raises_on_different_path(
        self, registry: Path, tmp_path: Path
    ) -> None:
        from kanibako.settings.paths import (
            register_primary_box_name,
            register_primary_box_name_if_absent,
        )

        primary = tmp_path / "primary_workset"
        register_primary_box_name(primary, registry, "myapp", "/p/myapp")
        with pytest.raises(ProjectError):
            register_primary_box_name_if_absent(
                primary, registry, "myapp", "/OTHER/myapp",
            )

    def test_home_guard(self, registry: Path, tmp_path: Path, monkeypatch) -> None:
        from kanibako.settings.paths import register_primary_box_name

        home = tmp_path / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        primary = tmp_path / "primary_workset"
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_primary_box_name(primary, registry, "bad", str(home))

    def test_unregister(self, registry: Path, tmp_path: Path) -> None:
        from kanibako.settings.paths import (
            load_primary_boxes,
            register_primary_box_name,
            unregister_primary_box_name,
        )

        primary = tmp_path / "primary_workset"
        register_primary_box_name(primary, registry, "myapp", "/p/myapp")
        unregister_primary_box_name(primary, "myapp")
        assert "myapp" not in load_primary_boxes(primary)


# ---------------------------------------------------------------------------
# Name assignment wiring into project/workset creation
# ---------------------------------------------------------------------------

class TestLocalNameAssignment:
    """Name assignment is wired into default-mode project creation."""

    def test_new_project_gets_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.name == "project"

    def test_name_stored_in_membership_not_on_disk(self, config_file, tmp_home, credentials_dir):
        """P8b/Option A: the box name lives in the PRIMARY membership, NOT a
        self-describing on-disk ``project:`` section (no ``project:`` on disk)."""
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_primary_boxes, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.name == "project"
        assert "project" not in load_doc(proj.metadata_path / "settings.yaml")
        assert load_primary_boxes(std.primary_workset).get("project") == project_dir

    def test_name_registered_in_membership(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_primary_boxes, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        boxes = load_primary_boxes(std.primary_workset)
        assert "project" in boxes
        assert boxes["project"] == project_dir

    def test_name_collision_on_second_project(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create first project named "mydir"
        dir1 = tmp_home / "mydir"
        dir1.mkdir()
        proj1 = resolve_project(std, config, project_dir=str(dir1), initialize=True)
        assert proj1.name == "mydir"

        # Create second project with same basename in different location
        parent2 = tmp_home / "other"
        parent2.mkdir()
        dir2 = parent2 / "mydir"
        dir2.mkdir()
        proj2 = resolve_project(std, config, project_dir=str(dir2), initialize=True)
        assert proj2.name == "mydir2"

    def test_existing_project_preserves_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj1 = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj1.name == "project"

        # Re-resolve same project — name should persist, not re-assign.
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj2.name == "project"


class TestWorksetNameRegistration:
    """Workset creation registers the name in the registry worksets section."""

    def test_create_workset_registers_name(self, std, tmp_home):
        from kanibako.workset import create_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)

        names = read_names(std.registry)
        assert "myworkset" in names["worksets"]
        assert names["worksets"]["myworkset"] == str(root.resolve())

    def test_delete_workset_unregisters_name(self, std, tmp_home):
        from kanibako.workset import create_workset, delete_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)
        assert "myworkset" in read_names(std.registry)["worksets"]

        delete_workset("myworkset", std, remove_files=True)
        assert "myworkset" not in read_names(std.registry)["worksets"]


class TestNameRegistration:
    """Name uniqueness and unregister operations on primary boxes."""

    def test_register_and_read_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_primary_boxes, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Project should be auto-registered under its directory name
        assert "project" in load_primary_boxes(std.primary_workset)

    def test_unregister_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import (
            load_primary_boxes,
            load_std_paths,
            resolve_project,
            unregister_primary_box_name,
        )

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert "project" in load_primary_boxes(std.primary_workset)
        unregister_primary_box_name(std.primary_workset, "project")
        assert "project" not in load_primary_boxes(std.primary_workset)


class TestBoxListName:
    """box list shows NAME column."""

    def test_list_shows_name_column(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_list
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        output = capsys.readouterr().out
        assert "NAME" in output
        assert "project" in output


# ---------------------------------------------------------------------------
# $HOME guard in register_name (worksets)
# ---------------------------------------------------------------------------

class TestRegisterNameHomeGuard:
    def test_refuses_home_as_project_path(self, registry: Path, monkeypatch) -> None:
        home = registry.parent.parent.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_name(registry, "bad", str(home), section="worksets")

    def test_allows_subdirectory_of_home(self, registry: Path, monkeypatch) -> None:
        home = registry.parent.parent.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        subdir = home / "worksets" / "myws"
        subdir.mkdir(parents=True)
        register_name(registry, "myws", str(subdir), section="worksets")
        assert read_names(registry)["worksets"]["myws"] == str(subdir)


# ---------------------------------------------------------------------------
# lookup_by_path (worksets)
# ---------------------------------------------------------------------------

class TestLookupByPath:
    def test_finds_workset_by_path(self, registry: Path) -> None:
        register_name(registry, "ws1", "/home/user/ws", section="worksets")
        result = lookup_by_path(registry, "/home/user/ws")
        assert result == ("ws1", "worksets")

    def test_returns_none_for_unknown(self, registry: Path) -> None:
        assert lookup_by_path(registry, "/nope") is None

    def test_resolves_symlinks(self, registry: Path, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        register_name(registry, "ws1", str(real), section="worksets")
        result = lookup_by_path(registry, str(link))
        assert result == ("ws1", "worksets")


# ---------------------------------------------------------------------------
# box rm (was: box forget)
# ---------------------------------------------------------------------------

class TestBoxRm:
    def test_rm_by_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_primary_boxes, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target="project", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        assert "project" not in load_primary_boxes(std.primary_workset)

        out = capsys.readouterr().out
        assert "Removed 'project' from the registry" in out

    def test_rm_shows_purge_hint(self, config_file, tmp_home, credentials_dir, capsys):
        """Without --purge, rm deregisters and points at BOTH recovery paths."""
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target="project", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Deregistered 'project'" in out
        assert "metadata retained" in out
        # Points at BOTH recovery paths: register (restore) and rm --purge (delete).
        assert "box register project" in out
        assert "box rm project --purge" in out

    def test_rm_by_path(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_primary_boxes, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target=project_dir, purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        assert "project" not in load_primary_boxes(std.primary_workset)

    def test_rm_unknown_target(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm

        args = argparse.Namespace(target="nonexistent", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 1
        assert "not a registered" in capsys.readouterr().err

    def test_rm_purge_deletes_metadata(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        metadata_dir = proj.metadata_path
        assert metadata_dir.is_dir()

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0

        assert not metadata_dir.is_dir()
        assert "Removed metadata" in capsys.readouterr().out

    def test_rm_purge_removes_logs(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Create a fake per-box helper log (PRIMARY → primary_workset/logs/<box>.jsonl).
        std.primary_logs.mkdir(parents=True, exist_ok=True)
        log_file = std.primary_logs / "project.jsonl"
        log_file.write_text("test")

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0
        assert not log_file.exists()

    def test_rm_preserves_workspace(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Create a file in the workspace to verify it survives.
        (project_dir / "important.txt").write_text("keep me")

        args = argparse.Namespace(target="project", purge=True, force=True)
        run_rm(args)

        assert project_dir.is_dir()
        assert (project_dir / "important.txt").read_text() == "keep me"

    def test_rm_workset(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "ws_root"
        create_workset("myws", ws_root, std)
        assert "myws" in read_names(std.registry)["worksets"]

        args = argparse.Namespace(target="myws", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0
        assert "myws" not in read_names(std.registry)["worksets"]


# ---------------------------------------------------------------------------
# box lifecycle I1: deregistered section + purge-by-name (the reported bug)
# ---------------------------------------------------------------------------

class TestBoxDeregisterPurge:
    """`rm` (no --purge) parks a deregistered entry; `rm <name> --purge` then
    resolves it BY NAME — closing the orphaned-metadata dead-end.
    """

    def _make_primary(self, config_file, tmp_home):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        return std, proj

    def test_rm_parks_deregistered_entry(self, config_file, tmp_home, credentials_dir):
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm

        std, proj = self._make_primary(config_file, tmp_home)
        metadata_dir = proj.metadata_path

        run_rm(argparse.Namespace(target="project", purge=False, force=False))

        entry = registry_store.lookup_deregistered(std.registry, "project")
        assert entry is not None
        assert entry["kind"] == "primary"
        assert entry["metadata"] == str(metadata_dir)
        assert entry["workspace"] == str(tmp_home / "project")
        # Metadata is RETAINED (not deleted).
        assert metadata_dir.is_dir()

    def test_purge_by_name_after_deregister(self, config_file, tmp_home, credentials_dir, capsys):
        """THE REPORTED BUG: rm then `rm <name> --purge` succeeds by name."""
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm

        std, proj = self._make_primary(config_file, tmp_home)
        metadata_dir = proj.metadata_path

        # Deregister (the active membership is now gone).
        run_rm(argparse.Namespace(target="project", purge=False, force=False))
        capsys.readouterr()

        # Purge BY NAME — before I1 this errored "not a registered project".
        rc = run_rm(argparse.Namespace(target="project", purge=True, force=True))
        assert rc == 0
        assert not metadata_dir.is_dir()
        # The deregistered entry is dropped once its metadata is gone.
        assert registry_store.lookup_deregistered(std.registry, "project") is None

    def test_purge_by_name_idempotent_on_missing_dir(self, config_file, tmp_home, credentials_dir, capsys):
        """Entry present but dir already gone → drop entry, no error."""
        import shutil

        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm

        std, proj = self._make_primary(config_file, tmp_home)
        metadata_dir = proj.metadata_path

        run_rm(argparse.Namespace(target="project", purge=False, force=False))
        # Delete the metadata out-of-band, leaving a stale deregistered entry.
        shutil.rmtree(metadata_dir)
        assert registry_store.lookup_deregistered(std.registry, "project") is not None
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target="project", purge=True, force=True))
        assert rc == 0
        assert registry_store.lookup_deregistered(std.registry, "project") is None
        out = capsys.readouterr().out
        assert "stale entry" in out.lower() or "No metadata" in out

    def test_re_rm_without_purge_shows_guidance(self, config_file, tmp_home, credentials_dir, capsys):
        """A second `rm` (no --purge) on a deregistered box guides, never errors."""
        from kanibako.commands.box._parser import run_rm

        self._make_primary(config_file, tmp_home)
        run_rm(argparse.Namespace(target="project", purge=False, force=False))
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target="project", purge=False, force=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already deregistered" in out
        assert "box register project" in out
        assert "box rm project --purge" in out

    def test_purge_by_name_deletes_only_its_own_metadata(self, config_file, tmp_home, credentials_dir):
        """Mutation proof: purge deletes the box dir and NOTHING else beside it."""
        from kanibako.commands.box._parser import run_rm

        std, proj = self._make_primary(config_file, tmp_home)
        # A sibling box dir under std.boxes/ that must survive.
        sibling = std.boxes / "other_box"
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "keep.txt").write_text("keep me")
        # The user's workspace files must survive too.
        (tmp_home / "project" / "important.txt").write_text("workspace")

        run_rm(argparse.Namespace(target="project", purge=False, force=False))
        run_rm(argparse.Namespace(target="project", purge=True, force=True))

        assert not proj.metadata_path.is_dir()
        assert sibling.is_dir()
        assert (sibling / "keep.txt").read_text() == "keep me"
        assert (tmp_home / "project" / "important.txt").read_text() == "workspace"

    def test_purge_refuses_uncontained_metadata_path(self, config_file, tmp_home, credentials_dir, capsys):
        """CONTAINMENT: a crafted deregistered entry escaping std.boxes is REFUSED."""
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)

        # A sentinel directory OUTSIDE std.boxes/ that must never be deleted.
        victim = tmp_home / "victim"
        victim.mkdir()
        (victim / "precious.txt").write_text("do not delete")

        registry_store.register_deregistered(
            std.registry, "evil", kind="primary",
            workspace=str(tmp_home / "project"), metadata=str(victim),
        )

        rc = run_rm(argparse.Namespace(target="evil", purge=True, force=True))
        assert rc == 1
        # Nothing deleted; the entry is NOT dropped (a refusal, not a success).
        assert victim.is_dir()
        assert (victim / "precious.txt").read_text() == "do not delete"
        assert registry_store.lookup_deregistered(std.registry, "evil") is not None
        assert "refusing" in capsys.readouterr().err.lower()

    def test_purge_refuses_dotdot_escape(self, config_file, tmp_home, credentials_dir, capsys):
        """CONTAINMENT: a `..` escape resolves outside std.boxes → REFUSED."""
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)

        outside = std.boxes.parent / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "keep").write_text("x")
        crafted = str(std.boxes / ".." / "outside")

        registry_store.register_deregistered(
            std.registry, "sneaky", kind="primary",
            workspace=None, metadata=crafted,
        )
        rc = run_rm(argparse.Namespace(target="sneaky", purge=True, force=True))
        assert rc == 1
        assert outside.is_dir()
        assert (outside / "keep").exists()
        assert "refusing" in capsys.readouterr().err.lower()

    def test_active_purge_still_deletes_via_shared_helper(self, config_file, tmp_home, credentials_dir, capsys):
        """Non-regression: a direct `rm --purge` on an ACTIVE box still purges."""
        from kanibako.commands.box._parser import run_rm

        std, proj = self._make_primary(config_file, tmp_home)
        metadata_dir = proj.metadata_path
        std.primary_logs.mkdir(parents=True, exist_ok=True)
        log_file = std.primary_logs / "project.jsonl"
        log_file.write_text("x")
        # Sibling that must survive.
        sibling = std.boxes / "sib"
        sibling.mkdir(parents=True, exist_ok=True)

        rc = run_rm(argparse.Namespace(target="project", purge=True, force=True))
        assert rc == 0
        assert not metadata_dir.is_dir()
        assert not log_file.exists()
        assert sibling.is_dir()
        assert "Removed metadata" in capsys.readouterr().out


class TestStandaloneDeregisterPurge:
    def _make_standalone(self, config_file, tmp_home):
        from kanibako import registry_store
        from kanibako.settings.config import BOX_META_FILE, load_config
        from kanibako.settings.paths import _STANDALONE_META_DIR, load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa_root"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        (root / BOX_META_FILE).write_text("box:\n  image: ghcr.io/x:1\n")
        (root / "vault").mkdir()
        (root / "keep.txt").write_text("workspace file")
        registry_store.register_standalone(std.registry, "k_box", root)
        return std, root

    def test_standalone_deregister_then_purge_by_name(self, config_file, tmp_home, credentials_dir):
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_rm
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.paths import _STANDALONE_META_DIR

        std, root = self._make_standalone(config_file, tmp_home)

        # Deregister: registry.standalone entry dropped, in-tree metadata retained,
        # deregistered blob parked.
        rc = run_rm(argparse.Namespace(target="k_box", purge=False, force=False))
        assert rc == 0
        assert "k_box" not in registry_store.load_standalone(std.registry)
        entry = registry_store.lookup_deregistered(std.registry, "k_box")
        assert entry is not None and entry["kind"] == "standalone"
        assert entry["metadata"] == str(root)
        assert (root / _STANDALONE_META_DIR).is_dir()

        # Purge BY NAME: deletes only the in-tree artifacts, never the root/workspace.
        rc = run_rm(argparse.Namespace(target="k_box", purge=True, force=True))
        assert rc == 0
        assert not (root / _STANDALONE_META_DIR).is_dir()
        assert not (root / BOX_META_FILE).exists()
        assert not (root / "vault").exists()
        assert root.is_dir()
        assert (root / "keep.txt").read_text() == "workspace file"
        assert registry_store.lookup_deregistered(std.registry, "k_box") is None


# ---------------------------------------------------------------------------
# box lifecycle I4: purge-side stale-entry guard (belt-and-suspenders)
# ---------------------------------------------------------------------------

class TestPurgeStaleDeregisteredGuard:
    """The deregistered ``--purge`` REFUSES to delete a metadata path a NEW active
    box now occupies (a reused name), dropping the stale entry instead — never
    deleting the live box's home (box-lifecycle I4, belt-and-suspenders).

    Exercised by calling :func:`_purge_deregistered` directly: through ``run_rm``
    the active-first name resolution already routes a reused name to the ACTIVE
    purge, so this is the defensive layer for direct / future callers.
    """

    def test_primary_stale_entry_refused_active_home_intact(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako import registry_store
        from kanibako.commands.box._parser import _purge_deregistered
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import (
            load_primary_boxes,
            load_std_paths,
            register_primary_box_name,
        )

        config = load_config(config_file)
        std = load_std_paths(config)

        # An ACTIVE primary box "dup" whose home is std.boxes/dup.
        ws = tmp_home / "dupws"
        ws.mkdir()
        home = std.boxes / "dup"
        (home / "home").mkdir(parents=True, exist_ok=True)
        (home / "home" / "LIVE.txt").write_text("live-box-data")
        register_primary_box_name(
            std.primary_workset, std.registry, "dup", str(ws),
        )
        assert "dup" in load_primary_boxes(std.primary_workset)

        # A STALE deregistered entry pointing at the SAME home path.
        registry_store.register_deregistered(
            std.registry, "dup", kind="primary",
            workspace=str(ws), metadata=str(home),
        )

        rc = _purge_deregistered(
            std, "dup",
            registry_store.lookup_deregistered(std.registry, "dup"),
            argparse.Namespace(purge=True, force=True),
        )
        assert rc == 1
        # The live box's home is INTACT; the stale entry was dropped.
        assert (home / "home" / "LIVE.txt").read_text() == "live-box-data"
        assert registry_store.lookup_deregistered(std.registry, "dup") is None
        assert "stale" in capsys.readouterr().err.lower()

    def test_standalone_stale_entry_refused_active_root_intact(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako import registry_store
        from kanibako.commands.box._parser import _purge_deregistered
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import _STANDALONE_META_DIR, load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)

        root = tmp_home / "sabox"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        (root / _STANDALONE_META_DIR / "KEEP.txt").write_text("standalone-live")
        # ACTIVE standalone box registered at this root.
        registry_store.register_standalone(std.registry, "sabox", root)
        # STALE deregistered entry for the same root.
        registry_store.register_deregistered(
            std.registry, "sabox", kind="standalone",
            workspace=str(root), metadata=str(root),
        )

        rc = _purge_deregistered(
            std, "sabox",
            registry_store.lookup_deregistered(std.registry, "sabox"),
            argparse.Namespace(purge=True, force=True),
        )
        assert rc == 1
        assert (root / _STANDALONE_META_DIR / "KEEP.txt").read_text() == "standalone-live"
        assert registry_store.lookup_deregistered(std.registry, "sabox") is None
        assert "stale" in capsys.readouterr().err.lower()
