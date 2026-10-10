"""A second Ctrl-C inside the unwind must not skip the remaining compensating actions."""

from __future__ import annotations

import pytest

from kanibako.commands.box._lifecycle import _Unwind


def _record(ran: list[str], name: str, exc: BaseException | None = None):
    def action() -> None:
        ran.append(name)
        if exc is not None:
            raise exc
    return action


class TestRunHoldsInterrupt:
    def test_interrupted_action_does_not_skip_the_rest(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "first"))
        unwind.push(_record(ran, "middle", KeyboardInterrupt()))
        unwind.push(_record(ran, "last"))
        with pytest.raises(KeyboardInterrupt):
            unwind.run()
        assert ran == ["last", "middle", "first"]
        assert unwind.actions == []

    def test_first_interrupt_is_the_one_reraised(self):
        first, second = KeyboardInterrupt("first"), KeyboardInterrupt("second")
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "a", second))
        unwind.push(_record(ran, "b", first))
        with pytest.raises(KeyboardInterrupt) as info:
            unwind.run()
        assert info.value is first
        assert ran == ["b", "a"]

    def test_ordinary_failure_still_swallowed(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "a"))
        unwind.push(_record(ran, "b", OSError("boom")))
        unwind.run()
        assert ran == ["b", "a"]


class TestNoteInterruptedHoldsInterrupt:
    def test_interrupted_note_does_not_drop_the_rest(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.on_success(lambda: None, interrupted=_record(ran, "one"))
        unwind.on_success(
            lambda: None, interrupted=_record(ran, "two", KeyboardInterrupt()))
        unwind.on_success(lambda: None, interrupted=_record(ran, "three"))
        with pytest.raises(KeyboardInterrupt):
            unwind.note_interrupted()
        assert ran == ["one", "two", "three"]

    def test_only_unfinished_cleanups_are_named(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.on_success(
            lambda: None, interrupted=_record(ran, "done", KeyboardInterrupt()))
        unwind.on_success(lambda: None, interrupted=_record(ran, "pending"))
        unwind.finished = 1
        unwind.note_interrupted()
        assert ran == ["pending"]


class TestWorksetUnwindHoldsInterrupt:
    def test_workset_unwind_does_not_skip_the_rest(self):
        from kanibako.project.workset import _Unwind as WorksetUnwind

        ran: list[str] = []
        unwind = WorksetUnwind()
        unwind.push(_record(ran, "first"))
        unwind.push(_record(ran, "middle", KeyboardInterrupt()))
        unwind.push(_record(ran, "last"))
        with pytest.raises(KeyboardInterrupt):
            unwind.run()
        assert ran == ["last", "middle", "first"]


def _interrupt(*a, **k):
    raise KeyboardInterrupt


class TestWorksetFirstInterruptRollsBack:
    def test_create_workset_rolls_back(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        from kanibako.project.names import read_names

        root = tmp_home / "worksets" / "my-set"
        monkeypatch.setattr(ws_mod, "register_name", _interrupt)
        with pytest.raises(KeyboardInterrupt):
            ws_mod.create_workset("my-set", root, std)
        assert not root.exists()
        assert "my-set" not in read_names(std.registry).get("worksets", {})

    def test_add_project_rolls_back(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        import kanibako.settings.paths as paths_mod

        root = tmp_home / "worksets" / "my-set"
        ws = ws_mod.create_workset("my-set", root, std)
        monkeypatch.setattr(paths_mod, "_register_workset_box_membership", _interrupt)
        with pytest.raises(KeyboardInterrupt):
            ws_mod.add_project(ws, "proj", ws.workspaces_dir / "proj")
        resolved = root.resolve()
        for leaf in ("boxes", "workspaces", "vault/ro", "vault/rw"):
            assert not (resolved / leaf / "proj").exists(), leaf
        assert all(p.name != "proj" for p in ws.projects)

    def test_external_connect_rolls_back_link(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        import kanibako.settings.paths as paths_mod

        ws = ws_mod.create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "keep.txt").write_text("keep me")
        monkeypatch.setattr(paths_mod, "_register_workset_box_membership", _interrupt)
        with pytest.raises(KeyboardInterrupt):
            ws_mod.add_project(ws, "extproj", external, std)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.projects_dir / "extproj").exists()
        assert (external / "keep.txt").read_text() == "keep me"

    def test_interrupt_after_the_name_write_rolls_back(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        from kanibako.project.names import read_names

        real = ws_mod.register_name

        def write_then_interrupt(*a, **k):
            real(*a, **k)
            raise KeyboardInterrupt

        root = tmp_home / "worksets" / "my-set"
        monkeypatch.setattr(ws_mod, "register_name", write_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            ws_mod.create_workset("my-set", root, std)
        assert not root.exists()
        assert "my-set" not in read_names(std.registry).get("worksets", {})

    def test_failed_name_write_keeps_another_roots_entry(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        from kanibako.project.names import read_names, register_name

        other = str(tmp_home / "elsewhere")
        real = ws_mod.register_name

        def lose_the_race(registry, name, path, section="worksets"):
            register_name(registry, name, other, section=section)
            real(registry, name, path, section=section)

        monkeypatch.setattr(ws_mod, "register_name", lose_the_race)
        with pytest.raises(Exception, match="already registered"):
            ws_mod.create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        assert read_names(std.registry)["worksets"].get("my-set") == other


class _Boom(Exception):
    pass


class TestAddProjectRowUndo:
    def test_interrupt_after_the_row_write_leaves_no_row(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        import kanibako.settings.paths as paths_mod
        from kanibako.project import workset_registry

        ws = ws_mod.create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        real = paths_mod._register_workset_box_membership

        def write_then_interrupt(*a, **k):
            real(*a, **k)
            raise KeyboardInterrupt

        monkeypatch.setattr(paths_mod, "_register_workset_box_membership", write_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            ws_mod.add_project(ws, "proj", ws.workspaces_dir / "proj")
        assert "proj" not in workset_registry.load_workset_boxes(ws.registry_path)

    def test_failure_after_the_write_restores_a_prior_row(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod
        from kanibako.project import workset_registry

        ws = ws_mod.create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        workset_registry.register_workset_box(
            ws.registry_path, "proj", tmp_home / "prior-workspace")
        before = ws.registry_path.read_bytes()

        def boom(*a, **k):
            raise _Boom

        monkeypatch.setattr(ws_mod, "WorksetProject", boom)
        with pytest.raises(_Boom):
            ws_mod.add_project(ws, "proj", ws.workspaces_dir / "proj")
        assert ws.registry_path.read_bytes() == before
