"""The bare ``box duplicate <src> <dst>`` box tier — the third door onto the shape guard.

``refuse_scalar_sections`` answers one stored ``box`` value one way however the file is
reached, so a value where the ``box`` table belongs is an ERROR that names the offending
key (spec §0, *Namespace*; §0's "Anything outside it is REJECTED: reading, setting, or
resolving an undeclared key is an ERROR that NAMES the offending key — never a silent
accept, never a fabricated default, never a free-form passthrough").

This door copies the source's metadata VERBATIM, so the new box's box tier IS the source's
file: the shape has to be settled before the copy, while a refusal can still land with
nothing written.  Asserted through the REAL CLI in a subprocess (isolated
``HOME``/``XDG_*``, assertions on the FILESYSTEM), because a unit test of a helper cannot
show whether the production call site is wired to the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: The subprocess must import THIS src, not an installed checkout.
REPO_SRC = Path(__file__).resolve().parents[1] / "src"

#: The non-table shapes that must refuse.  ``refuse_scalar_sections`` answers from
#: ``isinstance``, so these are a pin on the RULE, not an inventory; the all-shapes
#: inventory is pinned once, by ``carried_box_settings``'s own suite.
NON_TABLE_BOX_BODIES = [
    pytest.param("box: /x\n", id="path-string"),
    pytest.param("box:\n", id="null"),
    pytest.param("box: [[image, ubuntu]]\n", id="list-of-pairs"),
]


def _make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Build an isolated HOME/XDG tree + a default config; return the child env."""
    dirs = {n: tmp_path / n for n in (
        "home", "config", "data", "state", "cache", "runtime", "ws")}
    for d in dirs.values():
        d.mkdir()
    env = os.environ.copy()
    env.update({
        "HOME": str(dirs["home"]),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_DATA_HOME": str(dirs["data"]),
        "XDG_STATE_HOME": str(dirs["state"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
        "XDG_RUNTIME_DIR": str(dirs["runtime"]),
        "PYTHONPATH": str(REPO_SRC),
    })
    for key, value in env.items():
        if key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                   "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            monkeypatch.setenv(key, value)

    from kanibako.settings.config import write_global_config
    from tests.support.filenames import CONFIG_FILENAME

    write_global_config(dirs["config"] / CONFIG_FILENAME)
    return env


def _cli(env: dict[str, str], *args: str,
         stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the REAL CLI (``python -m kanibako``) in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "kanibako", *args],
        env=env, capture_output=True, text=True, timeout=300, check=False,
        input=stdin,
    )


def _std():
    """The REAL resolved paths for the tree the env points at."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(user_config_file()))


def _primary_index() -> dict[str, str]:
    """The REAL primary name index, as the lifecycle commands read it."""
    from kanibako.settings.paths import BoxMode, _early_scope, load_primary_boxes

    std = _std()
    return load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))


def _make_box(name: str, root: Path) -> Path:
    """Create a REAL primary box *name* through the product's own writer."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import resolve_project

    root.mkdir(parents=True, exist_ok=True)
    resolve_project(_std(), load_config(user_config_file()),
                    project_dir=str(root), initialize=True, name_override=name)
    return _std().boxes / name


def _expected_refusal(tier: Path, body: str) -> str:
    """The refusal wording ``refuse_scalar_sections`` produces for *body*."""
    from kanibako.settings.config_io import load_doc, render_stored_scalar

    stored = render_stored_scalar(load_doc(_write(tier, body))["box"])
    return (
        f"Error: the config file {tier} holds {stored} at 'box', where a table of keys "
        f"belongs, so 'box.' keys cannot be written under it. "
        f"Fix or delete 'box' in that file by hand, then retry."
    )


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


class TestBareDuplicateAsksTheShapeGuard:
    """A malformed source ``box`` refuses, and the refusal writes nothing."""

    @pytest.mark.parametrize("body", NON_TABLE_BOX_BODIES)
    def test_a_non_table_box_refuses_before_anything_is_written(
        self, tmp_path, monkeypatch, body,
    ):
        """The refusal lands with no workspace copy, no name and no metadata written.

        ⚑ THE BOUNDARY.  This path copies the metadata verbatim, so the new box's box
        tier is the source's file: a value where the ``box`` table belongs would land
        there as the new box's authored settings.  The guard is asked before the
        workspace copy, the name registration and the metadata copy, so nothing is
        unwound and nothing needs to be.
        MUTATION: delete the ``refuse_scalar_sections`` call in ``run_duplicate`` and
        every row here reds with rc 0 and a destination holding the value; move the call
        below ``assign_primary_box_name`` and it reds on the index.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        (src / "file.txt").write_text("payload\n")
        _write(tier, body)
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert res.stderr.strip() == _expected_refusal(tier, body)
        assert tier.read_text() == body, "the malformed file was rewritten"
        assert not dst.exists(), "the refusal left the workspace copy behind"
        assert not (_std().boxes / "dstbox").exists()
        assert set(_primary_index()) == {"srcbox"}, "the refusal left a name behind"

    def test_a_list_of_pairs_is_not_carried_into_the_new_box(self, tmp_path, monkeypatch):
        """``box: [[image, ubuntu]]`` refuses — a sequence of pairs is not a table.

        ⚑ THE ROW THAT DECIDES IT.  A sequence of pairs is coercible to a table, so a
        copy that accepts it reads settings out of a shape the keyspace does not define,
        and the new box inherits them as authored.
        MUTATION: coerce the source ``box`` to a table before the guard asks and this
        reds with rc 0.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box: [[image, ubuntu]]\n")
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "at 'box', where a table of keys belongs" in res.stderr
        assert not (_std().boxes / "dstbox").exists()

    def test_force_does_not_buy_a_malformed_box_tier(self, tmp_path, monkeypatch):
        """``--force`` cannot carry a value, and the occupied destination survives.

        ⚑ ``--force`` removes the destination's box store before copying, so a refusal
        that arrived late would destroy the box it was protecting.
        MUTATION: move the guard below the ``--force`` removal and this reds with the
        retained data gone.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box: /x\n")
        dst = tmp_path / "ws" / "dstbox"
        dst.mkdir()
        (dst / "keepme.txt").write_text("retained\n")

        res = _cli(env, "box", "duplicate", str(src), str(dst), "--force", stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "holds /x at 'box', where a table of keys belongs" in res.stderr
        assert (dst / "keepme.txt").read_text() == "retained\n"
        assert set(_primary_index()) == {"srcbox"}


class TestBareDuplicateStillCopies:
    """The guard refuses SHAPES, not the door — every well-formed tier still copies."""

    @pytest.mark.parametrize("body", [
        pytest.param("", id="no-box-section"),
        pytest.param("box: {}\n", id="empty-table"),
        pytest.param("box:\n  image: custom:v1\n", id="table"),
        pytest.param("box:\n  image: custom:v1\n  enable_vault: false\n", id="table-two-keys"),
    ])
    def test_a_well_formed_tier_survives_the_round_trip_unchanged(
        self, tmp_path, monkeypatch, body,
    ):
        """The new box's tier is BYTE-IDENTICAL to the source's — verbatim is the contract.

        ⚑ A guard that refused a well-formed ``box:`` table would make the bare form
        undupable for every box that has settings, so this row pins the other half of the
        door: the copy still happens and changes nothing.
        MUTATION: raise on any non-ABSENT ``box`` instead of on a non-table one and every
        row here reds with rc 1.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, body)
        (src / "file.txt").write_text("payload\n")
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 0, res.stdout + res.stderr
        copied = _std().boxes / "dstbox" / "box.yaml"
        assert copied.read_text() == body, "the copy did not survive verbatim"
        assert (dst / "file.txt").read_text() == "payload\n"
        assert set(_primary_index()) == {"srcbox", "dstbox"}

    def test_a_destination_that_already_exists_is_still_refused(self, tmp_path, monkeypatch):
        """A well-formed source onto an existing destination keeps the destination's refusal."""
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box:\n  image: custom:v1\n")
        dst = tmp_path / "ws" / "dstbox"
        dst.mkdir()
        (dst / "keepme.txt").write_text("retained\n")

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert f"destination already exists: {dst}" in res.stderr
        assert (dst / "keepme.txt").read_text() == "retained\n"
        assert set(_primary_index()) == {"srcbox"}