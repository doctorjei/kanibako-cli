"""``box move`` and ``box convert --move`` onto, or carrying away, another box's tree.

Keyspace § 0 "Per-owner resources": a workspace inside another box's workspace, root, or
store is refused unless --force (the check ``create`` and ``remap`` run, through
``_dest_nesting_refusal``).  A relocation that would carry away another box's workspace,
root, store, or workset root ("each instance's destructive verb removes only its own
part"), or that lands in the source's own store while the op moves or retires that store,
is refused whatever the flag.  Measured before the fix: every forceable cell passed
without --force, a carried box was deleted with the source's old tree, and a convert
into its own store lost the workspace (``--default``, ``--workset``) or recursed
(``--standalone``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._parser import run_create
from kanibako.project import registry_store

SHARE = "Both boxes would claim it: pass --force to share it, or choose another path."


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    return load_std_paths(load_config(config_file))


def _run(*argv: str) -> int:
    args = build_parser().parse_args(["box", *argv])
    return args.func(args)


def _create(*argv: str) -> int:
    return run_create(build_parser().parse_args(["box", "create", *argv]))


@pytest.fixture
def env(config_file, credentials_dir, tmp_home, monkeypatch, capsys):
    """``X`` (primary), ``XS`` (registered standalone), workset ``wn`` with external member
    ``XN``, the links ``xlnk``/``rlnk``/``slnk``, and a primary source ``s``.  A no-flag
    run answers yes to the confirmation, so only a guard refuses it."""
    from kanibako.project.workset import create_workset
    monkeypatch.chdir(tmp_home)
    monkeypatch.setenv("PWD", str(tmp_home))
    monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _message: None)
    std = _std(config_file)
    area = (tmp_home / "nest").resolve()
    area.mkdir()
    assert _create("--name", "X", str(area / "X")) == 0
    assert _create("--standalone", "--register", str(area / "XS")) == 0
    create_workset("wn", area / "wn", std)
    (area / "XN").mkdir()
    assert _create("--name", "s", str(area / "s")) == 0
    from kanibako.commands.workset_cmd import run_connect
    import argparse
    assert run_connect(argparse.Namespace(
        workset="wn", source=str(area / "XN"), project_name="XN", force=False)) == 0
    (area / "xlnk").symlink_to(area / "X")
    (area / "rlnk").symlink_to(area / "XS")
    (area / "slnk").symlink_to(area / "XS" / "box_data")
    (area / "s" / "f.txt").write_text("data")
    capsys.readouterr()
    return std, area


def _standalone_source(area: Path, capsys, name: str = "SS") -> Path:
    root = area / name
    assert _create("--standalone", "--register", str(root)) == 0
    (root / "workspace" / "f.txt").write_text("data")
    capsys.readouterr()
    return root


def _relocate(verb: str, src: Path, dest: Path, *, force: bool, name: str | None = None) -> int:
    flags = (["--force"] if force else []) + (["--name", name] if name else [])
    if verb == "mv":
        return _run("move", *flags, str(src), str(dest))
    target = {"cvS": ["--standalone"], "cvD": ["--default"], "cvW": ["--workset", "wn"]}[verb]
    return _run("convert", *target, *flags, "--move", str(dest), str(src))


def _source(verb: str, area: Path, capsys) -> Path:
    return _standalone_source(area, capsys) if verb == "cvD" else area / "s"


def _verb_word(verb: str) -> str:
    return "move" if verb == "mv" else "convert"


VERBS = ["mv", "cvS", "cvD", "cvW"]


def _forceable_dests(std, area: Path) -> dict[str, tuple[Path, str]]:
    """§ 1 rows 2–8: a new leaf, and what the refusal says it is."""
    xs_store = area / "XS" / "box_data"
    return {
        "in_X": (area / "X" / "sub", f"inside the workspace of primary box 'X' ({area / 'X'})"),
        "via_xlnk": (area / "xlnk" / "sub",
                     f"inside the workspace of primary box 'X' ({area / 'X'})"),
        "in_XS_workspace": (area / "XS" / "workspace" / "sub", "inside the root of standalone box"),
        "in_XS_root": (area / "XS" / "sub", "inside the root of standalone box"),
        "via_rlnk": (area / "rlnk" / "sub", "inside the root of standalone box"),
        "in_XN": (area / "XN" / "sub",
                  f"inside the workspace of member 'XN' of workset 'wn' ({area / 'XN'})"),
        "in_X_store": (std.boxes / "X" / "sub",
                       f"inside the store of primary box 'X' ({(std.boxes / 'X').resolve()})"),
        "in_XS_store": (xs_store / "sub", "inside the store of standalone box"),
        "via_slnk": (area / "slnk" / "sub", "inside the store of standalone box"),
    }


ROWS = ["in_X", "via_xlnk", "in_XS_workspace", "in_XS_root", "via_rlnk", "in_XN",
        "in_X_store", "in_XS_store", "via_slnk"]


class TestDestinationForceable:
    """§ 1 rows 2–8: refused without --force, and --force passes."""

    @pytest.mark.parametrize("row", ROWS)
    @pytest.mark.parametrize("verb", VERBS)
    def test_refused_without_force(self, env, capsys, verb, row):
        std, area = env
        src = _source(verb, area, capsys)
        dest, what = _forceable_dests(std, area)[row]
        assert _relocate(verb, src, dest, force=False) == 1
        err = capsys.readouterr().err
        assert f"Cannot {_verb_word(verb)} a box at '{dest.resolve()}': it is {what}" in err, err
        assert SHARE in err
        assert not dest.exists()
        assert (src / ("workspace" if verb == "cvD" else "") / "f.txt").read_text() == "data"

    @pytest.mark.parametrize("row", ROWS)
    @pytest.mark.parametrize("verb", VERBS)
    def test_force_passes(self, env, capsys, verb, row):
        std, area = env
        src = _source(verb, area, capsys)
        dest, _what = _forceable_dests(std, area)[row]
        rc = _relocate(verb, src, dest, force=True)
        assert rc == 0, capsys.readouterr().err


def _tree_digest(tree: Path) -> dict[str, str]:
    """Every file under *tree* (relative path → sha256)."""
    import hashlib
    return {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(tree.rglob("*")) if p.is_file() and not p.is_symlink()}


class TestInsideAMemberWithForce:
    """His Q4Jei 176-4B (C) answer, "yes": `convert --workset --move` inside a member is
    newly allowed with --force.  No file of the member is overwritten."""

    def test_convert_workset_move_inside_a_member(self, env, capsys):
        std, area = env
        (area / "XN" / "f.txt").write_text("theirs")
        before = _tree_digest(area / "XN")
        dest = area / "XN" / "sub"
        assert _relocate("cvW", area / "s", dest, force=True) == 0, capsys.readouterr().err
        after = _tree_digest(area / "XN")
        assert before and all(after.get(path) == digest for path, digest in before.items())
        assert (dest / "f.txt").read_text() == "data"


class TestInPlaceConvertIntoAWorkset:
    """An in-place `convert --workset` keeps a workspace that sits inside a member's or a
    primary's workspace: refused unless --force (keyspace § 0).  ``_to_workset`` forces its
    connect past the marker guard, so the flag must be judged up front."""

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("holder", ["member", "primary"])
    def test_inside_another_workspace(self, env, capsys, holder, force):
        from kanibako.project.workset import add_project, create_workset, load_workset
        std, area = env
        create_workset("wo", area / "wo", std)
        if holder == "member":  # made before its holder connects, as a user might
            inner = area / "YN" / "p"
            inner.mkdir(parents=True)
            assert _create("--name", "p", str(inner)) == 0
            ws = load_workset(area / "wn", "wn", early_system=std.early_system)
            add_project(ws, "YN", area / "YN", std)
        else:
            inner = area / "X" / "p"
            assert _create("--force", "--name", "p", str(inner)) == 0
        capsys.readouterr()
        rc = _run("convert", "--workset", "wo", *(["--force"] if force else []), "p")
        err = capsys.readouterr().err
        if force:
            assert rc == 0, err
            return
        what = (f"inside the workspace of member 'YN' of workset 'wn' ({area / 'YN'})"
                if holder == "member" else f"inside the workspace of primary box 'X' ({area / 'X'})")
        assert rc == 1
        assert f"Cannot convert 'p' in place at '{inner}': it is {what}. {SHARE}" in err, err
        assert not load_workset(area / "wo", "wo", early_system=std.early_system).projects

    @pytest.mark.parametrize("force", [False, True])
    def test_workspace_is_a_markerless_standalone_root(self, env, capsys, force):
        """A standalone whose workspace is the registered root of another standalone whose
        marker is gone (its registry row kept) holds that root: refused unless --force.  An
        in-place convert places nothing, so the remap arm refused whatever the flag does not
        apply."""
        from kanibako.project.workset import create_workset, load_workset
        std, area = env
        create_workset("wo", area / "wo", std)
        assert _create("--standalone", "--register", str(area / "C")) == 0
        held = area / "C" / "workspace"
        registry_store.register_standalone(std.registry, "T", held)  # T's row, no marker
        c_name = next(n for n, r in registry_store.load_standalone(std.registry).items()
                      if Path(r) == area / "C")
        capsys.readouterr()
        rc = _run("convert", "--workset", "wo", *(["--force"] if force else []), str(area / "C"))
        err = capsys.readouterr().err
        if force:
            assert rc == 0, err
            return
        assert rc == 1
        assert (f"Cannot convert '{c_name}' in place at '{area / 'C'}': it is a directory "
                f"holding the root of standalone box 'T' ({held}). {SHARE}") in err, err
        assert not load_workset(area / "wo", "wo", early_system=std.early_system).projects


class TestNamedSource:
    """A workset member relocated inside another box's workspace: refused unless --force."""

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("row", ["in_X", "in_XN"])
    def test_move(self, env, capsys, row, force):
        from kanibako.project.workset import add_project, load_workset
        std, area = env
        ws = load_workset(area / "wn", "wn", early_system=std.early_system)
        add_project(ws, "m", ws.require_workspaces_dir("m") / "m", std)
        capsys.readouterr()
        dest, what = _forceable_dests(std, area)[row]
        rc = _run("move", *(["--force"] if force else []), "wn/m", str(dest))
        err = capsys.readouterr().err
        if force:
            assert rc == 0, err
            return
        assert rc == 1
        assert f"Cannot move a box at '{dest}': it is {what}" in err, err
        assert SHARE in err
        assert not dest.exists()


class TestRepointedStandaloneStore:
    """§ 1 row 9 (³): a standalone's store repointed outside its root, reached through a link."""

    @pytest.mark.parametrize("force", [False, True])
    def test_move_into_it(self, env, capsys, force):
        import shutil

        from kanibako.settings.config_io import write_nested_key
        std, area = env
        root = area / "T"
        assert _create("--standalone", "--register", str(root)) == 0
        store = area / "tstore"
        write_nested_key(root / "workset.yaml", ("workset",), "boxes", str(store))
        shutil.move(str(root / "box_data"), str(store))
        (area / "tl").symlink_to(store)
        capsys.readouterr()
        dest = area / "tl" / "sub"
        rc = _relocate("mv", area / "s", dest, force=force)
        if force:
            assert rc == 0, capsys.readouterr().err
            return
        assert rc == 1
        err = capsys.readouterr().err
        assert "it is inside the store of standalone box" in err, err
        assert f"({store})" in err


class TestOwnStore:
    """⁵: the source's own store.  R! when the op moves or retires it; R/f when it stays."""

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("verb", ["cvS", "cvW"])
    def test_primary_converted_into_its_own_store(self, env, capsys, verb, force):
        std, area = env
        store = std.boxes / "s"
        dest = store / "w"
        assert _relocate(verb, area / "s", dest, force=force) == 1
        err = capsys.readouterr().err
        assert f"inside its own store ({store.resolve()})" in err, err
        assert "no flag overrides this" in err
        assert (area / "s" / "f.txt").read_text() == "data"
        assert not dest.exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_standalone_converted_into_its_own_store(self, env, capsys, force):
        std, area = env
        root = _standalone_source(area, capsys)
        dest = root / "box_data" / "w"
        assert _relocate("cvD", root, dest, force=force) == 1
        err = capsys.readouterr().err
        assert f"inside its own store ({(root / 'box_data').resolve()})" in err, err
        assert "no flag overrides this" in err
        assert (root / "workspace" / "f.txt").read_text() == "data"
        assert registry_store.standalone_name_for_root(std.registry, root) is not None

    @pytest.mark.parametrize("force", [False, True])
    def test_renaming_move_into_its_own_store(self, env, capsys, force):
        std, area = env
        store = std.boxes / "s"
        assert _relocate("mv", area / "s", store / "w", force=force, name="t") == 1
        err = capsys.readouterr().err
        assert f"inside its own store ({store.resolve()})" in err, err
        assert "no flag overrides this" in err

    @pytest.mark.parametrize("force", [False, True])
    def test_move_keeping_the_store(self, env, capsys, force):
        """Primary → primary, same name: the store stays, so --force passes it."""
        std, area = env
        store = std.boxes / "s"
        dest = store / "w"
        rc = _relocate("mv", area / "s", dest, force=force)
        if force:
            assert rc == 0, capsys.readouterr().err
            assert (dest / "f.txt").read_text() == "data"
            return
        assert rc == 1
        err = capsys.readouterr().err
        assert f"inside its own store ({store.resolve()}). A box's store is not its " in err, err
        assert (area / "s" / "f.txt").read_text() == "data"


def _nest(kind: str, std, inner: Path, capsys) -> str:
    """Make box *kind* (P, S, N, W) at *inner*; return the path text the refusal names."""
    if kind == "P":
        assert _create("--force", "--name", "i", str(inner)) == 0
    elif kind == "S":
        assert _create("--standalone", "--register", "--force", str(inner)) == 0
    elif kind == "N":
        import argparse

        from kanibako.commands.workset_cmd import run_connect
        inner.mkdir(parents=True)
        assert run_connect(argparse.Namespace(
            workset="wn", source=str(inner), project_name="n", force=True)) == 0
    else:
        from kanibako.project.workset import create_workset
        create_workset("w2", inner, std)
    capsys.readouterr()
    return str(inner)


class TestCarriesAnotherBox:
    """§ 2: a relocation whose source tree holds another box is refused, even with --force."""

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("kind", ["P", "S", "N", "W"])
    @pytest.mark.parametrize("verb", VERBS)
    def test_refused(self, env, capsys, verb, kind, force):
        std, area = env
        if verb == "cvD":
            src = _standalone_source(area, capsys, "o")
            inner = src / "workspace" / "inner"
        else:
            assert _create("--name", "o", str(area / "o")) == 0
            src = area / "o"
            inner = src / "inner"
        held = _nest(kind, std, inner, capsys)
        (inner / "keep.txt").write_text("theirs")
        dest = area / "d"
        assert _relocate(verb, src, dest, force=force) == 1
        err = capsys.readouterr().err
        assert f"({held})" in err, err
        assert "would carry it away and break that box, so no flag overrides this" in err, err
        assert not dest.exists()
        assert (inner / "keep.txt").read_text() == "theirs"


class TestVacatedRoot:
    """A standalone source's own root, outside its workspace and store, is no box's once the
    relocation leaves it: not another box's tree, so no --force is asked."""

    @pytest.mark.parametrize("verb", ["mv", "cvD"])
    def test_relocated_into_its_own_old_root(self, env, capsys, verb):
        std, area = env
        root = _standalone_source(area, capsys)
        dest = root / "sub"
        rc = _relocate(verb, root, dest, force=False)
        assert rc == 0, capsys.readouterr().err
        assert (dest / ("workspace" if verb == "mv" else "") / "f.txt").read_text() == "data"


class TestThroughALink:
    """Footnote ¹: a destination reached through a link to another box's workspace is inside
    it.  ``create`` and ``remap`` record the path as given ([R188]); the check resolves it."""

    @staticmethod
    def _held(area: Path, via: str) -> tuple[Path, str]:
        if via == "xlnk":
            return area / "xlnk", f"inside the workspace of primary box 'X' ({area / 'X'})"
        (area / "nlnk").symlink_to(area / "XN")
        return (area / "nlnk",
                f"inside the workspace of member 'XN' of workset 'wn' ({area / 'XN'})")

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("via", ["xlnk", "nlnk"])
    def test_create(self, env, capsys, via, force):
        _std_paths, area = env
        link, what = self._held(area, via)
        dest = link / "sub"
        rc = _create(*(["--force"] if force else []), "--name", "c", str(dest))
        if force:
            assert rc == 0, capsys.readouterr().err
            return
        assert rc == 1
        err = capsys.readouterr().err
        assert f"Cannot create a box at '{dest}': it is {what}. " + SHARE in err, err

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("via", ["xlnk", "nlnk"])
    def test_remap(self, env, capsys, via, force):
        _std_paths, area = env
        link, what = self._held(area, via)
        dest = link / "sub"
        dest.mkdir()
        rc = _run("remap", *(["--force"] if force else []), str(area / "s"), str(dest))
        if force:
            assert rc == 0, capsys.readouterr().err
            return
        assert rc == 1
        err = capsys.readouterr().err
        assert f"Cannot remap 's' onto '{dest}': it is {what}. " + SHARE in err, err


class TestKeptStoreIsNotCarried:
    """A standalone's store its own ``workset.yaml`` repoints outside the root is the user's
    directory: the relocation keeps it, so a box inside it is not carried away."""

    def test_box_in_a_kept_store(self, env, capsys):
        import shutil

        from kanibako.settings.config_io import write_nested_key
        std, area = env
        root = _standalone_source(area, capsys)
        store = area / "ustore"
        write_nested_key(root / "workset.yaml", ("workset",), "boxes", str(store))
        shutil.move(str(root / "box_data"), str(store))
        assert _create("--force", "--name", "i", str(store / "inner")) == 0
        (store / "inner" / "keep.txt").write_text("theirs")
        capsys.readouterr()
        dest = area / "d"
        assert _relocate("cvD", root, dest, force=False) == 0, capsys.readouterr().err
        assert (store / "inner" / "keep.txt").read_text() == "theirs"
        assert (dest / "f.txt").read_text() == "data"
