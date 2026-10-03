"""``create`` inside a named working set's path space is refused.

Pinned contract: a box's path space is the named working set whose root contains
the create target, or the PRIMARY space when none does; ``create`` refuses to
make a primary or standalone box in a NAMED working set's space, names the
working set, and points at ``kanibako workset connect <workset> <path>``.  The
refusal happens before any door that writes, so it leaves no box dir, no
registry entry and no journal entry.  A create outside every named working set
— including inside the PRIMARY workset's own store — is untouched.
"""

from __future__ import annotations

import argparse
import shlex

import pytest

from kanibako.project.workset import create_workset, list_worksets, load_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_primary_boxes, load_std_paths

_ADVICE_MARKER = "kanibako workset connect "


def _args(path, **over):
    """A ``create`` argv; the flags the verb reads through ``getattr`` are defaulted."""
    ns = argparse.Namespace(
        path=None if path is None else str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
    )
    for key, value in over.items():
        setattr(ns, key, value)
    return ns


@pytest.fixture
def wsa(config_file, tmp_home):
    """A registered named working set named ``wsa``, plus its live ``std``."""
    std = load_std_paths(load_config(config_file))
    root = (tmp_home / "wsa").resolve()
    create_workset("wsa", root, std)
    return root, std


def _assert_nothing_written(std, *names):
    """No box dir, no PRIMARY registry entry, no journal entry for *names*."""
    from kanibako.launch import journal

    for name in names:
        assert not (std.boxes / name).exists(), f"box dir {name} was written"
    assert load_primary_boxes(std.primary_workset) == {}
    assert journal.read_journal(std.journal) == {}


def _printed_advice(err):
    """The shell command the refusal printed, as a list of words."""
    line = next(ln for ln in err.splitlines() if _ADVICE_MARKER in ln)
    return shlex.split(line[line.index(_ADVICE_MARKER):])


class TestCreateInNamedWorksetSpace:
    """Each path-space shape is refused, and the refusal writes nothing."""

    @pytest.mark.parametrize("rel", ["", "workspaces", "workspaces/x"])
    def test_refused_at_every_depth(self, wsa, capsys, rel):
        """The workset root, ``workspaces/`` and a ``workspaces/`` child are all in it."""
        root, std = wsa
        target = root / rel if rel else root
        target.mkdir(parents=True, exist_ok=True)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(target)) == 1
        err = capsys.readouterr().err
        assert "Refusing to create a box" in err
        # The refusal NAMES the working set and names the way out.
        assert "'wsa'" in err
        assert _ADVICE_MARKER in err
        _assert_nothing_written(std, target.name, "x", "workspaces")

    def test_refused_when_the_target_is_the_working_directory(
        self, wsa, capsys, monkeypatch,
    ):
        """An ABSENT box designation takes the cwd as the path — refused as well."""
        root, std = wsa
        monkeypatch.chdir(root)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(None)) == 1
        assert "Refusing to create a box" in capsys.readouterr().err
        _assert_nothing_written(std, "wsa")

    def test_standalone_is_refused_in_the_same_space(self, wsa, capsys):
        """A STANDALONE box is refused there too, not only a primary one."""
        root, std = wsa
        target = root / "workspaces" / "solo"
        target.mkdir(parents=True)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(target, standalone=True)) == 1
        assert "Refusing to create a box" in capsys.readouterr().err
        assert not (target / "box_data").exists()
        _assert_nothing_written(std, "solo")

    def test_refusal_does_not_suppress_the_existing_name_guard_elsewhere(
        self, config_file, tmp_home, capsys,
    ):
        """A name collision OUTSIDE any working set still reaches its own refusal."""
        std = load_std_paths(load_config(config_file))
        from kanibako.commands.box._parser import run_create

        target = tmp_home / "collide"
        assert run_create(_args(target, name="collide")) == 0
        capsys.readouterr()
        assert run_create(_args(target, name="collide")) == 1
        assert "already" in capsys.readouterr().err.lower()
        assert load_primary_boxes(std.primary_workset)


class TestPrintedAdviceRuns:
    """The command the refusal prints is the one that does what it promises."""

    def test_printed_advice_registers_the_project(self, wsa, capsys, credentials_dir):
        root, std = wsa
        target = root / "workspaces" / "adv"
        target.mkdir(parents=True)

        from kanibako.commands.box._parser import run_create
        from kanibako.commands.workset_cmd import run_connect

        assert run_create(_args(target)) == 1
        err = capsys.readouterr().err

        # Run the advice AS PRINTED: its own words, split as a shell would.
        argv = _printed_advice(err)
        assert argv[:4] == ["kanibako", "workset", "connect", "wsa"]
        assert argv[4] == str(target)

        assert run_connect(argparse.Namespace(
            workset=argv[3], source=argv[4], project_name=None, force=False,
        )) == 0
        assert "Added project 'adv'" in capsys.readouterr().out

        ws = load_workset(list_worksets(std)["wsa"], "wsa")
        assert [p.name for p in ws.projects] == ["adv"]
        # It joined the working set, so it is not a PRIMARY box.
        assert load_primary_boxes(std.primary_workset) == {}


class TestCreateOutsideANamedWorkset:
    """Controls: the refusal must not reach the primary path space."""

    def test_create_outside_any_workset_still_makes_a_primary_box(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        std = load_std_paths(load_config(config_file))
        from kanibako.commands.box._parser import run_create

        target = tmp_home / "plain"
        assert run_create(_args(target)) == 0
        assert "Created default project" in capsys.readouterr().out
        assert list(load_primary_boxes(std.primary_workset)) == ["plain"]
        assert (std.boxes / "plain").is_dir()

    def test_the_primary_workset_is_not_a_named_workset(self, tmp_home, wsa):
        """The PRIMARY workset is virtual and unregistered, so its tree is primary space.

        This is the decision the refusal rests on: a lookup that found the primary
        workset would refuse a create in the primary space itself.
        """
        root, std = wsa
        from kanibako.commands.box._parser import _named_workset_owning

        assert _named_workset_owning(std.primary_workset, std) is None
        assert _named_workset_owning(std.boxes / "any", std) is None
        assert _named_workset_owning(tmp_home, std) is None
        assert _named_workset_owning(root, std) == "wsa"
        assert _named_workset_owning(root / "workspaces" / "x", std) == "wsa"
