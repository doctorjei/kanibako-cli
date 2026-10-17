"""A standalone purge's Note for the kept ``vault/.versions`` is TRUE.

It called the snapshot folder "the vault … not a vault arm of this box", and, piped,
the Note came out BEFORE the plan that deletes the box's store inside it.  The Note
now names the snapshot folder, is worded from what the folder holds after the run,
and comes out after the run's own lines.  Every case goes through a door that exists
at the base (``cli.build_parser`` or a real subprocess).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import kanibako
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_standalone_project
from kanibako.snapshots import LAYOUT_MARKER_NAME, box_snapshot_store, create_snapshot


def _door(*argv: str) -> int:
  """A real verb, through the production parser."""
  from kanibako import cli

  args = cli.build_parser().parse_args(list(argv))
  return int(args.func(args) or 0)


@pytest.fixture
def env(tmp_home, config_file, credentials_dir):
  config = load_config(config_file)
  return config, load_std_paths(config)


def _standalone_with_snapshot(env, tmp_home: Path):
  config, std = env
  root = tmp_home / "snapbox"
  root.mkdir()
  proj = resolve_standalone_project(std, config, str(root), initialize=True)
  (proj.vault_rw_path / "user.txt").write_text("data\n")
  assert create_snapshot(proj.vault_rw_path, box_name=proj.name, store_exclusive=True) is not None
  store = box_snapshot_store(proj.vault_rw_path, proj.name)
  assert store.is_dir() and (store.parent / LAYOUT_MARKER_NAME).is_file()
  return root, store


_LAYOUT_ONLY = "it holds only kanibako's snapshot layout marker and is yours to remove."


@pytest.mark.parametrize("verb", [("box", "rm"), ("box", "purge")])
class TestTheSnapshotFolderNoteIsTrue:
  def _purge(self, verb, root):
    if verb == ("box", "rm"):
      return _door(*verb, str(root), "--purge", "--force")
    return _door(*verb, str(root), "--force")

  def test_the_note_names_the_snapshot_folder_by_what_it_now_holds(
    self, env, tmp_home, capsys, verb
  ):
    root, store = _standalone_with_snapshot(env, tmp_home)

    assert self._purge(verb, root) == 0
    out, err = capsys.readouterr()

    assert not store.exists()
    assert f"left the vault at {store.parent}" not in err
    assert (f"Note: left the snapshot folder at {store.parent} in place — " + _LAYOUT_ONLY) in err
    if verb == ("box", "rm"):
      assert f"kept snapshot folder: {store.parent}" in out

  def test_an_empty_folder_is_not_said_to_hold_the_marker(self, env, tmp_home, capsys, verb):
    """A base with no ``.layout`` (the user removed it) is empty once the store goes."""
    root, store = _standalone_with_snapshot(env, tmp_home)
    (store.parent / LAYOUT_MARKER_NAME).unlink()

    assert self._purge(verb, root) == 0
    err = capsys.readouterr().err

    assert not any(store.parent.iterdir()), "precondition: the folder is empty"
    assert _LAYOUT_ONLY not in err
    assert (
      f"Note: left the snapshot folder at {store.parent} in place — it holds "
      "nothing and is yours to remove."
    ) in err

  def test_a_store_that_stays_is_not_called_empty(self, env, tmp_home, monkeypatch, capsys, verb):
    """Control on the wording: the layout-only phrase is earned, not a constant."""
    import kanibako.commands.box._parser as parser_mod
    import kanibako.commands.clean as clean_mod

    root, store = _standalone_with_snapshot(env, tmp_home)
    for module in (parser_mod, clean_mod):
      real = module.remove_path
      monkeypatch.setattr(
        module, "remove_path", lambda path, real=real: path != store and real(path)
      )

    self._purge(verb, root)
    err = capsys.readouterr().err

    assert store.is_dir()
    assert _LAYOUT_ONLY not in err
    assert (
      f"Note: left the snapshot folder at {store.parent} in place — it holds "
      "snapshots this purge did not delete"
    ) in err


def _cli_merged(*args: str) -> str:
  """The real CLI with stdout and stderr on ONE pipe, as ssh or ``2>&1 | less`` sees it."""
  child = os.environ.copy()
  child["PYTHONPATH"] = str(Path(kanibako.__file__).parents[1])
  return subprocess.run(
    [sys.executable, "-m", "kanibako", *args],
    env=child,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    timeout=300,
    check=False,
  ).stdout


def test_piped_the_note_comes_after_the_plan_and_the_removals(env, tmp_home):
  root, store = _standalone_with_snapshot(env, tmp_home)

  merged = _cli_merged("box", "rm", str(root), "--purge", "--force")

  note = merged.index(f"Note: left the snapshot folder at {store.parent}")
  assert merged.index("--purge deletes these") < merged.rindex("Removed metadata:") < note


@pytest.mark.skipif(os.geteuid() == 0, reason="root unlinks in a 0555 directory anyway")
def test_piped_a_steps_warning_comes_after_the_plan(env, tmp_home):
  """``box rm --purge``'s own stderr lines, not only the retained Notes, keep their place."""
  from kanibako.settings.paths import box_log_files, resolve_project

  config, std = env
  workspace = tmp_home / "pipebox"
  workspace.mkdir()
  proj = resolve_project(std, config, str(workspace), initialize=True)
  log = box_log_files(std.primary_logs, proj.name).helper
  log.parent.mkdir(parents=True, exist_ok=True)
  log.write_text("x\n")
  log.parent.chmod(0o555)
  try:
    merged = _cli_merged("box", "rm", str(workspace), "--purge", "--force")
  finally:
    log.parent.chmod(0o755)

  warning = merged.index(f"Warning: could not fully remove {log}")
  assert merged.index("--purge deletes these") < warning
  assert merged.index(f"  log: {log}") < warning
