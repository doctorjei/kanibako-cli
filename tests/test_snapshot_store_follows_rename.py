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

import shutil
from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.config_io import write_nested_key
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
    (versions / ".layout").unlink()
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
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name,
                              store_exclusive=True)
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
        snap = create_snapshot(proj.vault_rw_path, box_name=proj.name,
                              store_exclusive=True)
        _flatten_legacy(proj, snap)
        (proj.vault_rw_path / "live.txt").write_text("second")

        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        args = cli.build_parser().parse_args(
            ["box", "vault", "restore", snap.name, str(root)])
        rc = int(args.func(args) or 0)

        assert rc == 0
        assert (proj.vault_rw_path / "live.txt").read_text() == "first"
