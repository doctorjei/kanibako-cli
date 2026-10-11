"""``workset connect`` names a symlink loop instead of crashing on it.

On 3.11 ``Path.resolve()`` raises ``RuntimeError`` on a loop; on 3.13 it hides one.
Either way the connect must refuse with the looped path named, before the journal
bracket, and change nothing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.commands.workset_cmd import run_connect
from kanibako.launch import journal
from kanibako.project.workset import create_workset, load_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths

LOOP = "runs through a symlink loop that kanibako cannot resolve"


@pytest.fixture
def setup(config_file, tmp_home):
    std = load_std_paths(load_config(config_file))
    ws = create_workset("lw", tmp_home / "lw", std)
    return std, ws, tmp_home


def _connect(source: Path, name: str) -> int:
    return run_connect(argparse.Namespace(
        workset="lw", source=str(source), project_name=name, force=False))


def _loop(link: Path) -> None:
    """Make *link* a symlink to itself; a real dir there moves aside first."""
    if link.exists():
        link.rename(link.with_name(link.name + ".real"))
    link.symlink_to(link.name)


def _assert_untouched(std, ws, name: str) -> None:
    assert journal.read_journal(std.journal) == {}
    reloaded = load_workset(ws.root, ws.name, early_system=std.early_system)
    assert all(p.name != name for p in reloaded.projects)
    assert not (ws.root / "boxes" / name).exists()


class TestConnectNamesLoop:
    @pytest.mark.parametrize("rel", ["s", "L/x"], ids=["source", "under-source"])
    def test_looped_source_refused(self, setup, capsys, rel):
        std, ws, home = setup
        _loop(home / rel.split("/")[0])
        source = home / rel

        assert _connect(source, "x") == 1

        err = capsys.readouterr().err
        assert f"Cannot connect '{source}': it {LOOP}" in err
        _assert_untouched(std, ws, "x")

    @pytest.mark.parametrize("in_root", [False, True], ids=["external", "in-root"])
    def test_looped_workspaces_dir_refused(self, setup, capsys, in_root):
        std, ws, home = setup
        source = ws.root / "foo" if in_root else home / "ext"
        source.mkdir()
        _loop(ws.root / "workspaces")

        assert _connect(source, "foo") == 1

        err = capsys.readouterr().err
        assert f"Cannot connect '{source}': {ws.root / 'workspaces'} {LOOP}" in err
        _assert_untouched(std, ws, "foo")
        assert (ws.root / "workspaces").is_symlink()


class TestCaseVariantStoreSkipsLoops:
    """A looped store base, here or in another workset, holds no store: the
    case-variant refusal still fires on the real one."""

    @pytest.mark.parametrize("where", ["own-vault-arm", "other-boxes"])
    def test_case_variant_refusal_survives_loop(self, setup, capsys, where):
        std, ws, home = setup
        (home / "ext").mkdir()
        (ws.root / "boxes" / "EXT").mkdir(parents=True)
        if where == "own-vault-arm":
            (ws.root / "vault").mkdir(exist_ok=True)
            _loop(ws.root / "vault" / "rw")
        else:
            other = create_workset("ow", home / "ow", std)
            _loop(other.root / "boxes")

        assert _connect(home / "ext", "ext") == 1

        err = capsys.readouterr().err
        assert "would build a second store beside the kept store of 'EXT'" in err
        _assert_untouched(std, ws, "ext")
