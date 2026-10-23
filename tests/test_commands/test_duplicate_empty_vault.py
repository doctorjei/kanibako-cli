"""``box duplicate`` gives the copy an EMPTY vault, as ``create`` does (Q4Jei 197-DV).

Vaults do not travel: nothing of the source's vault reaches the copy, but the copy's own
vault dirs exist, so ``resolve_box_target`` has no "cannot find vault" to warn about.
``box.enable_vault`` off (the destination's RESOLVED value) creates none.
"""

from __future__ import annotations

import argparse
import logging

import pytest

from kanibako.commands.box import _duplicate
from kanibako.settings import paths
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import load_config
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
  BoxMode,
  WorksetSpec,
  load_std_paths,
  resolve_box_target,
  resolve_project,
  resolve_standalone_project,
  resolve_workset_project,
)

SECRET = "source-vault-secret.txt"


@pytest.fixture
def env(config_file, tmp_home, credentials_dir, monkeypatch):
  monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda msg: None)
  config = load_config(config_file)
  return config, load_std_paths(config), tmp_home


def _seed_vault(proj):
  """Put a file in the source's vault, so a copy that carried it would show."""
  for arm in (proj.vault_ro_path, proj.vault_rw_path):
    arm.mkdir(parents=True, exist_ok=True)
    (arm / SECRET).write_text("must not travel")


def _primary(env, name):
  config, std, tmp_home = env
  pdir = tmp_home / name
  pdir.mkdir()
  (pdir / "file.txt").write_text("hi")
  _seed_vault(resolve_project(std, config, project_dir=str(pdir), initialize=True))
  return pdir


def _standalone(env, name):
  config, std, tmp_home = env
  pdir = tmp_home / name
  pdir.mkdir()
  proj = resolve_standalone_project(std, config, project_dir=str(pdir), initialize=True)
  (proj.project_path / "file.txt").write_text("hi")
  _seed_vault(proj)
  return pdir


def _named(env, ws_name, name):
  config, std, tmp_home = env
  ws = create_workset(ws_name, tmp_home / f"{ws_name}_root", std)
  src = tmp_home / f"{name}_src"
  src.mkdir()
  (src / "file.txt").write_text("hi")
  add_project(ws, name, src)
  proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config, initialize=True)
  _seed_vault(proj)
  return ws, ws.workspaces_dir / name


def _args(source, dest, to_mode=None, *, workset=None, name=None):
  return argparse.Namespace(
    source_path=str(source),
    new_path=str(dest),
    box=None,
    to_mode=to_mode.value if to_mode else None,
    workset=workset,
    project_name=name,
    bare=False,
    force=False,
    register=False,
  )


def _resolve_dest(env, path, caplog):
  """The copy as ``box info``/``start`` resolve it, and whether that warned."""
  config, std, _ = env
  import contextlib
  import io

  caplog.clear()
  err = io.StringIO()
  with caplog.at_level(logging.WARNING, logger="kanibako"), contextlib.redirect_stderr(err):
    proj = resolve_box_target(std, config, str(path))
  warned = "cannot find vault" in err.getvalue()
  return proj, warned


def _assert_no_secret(*trees):
  for tree in trees:
    assert list(tree.rglob(SECRET)) == [], tree


def _assert_empty_vault(proj, warned):
  assert proj.vault_enabled() is True
  for arm in (proj.vault_ro_path, proj.vault_rw_path):
    assert arm.is_dir(), arm
    assert list(arm.iterdir()) == [], arm
  assert not warned


_SOURCES = {"primary": _primary, "standalone": _standalone}


@pytest.mark.parametrize(
  ("kind", "to_mode"),
  [
    ("primary", None),
    ("primary", BoxMode.primary),
    ("primary", BoxMode.standalone),
    ("standalone", None),
    ("standalone", BoxMode.primary),
    ("named", BoxMode.primary),
    ("named", BoxMode.standalone),
  ],
)
def test_every_route_lays_an_empty_vault_and_carries_nothing(env, caplog, kind, to_mode):
  config, std, tmp_home = env
  if kind == "named":
    _ws, src = _named(env, "dvws", "dvsrc")
  else:
    src = _SOURCES[kind](env, "dvsrc")
  dest = tmp_home / "dvsrc_copy"

  assert _duplicate.run_duplicate(_args(src, dest, to_mode)) == 0

  proj, warned = _resolve_dest(env, dest, caplog)
  _assert_empty_vault(proj, warned)
  _assert_no_secret(dest, proj.metadata_path)
  # The source keeps its vault, contents and all.
  src_proj = resolve_box_target(std, config, str(src))
  assert (src_proj.vault_rw_path / SECRET).is_file()


@pytest.mark.parametrize("source_kind", ["primary", "standalone"])
def test_to_named_lays_an_empty_vault_and_carries_nothing(env, caplog, source_kind):
  """``--to named`` lands through ``add_project``, ``create``'s own named setup."""
  config, std, tmp_home = env
  src = _SOURCES[source_kind](env, "tn")
  ws = create_workset("tnws", tmp_home / "tnws_root", std)
  rc = _duplicate.run_duplicate(_args(src, tmp_home / "unused", BoxMode.named, workset="tnws"))
  assert rc == 0
  proj, warned = _resolve_dest(env, ws.workspaces_dir / "tn", caplog)
  _assert_empty_vault(proj, warned)
  # Nowhere in the workset: not the box data, not the workspace (a standalone ROOT holds
  # its vault, so copying the root instead of its workspace carried it).
  _assert_no_secret(ws.root)
  assert (ws.workspaces_dir / "tn" / "file.txt").is_file()


def test_to_named_box_tier_on_over_a_workset_tier_off_lays_the_vault(env, caplog):
  """The vault is laid once the copy's box tier lands: its ``true`` beats the workset's ``false``."""
  config, std, tmp_home = env
  src = _primary(env, "bon")
  src_proj = resolve_project(std, config, project_dir=str(src), initialize=False)
  write_nested_key(src_proj.metadata_path / "box.yaml", ("box",), "enable_vault", True)
  ws = create_workset("bonws", tmp_home / "bonws_root", std)
  write_nested_key(ws.root / "workset.yaml", ("box",), "enable_vault", False)

  rc = _duplicate.run_duplicate(_args(src, tmp_home / "unused", BoxMode.named, workset="bonws"))
  assert rc == 0

  proj, warned = _resolve_dest(env, ws.workspaces_dir / "bon", caplog)
  _assert_empty_vault(proj, warned)


@pytest.mark.parametrize("to_mode", [None, BoxMode.primary, BoxMode.standalone])
def test_box_authored_vault_off_creates_none(env, caplog, to_mode):
  """The source's own ``enable_vault: false`` travels, so the copy gets no vault dirs."""
  config, std, tmp_home = env
  src = _primary(env, "off")
  src_proj = resolve_project(std, config, project_dir=str(src), initialize=False)
  write_nested_key(src_proj.metadata_path / "box.yaml", ("box",), "enable_vault", False)
  dest = tmp_home / "off_copy"

  assert _duplicate.run_duplicate(_args(src, dest, to_mode)) == 0

  proj, warned = _resolve_dest(env, dest, caplog)
  assert proj.vault_enabled() is False
  assert not proj.vault_ro_path.exists()
  assert not proj.vault_rw_path.exists()
  assert not warned


def test_workset_default_off_is_resolved_for_the_copy(env, caplog):
  """RESOLVED, not the box tier: the primary workset's ``enable_vault: false`` reaches a
  primary copy that authored nothing, so none is created."""
  config, std, tmp_home = env
  src = _primary(env, "wsoff")
  write_nested_key(std.primary_workset / "workset.yaml", ("box",), "enable_vault", False)
  dest = tmp_home / "wsoff_copy"

  assert _duplicate.run_duplicate(_args(src, dest, BoxMode.primary)) == 0

  proj, _ = _resolve_dest(env, dest, caplog)
  assert proj.vault_enabled() is False
  assert not proj.vault_rw_path.exists()
  assert not proj.vault_ro_path.exists()


@pytest.mark.parametrize("to_mode", [None, BoxMode.primary, BoxMode.standalone])
def test_a_failed_vault_setup_undoes_the_arms_it_made(env, monkeypatch, to_mode):
  """The ro arm is made, then the setup fails: the undo removes the arm, and the copy."""
  config, std, tmp_home = env
  src = _primary(env, "undo")
  dest = tmp_home / "undo_copy"
  real = paths.init_vault_dirs
  made_arms = []

  def failing(ro, rw, *, enable_vault, vault_root, created):
    real(ro, None, enable_vault=enable_vault, vault_root=vault_root, created=created)
    made_arms.extend(created)
    raise OSError("injected vault failure")

  monkeypatch.setattr(paths, "init_vault_dirs", failing)

  if to_mode is None:
    with pytest.raises(OSError, match="injected"):
      _duplicate.run_duplicate(_args(src, dest, to_mode))
  elif to_mode is BoxMode.primary:
    assert _duplicate.run_duplicate(_args(src, dest, to_mode)) == 1
  else:
    with pytest.raises(OSError, match="injected"):
      _duplicate.run_duplicate(_args(src, dest, to_mode))

  assert made_arms, "the injected failure ran after an arm was made"
  for arm in made_arms:
    assert not arm.exists(), arm
  assert not dest.exists()


@pytest.mark.parametrize(
  ("kind", "to_mode"),
  [
    ("primary", None),
    ("primary", BoxMode.standalone),
    ("standalone", BoxMode.primary),
    ("named", BoxMode.primary),
    ("primary", BoxMode.named),
  ],
)
def test_every_route_makes_the_copy_an_empty_inbox(env, kind, to_mode):
  """A box's inbox is made with the box; a copy carries no mail."""
  from kanibako.channels.channels import box_channel_addresses
  from kanibako.channels.roots import make_setup_channel_roots

  config, std, tmp_home = env
  make_setup_channel_roots(std)
  if kind == "named":
    _ws, src = _named(env, "ibws", "ibsrc")
  else:
    src = _SOURCES[kind](env, "ibsrc")
  src_inbox = box_channel_addresses(resolve_box_target(std, config, str(src)), std).inbox
  src_inbox.mkdir(parents=True, exist_ok=True)
  (src_inbox / "mail.md").write_text("for the source only")
  dest = tmp_home / "ibsrc_copy"
  if to_mode is BoxMode.named:
    ws = create_workset("ibto", tmp_home / "ibto_root", std)
    dest = ws.workspaces_dir / "ibsrc"
    args = _args(src, tmp_home / "unused", BoxMode.named, workset="ibto")
  else:
    args = _args(src, dest, to_mode)

  assert _duplicate.run_duplicate(args) == 0

  inbox = box_channel_addresses(resolve_box_target(std, config, str(dest)), std).inbox
  assert inbox != src_inbox
  assert inbox.is_dir()
  assert list(inbox.iterdir()) == []
  assert (src_inbox / "mail.md").is_file()
