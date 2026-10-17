"""A per-box log that will not unlink is named and left, never raised.

It raised a traceback over a half-printed "Removing session data..." line.  Now the
purge is a failed one: the box metadata stays so a re-run can finish.  Every case
goes through ``cli.build_parser``, so a base run fails on an assertion.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import kanibako
from kanibako.settings.config import load_config
from kanibako.settings.paths import box_log_files, load_std_paths, resolve_project


def _door(*argv: str) -> int:
  """A real verb, through the production parser."""
  from kanibako import cli

  args = cli.build_parser().parse_args(list(argv))
  return int(args.func(args) or 0)


@pytest.fixture
def env(tmp_home, config_file, credentials_dir):
  config = load_config(config_file)
  return config, load_std_paths(config)


def _refuse_unlink(monkeypatch, refused: Path) -> None:
  """Make ``unlink`` of *refused* fail the way a read-only log dir does."""
  real = Path.unlink

  def unlink(self, *args, **kwargs):
    if self == refused:
      raise PermissionError(13, "Permission denied", str(self))
    return real(self, *args, **kwargs)

  monkeypatch.setattr(Path, "unlink", unlink)


def _primary_with_logs(env, tmp_home: Path):
  config, std = env
  workspace = tmp_home / "logbox"
  workspace.mkdir()
  proj = resolve_project(std, config, str(workspace), initialize=True)
  logs = box_log_files(std.primary_logs, proj.name)
  for log in logs:
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x\n")
  return proj, logs


class TestAnUnremovableLogIsNamedNotRaised:
  def test_box_purge_names_the_log_and_keeps_the_metadata(
    self, env, tmp_home, monkeypatch, capsys
  ):
    proj, logs = _primary_with_logs(env, tmp_home)
    _refuse_unlink(monkeypatch, logs.helper)

    rc = _door("box", "purge", str(proj.project_path), "--force")
    err = capsys.readouterr().err

    assert rc == 1, "a log left behind is a failed purge"
    # ``box purge <box>`` runs ``rm --purge``'s plan, so it warns in that plan's words.
    assert f"could not fully remove {logs.helper}" in err
    assert logs.helper.is_file()
    assert not logs.creds_watcher.exists(), "the other log still goes"
    assert proj.metadata_path.is_dir(), "the anchor stays so a re-run can finish"
    assert "Purge incomplete" in err

  def test_box_purge_all_names_the_log_and_finishes(self, env, tmp_home, monkeypatch, capsys):
    proj, logs = _primary_with_logs(env, tmp_home)
    _refuse_unlink(monkeypatch, logs.helper)

    _door("box", "purge", "--all", "--force")

    assert f"could not remove the log {logs.helper}" in capsys.readouterr().err
    assert logs.helper.is_file()
    assert not logs.creds_watcher.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root unlinks in a 0555 directory anyway")
@pytest.mark.parametrize("argv", [("box", "purge", "{ws}"), ("box", "purge", "--all")])
def test_piped_the_warning_never_splits_a_progress_line(env, tmp_home, argv):
  """With stdout and stderr on one pipe, no stderr line lands after "Removing …… "."""
  proj, logs = _primary_with_logs(env, tmp_home)
  child = os.environ.copy()
  child["PYTHONPATH"] = str(Path(kanibako.__file__).parents[1])
  logs.helper.parent.chmod(0o555)
  try:
    merged = subprocess.run(
      [
        sys.executable,
        "-m",
        "kanibako",
        *(a.format(ws=proj.project_path) for a in argv),
        "--force",
      ],
      env=child,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      timeout=300,
      check=False,
    ).stdout
  finally:
    logs.helper.parent.chmod(0o755)

  # One box runs ``rm --purge``'s plan and warns in its words; ``--all`` keeps its own.
  warning = merged.index(
    f"Warning: could not fully remove {logs.helper}"
    if "--all" not in argv
    else f"Warning: could not remove the log {logs.helper}"
  )
  if "--all" in argv:
    assert merged.index("Found 1 project(s)") < warning
  for line in merged.splitlines():
    if line.startswith("Removing "):
      assert line.endswith(("... ", "... done.")), f"split progress line: {line!r}"
