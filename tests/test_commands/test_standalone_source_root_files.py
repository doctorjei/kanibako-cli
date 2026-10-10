"""A standalone SOURCE's root files on ``box move`` / ``box convert``.

The root ``workset.yaml`` is stashed before the success tail runs, so everything read
through it (``workset.canon``, ``workset.channels.*``) is resolved while the source is
whole.  The ``canon`` tier is carried to a standalone destination and left, named, for
any other; kanibako's ``.gitignore`` line goes with the box.
"""

from __future__ import annotations

import pytest

from kanibako.commands.box import _lifecycle as lc
from kanibako.commands.box._lifecycle import (
    INPLACE, TargetSpec, execute_lifecycle, resolve_lifecycle_target,
)
from kanibako.project import registry_store
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import BoxMode, load_std_paths, resolve_standalone_project
from kanibako.utils import write_project_gitignore


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    return config, load_std_paths(config), tmp_home


def _standalone(env, leaf="sa"):
    config, std, tmp_home = env
    root = tmp_home / leaf
    root.mkdir()
    resolve_standalone_project(std, config, project_dir=str(root), initialize=True)
    write_project_gitignore(root)  # as ``create --standalone`` does
    return root


def _run(env, root, spec):
    config, std, _ = env
    return execute_lifecycle(resolve_lifecycle_target(str(root), std, config), spec, std,
                             config, confirm=lambda: True)


def _repoint(root, key, value):
    from kanibako.settings.config_io import write_nested_key
    from kanibako.settings.config_keys import _KEY_ROUTES

    sections, leaf = _KEY_ROUTES[key]
    write_nested_key(root / WORKSET_META_FILE, sections, leaf, str(value))


class TestAStandaloneMoveCarriesItsCanon:

    def test_the_canon_lands_at_the_new_root_and_leaves_the_old(self, env):
        root = _standalone(env)
        (root / "canon" / "handbook" / "MINE.md").write_text("my directives")
        new = _run(env, root, TargetSpec(location=env[2] / "moved", ownership="standalone"))
        assert new.mode is BoxMode.standalone
        assert (env[2] / "moved" / "canon" / "handbook" / "MINE.md").read_text() == "my directives"
        assert not (root / "canon").exists()
        assert not (root / ".gitignore").exists()

    def test_a_user_gitignore_keeps_its_own_lines(self, env):
        root = _standalone(env)
        gitignore = root / ".gitignore"
        gitignore.write_text("*.log\n" + gitignore.read_text())
        _run(env, root, TargetSpec(location=env[2] / "moved", ownership="standalone"))
        assert gitignore.read_text() == "*.log\n"

    def test_a_canon_outside_the_root_stays_and_is_named(self, env, capsys):
        root = _standalone(env)
        outside = env[2] / "my-canon"
        (outside / "handbook").mkdir(parents=True)
        _repoint(root, "workset.canon", outside)
        capsys.readouterr()
        _run(env, root, TargetSpec(location=env[2] / "moved", ownership="standalone"))
        assert (outside / "handbook").is_dir()
        assert f"left the canon folder at {outside}" in capsys.readouterr().err


class TestAConvertOutOfStandaloneDropsTheGitignoreAndNamesTheCanon:

    def test_an_in_place_convert_to_default(self, env, capsys):
        root = _standalone(env)
        capsys.readouterr()
        new = _run(env, root, TargetSpec(location=INPLACE, ownership="default"))
        assert new.mode is BoxMode.primary
        assert not (root / ".gitignore").exists()
        assert (root / "canon").is_dir()
        assert f"left the canon folder at {root / 'canon'}" in capsys.readouterr().err


class TestTheSourcePartitionIsReadThroughItsOwnKeys:

    def _seed(self, env, root):
        _config, std, tmp_home = env
        repointed = tmp_home / "sa-mail"
        _repoint(root, "workset.channels.mailboxes", repointed)
        state = resolve_lifecycle_target(str(root), std, env[0])
        (repointed / state.name).mkdir(parents=True)
        (repointed / state.name / "msg.txt").write_text("mail")
        return repointed / state.name

    def test_the_repointed_mailbox_is_the_one_that_moves(self, env):
        _config, std, _ = env
        root = _standalone(env)
        old = self._seed(env, root)
        new = _run(env, root, TargetSpec(location=INPLACE, ownership="default"))
        assert not old.exists()
        assert (std.channels_mailboxes / "__PRIMARY__" / new.name / "msg.txt").read_text() == "mail"

    def test_an_interrupted_tail_names_the_repointed_mailbox(self, env, monkeypatch, capsys):
        root = _standalone(env)
        old = self._seed(env, root)

        def boom(*_a, **_kw):
            raise KeyboardInterrupt()

        monkeypatch.setattr(lc, "_relocate_channel_partition", boom)
        capsys.readouterr()
        with pytest.raises(KeyboardInterrupt):
            _run(env, root, TargetSpec(location=INPLACE, ownership="default"))
        assert f"its channel mailbox is still at {old}" in capsys.readouterr().err


class TestALegacyNamedStandaloneMoveDropsItsRow:

    def test_the_old_row_goes(self, env):
        _config, std, tmp_home = env
        root = _standalone(env)
        composed = registry_store.standalone_name_for_root(std.registry, root)
        registry_store.unregister_standalone(std.registry, composed)
        registry_store.register_standalone(std.registry, "bad name", root)
        new = _run(env, root, TargetSpec(location=tmp_home / "moved", ownership="standalone"))
        assert registry_store.load_standalone(std.registry) == {
            new.name: str(tmp_home / "moved")}
