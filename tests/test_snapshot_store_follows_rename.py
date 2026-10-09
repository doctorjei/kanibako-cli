"""A box's SNAPSHOT store follows the box: it is keyed on the box, not left behind.

Two faces of one rule, both measured through the real doors:

* A legacy flat entry that this call is about to attribute to the box must already
  be counted when the restore's confirmation prompt is decided (item 3).
* Every verb that renames or relocates a box carries ``.versions/<box>`` with it,
  so the snapshots are never stranded where the next box to use that name finds
  them (item 4).

⚑ EVERY CASE GOES THROUGH A DOOR THAT EXISTS AT THE BASE (``cli.build_parser`` plus
``args.func``), so a base run fails on an ASSERTION about behavior rather than on
an import of a name this change introduced.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project
from kanibako.settings.paths import resolve_standalone_project


def _standalone(config, std, tmp_home: Path, leaf: str):
    """A materialized standalone box at *leaf* under *tmp_home*."""
    root = tmp_home / leaf
    root.mkdir()
    (root / "user_code.py").write_text("print('mine')\n")
    return root, resolve_standalone_project(std, config, str(root), initialize=True)


def _flatten_legacy(proj, snap) -> Path:
    """Put *snap* back the way the OLD flat store kept it, unmarked.

    Returns the ``.versions`` base.  The box's own store is left empty, which is
    exactly the state a base has the first time new code touches it.
    """
    from kanibako.snapshots import _versions_dir

    versions = _versions_dir(proj.vault_rw_path)
    shutil.move(str(snap), str(versions / snap.name))
    marker = versions / ".layout"
    if marker.exists():
        marker.unlink()
    if (versions / proj.name).is_dir():
        shutil.rmtree(versions / proj.name)
    return versions


@pytest.fixture
def env(tmp_home, config_file, credentials_dir):
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std


class TestRestoreMigratesBeforeAsking:
    """``known`` must be read AFTER the migration that puts the name in the store."""

    def test_a_legacy_name_still_prompts_before_restoring(self, env, tmp_home,
                                                      monkeypatch):
        from kanibako import cli
        from kanibako.snapshots import create_snapshot

        config, std = env
        root, proj = _standalone(config, std, tmp_home, "promptbox")
        (proj.vault_rw_path / "live.txt").write_text("first")
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name)
        assert snap is not None
        _flatten_legacy(proj, snap)
        (proj.vault_rw_path / "live.txt").write_text("current")

        monkeypatch.setattr("builtins.input", lambda *_a: "no")
        args = cli.build_parser().parse_args(
            ["box", "vault", "restore", snap.name, str(root)])
        rc = int(args.func(args) or 0)

        assert rc == 2
        assert (proj.vault_rw_path / "live.txt").read_text() == "current"

    def test_a_legacy_name_restores_when_confirmed(self, env, tmp_home, monkeypatch):
        """The prompt is a gate, not a refusal: 'yes' still restores."""
        from kanibako import cli
        from kanibako.snapshots import create_snapshot

        config, std = env
        root, proj = _standalone(config, std, tmp_home, "yesbox")
        (proj.vault_rw_path / "live.txt").write_text("first")
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name)
        _flatten_legacy(proj, snap)
        (proj.vault_rw_path / "live.txt").write_text("second")

        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        args = cli.build_parser().parse_args(
            ["box", "vault", "restore", snap.name, str(root)])
        rc = int(args.func(args) or 0)

        assert rc == 0
        assert (proj.vault_rw_path / "live.txt").read_text() == "first"


class TestMoveCarriesTheStore:
    """``box move`` renames the box, so it must carry ``.versions/<box>`` too."""

    def test_a_primary_move_takes_the_snapshots_with_it(
        self, env, tmp_home,
    ):
        from kanibako.commands.box._lifecycle import run_move
        from kanibako.snapshots import create_snapshot, list_snapshots
        from tests.test_commands.test_box_move import _move_args

        config, std = env
        src = tmp_home / "snapmoveA"
        src.mkdir()
        proj = resolve_project(std, config, project_dir=str(src), initialize=True)
        (proj.vault_rw_path / "a.txt").write_text("one")
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name)
        assert snap is not None

        dest = tmp_home / "snapmoveB"
        assert run_move(_move_args(src, dest, name="snapmoveB")) == 0

        moved = list_snapshots(proj.vault_rw_path, box_name="snapmoveB")
        assert [n for n, _ts, _sz in moved] == [snap.name]
        assert list_snapshots(proj.vault_rw_path, box_name="proj") == []

    def test_a_reused_old_name_inherits_nothing(
        self, env, tmp_home,
    ):
        """The old key is EMPTY after the move, so a new box there starts clean."""
        from kanibako.commands.box._lifecycle import run_move
        from kanibako.snapshots import create_snapshot, list_snapshots
        from tests.test_commands.test_box_move import _move_args

        config, std = env
        src = tmp_home / "reuseA"
        src.mkdir()
        proj = resolve_project(std, config, project_dir=str(src), initialize=True)
        (proj.vault_rw_path / "a.txt").write_text("one")
        create_snapshot(proj.vault_rw_path, box_name=proj.name)

        dest = tmp_home / "reuseB"
        assert run_move(_move_args(src, dest, name="reuseB")) == 0

        # Nothing is left under the old key for a later box to inherit.
        from kanibako.snapshots import _versions_dir
        old_key = _versions_dir(proj.vault_rw_path) / "reuseA"
        assert not old_key.exists()
        assert list_snapshots(proj.vault_rw_path, box_name="reuseA") == []


class TestConvertCarriesTheStore:
    """A convert changes both the key AND the base; the store follows both."""

    def test_a_convert_to_standalone_carries_the_store_across_bases(
        self, env, tmp_home, monkeypatch,
    ):
        from kanibako.commands.box._lifecycle import run_convert
        from kanibako.snapshots import create_snapshot, list_snapshots

        config, std = env
        src = tmp_home / "convA"
        src.mkdir()
        proj = resolve_project(std, config, project_dir=str(src), initialize=True)
        (proj.vault_rw_path / "a.txt").write_text("one")
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name)
        old_base = proj.vault_rw_path.parent / ".versions"
        assert (old_base / proj.name).is_dir()

        dest = tmp_home / "convS"
        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        rc = run_convert(argparse.Namespace(
            old=str(src), box=None, to_standalone=True, to_default=False,
            to_workset=None, move=str(dest), name=None, force=False,
        ))
        assert rc == 0

        new_proj = resolve_standalone_project(std, config, str(dest),
                                            initialize=False)
        assert [n for n, _ts, _sz in
                list_snapshots(new_proj.vault_rw_path, box_name=new_proj.name)] \
            == [snap.name]
        assert not (old_base / proj.name).exists()

class TestStandaloneRenameAdoption:
    """A raw ``mv`` of a standalone tree carries the store but not its key.

    Both cases go through ``box vault`` doors, so a base run fails on what the
    user SEES rather than on the shape of a new API.
    """

    @staticmethod
    def _snapshot(root, capsys) -> str:
        from kanibako import cli

        args = cli.build_parser().parse_args(
            ["box", "vault", "snapshot", str(root)])
        assert int(args.func(args) or 0) == 0
        return capsys.readouterr().out.split("Snapshot created:")[-1].strip()

    @staticmethod
    def _names(root, capsys) -> list[str]:
        from kanibako import cli

        args = cli.build_parser().parse_args(
            ["box", "vault", "list", "--quiet", str(root)])
        assert int(args.func(args) or 0) == 0
        return [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]

    def test_a_renamed_standalone_still_lists_its_snapshots(
        self, env, tmp_home, capsys,
    ) -> None:
        config, std = env
        root, proj = _standalone(config, std, tmp_home, "adoptbox")
        (proj.vault_rw_path / "a.txt").write_text("one")
        snap = self._snapshot(root, capsys)

        new_root = tmp_home / "adoptbox2"
        shutil.move(str(root), str(new_root))

        assert self._names(new_root, capsys) == [snap]

    def test_an_ambiguous_tree_is_not_guessed_at(
        self, env, tmp_home, capsys,
    ) -> None:
        """Two stores carrying this box's kuid: nothing is picked, nothing moved."""
        from kanibako.snapshots import _versions_dir

        config, std = env
        root, proj = _standalone(config, std, tmp_home, "ambigbox")
        (proj.vault_rw_path / "a.txt").write_text("one")
        self._snapshot(root, capsys)
        versions = _versions_dir(proj.vault_rw_path)
        kuid_half = proj.name.partition("_")[0]
        if (versions / proj.name).is_dir():
            shutil.move(str(versions / proj.name), str(tmp_home / "parked"))
        (versions / f"{kuid_half}_x").mkdir(exist_ok=True)
        (versions / f"{kuid_half}_y").mkdir(exist_ok=True)

        assert self._names(root, capsys) == []
        assert (versions / f"{kuid_half}_x").is_dir()
        assert (versions / f"{kuid_half}_y").is_dir()
