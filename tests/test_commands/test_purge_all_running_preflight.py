"""``box purge --all`` asks the runtime about every box before it deletes anything (F2).

It used to delete a running box's vaults and box tree with no probe at all, while
``box purge <path>`` refused.  Now one live box refuses the whole run before the
question: nothing is deleted, each live box is named with its stop cure, and
``--force`` overrides.
"""

from __future__ import annotations

import argparse
import fcntl
import shlex

import pytest

from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
  WorksetSpec,
  load_std_paths,
  resolve_project,
  resolve_workset_project,
)


def _runtime(monkeypatch, tmp_path, running):
  """A stand-in container CLI whose ``ps`` lists *running*; returns its call log.

  The REAL ``_check_container_running`` asks it, so a probe that renders the wrong
  container name gets "not running" and the refusal does not fire.
  """
  log = tmp_path / "runtime-calls.log"
  body = ["#!/bin/sh", f'echo "$*" >> {shlex.quote(str(log))}', 'case "$1" in', "  ps)"]
  for cname in running:
    body.append(f'    printf "%s\\timg:tag\\tUp 1 minute\\n" {shlex.quote(cname)}')
  body += ["    ;;", "  inspect)", "    exit 1", "    ;;", "  *)", "    exit 0", "    ;;", "esac"]
  stub = tmp_path / "fake-runtime.sh"
  stub.write_text("\n".join(body) + "\n")
  stub.chmod(0o755)
  monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
  return log


def _no_question(monkeypatch):
  """Fail if the purge reaches its confirmation: a refusal comes before it."""

  def _asked(*_a, **_k):
    raise AssertionError("the purge asked its question despite a live box")

  monkeypatch.setattr("kanibako.utils.confirm_prompt", _asked)


def _world(config_file, tmp_home):
  """Two primaries (``pk``, ``pq``) and a named member ``paws/bar``, each with data."""
  config = load_config(config_file)
  std = load_std_paths(config)
  boxes = {}
  for name in ("pk", "pq"):
    d = tmp_home / name
    d.mkdir()
    boxes[name] = resolve_project(std, config, project_dir=str(d), initialize=True)
  ws = create_workset("paws", tmp_home / "worksets" / "paws", std)
  src = tmp_home / "bar_src"
  src.mkdir()
  add_project(ws, "bar", src)
  boxes["bar"] = resolve_workset_project(
    WorksetSpec.from_workset(ws), "bar", std, config, initialize=True
  )
  for proj in boxes.values():
    (proj.metadata_path / "data.txt").write_text("keep me")
    proj.vault_rw_path.mkdir(parents=True, exist_ok=True)
    (proj.vault_rw_path / "notes.txt").write_text("keep me too")
  assert boxes["pk"].metadata_path.name == "pk", "setup: the primary's box name"
  return std, boxes


def _purge_all(force=False):
  from kanibako.commands.clean import run

  return run(argparse.Namespace(all_projects=True, force=force, path=None))


def _all_intact(boxes):
  for name, proj in boxes.items():
    assert (proj.metadata_path / "data.txt").is_file(), f"{name}: box tree touched"
    assert (proj.vault_rw_path / "notes.txt").is_file(), f"{name}: vault touched"


class TestPurgeAllRefusesWhileABoxRuns:
  def test_one_live_primary_refuses_the_whole_run(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    std, boxes = _world(config_file, tmp_home)
    log = _runtime(monkeypatch, tmp_path, ["kb-primary-pk"])
    _no_question(monkeypatch)

    assert _purge_all() == 1
    out = capsys.readouterr()
    _all_intact(boxes)
    assert "kb-primary-pk" in out.err and "kanibako stop pk," in out.err, out.err
    assert "1 box(es) may be running" in out.err and "Nothing was purged" in out.err
    assert "Removing" not in out.out, out.out
    assert log.read_text().count("ps ") >= 3, "every box is probed before refusing"

  def test_a_live_named_member_refuses_with_its_workset_ref(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    _std, boxes = _world(config_file, tmp_home)
    _runtime(monkeypatch, tmp_path, ["kb-paws-bar"])
    _no_question(monkeypatch)

    assert _purge_all() == 1
    err = capsys.readouterr().err
    _all_intact(boxes)
    assert "kb-paws-bar" in err and "kanibako stop paws/bar," in err, err
    assert "may be running: paws/bar." in err, err

  def test_every_live_box_is_named(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    _std, boxes = _world(config_file, tmp_home)
    _runtime(monkeypatch, tmp_path, ["kb-primary-pq", "kb-paws-bar"])
    _no_question(monkeypatch)

    assert _purge_all() == 1
    out = capsys.readouterr()
    err = out.err
    _all_intact(boxes)
    assert "2 box(es) may be running" in err, err
    assert out.out.count("Aborted.") == 1, "one refusal, one Aborted line: " + out.out
    assert str(boxes["pq"].project_path) in err and "paws/bar" in err, err
    assert "kanibako stop pk" not in err, "a stopped box is not named"

  def test_a_held_session_lock_refuses_with_no_container(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    _std, boxes = _world(config_file, tmp_home)
    _runtime(monkeypatch, tmp_path, [])
    _no_question(monkeypatch)
    lock = boxes["pq"].metadata_path / ".kanibako.lock"
    with open(lock, "w") as fd:
      fcntl.flock(fd, fcntl.LOCK_EX)
      rc = _purge_all()
    assert rc == 1
    err = capsys.readouterr().err
    _all_intact(boxes)
    assert "a kanibako session holds its lock file" in err, err

  def test_no_runtime_and_a_lock_file_refuses(
    self, config_file, tmp_home, credentials_dir, monkeypatch, capsys
  ):
    """No runtime to ask: a lock file on disk still counts (the shared pre-flight)."""
    _std, boxes = _world(config_file, tmp_home)
    _no_question(monkeypatch)
    (boxes["bar"].metadata_path / ".kanibako.lock").touch()

    assert _purge_all() == 1
    _all_intact(boxes)
    assert "a lock file exists" in capsys.readouterr().err

  def test_force_purges_a_live_box_after_warning(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    _std, boxes = _world(config_file, tmp_home)
    _runtime(monkeypatch, tmp_path, ["kb-primary-pk"])

    assert _purge_all(force=True) == 0
    out = capsys.readouterr()
    assert "Warning: this box may be running" in out.err, out.err
    for name, proj in boxes.items():
      assert not proj.metadata_path.exists(), f"{name}: --force must purge it"

  def test_nothing_live_asks_and_purges(
    self, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
  ):
    _std, boxes = _world(config_file, tmp_home)
    _runtime(monkeypatch, tmp_path, ["kb-primary-someoneelse"])
    asked = []
    monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda *a, **k: asked.append(a))

    assert _purge_all() == 0
    assert asked, "the question is still asked when nothing runs"
    assert "may be running" not in capsys.readouterr().err
    for name, proj in boxes.items():
      assert not proj.metadata_path.exists(), f"{name}: not purged"


@pytest.mark.parametrize("force", [False, True])
def test_the_probe_runs_before_any_delete(
  force, config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
):
  """The refusal is decided before the first delete, so no box goes first."""
  _std, boxes = _world(config_file, tmp_home)
  _runtime(monkeypatch, tmp_path, ["kb-paws-bar"])
  removed = []
  import kanibako.commands.clean as C

  real = C.remove_box_tree
  monkeypatch.setattr(C, "remove_box_tree", lambda p: removed.append(p) or real(p))
  if not force:
    _no_question(monkeypatch)

  rc = _purge_all(force=force)
  capsys.readouterr()
  if force:
    assert rc == 0 and len(removed) == 3
  else:
    assert rc == 1 and removed == []
    _all_intact(boxes)


def test_the_all_help_says_standalone_boxes_are_skipped(capsys):
  """``--all`` skips standalone boxes on purpose (``test_purge_all_skips_standalone``)."""
  from kanibako.cli import build_parser

  with pytest.raises(SystemExit):
    build_parser().parse_args(["box", "purge", "--help"])
  out = " ".join(capsys.readouterr().out.split())
  assert "standalone boxes are skipped (purge each one by its path)" in out, out
