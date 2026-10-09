"""dupexisting: a duplicate into an existing destination refuses cleanly, and a
scalar-section refusal fires before any copy.

Two rows from the brief:

A. ``box duplicate`` into an existing destination without ``--force`` raises
   a raw ``FileExistsError`` traceback on ``--to standalone`` and ``--to
   primary`` instead of a clean refusal.  Data safe.
B. ``box duplicate --to standalone`` (no ``--force``) onto a ``<dst>`` whose
   ``box_data/box.yaml`` holds a scalar ``box:`` adds the workspace + home
   files, then refuses (``refuse_scalar_sections`` in
   ``write_box_enable_vault`` via ``establish_standalone``).  Adds only,
   overwrites nothing.

The fix refuses BEFORE any write in both cases.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest

from kanibako.errors import ConfigError

from kanibako.commands.box import _duplicate
from kanibako.settings.config import load_config
from kanibako.settings.messages import CURE_LEAF_NOT_ASCII
from kanibako.settings.paths import (
    BOX_META_FILE, BoxMode, load_std_paths, resolve_project,
)


def _primary(env, name="proj"):
    config, std, tmp_home = env
    pdir = tmp_home / name
    pdir.mkdir(parents=True)
    (pdir / "file.txt").write_text("hi")
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    return pdir


def _dupe_args(source, dest, *, to_mode=None, force=False, box=None, bare=False):
    return argparse.Namespace(
        source_path=str(source),
        new_path=str(dest),
        box=box,
        to_mode=to_mode.value if to_mode else None,
        to_default=to_mode is BoxMode.primary,
        to_standalone=to_mode is BoxMode.standalone,
        to_workset=None,
        bare=bare,
        force=force,
    )


def _hash_tree(root: Path) -> str:
    """Stable hash of every file under *root*."""
    if not root.exists():
        return ""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestAnExistingDestinationIsRefusedCleanly:
    def test_to_primary_refuses_cleanly_when_destination_exists(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        # A plain directory on disk — NOT registered as a box.  The F-3
        # registration guard does not fire here; the bug is the missing
        # filesystem existence check on the cross-mode path.
        dest = tmp_home / "dest"
        dest.mkdir()
        # Bypass the cross-mode confirmation prompt.
        monkeypatch.setattr(_duplicate, "confirm_prompt", lambda msg: None)
        rc = _duplicate.run_duplicate(_dupe_args(src, dest, to_mode=BoxMode.primary))
        assert rc == 1
        err = capsys.readouterr().err
        # The existing refusal wording (local-mode step 4) names the
        # destination and offers the --force cure.
        assert "destination" in err.lower() and "exists" in err.lower()
        assert "--force" in err
        # No raw traceback.
        assert "Traceback" not in err
        assert "FileExistsError" not in err

    def test_to_standalone_refuses_cleanly_when_destination_exists(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        # A plain directory on disk; bypass the prompt so we hit the copy
        # stage (the existing code aborts at the prompt if no input).
        dest = tmp_home / "dest"
        dest.mkdir()
        monkeypatch.setattr(_duplicate, "confirm_prompt", lambda msg: None)
        rc = _duplicate.run_duplicate(
            _dupe_args(src, dest, to_mode=BoxMode.standalone))
        assert rc == 1
        err = capsys.readouterr().err
        # The existing refusal wording names the destination and the
        # --force cure.
        assert "destination" in err.lower() and "exists" in err.lower()
        assert "--force" in err
        # No raw traceback.
        assert "Traceback" not in err
        assert "FileExistsError" not in err


def _scalar_box_dst(tmp_home: Path) -> Path:
    dest = tmp_home / "dst"
    (dest / "box_data").mkdir(parents=True)
    (dest / "box_data" / BOX_META_FILE).write_text("box: 42\n")
    return dest


class TestAScalarBoxSectionRefusesBeforeAnyCopy:
    def test_a_bare_duplicate_onto_a_scalar_box_writes_nothing(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """``--bare`` skips the existence check, so the scalar-``box:`` check is
        what refuses; it must fire before the home is laid down."""
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        dest = _scalar_box_dst(tmp_home)
        monkeypatch.setattr(_duplicate, "confirm_prompt", lambda msg: None)
        before = _hash_tree(dest)
        with pytest.raises(ConfigError, match="box"):
            _duplicate.run_duplicate(
                _dupe_args(src, dest, to_mode=BoxMode.standalone, bare=True))
        assert _hash_tree(dest) == before
        assert sorted(p.name for p in dest.rglob("*")) == ["box.yaml", "box_data"]

    @pytest.mark.parametrize("bare", [False, True])
    def test_force_rebuilds_a_scalar_box_destination(
            self, config_file, tmp_home, credentials_dir, bare):
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        dest = _scalar_box_dst(tmp_home)
        rc = _duplicate.run_duplicate(
            _dupe_args(src, dest, to_mode=BoxMode.standalone, force=True, bare=bare))
        assert rc == 0
        box_yaml = dest / "box_data" / BOX_META_FILE
        assert not box_yaml.exists() or "box: 42" not in box_yaml.read_text()
        assert (dest / "box_data" / "home").is_dir()


class TestAKanjiLeafDestinationIsRefusedBeforeAnyWrite:
    """`dupkanji` — row A + row B in one assertion.

    A duplicate whose destination LEAF has no ASCII spelling (e.g. ``日本語``)
    must refuse BEFORE any mkdir/copy/merge AND the refusal must carry the
    ``CURE_LEAF_NOT_ASCII`` directory cure.  No ``--name``: row A is the
    no-``--name`` case.

    AT BASE: the code falls through to the copy phase — ``box_data/`` and
    ``workspace/`` are written (row A), and the bare ``ProjectError`` handler
    strips the cure so the user gets a refusal with no remedy (row B).
    AFTER FIX: ``sanitize_cap(new_path.name)`` raises first; the cure is
    carried; nothing was written under the destination parent.
    """

    def test_refuses_before_any_write_with_the_directory_cure(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        # An EMPTY parent containing exactly one entry we are about to refuse —
        # so the `before` hash is non-trivial, and a write under it would change it.
        dest_parent = tmp_home / "kanji_parent"
        dest_parent.mkdir()
        sentinel = dest_parent / "untouched.txt"
        sentinel.write_text("untouched")
        dest = dest_parent / "日本語"

        monkeypatch.setattr(_duplicate, "confirm_prompt", lambda msg: None)

        before_parent = _hash_tree(dest_parent)

        rc = _duplicate.run_duplicate(
            _dupe_args(src, dest, to_mode=BoxMode.standalone, force=True))

        err = capsys.readouterr().err
        after_parent = _hash_tree(dest_parent)

        # Row B: the ASCII refusal carries the directory cure.
        assert rc == 1
        assert "cannot spell in ASCII" in err, err
        assert CURE_LEAF_NOT_ASCII in err, err

        # Row A: nothing was written under the destination parent.  The
        # untouched sentinel survives, and the kanji leaf never landed.
        assert after_parent == before_parent
        assert sentinel.is_file() and sentinel.read_text() == "untouched"
        assert not dest.exists()
