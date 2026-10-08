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

from kanibako.commands.box import _duplicate
from kanibako.settings.config import load_config
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


def _dupe_args(source, dest, *, to_mode=None, force=False, box=None):
    return argparse.Namespace(
        source_path=str(source),
        new_path=str(dest),
        box=box,
        to_mode=to_mode.value if to_mode else None,
        to_default=to_mode is BoxMode.primary,
        to_standalone=to_mode is BoxMode.standalone,
        to_workset=None,
        bare=False,
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


class TestAScalarBoxSectionRefusesBeforeAnyCopy:
    def test_a_scalar_box_in_dst_box_yaml_refuses_before_copying_anything(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        """Row B: a duplicate onto a dst whose box.yaml has scalar ``box:`` must
        refuse BEFORE any workspace or home file is copied, so the on-disk
        dst tree is byte-identical before and after the refused run."""
        env = (load_config(config_file), load_std_paths(load_config(config_file)),
               tmp_home)
        src = _primary(env, "src")
        # ``dest`` is a directory that already has ``box_data/box.yaml`` with a
        # scalar ``box:`` — the shape the brief describes.
        dest = tmp_home / "dst"
        dest.mkdir()
        box_data = dest / "box_data"
        box_data.mkdir()
        # The scalar ``box:`` value is an INT (any non-table scalar qualifies).
        (box_data / BOX_META_FILE).write_text("box: 42\n")
        # Bypass the interactive prompt so the copy step actually runs.
        monkeypatch.setattr(_duplicate, "confirm_prompt", lambda msg: None)
        # Hash the destination BEFORE the refused run.
        before = _hash_tree(dest)

        rc = _duplicate.run_duplicate(
            _dupe_args(src, dest, to_mode=BoxMode.standalone))
        assert rc != 0  # refused
        err = capsys.readouterr().err
        # Scalar-section refusal wording, not a traceback.
        assert "Traceback" not in err
        assert "FileExistsError" not in err
        # Hash the destination AFTER the refused run — must be unchanged.
        after = _hash_tree(dest)
        assert before == after, (
            f"refused duplicate wrote to {dest}: "
            f"before=\n{before}\nafter=\n{after}"
        )
