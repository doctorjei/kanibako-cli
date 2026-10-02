"""``box rm`` must NEVER act on a workset — the data-loss cure.

⚑⚑ WHY THE COMMAND UNDER TEST RUNS IN A SUBPROCESS.  The defect was DATA LOSS:
``box rm <workset-name> --purge`` resolved the ``worksets`` section, unregistered
the workset, and then tore down ``std.boxes / <name>`` — a PRIMARY box's data
dir.  A test that patches the teardown helper proves nothing about the files on
disk, so each test below runs the REAL CLI (``python -m kanibako``, the real
entry point) against an isolated ``HOME``/``XDG_*`` tree and then asserts on the
FILESYSTEM.

⚑ SETUP GOES THROUGH THE PRODUCT'S OWN WRITERS, never through hand-built
directories: ``resolve_project`` (real box + real metadata under ``std.boxes``),
``create_workset`` (real workset tree + real registry entry),
``unregister_primary_box_name`` (a real membership drop).  The orphan state in
the first test is the one the product itself names — ``box list --orphans``: a
populated box data dir whose membership entry is gone.

⚑ WHY THE LOSS NEEDS THAT STATE.  A *registered* primary box always wins the
name lookup in ``run_rm`` (the membership is consulted first), so with both
live the old code took the box branch and no workset data was touched.  The
branch destroyed ``std.boxes / <name>`` for a name that resolved to a workset,
which is a box dir whose membership entry is missing.

Before the cure the first test failed: rc 0, the workset unregistered, and
``MARKER.txt`` deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# The tree under test — the SUBPROCESS must import THIS src, not an installed
# checkout, so the fix is what actually runs.
REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Build an isolated HOME/XDG tree + a default config; return the child env."""
    home = tmp_path / "home"
    dirs = {
        name: tmp_path / name
        for name in ("home", "config", "data", "state", "cache", "runtime")
    }
    for d in dirs.values():
        d.mkdir()
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_DATA_HOME": str(dirs["data"]),
        "XDG_STATE_HOME": str(dirs["state"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
        # Without it the CLI falls back to a fresh /tmp runtime dir per process
        # and prints a warning over the output the assertions read.
        "XDG_RUNTIME_DIR": str(dirs["runtime"]),
        "PYTHONPATH": str(REPO_SRC),
    })
    # The in-process setup helpers below must resolve against the SAME tree the
    # subprocesses use, so install it here too.
    for key, value in env.items():
        if key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                   "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            monkeypatch.setenv(key, value)

    from kanibako.settings.config import write_global_config
    from tests.support.filenames import CONFIG_FILENAME

    write_global_config(dirs["config"] / CONFIG_FILENAME)
    return env


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run the REAL CLI (``python -m kanibako``) in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "kanibako", *args],
        env=env, capture_output=True, text=True, timeout=300, check=False,
    )


def _std():
    """The REAL resolved paths for the tree the env points at."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(user_config_file()))


def _workset_names() -> list[str]:
    from kanibako.project.names import read_names

    return sorted(read_names(_std().registry)["worksets"])


def _primary_names() -> list[str]:
    from kanibako.settings.paths import load_primary_boxes

    return sorted(load_primary_boxes(_std().primary_workset))


def _make_box(name: str, root: Path) -> Path:
    """Create a REAL primary box *name* (data under ``std.boxes/<name>``).

    Must run BEFORE the workset of the same name: a box created second is
    refused, which is what makes the shadowing an explicit, sanctioned state
    (``workset create --force``) rather than an accident.
    """
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import resolve_project

    root.mkdir(parents=True, exist_ok=True)
    config = load_config(user_config_file())
    resolve_project(
        _std(), config, project_dir=str(root), initialize=True, name_override=name,
    )
    return _std().boxes / name


def _make_workset(name: str, root: Path, *, force: bool = False) -> Path:
    """Create a REAL workset *name* and register it in the global index."""
    from kanibako.project.workset import create_workset

    create_workset(name, root, _std(), force=force)
    return root


def _snapshot(root: Path) -> list[str]:
    """Sorted top-level names under *root* — the workset's own files."""
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def _drop_membership(name: str) -> None:
    """Drop the box's PRIMARY membership entry, leaving its data in place.

    The real orphan state. Uses the product's own writer — the same call
    ``run_rm`` makes — so nothing here is a hand-built directory.
    """
    from kanibako.settings.paths import unregister_primary_box_name

    unregister_primary_box_name(_std().primary_workset, name)


def test_rm_of_a_workset_name_refuses_and_keeps_the_box_data_sharing_the_name(
    tmp_path, monkeypatch,
):
    """DATA LOSS pin: `box rm <workset> --purge` must not touch `std.boxes/<name>`.

    Before the cure: rc 0, the workset unregistered, and the marker DELETED.
    """
    env = _make_env(tmp_path, monkeypatch)
    boxes = _make_box("foo", tmp_path / "box" / "foo")
    ws_root = _make_workset("foo", tmp_path / "ws" / "foo", force=True)
    _drop_membership("foo")                       # -> the orphan state
    marker = boxes / "MARKER.txt"
    marker.write_text("user data a workset rm must not delete\n")
    ws_files = _snapshot(ws_root)
    assert marker.is_file()

    res = _cli(env, "box", "rm", "foo", "--purge", "--force")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "not a registered box" in res.stderr
    assert marker.is_file(), "box rm of a workset name DELETED a box's data"
    assert boxes.is_dir()
    assert _workset_names() == ["foo"]
    assert _snapshot(ws_root) == ws_files


def test_rm_of_a_deregistered_box_purges_it_and_leaves_the_workset_alone(
    tmp_path, monkeypatch,
):
    """The name routes to the BOX: retained data goes, the workset stays put.

    Before the cure this printed "Removing workset: foo" and unregistered the
    workset while purging the box — the wrong carrier for both halves.
    """
    env = _make_env(tmp_path, monkeypatch)
    boxes = _make_box("foo", tmp_path / "box" / "foo")
    ws_root = _make_workset("foo", tmp_path / "ws" / "foo", force=True)
    # Deregister the box the supported way — its metadata is RETAINED.
    assert _cli(env, "box", "rm", "foo").returncode == 0
    marker = boxes / "MARKER.txt"
    marker.write_text("retained box data\n")
    ws_files = _snapshot(ws_root)

    res = _cli(env, "box", "rm", "foo", "--purge", "--force")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not marker.exists(), "the box's own purge must delete its data"
    assert _workset_names() == ["foo"]
    assert _snapshot(ws_root) == ws_files


def test_rm_of_a_workset_only_name_is_the_ordinary_not_found_refusal(
    tmp_path, monkeypatch,
):
    """A name that is only a workset: the plain refusal, rc 1, nothing changed.

    No workset lookup and no ``workset rm`` hint — the maintainer ruled the
    existing not-found path is the whole answer.
    """
    env = _make_env(tmp_path, monkeypatch)
    ws_root = _make_workset("bar", tmp_path / "ws" / "bar")
    ws_files = _snapshot(ws_root)
    assert _workset_names() == ["bar"]

    res = _cli(env, "box", "rm", "bar")

    assert res.returncode == 1
    assert "not a registered box" in res.stderr
    assert "workset rm" not in (res.stdout + res.stderr)
    assert _workset_names() == ["bar"], "box rm unregistered a workset"
    assert _snapshot(ws_root) == ws_files


def test_rm_of_a_name_shared_with_a_workset_removes_the_box_only(
    tmp_path, monkeypatch,
):
    """Both registered: the BOX goes (data included), the workset stays."""
    env = _make_env(tmp_path, monkeypatch)
    boxes = _make_box("foo", tmp_path / "box" / "foo")
    ws_root = _make_workset("foo", tmp_path / "ws" / "foo", force=True)
    marker = boxes / "MARKER.txt"
    marker.write_text("box data\n")
    ws_files = _snapshot(ws_root)
    assert _workset_names() == ["foo"] and _primary_names() == ["foo"]

    res = _cli(env, "box", "rm", "foo", "--purge", "--force")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not marker.exists(), "--purge must still delete the BOX's data"
    assert not boxes.is_dir()
    assert _primary_names() == []
    assert _workset_names() == ["foo"]
    assert _snapshot(ws_root) == ws_files


def test_workset_rm_still_removes_the_workset_of_a_shared_name(
    tmp_path, monkeypatch,
):
    """`workset rm` is the one carrier for workset removal — it still works."""
    env = _make_env(tmp_path, monkeypatch)
    boxes = _make_box("foo", tmp_path / "box" / "foo")
    ws_root = _make_workset("foo", tmp_path / "ws" / "foo", force=True)
    marker = boxes / "MARKER.txt"
    marker.write_text("box data\n")

    res = _cli(env, "workset", "rm", "foo", "--force")

    assert res.returncode == 0, res.stdout + res.stderr
    assert _workset_names() == []
    assert _primary_names() == ["foo"]
    assert marker.is_file(), "workset rm touched the box that shares the name"
