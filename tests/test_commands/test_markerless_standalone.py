"""A registered standalone box whose ``workset.yaml`` is gone (D4 state 3, plan S1c).

``duplicate``, ``move``, ``convert``, and ``remap`` refuse it with the launch's own
message; ``rm`` (bare or ``--purge``) and ``info`` still act on it (cleanup and diagnosis).
"""

from __future__ import annotations

import pytest

from kanibako.project import registry_store
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths


def _cli(*argv: str) -> int:
    from kanibako.cli import main

    with pytest.raises(SystemExit) as info:
        main(list(argv))
    return int(info.value.code or 0)


@pytest.fixture
def unmarked(config_file, tmp_home, credentials_dir, capsys):
    """A registered standalone box at ``tmp_home/p/solo`` with its ``workset.yaml`` deleted."""
    root = (tmp_home / "p" / "solo").resolve()
    root.mkdir(parents=True)
    assert _cli("create", "--standalone", "--register", str(root)) == 0
    (root / "workset.yaml").unlink()
    std = load_std_paths(load_config(config_file))
    name = registry_store.standalone_name_for_root(std.registry, root)
    assert name
    capsys.readouterr()
    return root, name, std


def _refusal(root, name) -> str:
    return (
        f"Error: box '{name}' is registered as a standalone box at {root}, but "
        f"{root / 'workset.yaml'} is missing, so kanibako does not see a box there.\n"
        "  Restore it: create that file with the line 'registry: null' under 'workset:'. "
        "Or unregister it, keeping its files:\n"
        f"  kanibako box rm {name}\n"
    )


@pytest.mark.parametrize("verb", [
    ("box", "duplicate", "--force", "{root}", "{dst}"),
    ("box", "duplicate", "--force", "--to", "primary", "{root}", "{dst}"),
    ("box", "move", "--force", "{root}", "{dst}"),
    ("box", "convert", "--force", "--default", "{root}"),
    ("box", "remap", "--force", "{root}", "{dst}"),
], ids=["duplicate", "duplicate-to-primary", "move", "convert", "remap"])
def test_a_verb_that_acts_on_the_box_refuses_it(unmarked, tmp_home, capsys, verb):
    root, name, std = unmarked
    dst = tmp_home / "p" / "dst"
    before = registry_store.load_standalone(std.registry)

    argv = [a.format(root=root, dst=dst) for a in verb]
    assert _cli(*argv) == 1
    assert capsys.readouterr().err == _refusal(root, name)
    assert not dst.exists()
    assert registry_store.load_standalone(std.registry) == before
    assert (root / "box_data").is_dir()


def test_rm_purge_still_cleans_it_up(unmarked, capsys):
    root, name, std = unmarked

    assert _cli("box", "rm", "--purge", "--force", str(root)) == 0, capsys.readouterr().err
    assert name not in registry_store.load_standalone(std.registry)
    assert not (root / "box_data").exists()


def test_info_still_reports_it(unmarked, capsys):
    root, _name, _std = unmarked

    assert _cli("box", "info", str(root)) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "Mode:         standalone" in out
    assert f"Metadata:     {root}\n" in out


def test_duplicate_into_a_workset_refuses_it(unmarked, tmp_home, capsys):
    root, name, _std = unmarked
    assert _cli("workset", "create", "--name", "ws", str(tmp_home / "ws")) == 0
    capsys.readouterr()

    assert _cli("box", "duplicate", "--force", "--to", "named", "--workset", "ws",
                str(root), str(tmp_home / "p" / "dst")) == 1
    assert capsys.readouterr().err == _refusal(root, name)
    assert not (tmp_home / "ws" / "workspaces" / "dst").exists()


def test_move_onto_a_registered_box_still_refuses_it(unmarked, tmp_home, capsys):
    """The refusal is not mistaken for a finished move when *new* is already a box."""
    root, name, std = unmarked
    other = (tmp_home / "p" / "other").resolve()
    other.mkdir()
    assert _cli("create", "--standalone", "--register", str(other)) == 0
    capsys.readouterr()
    before = registry_store.load_standalone(std.registry)

    assert _cli("box", "move", "--force", str(root), str(other)) == 1
    assert capsys.readouterr().err == _refusal(root, name)
    assert registry_store.load_standalone(std.registry) == before
    assert (root / "box_data").is_dir()


def test_the_printed_rm_cure_unregisters_it_and_keeps_its_files(unmarked, capsys):
    """The refusal's second cure, run as printed: the box leaves the registry, its files stay."""
    import shlex

    root, name, std = unmarked
    assert _cli("box", "move", "--force", str(root), str(root.parent / "dst")) == 1
    cure = capsys.readouterr().err.splitlines()[-1]
    argv = shlex.split(cure)
    assert argv[:3] == ["kanibako", "box", "rm"]

    assert _cli(*argv[1:]) == 0, capsys.readouterr().err
    assert name not in registry_store.load_standalone(std.registry)
    assert (root / "box_data").is_dir()
    assert (root / "vault").is_dir()


def _printed_cures(text: str) -> "list[list[str]]":
    """Every ``kanibako ...`` command line in *text*, split as a shell would."""
    import shlex

    return [shlex.split(line) for line in text.splitlines()
            if line.startswith("  kanibako ")]


_RESTORE = "Restore the box (first create that file with the line 'registry: null' under 'workset:') or delete it"


@pytest.mark.parametrize("route", ["restore", "delete"])
@pytest.mark.parametrize("second_rm", [False, True], ids=["at-rm", "re-rm-of-parked"])
def test_a_plain_rm_names_the_marker_restore_and_each_route_works(
        unmarked, capsys, second_rm, route):
    """``register`` restores a box with no marker once the marker is back, so the advice
    names that step first; the purge stays the delete route."""
    root, name, std = unmarked
    assert _cli("box", "rm", name) == 0
    if second_rm:
        capsys.readouterr()
        assert _cli("box", "rm", name) == 0
    out = capsys.readouterr().out

    assert f"{root / 'workset.yaml'} is missing. {_RESTORE}:" in out
    assert "cannot be restored" not in out
    register, purge = _printed_cures(out)
    assert register == ["kanibako", "box", "register", name]
    assert purge == ["kanibako", "box", "rm", name, "--purge"]
    if route == "restore":
        (root / "workset.yaml").write_text("workset:\n  registry: null\n")
        assert _cli(*register[1:]) == 0, capsys.readouterr().err
        assert registry_store.standalone_root(std.registry, name) == str(root)
        assert (root / "box_data").is_dir()
    else:
        assert _cli(*purge[1:], "--force") == 0, capsys.readouterr().err
        assert not (root / "box_data").exists()
    assert registry_store.lookup_deregistered(std.registry, name) is None


def test_the_create_refusal_names_the_marker_restore(unmarked, capsys):
    from kanibako.commands.box._parser import _assert_primary_home_free_for_create
    from kanibako.errors import ProjectError

    root, name, std = unmarked
    assert _cli("box", "rm", name) == 0
    with pytest.raises(ProjectError) as raised:
        _assert_primary_home_free_for_create(std, name)

    assert f"{root / 'workset.yaml'} is missing. {_RESTORE}, then retry:" in str(raised.value)
    assert _printed_cures(str(raised.value)) == [
        ["kanibako", "box", "register", name], ["kanibako", "box", "rm", name, "--purge"]]


def test_a_marker_that_lost_its_line_is_named_as_such(unmarked, capsys):
    """A ``workset.yaml`` that exists without the marker gets "add the line", not "create"."""
    root, name, _std = unmarked
    (root / "workset.yaml").write_text("workset: {}\n")
    assert _cli("box", "rm", name) == 0

    assert (f"{root / 'workset.yaml'} does not store the standalone marker. Restore the "
            "box (first add the line 'registry: null' under 'workset:') or delete it:"
            ) in capsys.readouterr().out


def test_box_list_does_not_offer_the_register_that_fails(unmarked, capsys):
    """The list's generic pair would offer ``register``; it points at ``rm <name>``."""
    _root, name, _std = unmarked
    assert _cli("box", "rm", name) == 0
    capsys.readouterr()
    assert _cli("box", "list") == 0
    out = capsys.readouterr().out

    assert "box register" not in out
    assert "For the commands that restore or delete one: kanibako box rm <name>" in out


def test_a_gone_directory_offers_only_the_purge_and_it_works(unmarked, capsys):
    root, name, std = unmarked
    assert _cli("box", "rm", name) == 0
    root.rename(root.with_name("moved-away"))
    capsys.readouterr()
    assert _cli("box", "rm", name) == 0
    out = capsys.readouterr().out

    assert f"It cannot be restored: {root} is gone. Delete its entry:" in out
    assert _printed_cures(out) == [["kanibako", "box", "rm", name, "--purge"]]
    assert _cli("box", "rm", name, "--purge", "--force") == 0, capsys.readouterr().err
    assert registry_store.lookup_deregistered(std.registry, name) is None


@pytest.fixture
def unreadable_parent(unmarked, capsys):
    """*unmarked*, parked by a plain rm, with its parent directory made unreadable (EACCES)."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 directory")
    root, name, std = unmarked
    assert _cli("box", "rm", name) == 0
    capsys.readouterr()
    root.parent.chmod(0)
    yield root, name, std
    root.parent.chmod(0o755)


def test_an_unreadable_directory_is_not_called_gone_and_does_not_crash(unreadable_parent, capsys):
    """Only ENOENT/ENOTDIR prove "gone"; EACCES gets the generic advice, never a traceback."""
    _root, name, std = unreadable_parent

    assert _cli("box", "rm", name) == 0
    out = capsys.readouterr().out
    assert "gone" not in out
    assert "Restore it or delete it:" in out
    assert _printed_cures(out) == [
        ["kanibako", "box", "register", name], ["kanibako", "box", "rm", name, "--purge"]]

    assert _cli("box", "list") == 0
    out = capsys.readouterr().out
    assert name in out and "gone" not in out
    assert registry_store.lookup_deregistered(std.registry, name) is not None


def test_an_unparsable_marker_file_is_not_told_to_add_a_line(unmarked, capsys):
    """A line added to unparsable YAML leaves it unparsable, and both routes refuse it: fix it first."""
    root, name, std = unmarked
    assert _cli("box", "rm", name) == 0
    (root / "workset.yaml").write_text("workset: [unclosed\n")
    capsys.readouterr()

    assert _cli("box", "rm", name) == 0
    out = capsys.readouterr().out
    assert "add the line" not in out
    assert (f"{root / 'workset.yaml'} will not parse. Fix it, then restore the box (it "
            "needs the line 'registry: null' under 'workset:') or delete it:") in out
    register, _purge = _printed_cures(out)
    (root / "workset.yaml").write_text("workset:\n  registry: null\n")
    assert _cli(*register[1:]) == 0, capsys.readouterr().err
    assert registry_store.standalone_root(std.registry, name) == str(root)
