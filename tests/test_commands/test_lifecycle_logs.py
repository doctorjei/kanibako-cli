"""Log-carry tests for the lifecycle engine (commands/box/_lifecycle.py).

A box's ``workset.logs`` files are named after the box, so every path that gives the
box a new name or a new ``workset.logs`` dir leaves them behind: the move reports
success and the logs stay readable only under the old name, where nothing writes
them any more.  These tests pin the carry beside the vault carry, on each
relocating path, and pin the rollback — a failed move must leave the logs exactly
where they were.
"""

from __future__ import annotations

import pytest

from kanibako.commands.box._lifecycle import (
    TargetSpec,
    _carry_box_logs,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.settings.config import load_config
from kanibako.channels.channels import WS_TOKEN_STANDALONE
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    box_log_files,
    box_logs_dir_for,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
    standalone_logs_dir,
)
from kanibako.settings.workset_dirkeys import EarlyScope
from kanibako.project.workset import add_project, create_workset
from tests.support.early import early_record


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    """Loaded config + std + temp home."""
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std, tmp_home


def _conf_yes():
    return lambda: True


def _gitproj(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("hi")
    return path


def _state_logs_dir(state, std):
    """The source box's resolved ``workset.logs`` dir, resolved as the carry does."""
    return box_logs_dir_for(
        std, state.mode, state.metadata_path,
        state.ws.root if state.ws is not None else None,
        workset_name=state.ws.name if state.ws is not None else None,
    )


def _seed_logs(logs_dir, box, text="seeded"):
    """Write both per-box log files named by :func:`box_log_files`; return their paths."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    for log_file in box_log_files(logs_dir, box):
        log_file.write_text(f"{text}\n")
    return box_log_files(logs_dir, box)


def _assert_both_present(logs_dir, box, text="seeded"):
    for log_file in box_log_files(logs_dir, box):
        assert log_file.read_text() == f"{text}\n", log_file


def _assert_neither_present(logs_dir, box):
    for log_file in box_log_files(logs_dir, box):
        assert not log_file.exists(), log_file


class TestLogCarry:
    def test_primary_rename_carries_logs(self, env):
        """A rename in place gives the box a new log name; the logs follow it."""
        config, std, tmp_home = env
        pdir = _gitproj(tmp_home / "proj")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        state = resolve_lifecycle_target(str(pdir), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name)

        new = execute_lifecycle(
            state, TargetSpec(name="renamed", location=tmp_home / "moved"),
            std, config, confirm=_conf_yes(),
        )

        _assert_both_present(src_logs, new.name)
        _assert_neither_present(src_logs, state.name)

    def test_move_across_worksets_carries_logs(self, env):
        """The destination is a different ``workset.logs`` dir as well as a new owner."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        leaf = ws_a.workspaces_dir / "b1"
        leaf.mkdir(parents=True)
        add_project(ws_a, "b1", leaf, std)
        state = resolve_lifecycle_target(str(leaf), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name)

        new = execute_lifecycle(
            state, TargetSpec(location=ws_b.workspaces_dir / "b1", ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )

        dst_logs = _state_logs_dir(new, std)
        assert dst_logs != src_logs
        _assert_both_present(dst_logs, new.name)
        _assert_neither_present(src_logs, state.name)

    def test_convert_to_primary_carries_logs(self, env):
        """A standalone box has its logs in its own ``box_data/``; the convert moves them."""
        config, std, tmp_home = env
        pdir = _gitproj(tmp_home / "sa")
        resolve_standalone_project(
            std, config, project_dir=str(pdir), initialize=True,
        )
        state = resolve_lifecycle_target(str(pdir), std, config)
        src_logs = standalone_logs_dir(
            state.metadata_path, early=_early_scope(std, BoxMode.standalone),
        )
        _seed_logs(src_logs, state.name)

        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )

        _assert_both_present(_state_logs_dir(new, std), new.name)
        _assert_neither_present(src_logs, state.name)

    def test_convert_to_standalone_carries_logs(self, env):
        """The other direction: the destination's logs dir is the new root's ``box_data/``."""
        config, std, tmp_home = env
        pdir = _gitproj(tmp_home / "p")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        state = resolve_lifecycle_target(str(pdir), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name)
        dest = tmp_home / "sa_dest"

        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )

        _assert_both_present(
            standalone_logs_dir(new.metadata_path, early=_early_scope(std, BoxMode.standalone)),
            new.name,
        )
        _assert_neither_present(src_logs, state.name)

    def test_rollback_leaves_logs_in_place(self, env, monkeypatch):
        """A move that fails after the carry restores the logs to the source."""
        config, std, tmp_home = env
        pdir = _gitproj(tmp_home / "proj")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        state = resolve_lifecycle_target(str(pdir), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name)
        dst_logs = std.primary_logs

        def _fail(*args, **kwargs):
            raise OSError("injected post-carry teardown failure")

        monkeypatch.setattr(
            "kanibako.commands.box._lifecycle._stash_source_marker", _fail,
        )
        with pytest.raises(OSError, match="injected post-carry"):
            execute_lifecycle(
                state, TargetSpec(name="rolled", location=tmp_home / "moved"),
                std, config, confirm=_conf_yes(),
            )

        # The source is whole: same files, same bytes, and nothing landed at the
        # destination name.
        _assert_both_present(src_logs, state.name)
        _assert_neither_present(dst_logs, "rolled")

    def test_rollback_across_worksets_leaves_logs_in_place(self, env, monkeypatch):
        """The ws→ws path carries through the same bracket, after the source release."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        leaf = ws_a.workspaces_dir / "b1"
        leaf.mkdir(parents=True)
        add_project(ws_a, "b1", leaf, std)
        state = resolve_lifecycle_target(str(leaf), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name)
        dst_logs = box_logs_dir_for(
            std, BoxMode.named, ws_b.projects_dir / "b2", ws_b.root, workset_name=ws_b.name,
        )
        assert dst_logs != src_logs

        # ⚑ The link repoint runs after the carry and inside the same unwind, so a failure
        # there is what a rolled-back ws→ws move looks like from here.
        def _fail(*args, **kwargs):
            raise OSError("injected post-carry repoint failure")

        monkeypatch.setattr(
            "kanibako.commands.box._lifecycle.repoint_box_mounted_links", _fail,
        )
        with pytest.raises(OSError, match="injected post-carry"):
            execute_lifecycle(
                state, TargetSpec(
                    location=ws_b.workspaces_dir / "b2", ownership="wsb", name="b2",
                ),
                std, config, confirm=_conf_yes(),
            )

        _assert_both_present(src_logs, state.name)
        _assert_neither_present(dst_logs, "b2")

    def test_move_onto_an_occupied_log_name_keeps_both(self, env, capsys):
        """The destination name already has a log; the carry leaves it and the source both."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        leaf = ws_a.workspaces_dir / "b1"
        leaf.mkdir(parents=True)
        add_project(ws_a, "b1", leaf, std)
        state = resolve_lifecycle_target(str(leaf), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name, text="source")
        dst_logs = box_logs_dir_for(
            std, BoxMode.named, ws_b.projects_dir / "b2", ws_b.root, workset_name=ws_b.name,
        )
        _seed_logs(dst_logs, "b2", text="resident")

        execute_lifecycle(
            state, TargetSpec(
                location=ws_b.workspaces_dir / "b2", ownership="wsb", name="b2",
            ),
            std, config, confirm=_conf_yes(),
        )

        _assert_both_present(dst_logs, "b2", text="resident")
        _assert_both_present(src_logs, state.name, text="source")

    def test_rollback_onto_an_occupied_log_name_keeps_both(self, env, monkeypatch):
        """A rolled-back move onto an occupied log name keeps the destination log."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
        leaf = ws_a.workspaces_dir / "b1"
        leaf.mkdir(parents=True)
        add_project(ws_a, "b1", leaf, std)
        state = resolve_lifecycle_target(str(leaf), std, config)
        src_logs = _state_logs_dir(state, std)
        _seed_logs(src_logs, state.name, text="source")
        dst_logs = box_logs_dir_for(
            std, BoxMode.named, ws_b.projects_dir / "b2", ws_b.root, workset_name=ws_b.name,
        )
        _seed_logs(dst_logs, "b2", text="resident")

        def _fail(*args, **kwargs):
            raise OSError("injected post-carry repoint failure")

        monkeypatch.setattr(
            "kanibako.commands.box._lifecycle.repoint_box_mounted_links", _fail,
        )
        with pytest.raises(OSError, match="injected post-carry"):
            execute_lifecycle(
                state, TargetSpec(
                    location=ws_b.workspaces_dir / "b2", ownership="wsb", name="b2",
                ),
                std, config, confirm=_conf_yes(),
            )

        _assert_both_present(dst_logs, "b2", text="resident")
        _assert_both_present(src_logs, state.name, text="source")

    def test_box_without_logs_moves(self, env):
        """No log files at all: the carry is hands-off and the move still completes."""
        config, std, tmp_home = env
        pdir = _gitproj(tmp_home / "proj")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        state = resolve_lifecycle_target(str(pdir), std, config)

        new = execute_lifecycle(
            state, TargetSpec(name="quiet", location=tmp_home / "moved"),
            std, config, confirm=_conf_yes(),
        )

        assert new.name == "quiet"


class _NullPrimaryLogsStd:
    """A ``StandardPaths`` stand-in whose PRIMARY ``workset.logs`` is a present ``<None>``."""

    primary_logs = None

    def __init__(self, tmp_path):
        self.early_system = early_record(tmp_path)


class TestCarryBoxLogs:
    """The carry's own guards — the cases the relocating paths above never reach."""

    def _standalone(self, tmp_path, name="b1"):
        """A standalone state: its ``workset.logs`` is ``<root>/box_data`` by default."""
        from kanibako.commands.box._lifecycle import ProjectState

        root = tmp_path / "root"
        return ProjectState(
            owner="standalone", mode=BoxMode.standalone, name=name,
            workspace_path=root / "ws", metadata_path=root,
            shell_path=root / "box_data" / "home",
            vault_ro=None, vault_rw=None,
        )

    def test_null_source_logs_dir_is_hands_off(self, tmp_path):
        """A present ``<None>`` ``workset.logs`` on the source holds nothing to carry."""
        from kanibako.commands.box._lifecycle import ProjectState, _Unwind

        state = ProjectState(
            owner="primary", mode=BoxMode.primary, name="b1",
            workspace_path=tmp_path / "ws", metadata_path=tmp_path / "meta",
            shell_path=tmp_path / "meta" / "home", vault_ro=None, vault_rw=None,
        )
        _carry_box_logs(
            state, _NullPrimaryLogsStd(tmp_path), _Unwind(),
            dst_logs=tmp_path / "dst", dst_name="b1",
        )
        assert not (tmp_path / "dst").exists()

    def test_null_destination_logs_dir_is_hands_off(self, tmp_path):
        """A present ``<None>`` ``workset.logs`` at the destination keeps the source logs."""
        from kanibako.commands.box._lifecycle import _Unwind

        state = self._standalone(tmp_path)
        std = _NullPrimaryLogsStd(tmp_path)
        src_logs = standalone_logs_dir(
            state.metadata_path, early=EarlyScope(std.early_system, WS_TOKEN_STANDALONE),
        )
        seeded = _seed_logs(src_logs, state.name)
        _carry_box_logs(
            state, std, _Unwind(), dst_logs=None, dst_name="b1",
        )
        for log_file in seeded:
            assert log_file.exists(), log_file

    def test_same_file_on_both_sides_is_a_noop(self, tmp_path):
        """Two worksets sharing one logs dir make the same path the common case."""
        from kanibako.commands.box._lifecycle import _Unwind

        state = self._standalone(tmp_path)
        std = _NullPrimaryLogsStd(tmp_path)
        src_logs = standalone_logs_dir(
            state.metadata_path, early=EarlyScope(std.early_system, WS_TOKEN_STANDALONE),
        )
        seeded = _seed_logs(src_logs, state.name)
        _carry_box_logs(
            state, std, _Unwind(),
            dst_logs=src_logs, dst_name=state.name,
        )
        for log_file in seeded:
            assert log_file.read_text() == "seeded\n", log_file

    def test_carried_move_is_undone_by_the_unwind(self, tmp_path):
        """The pushed action restores the file, which is what a rollback runs."""
        from kanibako.commands.box._lifecycle import _Unwind

        state = self._standalone(tmp_path)
        std = _NullPrimaryLogsStd(tmp_path)
        src_logs = standalone_logs_dir(
            state.metadata_path, early=EarlyScope(std.early_system, WS_TOKEN_STANDALONE),
        )
        dst_logs = tmp_path / "elsewhere"
        seeded = _seed_logs(src_logs, state.name)
        unwind = _Unwind()
        _carry_box_logs(
            state, std, unwind,
            dst_logs=dst_logs, dst_name="b2",
        )
        _assert_both_present(dst_logs, "b2")
        _assert_neither_present(src_logs, state.name)

        unwind.run()

        # The files are back; the guarantee-created destination dir is left empty.
        _assert_both_present(src_logs, state.name)
        assert list(dst_logs.iterdir()) == []
        assert seeded.helper.read_text() == "seeded\n"

    def test_occupied_destination_file_is_left_and_named(self, tmp_path, capsys):
        """One destination file already holds a log; it is left, and both paths are named."""
        from kanibako.commands.box._lifecycle import _Unwind

        state = self._standalone(tmp_path)
        std = _NullPrimaryLogsStd(tmp_path)
        src_logs = standalone_logs_dir(
            state.metadata_path, early=EarlyScope(std.early_system, WS_TOKEN_STANDALONE),
        )
        dst_logs = tmp_path / "elsewhere"
        _seed_logs(src_logs, state.name, text="source")
        dst = box_log_files(dst_logs, "b2")
        dst.helper.parent.mkdir(parents=True, exist_ok=True)
        dst.helper.write_text("resident\n")

        unwind = _Unwind()
        _carry_box_logs(
            state, std, unwind, dst_logs=dst_logs, dst_name="b2",
        )

        # The occupied file is untouched and its source is untouched; the free one moved.
        assert dst.helper.read_text() == "resident\n"
        assert box_log_files(src_logs, state.name).helper.read_text() == "source\n"
        assert dst.creds_watcher.read_text() == "source\n"
        err = capsys.readouterr().err
        assert str(dst.helper) in err
        assert str(box_log_files(src_logs, state.name).helper) in err

        unwind.run()

        # The undo restores the file it moved and touches nothing it did not.
        assert dst.creds_watcher.exists() is False
        assert dst.helper.read_text() == "resident\n"
        _assert_both_present(src_logs, state.name, text="source")
