"""A box name DERIVED from a directory basename meets the box-name rule (spec §0).

``create``, ``workset connect``, and ``box duplicate --to named`` name a box after
a directory when no ``--name`` is given.  A basename the rule refuses is refused
before anything is written, and the refusal names a ``--name`` cure.
"""

from __future__ import annotations

import contextlib
import io
import shlex

import pytest

_BAD = ["q$(touch PWNED)", "a b'c", "--purge", "-x"]


def _cli(*argv: str) -> "tuple[int, str]":
    """Run the shipped CLI entry point; return its exit code and everything it printed."""
    from kanibako.cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            main(list(argv))
            rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue()


def _primary_boxes() -> dict:
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import BoxMode, _early_scope, load_primary_boxes, load_std_paths

    std = load_std_paths(load_config(user_config_file()))
    return load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))


def _cure(text: str) -> "list[str]":
    lines = text.splitlines()
    return shlex.split(lines[lines.index(next(
        ln for ln in lines if ln.endswith("Give the box a valid name:"))) + 1])


@pytest.mark.parametrize("name", _BAD)
def test_create_refuses_a_new_directory_and_writes_nothing(
        name, tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / name
    rc, text = _cli("create", "--no-vault", "--", str(path))

    assert rc == 1, text
    assert f"The directory name '{name}' is not a valid box name" in text
    assert _cure(text) == ["kanibako", "create", "--name", "<new-name>", str(path)]
    assert not path.exists()
    assert _primary_boxes() == {}


@pytest.mark.parametrize("name", _BAD)
def test_create_in_an_existing_directory_refuses_and_registers_nothing(
        name, tmp_home, config_file, credentials_dir, monkeypatch):
    path = tmp_home / "work" / name
    path.mkdir(parents=True)
    monkeypatch.chdir(path)
    monkeypatch.setenv("PWD", str(path))
    rc, text = _cli("create", "--no-vault")

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "create", "--name", "<new-name>", str(path)]
    assert _primary_boxes() == {}
    assert not (tmp_home / "work" / "PWNED").exists()


def test_the_named_cure_creates_the_box(tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / "-x"
    rc, text = _cli("create", "--no-vault", "--", str(path))
    assert rc == 1, text

    cure = [w if w != "<new-name>" else "okbox" for w in _cure(text)[1:]]
    rc, text = _cli(*cure[:1], "--no-vault", *cure[1:])

    assert rc == 0, text
    assert list(_primary_boxes()) == ["okbox"]


def _workset(tmp_home):
    from kanibako.project.workset import create_workset
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    std = load_std_paths(load_config(user_config_file()))
    return create_workset("wsx", (tmp_home / "ws").resolve(), std), std


def _members(ws, std) -> "list[str]":
    from kanibako.project.workset import load_workset

    return [p.name for p in load_workset(ws.root, ws.name, early_system=std.early_system).projects]


def test_connect_refuses_a_derived_name(tmp_home, config_file, credentials_dir):
    ws, std = _workset(tmp_home)
    source = tmp_home / "work" / "-x"
    source.mkdir(parents=True)
    rc, text = _cli("workset", "connect", "wsx", str(source))

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "workset", "connect", "wsx", str(source),
                           "--name", "<new-name>"]
    assert _members(ws, std) == []


def test_connect_refuses_a_typed_name(tmp_home, config_file, credentials_dir):
    ws, std = _workset(tmp_home)
    source = tmp_home / "work" / "ok"
    source.mkdir(parents=True)
    rc, text = _cli("workset", "connect", "wsx", str(source), "--name=a b")

    assert rc == 1, text
    assert "Invalid box name 'a b'" in text
    assert _members(ws, std) == []


def test_duplicate_to_named_refuses_a_derived_name(tmp_home, config_file, credentials_dir):
    ws, std = _workset(tmp_home)
    source = tmp_home / "work" / "-x"
    source.mkdir(parents=True)
    rc, text = _cli("box", "duplicate", str(source), str(tmp_home / "unused"),
                    "--to", "named", "--workset", "wsx")

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "box", "duplicate", str(source),
                           str(tmp_home / "unused"), "--to", "named", "--workset", "wsx",
                           "--name", "<new-name>"]
    assert _members(ws, std) == []
