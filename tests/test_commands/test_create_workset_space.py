"""``create`` inside a named working set's path space is refused.

Pinned contract: a box's path space is the named working set whose root contains
the create target, or the PRIMARY space when none does.  ``create`` refuses a
target in a NAMED working set's space, names that working set, and states the
space's rule — a path-based, standalone or primary-working-set box is refused,
because the space makes only named boxes of that working set.

The refusal prints NO command.  The pinned invariant is that no printed line
promises an outcome the printed command does not produce, and it is met by
stronger means: the refusal carries no command line at all, so nothing in the
output can be pasted and believed.
"""

from __future__ import annotations

import argparse
import re

import pytest

from kanibako.project.workset import create_workset, list_worksets, load_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_primary_boxes, load_std_paths

#: A line that reads as a shell command the user could paste.
_COMMAND_LINE = re.compile(r"^\s*kanibako\s+\S")


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


def _printed_commands(err: str) -> list[str]:
    """Every line of *err* that reads as a shell command to paste."""
    return [ln.strip() for ln in err.splitlines() if _COMMAND_LINE.match(ln)]


def _refusal(err: str) -> str:
    """The refusal text, asserting it promises nothing runnable."""
    assert "Refusing to create a box" in err
    assert "'wsa'" in err
    # ⚑ THE CURE INVARIANT: a printed command is a promise that running it does what
    # the message says, so the refusal names NO command and no line of it can promise
    # an outcome.  ``workset connect`` is called out because it is the one command
    # that does NOT adopt an in-tree directory: for a source inside the tree it
    # opens a NEW, EMPTY project under ``workspaces/``.
    assert not _printed_commands(err), _printed_commands(err)
    assert "workset connect" not in err
    return err


class TestRefusedAtEveryPathKind:
    """Each path-space shape is refused, and the refusal writes nothing."""

    @pytest.mark.parametrize("rel", ["workspaces/x", "workspaces/x/sub", "", "workspaces", "notes"])
    def test_refused_at_every_depth(self, wsa, capsys, rel):
        """A member's root, a member's child, the working set root, ``workspaces/``
        and a sibling directory are all inside the same path space."""
        root, std = wsa
        target = root / rel if rel else root
        target.mkdir(parents=True, exist_ok=True)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(target)) == 1
        _refusal(capsys.readouterr().err)
        _assert_nothing_written(std, target.name, "x", "workspaces", "notes", "sub")

    def test_refused_when_the_target_is_the_working_directory(
        self, wsa, capsys, monkeypatch,
    ):
        """An ABSENT box designation takes the cwd as the path — refused as well."""
        root, std = wsa
        monkeypatch.chdir(root)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(None)) == 1
        _refusal(capsys.readouterr().err)
        _assert_nothing_written(std, "wsa")

    def test_a_path_outside_the_tree_is_refused_by_the_working_directory(
        self, wsa, tmp_home, capsys, monkeypatch,
    ):
        """The path space is the working set holding the CWD, so a PATH outside the
        tree is refused there too, and the refusal says the CWD is the reason."""
        root, std = wsa
        outside = tmp_home / "elsewhere"
        outside.mkdir()
        monkeypatch.chdir(root)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(outside)) == 1
        err = _refusal(capsys.readouterr().err)
        assert "the current directory is in the path space" in err
        _assert_nothing_written(std, "elsewhere")

    def test_standalone_is_refused_as_a_standalone_box(self, wsa, capsys):
        """``--standalone`` is refused as the STANDALONE box it would make."""
        root, std = wsa
        target = root / "workspaces" / "solo"
        target.mkdir(parents=True)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(target, standalone=True)) == 1
        err = _refusal(capsys.readouterr().err)
        assert "would be a STANDALONE box" in err
        assert "PRIMARY" not in err
        assert not (target / "box_data").exists()
        _assert_nothing_written(std, "solo")

    def test_a_primary_create_names_the_primary_box_it_would_make(self, wsa, capsys):
        """Without ``--standalone`` the refusal names the PRIMARY box."""
        root, std = wsa
        target = root / "workspaces" / "prim"
        target.mkdir(parents=True)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args(target)) == 1
        err = _refusal(capsys.readouterr().err)
        assert "would be a PRIMARY box" in err
        assert "STANDALONE box" not in err


class TestCreateOutsideANamedWorkset:
    """Controls: the refusal must not reach the primary path space."""

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

    def test_a_workset_member_is_untouched_by_the_refusal(self, wsa, capsys):
        """A member registered in the working set keeps its own recorded workspace."""
        from kanibako.project.workset import add_project

        root, std = wsa
        add_project(load_workset(root, "wsa"), "x", root / "workspaces" / "x", std)
        assert (root / "workspaces" / "x").is_dir()
        assert [p.name for p in load_workset(root, "wsa").projects] == ["x"]
        assert load_primary_boxes(std.primary_workset) == {}
        assert list_worksets(std)["wsa"] == root
        assert not _printed_commands(capsys.readouterr().err)


class TestAnIdentifierMakesANamedBoxOfTheSpace:
    """The one box the space accepts: a member NAME (system-design § Box designation
    & workset path space — "Within a named workset's path space, 'create' accepts only
    named boxes within that workset").  The space is the CWD's, at any depth.
    """

    @pytest.mark.parametrize("rel", ["", "workspaces", "workspaces/x"])
    def test_makes_a_member_of_the_working_set(self, wsa, capsys, monkeypatch, rel):
        root, std = wsa
        (root / rel).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(root / rel)

        from kanibako.commands.box._parser import run_create

        assert run_create(_args("newbox")) == 0
        capsys.readouterr()
        ws = load_workset(root, "wsa")
        assert [p.name for p in ws.projects] == ["newbox"]
        # The member's workspace is the in-tree one, never ``<cwd>/<identifier>``.
        assert (root / "workspaces" / "newbox").is_dir()
        assert load_primary_boxes(std.primary_workset) == {}

    def test_resolves_as_a_named_box(self, wsa, credentials_dir, monkeypatch):
        """A named resolve: the box dir is the working set's, under its own vault."""
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import (
            BoxMode, WorksetSpec, resolve_workset_project,
        )

        root, std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("newbox")) == 0
        proj = resolve_workset_project(
            WorksetSpec.from_workset(load_workset(root, "wsa")), "newbox", std,
            load_config(user_config_file()),
        )
        assert proj.mode is BoxMode.named
        assert proj.project_path == root / "workspaces" / "newbox"
        assert proj.metadata_path == root / "boxes" / "newbox"

    def test_a_second_create_of_the_same_name_is_refused(self, wsa, capsys, monkeypatch):
        """Conflict = REFUSE, and the refusal writes nothing."""
        root, std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("newbox")) == 0
        capsys.readouterr()
        assert run_create(_args("newbox")) == 1
        err = capsys.readouterr().err
        assert "already exists in working set 'wsa'" in err
        assert "workset connect" not in err
        assert not _printed_commands(err)
        assert [p.name for p in load_workset(root, "wsa").projects] == ["newbox"]
        assert load_primary_boxes(std.primary_workset) == {}
        from kanibako.launch import journal
        assert journal.read_journal(std.journal) == {}

    def test_a_case_variant_of_a_taken_name_is_refused(self, wsa, capsys, monkeypatch):
        """The membership key is folded, so a case variant would REPLACE the taken
        member's recorded workspace and strand its box — it refuses instead."""
        root, _std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("newbox")) == 0
        capsys.readouterr()
        assert run_create(_args("NEWBOX")) == 1
        err = capsys.readouterr().err
        assert "already exists in working set 'wsa'" in err
        assert not _printed_commands(err)
        # The first member keeps its own recorded workspace.
        assert [p.name for p in load_workset(root, "wsa").projects] == ["newbox"]
        assert (root / "workspaces" / "NEWBOX").exists() is False

    def test_a_second_name_for_one_box_is_refused(self, wsa, capsys, monkeypatch):
        """A named box's name IS its member name, so ``--name`` may not add one."""
        root, _std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("newbox", name="other")) == 1
        err = capsys.readouterr().err
        assert "two names for one box" in err
        assert not _printed_commands(err)
        assert list(load_workset(root, "wsa").projects) == []

    def test_recover_is_refused_before_the_membership_write(self, wsa, capsys, monkeypatch):
        """No member and no pending create, so ``--recover`` has nothing to resume —
        and the refusal runs before the membership write, so nothing is left."""
        root, std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("fresh", recover=True)) == 1
        err = capsys.readouterr().err
        assert "no interrupted 'create' of 'fresh' in working set 'wsa'" in err
        assert not _printed_commands(err)
        assert list(load_workset(root, "wsa").projects) == []
        assert not (root / "boxes" / "fresh").exists()
        assert not (root / "workspaces" / "fresh").exists()
        from kanibako.launch import journal
        assert journal.read_journal(std.journal) == {}

    def test_standalone_stays_refused_in_the_space(self, wsa, capsys, monkeypatch):
        """``--standalone`` asks for a standalone box, which the space rejects."""
        root, _std = wsa
        monkeypatch.chdir(root)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("solo", standalone=True)) == 1
        err = _refusal(capsys.readouterr().err)
        assert "would be a STANDALONE box" in err
        assert list(load_workset(root, "wsa").projects) == []

    def test_an_identifier_outside_every_space_stays_a_primary_box(
        self, wsa, tmp_home, capsys, credentials_dir, monkeypatch,
    ):
        """The control: the primary path space is untouched by the member route."""
        _root, std = wsa
        outside = tmp_home / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)
        from kanibako.commands.box._parser import run_create

        assert run_create(_args("plain")) == 0
        assert "Created default project" in capsys.readouterr().out
        assert list(load_primary_boxes(std.primary_workset)) == ["plain"]


def _tree(root):
    """Every path under *root* with its file bytes or symlink target."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = ("link", str(p.readlink()))
        elif p.is_file():
            out[rel] = ("file", p.read_bytes())
        else:
            out[rel] = ("dir", None)
    return out


class TestAnInterruptedNamedCreateIsRecoverable:
    """§ Detection & import: an interrupted create is completed by replay; a create
    on a pending entry refuses and names it; ``create --recover`` replays it."""

    def _crash(self, monkeypatch, name):
        from kanibako.commands.box._parser import run_create

        def boom(*_a, **_kw):
            raise KeyboardInterrupt

        with monkeypatch.context() as m:
            m.setattr("kanibako.commands.start.seed_new_box", boom)
            with pytest.raises(KeyboardInterrupt):
                run_create(_args(name))

    def test_crash_then_recover(self, wsa, capsys, credentials_dir, monkeypatch):
        from kanibako.commands.box._parser import run_create
        from kanibako.launch import journal

        root, std = wsa
        monkeypatch.chdir(root)
        self._crash(monkeypatch, "crashbox")
        assert [p.name for p in load_workset(root, "wsa").projects] == ["crashbox"]
        pending = journal.pending_create(std.journal, str(root / "boxes" / "crashbox"))
        assert pending is not None and pending["mode"] == "named"
        capsys.readouterr()

        # A plain create names the pending entry and prints the recover cure by NAME.
        assert run_create(_args("crashbox")) == 1
        err = capsys.readouterr().err
        assert "an interrupted 'create' is pending" in err
        assert "'crashbox' (named)" in err
        assert "kanibako create --recover crashbox" in err
        assert "already exists in working set" not in err

        # ``no_vault=False``: the flag as the CLI spells it absent (a given one is refused).
        assert run_create(_args("crashbox", recover=True, no_vault=False)) == 0
        assert "Resumed interrupted named project" in capsys.readouterr().out
        assert journal.read_journal(std.journal) == {}
        assert [p.name for p in load_workset(root, "wsa").projects] == ["crashbox"]
        assert (root / "boxes" / "crashbox" / "home").is_dir()
        assert load_primary_boxes(std.primary_workset) == {}

    def test_recover_on_a_complete_member_is_refused(self, wsa, capsys, monkeypatch, tmp_home):
        from kanibako.commands.box._parser import run_create

        root, _std = wsa
        monkeypatch.chdir(root)
        assert run_create(_args("newbox")) == 0
        capsys.readouterr()
        before = _tree(tmp_home)
        assert run_create(_args("newbox", recover=True, no_vault=False)) == 1
        err = capsys.readouterr().err
        assert "no interrupted 'create' of 'newbox' in working set 'wsa'" in err
        assert _tree(tmp_home) == before


class TestNoRefusalStrandsAMember:
    """Every refusal runs before the membership write: the working set's records,
    its ``boxes/`` and every other file under the test tree are unchanged."""

    @pytest.mark.parametrize("over", [
        {"agent": "nosuchpersona+claude"},
        {"agent": "Not A Ref!"},
        {"name": "other"},
        {"recover": True},
        {"standalone": True},
    ], ids=["no-such-persona", "bad-agent-ref", "two-names", "recover", "standalone"])
    def test_a_refused_new_member_leaves_nothing(self, wsa, tmp_home, monkeypatch, over):
        from kanibako.commands.box._parser import run_create
        from kanibako.errors import KanibakoError

        root, _std = wsa
        monkeypatch.chdir(root)
        before = _tree(tmp_home)
        try:
            rc = run_create(_args("pbox2", **over))
        except KanibakoError:
            rc = 1
        assert rc == 1
        assert _tree(tmp_home) == before
        assert list(load_workset(root, "wsa").projects) == []

    def test_a_stale_box_dir_is_refused_and_left_alone(self, wsa, tmp_home, capsys, monkeypatch):
        from kanibako.commands.box._parser import run_create

        root, _std = wsa
        (root / "boxes" / "stale").mkdir(parents=True)
        monkeypatch.chdir(root)
        before = _tree(tmp_home)
        assert run_create(_args("stale")) == 1
        err = capsys.readouterr().err
        # It names the box dir that is in the way, not the workspace.
        assert str(root / "boxes" / "stale") in err
        assert _tree(tmp_home) == before
        assert list(load_workset(root, "wsa").projects) == []

    @pytest.mark.parametrize("second", ["newbox", "NEWBOX"])
    def test_a_taken_name_leaves_nothing(self, wsa, tmp_home, capsys, monkeypatch, second):
        from kanibako.commands.box._parser import run_create

        root, _std = wsa
        monkeypatch.chdir(root)
        assert run_create(_args("newbox")) == 0
        before = _tree(tmp_home)
        assert run_create(_args(second)) == 1
        assert "already exists in working set 'wsa'" in capsys.readouterr().err
        assert _tree(tmp_home) == before
