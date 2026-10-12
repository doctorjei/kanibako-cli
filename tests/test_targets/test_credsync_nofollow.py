"""The credential sync never follows a symlink in a box (no-follow patch, lane 1).

Per site, the proof matrix: a link at the LEAF and at a PARENT, pointing outside the
box tree at a sentinel host file and inside the tree; the sentinel's hash is
unchanged, the warning names the path, and the run carries on.  A no-link control
shows the sync unchanged.  Sites: the seed and refresh into the box (H1), the
directory writeback out of it (B1), and the creds-dirty flag (X2).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
from pathlib import Path

import pytest

from kanibako import boxfs
from kanibako.launch.creds_watcher import (
    clear_creds_dirty,
    creds_dirty_flag_path,
    read_creds_dirty,
)
from kanibako.targets.base import (
    AgentInstall,
    Cadence,
    CredFileSpec,
    Mount,
    PluginDescriptor,
    Target,
)
from kanibako.targets.credsync import (
    refresh_cred_files,
    seed_cred_files,
    writeback_cred_files,
)

FILE = ".creds/token.json"      # unfiltered
MERGED = ".creds/merged.json"   # filtered (transform_cred)
TREE = ".creds/providers"       # is_dir
RELS = (FILE, MERGED, TREE)

DESC = PluginDescriptor(
    command=("stub",),
    bindings=(),
    mode={},
    init_dirs=(),
    auth_share_support=True,
    cred_files=(
        CredFileSpec(home_rel=FILE, host_rel=FILE, cadence=Cadence.SYNC,
                     mtime_gate=True, filtered=False),
        CredFileSpec(home_rel=MERGED, host_rel=MERGED, cadence=Cadence.SYNC,
                     mtime_gate=True, filtered=True),
        CredFileSpec(home_rel=TREE, host_rel=TREE, cadence=Cadence.SYNC,
                     mtime_gate=True, filtered=False, is_dir=True),
    ),
)


class _Target(Target):
    """transform_cred tags its output with the direction and keeps what *dst* held."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def display_name(self) -> str:
        return "Stub"

    def detect(self) -> AgentInstall | None:
        return None

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        return []

    def build_cli_args(
        self, *, safe_mode: bool, resume_mode: bool, new_session: bool,
        is_new_project: bool, extra_args: list[str],
    ) -> list[str]:
        return []

    def transform_cred(self, spec: CredFileSpec, src: Path | None, dst: Path,
                       direction: str) -> None:
        before = dst.read_text() if dst.is_file() else ""
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f"{direction}:{src.read_text() if src else 'none'}|{before}")


def _digest(root: Path) -> dict[str, str]:
    """Every file under *root* and its hash; every link and its text."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            out[str(p)] = "->" + str(p.readlink())
        elif p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _fill(base: Path, text: str) -> None:
    """The three spec paths under *base*: two files and a dir with one file."""
    (base / FILE).parent.mkdir(parents=True, exist_ok=True)
    (base / FILE).write_text(text)
    (base / MERGED).write_text(text)
    (base / TREE).mkdir(exist_ok=True)
    (base / TREE / "p.json").write_text(text)


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    host, proj, outside = tmp_path / "host", tmp_path / "proj", tmp_path / "outside"
    _fill(host, "HOST")
    proj.mkdir()
    outside.mkdir()
    return {"host": host, "proj": proj, "outside": outside}


def _plant(world: dict[str, Path], where: str, to: str, rel: str) -> tuple[Path, Path]:
    """Plant a link for *rel*; return (the link, the tree holding its target).

    *where*: ``leaf`` links the spec path itself, ``parent`` links ``.creds``.
    *to*: ``outside`` the box (a sentinel host tree) or ``inside`` it.
    """
    proj = world["proj"]
    held = world["outside"] if to == "outside" else proj / "elsewhere"
    _fill(held, "SENTINEL")
    if where == "leaf":
        link = proj / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(held / rel)
    else:
        link = proj / ".creds"
        link.symlink_to(held / ".creds")
    return link, held


# --- H1: seed and refresh into the box ---------------------------------------------


_INTO = [seed_cred_files, refresh_cred_files]


@pytest.mark.parametrize("prim", _INTO, ids=lambda f: f.__name__)
def test_into_box_control_writes_every_spec(world, prim) -> None:
    proj = world["proj"]
    prim(DESC, _Target(), source_root=world["host"], project_home=proj)
    assert (proj / FILE).read_text() == "HOST"
    assert (proj / MERGED).read_text() == "in:HOST|"
    assert (proj / TREE / "p.json").read_text() == "HOST"
    for p in (proj / FILE, proj / MERGED, proj / TREE / "p.json"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_refresh_hands_the_plugin_the_box_content_not_the_box_path(world) -> None:
    proj = world["proj"]
    (proj / ".creds").mkdir()
    (proj / MERGED).write_text("BOX")
    os.utime(proj / MERGED, (1, 1))   # older than the host file: the gate lets it run
    refresh_cred_files(DESC, _Target(), source_root=world["host"], project_home=proj)
    assert (proj / MERGED).read_text() == "in:HOST|BOX"


@pytest.mark.parametrize("prim", _INTO, ids=lambda f: f.__name__)
@pytest.mark.parametrize("to", ["outside", "inside"])
@pytest.mark.parametrize("rel", RELS)
def test_into_box_leaf_link_is_skipped(world, prim, to, rel, caplog) -> None:
    proj = world["proj"]
    link, held = _plant(world, "leaf", to, rel)
    before = _digest(held)
    with caplog.at_level(logging.WARNING):
        prim(DESC, _Target(), source_root=world["host"], project_home=proj)
    assert _digest(held) == before
    assert link.is_symlink() and link.readlink() == held / rel
    assert str(link) in caplog.text and "symlink" in caplog.text
    # The other specs still ran.
    for other in set(RELS) - {rel}:
        assert not (proj / other).is_symlink() and (proj / other).exists()


@pytest.mark.parametrize("prim", _INTO, ids=lambda f: f.__name__)
@pytest.mark.parametrize("to", ["outside", "inside"])
def test_into_box_parent_link_skips_every_path_under_it(world, prim, to, caplog) -> None:
    proj = world["proj"]
    link, held = _plant(world, "parent", to, FILE)
    before = _digest(held)
    with caplog.at_level(logging.WARNING):
        prim(DESC, _Target(), source_root=world["host"], project_home=proj)
    assert _digest(held) == before
    assert link.is_symlink()
    for rel in RELS:
        assert str(proj / rel) in caplog.text


@pytest.mark.parametrize("prim", _INTO, ids=lambda f: f.__name__)
def test_into_box_link_planted_after_the_check_is_skipped(world, prim, monkeypatch,
                                                          caplog) -> None:
    """The race: the check passes, then the box swaps in a link; boxfs refuses it."""
    proj = world["proj"]
    link, held = _plant(world, "parent", "outside", FILE)
    before = _digest(held)
    monkeypatch.setattr(boxfs, "no_symlink_or_warn", lambda path, root: True)
    with caplog.at_level(logging.WARNING):
        prim(DESC, _Target(), source_root=world["host"], project_home=proj)
    assert _digest(held) == before
    assert str(link) in caplog.text


def test_seed_init_dir_link_is_skipped(world, caplog) -> None:
    proj = world["proj"]
    (world["outside"] / "d").mkdir()
    (proj / ".creds").symlink_to(world["outside"] / "d")
    desc = PluginDescriptor(command=("stub",), bindings=(), mode={},
                            init_dirs=(".creds/sub",), auth_share_support=True,
                            cred_files=())
    with caplog.at_level(logging.WARNING):
        seed_cred_files(desc, _Target(), source_root=None, project_home=proj)
    assert list((world["outside"] / "d").iterdir()) == []
    assert str(proj / ".creds/sub") in caplog.text


# --- B1: the directory writeback out of the box ------------------------------------


def _box_newer(proj: Path) -> None:
    _fill(proj, "BOXNEW")
    (proj / MERGED).write_text("BOXNEW")


def test_writeback_control_mirrors_the_tree(world) -> None:
    proj, host = world["proj"], world["host"]
    _box_newer(proj)
    assert writeback_cred_files(DESC, _Target(), source_root=host, project_home=proj)
    assert (host / TREE / "p.json").read_text() == "BOXNEW"
    assert stat.S_IMODE((host / TREE / "p.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("where", ["leaf", "parent"])
@pytest.mark.parametrize("to", ["outside", "inside"])
def test_writeback_tree_link_is_never_read(world, where, to, caplog) -> None:
    proj, host = world["proj"], world["host"]
    link, held = _plant(world, where, to, TREE)
    before = _digest(held)
    host_before = _digest(host)
    with caplog.at_level(logging.WARNING):
        assert writeback_cred_files(DESC, _Target(), source_root=host, project_home=proj)
    assert _digest(held) == before
    assert _digest(host) == host_before   # nothing the link names reached the store
    assert str(proj / TREE) in caplog.text and "symlink" in caplog.text


def test_writeback_tree_with_a_host_side_link_still_follows_it(world) -> None:
    """The store is the user's: a link there is followed, as before."""
    proj, host = world["proj"], world["host"]
    real = world["outside"] / "real-providers"
    real.mkdir()
    shutil.rmtree(host / TREE)
    (host / TREE).symlink_to(real)
    _box_newer(proj)
    assert writeback_cred_files(DESC, _Target(), source_root=host, project_home=proj)
    assert (host / TREE).is_symlink()
    assert (real / "p.json").read_text() == "BOXNEW"


# --- X2: the creds-dirty flag ---------------------------------------------------------


def test_clear_flag_control(tmp_path: Path) -> None:
    flag = creds_dirty_flag_path(tmp_path)
    flag.parent.mkdir()
    flag.write_text("1")
    assert read_creds_dirty(tmp_path)
    clear_creds_dirty(tmp_path)
    assert not flag.exists() and not read_creds_dirty(tmp_path)


@pytest.mark.parametrize("to", ["outside", "inside"])
def test_clear_flag_parent_link_is_not_followed(tmp_path: Path, to, caplog) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    held = tmp_path / "outside" if to == "outside" else proj / "elsewhere"
    held.mkdir()
    (held / "creds-dirty").write_text("SENTINEL")
    (proj / ".kanibako").symlink_to(held)
    with caplog.at_level(logging.WARNING):
        assert not read_creds_dirty(proj)
        clear_creds_dirty(proj)
    assert (held / "creds-dirty").read_text() == "SENTINEL"
    assert str(creds_dirty_flag_path(proj)) in caplog.text


@pytest.mark.parametrize("to", ["outside", "inside"])
def test_clear_flag_leaf_link_removes_the_link_only(tmp_path: Path, to) -> None:
    proj = tmp_path / "proj"
    (proj / ".kanibako").mkdir(parents=True)
    held = tmp_path / "outside" if to == "outside" else proj / "elsewhere"
    held.mkdir()
    (held / "secret").write_text("SENTINEL")
    flag = creds_dirty_flag_path(proj)
    flag.symlink_to(held / "secret")
    assert not read_creds_dirty(proj)
    clear_creds_dirty(proj)
    assert not flag.is_symlink() and not flag.exists()
    assert (held / "secret").read_text() == "SENTINEL"
