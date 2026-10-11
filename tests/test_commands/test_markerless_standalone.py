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
