"""Every verb that makes a NEW member refuses a case variant of a kept store, writing nothing.

``proj`` is disconnected from ``ws1`` with its store kept; a new member spelled ``PROJ``
would build a second store beside it.  Each caller of ``refuse_case_variant_store``
has a test here, so dropping any one call turns a test red.
"""

from __future__ import annotations

import argparse

import pytest

from kanibako.launch import journal
from kanibako.project.workset import (
    add_project, create_workset, load_workset, remove_project,
)
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project

_ERR = "would build a second store beside the kept store of"


@pytest.fixture
def kept(config_file, tmp_home, credentials_dir):
    """``ws1`` keeping the store of a disconnected ``proj``: (config, std, root)."""
    config = load_config(config_file)
    std = load_std_paths(config)
    root = (tmp_home / "ws1").resolve()
    ws = create_workset("ws1", root, std)
    ext = (tmp_home / "ext_proj").resolve()
    ext.mkdir()
    add_project(ws, "proj", ext, std)
    remove_project(ws, "proj", std=std)
    assert (root / "boxes" / "proj").is_dir()
    return config, std, root


def _snapshot(root):
    return sorted(str(p) for p in root.rglob("*"))


def test_named_create_refuses(kept, monkeypatch, capsys):
    from kanibako.commands.box._parser import run_create

    _config, std, root = kept
    before = _snapshot(root)
    monkeypatch.chdir(root)

    rc = run_create(argparse.Namespace(
        path="PROJ", standalone=False, no_vault=False, name=None, image=None,
        agent=None, allow_home=False,
    ))

    assert rc == 1
    assert _ERR in capsys.readouterr().err
    assert _snapshot(root) == before
    assert journal.read_journal(std.journal) == {}


def test_duplicate_into_the_workset_refuses(kept, tmp_home, capsys):
    from kanibako.commands.box import run_duplicate

    config, std, root = kept
    src = (tmp_home / "src1").resolve()
    src.mkdir()
    resolve_project(std, config, project_dir=str(src), initialize=True)
    capsys.readouterr()
    before = _snapshot(root)

    rc = run_duplicate(argparse.Namespace(
        source_path=str(src), new_path=str(root / "workspaces" / "PROJ"),
        to_mode="named", bare=False, force=True, workset="ws1", project_name="PROJ",
    ))

    assert rc == 1
    assert _ERR in capsys.readouterr().err
    assert _snapshot(root) == before


def _convert_into_ws1(old):
    from kanibako.commands.box._lifecycle import run_convert

    return run_convert(argparse.Namespace(
        old=str(old), force=True, to_default=False, to_standalone=False,
        to_workset="ws1", move=None, name=None,
    ))


def test_converting_a_primary_box_into_the_workset_refuses(kept, tmp_home, capsys):
    """C1: primary ``PROJ`` → ``ws1``, which keeps ``proj``."""
    config, std, root = kept
    src = (tmp_home / "PROJ").resolve()
    src.mkdir()
    proj = resolve_project(std, config, project_dir=str(src), initialize=True)
    assert proj.name == "PROJ"
    capsys.readouterr()
    before = _snapshot(root)

    assert _convert_into_ws1(src) == 1

    assert _ERR in capsys.readouterr().err
    assert _snapshot(root) == before
    assert proj.metadata_path.is_dir()


def test_converting_another_worksets_member_refuses(kept, tmp_home, capsys):
    """C2: ``ws2`` member ``Q2`` → ``ws1``, which keeps ``q2``."""
    _config, std, root = kept
    ws1 = load_workset(root, "ws1", early_system=std.early_system)
    ext_q2 = (tmp_home / "ext_q2").resolve()
    ext_q2.mkdir()
    add_project(ws1, "q2", ext_q2, std)
    remove_project(ws1, "q2", std=std)
    ws2 = create_workset("ws2", (tmp_home / "ws2").resolve(), std)
    src = (tmp_home / "q2b").resolve()
    src.mkdir()
    add_project(ws2, "Q2", src, std)
    capsys.readouterr()
    before = _snapshot(root)

    assert _convert_into_ws1(src) == 1

    assert _ERR in capsys.readouterr().err
    assert _snapshot(root) == before
    ws2 = load_workset(ws2.root, "ws2", early_system=std.early_system)
    assert [p.name for p in ws2.projects] == ["Q2"]
