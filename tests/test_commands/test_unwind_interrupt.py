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
        assert unwind.failed == 1

    def test_a_partial_restore_counts_as_failed(self):
        """An action returning ``False`` restored only part; ``None`` is a success."""
        unwind = _Unwind()
        unwind.push(lambda: None)
        unwind.push(lambda: False)
        unwind.run()
        assert unwind.failed == 1


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


class TestUnwindTargetMemberHoldsInterrupt:
    def _member(self, std, tmp_home):
        import kanibako.project.workset as ws_mod

        ws = ws_mod.create_workset("ws2", tmp_home / "worksets" / "ws2", std)
        ws_mod.add_project(ws, "alpha", ws.workspaces_dir / "alpha")
        leaves = [ws.workspaces_dir / "alpha", ws.projects_dir / "alpha"]
        assert all(leaf.is_dir() for leaf in leaves)
        return ws, leaves

    def test_interrupted_release_still_removes_the_leaves(
        self, std, tmp_home, monkeypatch, capsys,
    ):
        import kanibako.commands.box._lifecycle as lc

        ws, leaves = self._member(std, tmp_home)
        monkeypatch.setattr(lc, "release_project", _interrupt)
        with pytest.raises(KeyboardInterrupt):
            lc._unwind_target_member(ws, "alpha", {})
        assert not any(leaf.exists() for leaf in leaves)
        assert "may not have dropped the record of 'alpha'" in capsys.readouterr().err

    def test_interrupted_leaf_removal_still_removes_the_rest(
        self, std, tmp_home, monkeypatch,
    ):
        import kanibako.commands.box._lifecycle as lc

        ws, leaves = self._member(std, tmp_home)
        real = lc.remove_path
        calls: list[object] = []

        def first_interrupted(path, *a, **k):
            calls.append(path)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real(path, *a, **k)

        monkeypatch.setattr(lc, "remove_path", first_interrupted)
        with pytest.raises(KeyboardInterrupt):
            lc._unwind_target_member(ws, "alpha", {})
        assert len(calls) >= 2
        assert not leaves[1].exists()


class TestUnwindTargetMemberVerdict:
    def test_an_undropped_record_returns_false(self, std, tmp_home, monkeypatch, capsys):
        import kanibako.commands.box._lifecycle as lc
        import kanibako.project.workset as ws_mod

        ws = ws_mod.create_workset("ws3", tmp_home / "worksets" / "ws3", std)
        ws_mod.add_project(ws, "alpha", ws.workspaces_dir / "alpha")

        def refuse(*_a, **_k):
            raise OSError("injected")

        monkeypatch.setattr(lc, "release_project", refuse)
        assert lc._unwind_target_member(ws, "alpha", {}) is False
        assert "could not drop the record of 'alpha'" in capsys.readouterr().err

    def test_a_dropped_record_returns_true(self, std, tmp_home):
        import kanibako.commands.box._lifecycle as lc
        import kanibako.project.workset as ws_mod

        ws = ws_mod.create_workset("ws3", tmp_home / "worksets" / "ws3", std)
        ws_mod.add_project(ws, "alpha", ws.workspaces_dir / "alpha")
        assert lc._unwind_target_member(ws, "alpha", {}) is True


class TestUndoConsolidateHoldsInterrupt:
    def test_interrupted_entry_still_moves_the_rest_back(self, tmp_path, monkeypatch, capsys):
        import kanibako.commands.box._lifecycle as lc

        src_dir, dest_dir = tmp_path / "moved-to", tmp_path / "workspace"
        src_dir.mkdir()
        names = ["a.txt", "b.txt", "c.txt"]
        for name in names:
            (src_dir / name).write_text(name)
        real = lc._move_entry
        calls: list[object] = []

        def first_interrupted(src, dst, *, root):
            calls.append(src)
            if len(calls) == 1:
                raise KeyboardInterrupt
            real(src, dst, root=root)

        monkeypatch.setattr(lc, "_move_entry", first_interrupted)
        with pytest.raises(KeyboardInterrupt):
            lc._undo_consolidate(src_dir, dest_dir, [src_dir / n for n in names], root=tmp_path)
        assert sorted(p.name for p in dest_dir.iterdir()) == ["b.txt", "c.txt"]
        err = capsys.readouterr().err
        assert f"a.txt did not go back to {dest_dir}; it is at {src_dir / 'a.txt'}" in err


class TestUndoConsolidateReportsAPartialRestore:
    def test_an_entry_left_behind_returns_false(self, tmp_path, monkeypatch):
        import kanibako.commands.box._lifecycle as lc

        src_dir, dest_dir = tmp_path / "moved-to", tmp_path / "workspace"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("a")

        def refuse(*_a, **_k):
            raise OSError("injected")

        monkeypatch.setattr(lc, "_move_entry", refuse)
        assert lc._undo_consolidate(src_dir, dest_dir, [src_dir / "a.txt"],
                                    root=tmp_path) is False

    def test_a_full_restore_returns_true(self, tmp_path):
        import kanibako.commands.box._lifecycle as lc

        src_dir, dest_dir = tmp_path / "moved-to", tmp_path / "workspace"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("a")
        assert lc._undo_consolidate(src_dir, dest_dir, [src_dir / "a.txt"],
                                    root=tmp_path) is True
        assert (dest_dir / "a.txt").read_text() == "a"


class TestRestoreRowsHoldInterrupt:
    def test_standalone_rows_all_restored_past_an_interrupt(
        self, std, tmp_home, monkeypatch, capsys,
    ):
        import kanibako.commands.box._lifecycle as lc
        from kanibako.project import registry_store

        for name in ("one", "two"):
            registry_store.register_standalone(std.registry, name, tmp_home / name)
        real = registry_store.unregister_standalone
        calls: list[str] = []

        def first_interrupted(registry, name):
            calls.append(name)
            if len(calls) == 1:
                raise KeyboardInterrupt
            real(registry, name)

        monkeypatch.setattr(registry_store, "unregister_standalone", first_interrupted)
        with pytest.raises(KeyboardInterrupt):
            lc._restore_standalone_rows(std, {})
        left = registry_store.load_standalone(std.registry)
        assert list(left) == [calls[0]]
        assert f"may not have restored the standalone registry row '{calls[0]}'" in (
            capsys.readouterr().err)
