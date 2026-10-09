"""A box name DERIVED from a directory basename meets the box-name rule (spec §0).

``create``, ``workset connect``, ``box duplicate``, ``box extract``, ``box move``,
``box convert``, and the helper fork name a box after a directory when no ``--name``
is given.  A basename the rule refuses is refused before anything is written, and
each command prints its OWN cure, with ``--name <new-name>`` last.
"""

from __future__ import annotations

import contextlib
import io
import shlex

import pytest

_BAD = ["q$(touch PWNED)", "a b'c", "--purge", "-x", "café"]


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
    assert _cure(text) == ["kanibako", "create", str(path), "--name", "<new-name>"]
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
    assert _cure(text) == ["kanibako", "create", str(path), "--name", "<new-name>"]
    assert _primary_boxes() == {}
    assert not (tmp_home / "work" / "PWNED").exists()


def test_the_named_cure_creates_the_box(tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / "-x"
    rc, text = _cli("create", "--no-vault", "--", str(path))
    assert rc == 1, text

    cure = [w if w != "<new-name>" else "okbox" for w in _cure(text)[1:]]
    rc, text = _cli(*cure, "--no-vault")

    assert rc == 0, text
    assert list(_primary_boxes()) == ["okbox"]



@pytest.mark.parametrize("name", ["日本語プロジェクト", "my 日本語 app", "東京"])
@pytest.mark.parametrize("register", [(), ("--register",)])
def test_standalone_create_refuses_a_leaf_with_no_ascii_spelling(
        name, register, tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / name
    path.mkdir(parents=True)
    rc, text = _cli("create", "--standalone", *register, "--no-vault", "--", str(path))

    assert rc == 1, text
    assert f"The directory name '{name}' is not a valid box name" in text
    assert "cannot spell in ASCII" in text
    assert text.rstrip().endswith("Rename or move the directory to an ASCII name.")
    assert list(path.iterdir()) == []


def test_rm_of_a_standalone_box_moved_to_a_non_ascii_name_refuses_with_the_cure(
        tmp_home, config_file, credentials_dir):
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    root = tmp_home / "work" / "movee"
    root.mkdir(parents=True)
    rc, text = _cli("create", "--standalone", "--register", "--no-vault", "--", str(root))
    assert rc == 0, text
    moved = root.rename(root.with_name("東京"))
    registry = load_std_paths(load_config(user_config_file())).registry
    before = registry.read_bytes()

    rc, text = _cli("box", "rm", str(moved))

    assert rc == 1, text
    assert "cannot spell in ASCII" in text
    assert text.rstrip().endswith("Rename the directory to an ASCII name (or move it back).")
    assert registry.read_bytes() == before


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


def _primary_box(tmp_home, leaf: str = "src"):
    path = tmp_home / "work" / leaf
    rc, text = _cli("create", "--no-vault", str(path))
    assert rc == 0, text
    return path


def _standalone_box(tmp_home, leaf: str):
    path = tmp_home / "work" / leaf
    path.mkdir(parents=True, exist_ok=True)
    rc, text = _cli("create", "--no-vault", "--standalone", str(path))
    assert rc == 0, text
    return path


_DEST = "The directory name '-dup' is not a valid box name"
_PICK = "Pick a destination directory whose name is a valid box name."


def test_duplicate_names_the_destination_cure(tmp_home, config_file, credentials_dir):
    source = _primary_box(tmp_home)
    dest = tmp_home / "work" / "-dup"
    rc, text = _cli("box", "duplicate", str(source), str(dest), "--force")

    assert rc == 1, text
    assert _DEST in text and _PICK in text and "kanibako create" not in text
    assert not dest.exists()
    assert list(_primary_boxes()) == ["src"]


def test_duplicate_to_primary_names_the_destination_cure(
        tmp_home, config_file, credentials_dir):
    source = _standalone_box(tmp_home, "sa")
    dest = tmp_home / "work" / "-dup"
    rc, text = _cli("box", "duplicate", str(source), str(dest), "--to", "primary", "--force")

    assert rc == 1, text
    assert _DEST in text and _PICK in text and "kanibako create" not in text
    assert not dest.exists()
    assert _primary_boxes() == {}


def test_extract_names_its_own_cure_and_the_cure_works(tmp_home, config_file, credentials_dir):
    source = _primary_box(tmp_home)
    archive = tmp_home / "a.txz"
    rc, text = _cli("box", "archive", str(source), str(archive), "--force")
    assert rc == 0, text
    dest = tmp_home / "work" / "-e1"
    dest.mkdir()
    rc, text = _cli("box", "extract", str(archive), str(dest), "--force")

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "box", "extract", str(archive), str(dest),
                           "--name", "<new-name>"]
    assert "pick another --name" not in text
    assert list(_primary_boxes()) == ["src"]

    rc, text = _cli(*[w if w != "<new-name>" else "exok" for w in _cure(text)[1:]], "--force")
    assert rc == 0, text
    assert sorted(_primary_boxes()) == ["exok", "src"]


def _tree(root) -> str:
    """A digest of every path, type, and file's bytes under *root*."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        kind = "l" if path.is_symlink() else "d" if path.is_dir() else "f"
        digest.update(f"{path.relative_to(root)}\0{kind}\0".encode())
        if kind == "f":
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_move_names_its_own_cure_and_the_cure_works(tmp_home, config_file, credentials_dir):
    source = _standalone_box(tmp_home, "sa")
    before = _tree(source)
    dest = tmp_home / "work" / "-m1"
    rc, text = _cli("box", "move", str(source), str(dest), "--default", "--force")

    assert rc == 1, text
    assert _tree(source) == before
    assert _cure(text) == ["kanibako", "box", "move", str(source), str(dest), "--default",
                           "--name", "<new-name>"]
    assert not dest.exists()
    assert _primary_boxes() == {}

    rc, text = _cli(*[w if w != "<new-name>" else "mvok" for w in _cure(text)[1:]], "--force")
    assert rc == 0, text
    assert list(_primary_boxes()) == ["mvok"]


def test_convert_names_its_own_cure_and_leaves_the_box_as_it_was(
        tmp_home, config_file, credentials_dir):
    source = _standalone_box(tmp_home, "-c1")
    assert (source / "workspace").is_dir()
    before = _tree(source)
    rc, text = _cli("box", "convert", str(source), "--default", "--force")

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "box", "convert", str(source), "--default",
                           "--name", "<new-name>"]
    assert _tree(source) == before
    assert _primary_boxes() == {}


def test_convert_with_a_move_into_a_rule_breaking_directory_moves_nothing(
        tmp_home, config_file, credentials_dir):
    source = _standalone_box(tmp_home, "sa")
    before = _tree(source)
    dest = tmp_home / "work" / "-c2"
    rc, text = _cli("box", "convert", str(source), "--default", "--move", str(dest), "--force")

    assert rc == 1, text
    assert _cure(text) == ["kanibako", "box", "convert", str(source), "--default",
                           "--move", str(dest), "--name", "<new-name>"]
    assert _tree(source) == before
    assert not dest.exists()
    assert _primary_boxes() == {}


def _fork_hub(tmp_home, workspace):
    from unittest.mock import MagicMock

    from kanibako.channels.helper_listener import HelperContext, HelperHub
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import BoxMode, _early_scope, load_std_paths

    std = load_std_paths(load_config(user_config_file()))
    shell = std.boxes / "x" / "home"
    (shell / "helpers").mkdir(parents=True)
    hub = HelperHub()
    hub._ctx = HelperContext(
        runtime=MagicMock(), image="test:latest", container_name_segments=("primary", "x"),
        shell_path=shell, helpers_dir=shell / "helpers", socket_path=tmp_home / "h.sock",
        project_path=workspace, data_path=std.data_path, boxes=std.boxes,
        primary_workset=std.primary_workset, early=_early_scope(std, BoxMode.primary),
    )
    return hub


def test_fork_of_a_rule_breaking_box_names_the_move_cure(tmp_home, config_file, credentials_dir):
    workspace = tmp_home / "work" / "-legacy"
    workspace.mkdir(parents=True)
    reply = _fork_hub(tmp_home, workspace)._handle_fork({"name": "two"})

    assert reply["status"] == "error"
    assert shlex.split(reply["message"].splitlines()[-1]) == [
        "kanibako", "box", "move", str(workspace), "<new-path>", "--name", "<new-name>"]
    assert not (tmp_home / "work" / "-legacy.two").exists()
    assert _primary_boxes() == {}


def test_fork_with_a_rule_breaking_name_asks_for_another(tmp_home, config_file, credentials_dir):
    workspace = tmp_home / "work" / "app"
    workspace.mkdir(parents=True)
    reply = _fork_hub(tmp_home, workspace)._handle_fork({"name": "a b"})

    assert reply["status"] == "error"
    assert reply["message"].endswith("Pick a fork name that is a valid box name.")
    assert not (tmp_home / "work" / "app.a b").exists()
