"""A convert's root files: the canon tier INTO standalone, the ``.gitignore`` OUT of it,
and a finished session's ``.kanibako.lock``.
"""

from __future__ import annotations

import argparse
import fcntl

import pytest

from kanibako.commands.box import _lifecycle
from kanibako.commands.box._lifecycle import (
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
    run_convert,
    run_move,
)
from kanibako.errors import ProjectError
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project, resolve_standalone_project
from kanibako.utils import write_project_gitignore

_USER = b"node_modules/\n*.log\n"


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    return config, load_std_paths(config), tmp_home


def _standalone(env, name="sa"):
    config, std, tmp_home = env
    root = tmp_home / name
    root.mkdir()
    resolve_standalone_project(std, config, project_dir=str(root), initialize=True)
    return root


def _convert(root, **target):
    args = argparse.Namespace(old=str(root), force=True, to_default=False,
                              to_standalone=False, to_workset=None, move=None, name=None)
    for key, value in target.items():
        setattr(args, key, value)
    return run_convert(args)


def _execute(env, path, *, ownership):
    """Run the engine on *path* in place, so a refusal surfaces as its exception."""
    config, std, _ = env
    state = resolve_lifecycle_target(str(path), std, config)
    return execute_lifecycle(state, TargetSpec(ownership=ownership, verb="convert"), std, config,
                             force=True)


def _tree(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


class TestConvertIntoStandaloneStampsCanon:
    def test_a_primary_convert_stamps_what_create_stamps(self, env):
        config, std, tmp_home = env
        created = _standalone(env, "made")
        assert (created / "canon").is_dir()
        pdir = tmp_home / "proj"
        pdir.mkdir()
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        assert _convert(pdir, to_standalone=True) == 0
        assert _tree(pdir / "canon") == _tree(created / "canon")

    def test_a_failed_convert_takes_the_stamped_canon_back(self, env, monkeypatch):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        pdir.mkdir()
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        def boom(*_a, **_k):
            raise OSError("injected")
        monkeypatch.setattr(_lifecycle, "_copy_metadata", boom)
        assert _convert(pdir, to_standalone=True) != 0
        assert not (pdir / "canon").exists()


class TestConvertOutOfStandaloneGitignore:
    def _root(self, env, root_extra=b""):
        root = _standalone(env)
        write_project_gitignore(root)  # as ``box create --standalone`` does
        (root / "workspace" / ".gitignore").write_bytes(_USER)
        if root_extra:
            with open(root / ".gitignore", "ab") as f:
                f.write(root_extra)
        return root

    def test_the_users_file_wins_the_root(self, env):
        root = self._root(env)
        assert _convert(root, to_default=True) == 0
        assert (root / ".gitignore").read_bytes() == _USER
        assert not (root / "workspace").exists()

    def test_root_lines_beyond_kanibakos_are_kept(self, env):
        root = self._root(env, b"secret/\n")
        assert _convert(root, to_default=True) == 0
        assert (root / ".gitignore").read_bytes() == _USER + b"secret/\n"

    @pytest.mark.parametrize("root_extra", [b"", b"secret/\n"])
    def test_a_rollback_puts_both_files_back(self, env, monkeypatch, root_extra):
        root = self._root(env, root_extra)
        before = (root / ".gitignore").read_bytes()

        def boom(*_a, **_k):
            raise OSError("injected")
        monkeypatch.setattr(_lifecycle, "_apply_ownership_and_markers", boom)
        assert _convert(root, to_default=True) != 0
        assert (root / ".gitignore").read_bytes() == before
        assert (root / "workspace" / ".gitignore").read_bytes() == _USER


def _runtime_says(monkeypatch, running):
    """Answer the running-box check as a live runtime would."""
    detail = "running (kb-x: img)" if running else "not running (kb-x)"
    monkeypatch.setattr("kanibako.commands.box._parser._check_container_running",
                        lambda _proj, **_kw: (running, detail))


def _stub_runtime(monkeypatch, tmp_path, ps):
    """A runtime whose ``ps`` runs *ps* and whose ``inspect`` finds nothing."""
    stub = tmp_path / "runtime.sh"
    stub.write_text(f'#!/bin/sh\nif [ "$1" = ps ]; then {ps}; fi\nexit 1\n')
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))


def _move_args(old, new, force=False):
    return argparse.Namespace(old=str(old), new=str(new), force=force, to_default=False,
                              to_standalone=False, to_workset=None, name=None)


class TestFinishedSessionLock:
    def _locked(self, env, name):
        root = _standalone(env, name)
        lock = root / ".kanibako.lock"
        lock.write_text("box-container\n")
        return root, lock

    def test_an_unheld_lock_of_a_stopped_box_needs_no_force(self, env, monkeypatch):
        config, std, tmp_home = env
        root, _lock = self._locked(env, "sa")
        _runtime_says(monkeypatch, running=False)
        monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _msg: None)
        assert run_move(_move_args(root, tmp_home / "sa2")) == 0
        assert not root.exists()
        assert not (tmp_home / "sa2" / ".kanibako.lock").exists()

    def test_a_held_lock_still_refuses(self, env, capsys, monkeypatch):
        config, std, tmp_home = env
        root, lock = self._locked(env, "sa")
        _runtime_says(monkeypatch, running=False)
        with open(lock, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert run_move(_move_args(root, tmp_home / "sa2")) == 2
        assert "a kanibako session holds its lock file" in capsys.readouterr().err
        assert (root / "workset.yaml").is_file() and not (tmp_home / "sa2").exists()

    @pytest.mark.parametrize("lock", [False, True])
    def test_a_running_container_refuses_with_or_without_a_lock_file(
            self, env, capsys, monkeypatch, lock):
        config, std, tmp_home = env
        root = _standalone(env)
        if lock:
            (root / ".kanibako.lock").write_text("box-container\n")
        _runtime_says(monkeypatch, running=True)
        assert run_move(_move_args(root, tmp_home / "sa2")) == 2
        assert "its container is running (kb-x: img)" in capsys.readouterr().err
        assert (root / "workset.yaml").is_file() and not (tmp_home / "sa2").exists()

    def test_with_no_runtime_a_lock_file_refuses(self, env, capsys):
        # The conftest default answers "no runtime".
        config, std, tmp_home = env
        root, _lock = self._locked(env, "sa")
        assert run_move(_move_args(root, tmp_home / "sa2")) == 2
        assert "no container runtime is available" in capsys.readouterr().err
        assert (root / "workset.yaml").is_file()

    @pytest.mark.parametrize("ps", ["echo broken >&2; exit 125", "echo garbage"])
    def test_a_runtime_that_cannot_answer_counts_as_none(
            self, env, capsys, monkeypatch, tmp_path, ps):
        config, std, tmp_home = env
        root, lock = self._locked(env, "sa")
        _stub_runtime(monkeypatch, tmp_path, ps)
        assert run_move(_move_args(root, tmp_home / "sa2")) == 2
        assert "the container runtime could not tell" in capsys.readouterr().err
        assert (root / "workset.yaml").is_file() and not (tmp_home / "sa2").exists()
        lock.unlink()
        monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _msg: None)
        assert run_move(_move_args(root, tmp_home / "sa2")) == 0

    def test_box_info_still_reads_a_failing_runtime_as_not_running(
            self, env, monkeypatch, tmp_path):
        from kanibako.commands.box._parser import _check_container_running

        config, std, tmp_home = env
        root = _standalone(env)
        _stub_runtime(monkeypatch, tmp_path, "exit 125")
        proj = resolve_standalone_project(std, config, project_dir=str(root))
        running, detail = _check_container_running(proj)
        assert not running and detail.startswith("not running")

    def test_an_in_place_convert_out_drops_the_stale_lock(self, env):
        config, std, tmp_home = env
        root, lock = self._locked(env, "sa")
        assert _convert(root, to_default=True) == 0
        assert resolve_lifecycle_target(str(root), std, config).workspace_path == root
        assert not lock.exists()


class TestRootNameCollisions:
    @pytest.mark.parametrize("entry", ["workset.yaml", "box_data", "vault", "canon"])
    def test_a_convert_out_refuses_a_workspace_entry_named_like_a_root_one(self, env, entry):
        config, std, tmp_home = env
        root = _standalone(env)
        target = root / "workspace" / entry
        if "." in entry:
            target.write_text("mine: true\n")
        else:
            target.mkdir()
            (target / "mine.txt").write_text("mine")
        before = _tree(root)
        with pytest.raises(ProjectError, match=f"Refusing: {entry} in "):
            _execute(env, root, ownership="default")
        assert _tree(root) == before

    @pytest.mark.parametrize("held, refusal", [
        ("canon/mine.md", "Refusing: canon in "),
        ("box_data/mine.txt", "Refusing: box_data in "),
        # The existing vault-arm refusal owns these; pinned so the class stays covered.
        ("vault/ro/mine.txt", "already exists and this operation did not create it"),
        ("vault/rw/mine.txt", "already exists and this operation did not create it"),
    ])
    def test_a_convert_into_standalone_refuses_user_data_at_a_tier_path(
            self, env, held, refusal):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        (pdir / held).parent.mkdir(parents=True)
        (pdir / held).write_text("mine")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        before = _tree(pdir)
        with pytest.raises(ProjectError, match=refusal):
            _execute(env, pdir, ownership="standalone")
        assert _tree(pdir) == before

    def test_a_convert_into_standalone_refuses_a_name_its_workspace_already_holds(self, env):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        (pdir / "workspace" / "src").mkdir(parents=True)
        (pdir / "workspace" / "main.py").write_text("theirs")
        (pdir / "workspace" / "src" / "a.py").write_text("theirs")
        (pdir / "main.py").write_text("mine")
        (pdir / "src").mkdir()
        (pdir / "src" / "a.py").write_text("mine")
        (pdir / "notes.txt").write_text("mine")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        before = _tree(pdir)
        with pytest.raises(ProjectError, match=r"Refusing: main\.py, src in .*workspace"):
            _execute(env, pdir, ownership="standalone")
        assert _tree(pdir) == before

    def test_a_user_file_beside_the_vault_arms_is_not_refused(self, env):
        # Teardown keeps a skeleton holding anything but its arms, so nothing is adopted.
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        (pdir / "vault").mkdir(parents=True)
        (pdir / "vault" / "notes.txt").write_text("mine")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        _execute(env, pdir, ownership="standalone")
        assert (pdir / "vault" / "notes.txt").read_text() == "mine"


class TestDuplicateSharesThePreflight:
    def _args(self, src, dst, force=False):
        return argparse.Namespace(source_path=str(src), new_path=str(dst), bare=False,
                                  force=force, to_mode=None, workset=None,
                                  project_name=None, register=False)

    def _source(self, env):
        config, std, tmp_home = env
        src = tmp_home / "src"
        src.mkdir()
        proj = resolve_project(std, config, project_dir=str(src), initialize=True)
        (proj.metadata_path / ".kanibako.lock").write_text("box-container\n")
        return src

    def test_an_unheld_lock_of_a_stopped_box_does_not_refuse(self, env, monkeypatch):
        from kanibako.commands.box import run_duplicate

        config, std, tmp_home = env
        src = self._source(env)
        _runtime_says(monkeypatch, running=False)
        monkeypatch.setattr("kanibako.commands.box._duplicate.confirm_prompt", lambda _msg: None)
        assert run_duplicate(self._args(src, tmp_home / "dst")) == 0

    def test_a_running_container_refuses(self, env, capsys, monkeypatch):
        from kanibako.commands.box import run_duplicate

        config, std, tmp_home = env
        src = self._source(env)
        _runtime_says(monkeypatch, running=True)
        assert run_duplicate(self._args(src, tmp_home / "dst")) == 2
        assert "its container is running" in capsys.readouterr().err
