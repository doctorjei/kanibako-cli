"""A convert's root files: the canon tier INTO standalone, the ``.gitignore`` OUT of it,
and a finished session's ``.kanibako.lock``.
"""

from __future__ import annotations

import argparse
import fcntl

import pytest

from kanibako.commands.box import _lifecycle
from kanibako.commands.box._lifecycle import resolve_lifecycle_target, run_convert, run_move
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

    def test_a_canon_dir_already_at_the_root_is_not_written_into(self, env):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        (pdir / "canon").mkdir(parents=True)
        (pdir / "canon" / "mine.md").write_text("mine")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        assert _convert(pdir, to_standalone=True) == 0
        assert _tree(pdir / "canon") == ["mine.md"]

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


class TestFinishedSessionLock:
    def _locked(self, env, name):
        root = _standalone(env, name)
        lock = root / ".kanibako.lock"
        lock.write_text("box-container\n")
        return root, lock

    def test_an_unheld_lock_needs_no_force_and_goes_with_the_root(self, env, monkeypatch):
        config, std, tmp_home = env
        root, _lock = self._locked(env, "sa")
        monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _msg: None)
        args = argparse.Namespace(old=str(root), new=str(tmp_home / "sa2"), force=False,
                                  to_default=False, to_standalone=False, to_workset=None,
                                  name=None)
        assert run_move(args) == 0
        assert not root.exists()
        assert not (tmp_home / "sa2" / ".kanibako.lock").exists()

    def test_a_held_lock_still_refuses(self, env, capsys):
        config, std, tmp_home = env
        root, lock = self._locked(env, "sa")
        args = argparse.Namespace(old=str(root), new=str(tmp_home / "sa2"), force=False,
                                  to_default=False, to_standalone=False, to_workset=None,
                                  name=None)
        with open(lock, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert run_move(args) == 2
        assert "lock file found" in capsys.readouterr().err
        assert (root / "workset.yaml").is_file() and not (tmp_home / "sa2").exists()

    def test_an_in_place_convert_out_drops_the_stale_lock(self, env):
        config, std, tmp_home = env
        root, lock = self._locked(env, "sa")
        assert _convert(root, to_default=True) == 0
        assert resolve_lifecycle_target(str(root), std, config).workspace_path == root
        assert not lock.exists()
