"""``box duplicate`` refuses a copy placed on another box's tree (keyspace § 0).

"Per-owner resources": a workspace inside another box's workspace, root, or store, or
holding another box's, is refused unless --force (one --force, as remap's: plan
``176-step4b-matrix.md`` Q-B).  Another box's exact workspace, a standalone root, or a link
to one is refused whatever the flag, as is a landing in a workset's path space ([PS]) and a
standalone copy rooted in its source's own store (⁵).  The cells are the plan's § 1 ``dp``
and ``dpS`` columns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._duplicate import run_duplicate
from kanibako.commands.box._parser import run_create

SHARE = "Both boxes would claim it: pass --force to share it, or choose another path."
NO_FLAG = "no flag overrides this"


def _create(*argv: str) -> int:
    return run_create(build_parser().parse_args(["box", "create", *argv]))


def _dup(*argv: str) -> int:
    return run_duplicate(build_parser().parse_args(["box", "duplicate", *argv]))


def _refused_at(err: str, dest: Path) -> bool:
    """The refusal names *dest*, as typed or (a link judged where it leads) resolved."""
    return any(f"Error: Cannot duplicate a box at '{p}': it is " in err
               for p in (dest, dest.resolve()))


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    return load_std_paths(load_config(config_file))


@pytest.fixture
def world(config_file, credentials_dir, tmp_home, monkeypatch):
    """The plan's § 1 fixtures, plus the duplicate source ``dupsrc``."""
    from kanibako.project.workset import add_project, create_workset

    monkeypatch.chdir(tmp_home)
    monkeypatch.setenv("PWD", str(tmp_home))
    monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda *_a, **_k: None)
    area = tmp_home / "nest"
    std = _std(config_file)
    assert _create("--name", "X", str(area / "X")) == 0
    assert _create("--standalone", "--register", str(area / "XS")) == 0
    ws = create_workset("WN", area / "wn", std)
    (area / "XN").mkdir(parents=True)
    add_project(ws, "XN", area / "XN", std)
    assert _create("--name", "Y", str(area / "hold" / "inner")) == 0
    assert _create("--name", "dupsrc", str(area / "dupsrc")) == 0
    (area / "dupsrc" / "f.txt").write_text("src")
    (area / "xlnk").symlink_to(area / "X")
    (area / "rlnk").symlink_to(area / "XS")
    (area / "slnk").symlink_to(area / "XS" / "box_data")
    return area, std, ws


# label -> destination, relative to the area (``std.boxes`` for a primary store)
FORCEABLE = {
    "in X's workspace": lambda a, s, w: a / "X" / "sub",
    "in X's workspace via xlnk": lambda a, s, w: a / "xlnk" / "sub",
    "in XS/workspace": lambda a, s, w: a / "XS" / "workspace" / "sub",
    "in XS root": lambda a, s, w: a / "XS" / "sub",
    "in XS root via rlnk": lambda a, s, w: a / "rlnk" / "sub",
    "in XN": lambda a, s, w: a / "XN" / "sub",
    "in X's store": lambda a, s, w: s.boxes / "X" / "sub",
    "in XS/box_data": lambda a, s, w: a / "XS" / "box_data" / "sub",
    "in XS/box_data via slnk": lambda a, s, w: a / "slnk" / "sub",
    "= hold": lambda a, s, w: a / "hold",
}
UNFORCEABLE = {
    "= X": lambda a, s, w: a / "X",
    "= XS/workspace": lambda a, s, w: a / "XS" / "workspace",
    "= XS": lambda a, s, w: a / "XS",
    "= rlnk": lambda a, s, w: a / "rlnk",
    "= XN": lambda a, s, w: a / "XN",
}
PATH_SPACE = {
    "in XN's store": lambda a, s, w: w.projects_dir / "XN" / "sub",
    "in wn": lambda a, s, w: a / "wn" / "sub",
}
VERBS = {"dp": ["--name", "n"], "dpS": ["--to", "standalone"]}


# ``= hold`` with ``--to standalone`` is left out: the cross-mode route runs only the
# unforceable arms before "already exists", which already refused it without --force.
@pytest.mark.parametrize(("cell", "verb"), [
    (cell, verb) for cell in FORCEABLE for verb in VERBS if (cell, verb) != ("= hold", "dpS")])
def test_forceable_cell_needs_force(world, capsys, verb, cell):
    area, std, ws = world
    dest = FORCEABLE[cell](area, std, ws)
    existed = dest.exists()
    assert _dup(*VERBS[verb], str(area / "dupsrc"), str(dest)) == 1
    err = capsys.readouterr().err
    assert _refused_at(err, dest), err
    assert SHARE in err, err
    assert dest.exists() == existed  # refused before any write
    assert _dup("--force", *VERBS[verb], str(area / "dupsrc"), str(dest)) == 0, (
        capsys.readouterr().err)


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("cell", UNFORCEABLE)
def test_unforceable_cell_refused_whatever_the_flag(world, capsys, verb, force, cell):
    area, std, ws = world
    dest = UNFORCEABLE[cell](area, std, ws)
    kuid_doc = (area / "XS" / "workset.yaml").read_text()
    rc = _dup(*(["--force"] if force else []), *VERBS[verb], str(area / "dupsrc"), str(dest))
    assert rc == 1
    err = capsys.readouterr().err
    assert _refused_at(err, dest), err
    assert NO_FLAG in err, err
    assert "Use --force to overwrite" not in err  # the E cure no longer offers --force
    assert (area / "XS" / "workset.yaml").read_text() == kuid_doc  # ⁴: XS keeps its identity
    assert not (dest / "f.txt").exists()


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("cell", PATH_SPACE)
def test_workset_path_space_refused_whatever_the_flag(world, capsys, verb, force, cell):
    """Q-A (1): the path-space rule wins over the store's R/f."""
    area, std, ws = world
    dest = PATH_SPACE[cell](area, std, ws)
    rc = _dup(*(["--force"] if force else []), *VERBS[verb], str(area / "dupsrc"), str(dest))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Refusing to land the project inside workset 'WN'" in err, err
    assert not dest.exists()


@pytest.mark.parametrize("force", [False, True])
def test_unregistered_standalone_root_refused(world, capsys, force):
    area, _std_, _ws = world
    root = area / "US"
    assert _create("--standalone", str(root)) == 0
    capsys.readouterr()
    doc = (root / "workset.yaml").read_text()
    rc = _dup(*(["--force"] if force else []), "--to", "standalone",
              str(area / "dupsrc"), str(root))
    assert rc == 1
    err = capsys.readouterr().err
    assert "it is the root of an unregistered standalone box" in err, err
    assert (root / "workset.yaml").read_text() == doc


class TestOwnStore:
    """⁵: a standalone copy rooted in its source's store copies that store into itself."""

    @pytest.mark.parametrize("force", [False, True])
    def test_dps_into_a_primary_sources_store(self, world, capsys, force):
        area, std, _ws = world
        dest = std.boxes / "dupsrc" / "x"
        rc = _dup(*(["--force"] if force else []), "--to", "standalone",
                  str(area / "dupsrc"), str(dest))
        assert rc == 1
        err = capsys.readouterr().err
        assert "it is inside the source box's own store" in err, err
        assert NO_FLAG in err
        assert not dest.exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_dps_into_a_standalone_sources_store(self, world, capsys, force):
        area, _std_, _ws = world
        src = area / "SS"
        assert _create("--standalone", "--register", str(src)) == 0
        capsys.readouterr()
        dest = src / "box_data" / "x"
        rc = _dup(*(["--force"] if force else []), str(src), str(dest))
        assert rc == 1
        err = capsys.readouterr().err
        assert "it is inside the source box's own store" in err, err
        assert not dest.exists()

    def test_dp_into_its_sources_store_is_another_boxs_store(self, world, capsys):
        """To the copy, ``boxes/dupsrc`` is another box's store: R/f."""
        area, std, _ws = world
        dest = std.boxes / "dupsrc" / "x"
        assert _dup("--name", "n", str(area / "dupsrc"), str(dest)) == 1
        err = capsys.readouterr().err
        assert "it is inside the store of primary box 'dupsrc'" in err, err
        assert _dup("--force", "--name", "n", str(area / "dupsrc"), str(dest)) == 0


class TestWorksetRoutes:
    def test_to_named_judges_the_members_leaf(self, world, capsys):
        from kanibako.project.workset import create_workset
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        area, std, _ws = world
        ws2 = create_workset("W2", area / "w2", std)
        meta = ws2.root / WORKSET_META_FILE
        doc = load_doc(meta) or {}
        doc.setdefault("workset", {})["workspaces"] = str(area / "X" / "wsp")
        dump_doc(meta, doc)
        leaf = area / "X" / "wsp" / "m"
        argv = ["--to", "named", "--workset", "W2", "--name", "m", str(area / "dupsrc"), "-"]
        assert _dup(*argv) == 1
        err = capsys.readouterr().err
        assert (f"Error: Cannot duplicate a box at '{leaf}': it is inside the workspace of "
                f"primary box 'X' ({area / 'X'}). " + SHARE) in err, err
        assert not leaf.exists()
        assert _dup("--force", *argv) == 0, capsys.readouterr().err
        assert (leaf / "f.txt").read_text() == "src"

    @pytest.mark.parametrize("to", ["primary", "standalone"])
    def test_from_named_judges_the_destination(self, world, capsys, to):
        area, _std_, ws = world
        assert _dup("--force", "--to", "named", "--workset", "WN", "--name", "m1",
                    str(area / "dupsrc"), "-") == 0
        member = ws.workspaces_dir / "m1"
        capsys.readouterr()
        dest = area / "X" / "sub"
        name = ["--name", "c"] if to == "primary" else []
        assert _dup("--to", to, *name, str(member), str(dest)) == 1
        err = capsys.readouterr().err
        assert (f"Error: Cannot duplicate a box at '{dest}': it is inside the workspace of "
                f"primary box 'X' ({area / 'X'}). " + SHARE) in err, err
        assert _dup("--force", "--to", to, *name, str(member), str(dest)) == 0
        for force in ([], ["--force"]):
            assert _dup(*force, "--to", to, *name, str(member), str(area / "X")) == 1
            assert NO_FLAG in capsys.readouterr().err


def test_every_entry_path_asks_the_one_judge(world, capsys, monkeypatch):
    """``run_duplicate``, the cross-mode route, the to-workset leaf, and the from-workset
    route all ask ``_dest_nesting_refusal`` with ``verb="duplicate"``."""
    from kanibako.commands.box import _lifecycle
    area, _std_, ws = world
    seen: list[Path] = []
    real = _lifecycle._dest_nesting_refusal

    def spy(std, **kw):
        assert kw["verb"] == "duplicate"
        seen.append(kw["dest"])
        return real(std, **kw)

    monkeypatch.setattr(_lifecycle, "_dest_nesting_refusal", spy)
    src = str(area / "dupsrc")
    assert _dup("--force", "--name", "a", src, str(area / "a")) == 0
    assert area / "a" in seen
    assert _dup("--force", "--to", "standalone", src, str(area / "b")) == 0
    assert area / "b" in seen
    assert _dup("--force", "--to", "named", "--workset", "WN", "--name", "c", src, "-") == 0
    assert ws.workspaces_dir / "c" in seen
    seen.clear()
    assert _dup("--force", "--to", "primary", "--name", "d",
                str(ws.workspaces_dir / "c"), str(area / "d")) == 0
    assert area / "d" in seen
