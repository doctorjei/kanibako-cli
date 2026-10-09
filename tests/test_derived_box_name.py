"""A box name DERIVED from a directory basename meets the box-name rule (spec §0).

``create``, ``workset connect``, ``box duplicate``, ``box extract``, ``box move``,
``box convert``, and the helper fork name a box after a directory when no ``--name``
is given.  A basename the rule refuses is refused before anything is written, and
each command prints its OWN cure, with ``--name`` last: the name's ASCII spelling
when it has one, else ``<new-name>``.
"""

from __future__ import annotations

import contextlib
import io
import shlex

import pytest

_BAD = ["q$(touch PWNED)", "a b'c", "--purge", "-x", "東京"]


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


def _cure(text: str, prose: str = "Give the box a valid name:") -> "list[str]":
    lines = text.splitlines()
    return shlex.split(lines[lines.index(next(ln for ln in lines if ln.endswith(prose))) + 1])


_SPELLED = "Its ASCII spelling works:"


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


@pytest.mark.parametrize("name, spelled", [
    ("café", "cafe"), ("かにばこ", "kanibako"), ("きんや", "kin_ya")])
def test_create_offers_the_ascii_spelling_and_the_offer_creates_the_box(
        name, spelled, tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / name
    rc, text = _cli("create", "--no-vault", "--", str(path))

    assert rc == 1, text
    assert f"The directory name '{name}' is not a valid box name" in text
    assert _cure(text, _SPELLED) == ["kanibako", "create", str(path), "--name", spelled]
    assert _primary_boxes() == {}

    rc, text = _cli(*_cure(text, _SPELLED)[1:], "--no-vault")
    assert rc == 0, text
    assert list(_primary_boxes()) == [spelled]


def test_create_offers_the_spelling_of_a_typed_name(tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / "ok"
    rc, text = _cli("create", "--no-vault", str(path), "--name", "かに")

    assert rc == 1, text
    assert "Invalid box name 'かに'" in text
    assert _cure(text, _SPELLED) == ["kanibako", "create", str(path), "--name", "kani"]
    assert _primary_boxes() == {}


def test_a_name_with_no_spelling_gets_the_generic_cure(tmp_home, config_file, credentials_dir):
    path = tmp_home / "work" / "ok"
    rc, text = _cli("create", "--no-vault", str(path), "--name", "かに東")

    assert rc == 1, text
    assert _SPELLED not in text
    assert _cure(text) == ["kanibako", "create", str(path), "--name", "<new-name>"]


@pytest.mark.parametrize("leaf, spelled", [
    ("かに", "kani"), ("Straße", "Strasse"), ("きんや", "kin_ya")])
def test_standalone_create_writes_the_leaf_in_its_ascii_spelling(
        leaf, spelled, tmp_home, config_file, credentials_dir):
    from kanibako.launch.box_resolve import standalone_box_name

    path = tmp_home / "work" / leaf
    path.mkdir(parents=True)
    rc, text = _cli("create", "--standalone", "--no-vault", "--", str(path))

    assert rc == 0, text
    assert standalone_box_name(path, None).endswith(f"_{spelled}")


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


def test_connect_offers_the_spelling_of_a_typed_name(tmp_home, config_file, credentials_dir):
    ws, std = _workset(tmp_home)
    source = tmp_home / "work" / "ok"
    source.mkdir(parents=True)
    rc, text = _cli("workset", "connect", "wsx", str(source), "--name=Łódź")

    assert rc == 1, text
    assert _cure(text, _SPELLED) == ["kanibako", "workset", "connect", "wsx", str(source),
                                     "--name", "Lodz"]
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


@pytest.mark.parametrize("to", [(), ("--to", "primary")])
def test_duplicate_to_primary_names_its_own_cure_and_the_cure_works(
        to, tmp_home, config_file, credentials_dir):
    source = _primary_box(tmp_home) if not to else _standalone_box(tmp_home, "sa")
    before = dict(_primary_boxes())
    dest = tmp_home / "work" / "-dup"
    rc, text = _cli("box", "duplicate", str(source), str(dest), *to, "--force")

    assert rc == 1, text
    assert _DEST in text
    assert _cure(text) == ["kanibako", "box", "duplicate", str(source), str(dest), *to,
                           "--name", "<new-name>"]
    assert not dest.exists()
    assert _primary_boxes() == before

    rc, text = _cli(*[w if w != "<new-name>" else "dupok" for w in _cure(text)[1:]], "--force")
    assert rc == 0, text
    assert _primary_boxes()["dupok"] == str(dest)


@pytest.mark.parametrize("to", [(), ("--to", "primary")])
def test_duplicate_to_primary_takes_the_typed_name(to, tmp_home, config_file, credentials_dir):
    source = _primary_box(tmp_home) if not to else _standalone_box(tmp_home, "sa")
    dest = tmp_home / "work" / "dst"
    rc, text = _cli("box", "duplicate", str(source), str(dest), *to, "--name", "Custom",
                    "--force")

    assert rc == 0, text
    assert _primary_boxes()["Custom"] == str(dest)
    assert "dst" not in _primary_boxes()


def test_duplicate_from_named_to_primary_takes_the_typed_name(
        tmp_home, config_file, credentials_dir):
    _workset(tmp_home)
    rc, text = _cli("box", "duplicate", str(_primary_box(tmp_home)), str(tmp_home / "unused"),
                    "--to", "named", "--workset", "wsx", "--force")
    assert rc == 0, text
    member = (tmp_home / "ws" / "workspaces" / "src").resolve()
    assert member.is_dir(), text
    dest = tmp_home / "work" / "dst"
    rc, text = _cli("box", "duplicate", str(member), str(dest), "--to", "primary",
                    "--name", "Custom", "--force")

    assert rc == 0, text
    assert _primary_boxes()["Custom"] == str(dest)


@pytest.mark.parametrize("name, said", [("bad name", "Invalid box name 'bad name'"),
                                        ("SRC", "Name 'SRC' is already registered")])
def test_duplicate_to_primary_refuses_a_bad_typed_name_before_any_write(
        name, said, tmp_home, config_file, credentials_dir):
    source = _primary_box(tmp_home)
    dest = tmp_home / "work" / "dst"
    rc, text = _cli("box", "duplicate", str(source), str(dest), "--name", name, "--force")

    assert rc == 1, text
    assert said in text
    assert not dest.exists()
    assert list(_primary_boxes()) == ["src"]


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


def test_move_offers_the_spelling_of_a_typed_name(tmp_home, config_file, credentials_dir):
    source = _standalone_box(tmp_home, "sa")
    dest = tmp_home / "work" / "m2"
    rc, text = _cli("box", "move", str(source), str(dest), "--default", "--name", "かに", "--force")

    assert rc == 1, text
    assert _cure(text, _SPELLED) == ["kanibako", "box", "move", str(source), str(dest),
                                     "--default", "--name", "kani"]
    assert not dest.exists()


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


def test_fork_with_a_kana_name_offers_its_spelling(tmp_home, config_file, credentials_dir):
    workspace = tmp_home / "work" / "app"
    workspace.mkdir(parents=True)
    reply = _fork_hub(tmp_home, workspace)._handle_fork({"name": "かに"})

    assert reply["status"] == "error"
    assert reply["message"].endswith("Pick a fork name that is a valid box name; 'kani' works.")


# ---------------------------------------------------------------------------
# Review r1 item 1 — the ASCII cure must not loop on a standalone door.
#
# The generic cure is `Give the box a valid name: … --name <new-name>`.  A
# standalone box's name is composed from its directory and the standalone doors
# REFUSE a --name that differs from it, so printing that cure here told the user to
# run a command that produces the identical error.  kanibako's example:
#   box move …/q3 …/京都 --name foo
# A standalone name can only be fixed at the directory, so that is what the cure
# must name.
# ---------------------------------------------------------------------------

def _make_standalone_box(_cli, tmp_home, leaf):
    src = tmp_home / "work" / leaf
    src.mkdir(parents=True)
    rc, text = _cli("create", "--standalone", "--register", "--no-vault", "--", str(src))
    assert rc == 0, text
    return src


def test_move_to_a_non_ascii_leaf_cures_at_the_directory_not_with_a_name(
        tmp_home, config_file, credentials_dir):
    """⚑ `box move` onto a non-ASCII destination must NOT print a `--name` cure."""
    from kanibako.settings.messages import CURE_MOVED_LEAF_NOT_ASCII

    src = _make_standalone_box(_cli, tmp_home, "mvsrc")
    dst = tmp_home / "work" / "京都"

    rc, text = _cli("box", "move", str(src), str(dst), "--force")

    assert rc == 1, text
    assert "cannot spell in ASCII" in text
    # The imported constant, not a paraphrase, so the two cannot drift.
    assert CURE_MOVED_LEAF_NOT_ASCII in text
    # ⚑ The loop is gone: no `--name` anywhere in the refusal.
    assert "--name" not in text
    assert not dst.exists()


def test_convert_to_standalone_at_a_non_ascii_leaf_cures_at_the_directory(
        tmp_home, config_file, credentials_dir):
    """⚑ Same rule at the convert door; the cure must not hard-code `--default` either."""
    from kanibako.settings.messages import CURE_MOVED_LEAF_NOT_ASCII

    src = tmp_home / "work" / "cvsrc"
    src.mkdir(parents=True)
    rc, text = _cli("create", "--no-vault", "--", str(src))
    assert rc == 0, text
    dst = tmp_home / "work" / "大阪"

    rc, text = _cli("box", "convert", str(src), "--standalone",
                   "--move", str(dst), "--force")

    assert rc == 1, text
    assert "cannot spell in ASCII" in text
    assert CURE_MOVED_LEAF_NOT_ASCII in text
    assert "--name" not in text
    assert "--default" not in text


def test_move_with_standalone_from_a_primary_cures_at_the_directory(
        tmp_home, config_file, credentials_dir):
    """⚑ `box move <primary> …/大阪 --standalone`: the TARGET is standalone, so no `--name` cure."""
    from kanibako.settings.messages import CURE_MOVED_LEAF_NOT_ASCII

    src = tmp_home / "work" / "mvprim"
    src.mkdir(parents=True)
    rc, text = _cli("create", "--no-vault", "--", str(src))
    assert rc == 0, text
    dst = tmp_home / "work" / "大阪"

    rc, text = _cli("box", "move", str(src), str(dst), "--standalone", "--force")

    assert rc == 1, text
    assert "cannot spell in ASCII" in text
    assert CURE_MOVED_LEAF_NOT_ASCII in text
    assert "--name" not in text
    assert not dst.exists()


def test_the_convert_cure_echoes_the_target_that_was_asked_for():
    """⚑ The cure used to hard-code `--default`, so a `--workset ws` failure advised a
    DIFFERENT operation.  It must echo the target the user actually chose."""
    from types import SimpleNamespace

    from kanibako.commands.box._lifecycle import _convert_target_flags

    assert _convert_target_flags(SimpleNamespace(to_default=True)) == ["--default"]
    assert _convert_target_flags(
        SimpleNamespace(to_default=False, to_workset="wsx")) == ["--workset", "wsx"]
    # A standalone target needs no target flag here; it never reaches the --name cure.
    assert _convert_target_flags(SimpleNamespace(to_standalone=True)) == []
    assert _convert_target_flags(SimpleNamespace()) == []


def test_the_move_cure_echoes_the_workset_that_was_asked_for(
        tmp_home, config_file, credentials_dir):
    """⚑ `box move … --workset ws` must print a cure that still targets `ws`; dropping
    the flag advised a move into the PRIMARY workset, a different operation."""
    src = tmp_home / "work" / "mvws"
    src.mkdir(parents=True)
    rc, text = _cli("create", "--no-vault", "--", str(src))
    assert rc == 0, text
    rc, text = _cli("workset", "create", str(tmp_home / "ws" / "wsx"))
    assert rc == 0, text
    dst = tmp_home / "work" / "dst"

    rc, text = _cli("box", "move", str(src), str(dst), "--workset", "wsx",
                    "--name", "かに", "--force")

    assert rc == 1, text
    assert _cure(text, _SPELLED) == [
        "kanibako", "box", "move", str(src), str(dst), "--workset", "wsx", "--name", "kani"]
    assert not dst.exists()
