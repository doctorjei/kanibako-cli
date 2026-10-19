"""``box rm --purge`` lists every path it deletes, asks before unregistering, and keeps
a symlinked vault's target.

Each case runs the real ``run_rm`` over a box ``run_create`` made, so the list, the
question, and the deletions come from the same plan the CLI uses.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
  monkeypatch.setattr("kanibako.commands.start.seed_new_box", lambda std, config, proj, **kw: None)


def _std(config_file):
  from kanibako.settings.config import load_config
  from kanibako.settings.paths import load_std_paths

  return load_std_paths(load_config(config_file))


def _create(path: Path, *, standalone: bool = False, register: bool = True) -> None:
  from kanibako.commands.box._parser import run_create

  path.mkdir(parents=True, exist_ok=True)
  assert (
    run_create(
      argparse.Namespace(
        path=str(path),
        standalone=standalone,
        no_vault=False,
        name=None,
        image=None,
        agent=None,
        allow_home=False,
        register=standalone and register,
        recover=False,
      )
    )
    == 0
  )


def _rm(target: str, *, force: bool) -> int:
  from kanibako.commands.box._parser import run_rm

  return run_rm(argparse.Namespace(target=target, purge=True, force=force))


def _closed_stdin(monkeypatch):
  def _eof(*_a):
    raise EOFError

  monkeypatch.setattr("builtins.input", _eof)


def _symlink_to_canary(arm: Path, outside: Path) -> str:
  """Replace the vault dir *arm* with a link to *outside*, which holds a canary."""
  outside.mkdir()
  (outside / "canary.txt").write_text("keep me\n")
  arm.rmdir()
  arm.symlink_to(outside)
  return hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest()


def _plan_lines(out: str) -> list[str]:
  head, _, rest = out.partition("--purge deletes these ")
  assert rest, out
  return [line.strip() for line in rest.splitlines()[1:] if line.startswith("  ")]


class TestPrimaryPurge:
  def test_declined_question_changes_nothing(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """Closed stdin: the box stays registered and every listed path stays on disk."""
    from kanibako.settings.paths import load_primary_boxes

    _create(tmp_home / "proj")
    std = _std(config_file)
    meta = std.boxes / "proj"
    capsys.readouterr()
    _closed_stdin(monkeypatch)

    assert _rm("proj", force=False) == 2
    out = capsys.readouterr().out
    assert "Delete these 3 paths? This cannot be undone." in out
    assert "proj" in load_primary_boxes(std.primary_workset)
    assert meta.is_dir()
    assert (std.primary_vault_rw / "proj").is_dir()

  def test_list_names_every_deleted_path_and_force_still_prints_it(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    _create(tmp_home / "proj")
    std = _std(config_file)
    std.primary_logs.mkdir(parents=True, exist_ok=True)
    log = std.primary_logs / "proj.jsonl"
    log.write_text("{}\n")
    # ⚑ The anchor (box metadata) is listed LAST: it is the address a retry needs,
    # so the plan spends everything else before it.
    listed = [
      std.primary_vault_ro / "proj",
      std.primary_vault_rw / "proj",
      log,
      std.boxes / "proj",
    ]
    capsys.readouterr()

    assert _rm("proj", force=True) == 0
    out = capsys.readouterr().out
    lines = _plan_lines(out)
    assert [line.split(": ", 1)[1] for line in lines] == [str(p) for p in listed]
    assert f"vault rw (your files): {listed[1]}" in lines
    assert "Delete these" not in out
    assert not any(p.exists() for p in listed)

  def test_symlinked_vault_loses_only_the_link(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    _create(tmp_home / "proj")
    std = _std(config_file)
    arm = std.primary_vault_rw / "proj"
    outside = tmp_home / "elsewhere"
    digest = _symlink_to_canary(arm, outside)
    capsys.readouterr()

    assert _rm("proj", force=True) == 0
    out = capsys.readouterr().out
    assert f"{arm} → {outside} (link only; target kept)" in out
    assert not arm.is_symlink() and not arm.exists()
    assert hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest() == digest

  def test_unremovable_metadata_keeps_the_anchor_and_fails(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """The anchor cannot go: it is what survives, the box is parked, rm exits 1.

    ⚑ THE PROTECTION MOVED, IT DID NOT VANISH.  Under the old anchor-first order an
    unremovable metadata dir held the vaults back; under the anchor-LAST order the
    vaults the user just approved do go, and the METADATA is what survives — because
    the metadata is the address a retry reads, and a purge that stranded its leftovers
    with no address is the failure this series exists to prevent.
    """
    from kanibako.runtime import container

    _create(tmp_home / "proj")
    std = _std(config_file)
    meta = std.boxes / "proj"
    real = container.remove_box_tree
    monkeypatch.setattr(
      container, "remove_box_tree", lambda target: False if target == meta else real(target)
    )
    capsys.readouterr()

    assert _rm("proj", force=True) == 1
    out = capsys.readouterr()
    for arm in (std.primary_vault_ro / "proj", std.primary_vault_rw / "proj"):
      assert not arm.exists(), arm
    assert meta.is_dir()
    assert f"could not fully remove {meta}" in out.err
    # The box is parked, and the printed cure names the retry that finishes it.
    assert "Deregistered 'proj' (metadata retained)." in out.out
    assert "kanibako box rm proj --purge" in out.out

  def test_anchor_kept_retry_by_name_finishes_the_purge(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """The retry the kept anchor exists for: by NAME, after the block is cleared."""
    from kanibako.project import registry_store
    from kanibako.runtime import container

    _create(tmp_home / "proj")
    std = _std(config_file)
    meta = std.boxes / "proj"
    blocked = [meta]
    real = container.remove_box_tree
    monkeypatch.setattr(
      container, "remove_box_tree", lambda target: False if target in blocked else real(target)
    )
    capsys.readouterr()
    assert _rm("proj", force=True) == 1
    capsys.readouterr()
    assert sorted(registry_store.load_deregistered(std.registry)) == ["proj"]

    blocked.clear()
    assert _rm("proj", force=True) == 0
    out = capsys.readouterr()
    assert "Removing deregistered box: proj" in out.out
    assert not meta.exists()
    assert not registry_store.load_deregistered(std.registry)

  def test_empty_plan_says_nothing_is_deleted(self, capsys):
    from kanibako.commands.box._parser import _confirm_purge

    assert _confirm_purge([], force=False) is True
    out = capsys.readouterr().out
    assert "--purge deletes nothing" in out
    assert "Delete these" not in out

  @pytest.mark.parametrize("verb", ["rm --purge", "box purge"])
  def test_dangling_vault_link_is_listed_and_unlinked(
    self,
    verb,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """The primary twin of the standalone case: a vault leaf linking nowhere is listed
    and unlinked, never skipped as "not a dir"."""
    _create(tmp_home / "proj")
    std = _std(config_file)
    leaf = std.primary_vault_ro / "proj"
    nowhere = tmp_home / "nowhere"
    shutil.rmtree(leaf)
    leaf.symlink_to(nowhere)
    capsys.readouterr()

    if verb == "rm --purge":
      assert _rm("proj", force=True) == 0
    else:
      from kanibako import cli

      args = cli.build_parser().parse_args(["box", "purge", str(tmp_home / "proj"), "--force"])
      assert int(args.func(args) or 0) == 0
    assert f"vault ro (your files): {leaf} → {nowhere} (link only; target kept)" in _plan_lines(
      capsys.readouterr().out
    )
    assert not leaf.is_symlink()


class TestUnregisteredStandalone:
  """An unregistered standalone box is reached by its path; ``rm`` adds no entry."""

  def test_plain_rm_changes_nothing_and_names_the_purge(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    from kanibako.commands.box._parser import run_rm

    root = tmp_home / "sa"
    _create(root, standalone=True, register=False)
    registry = _std(config_file).registry
    before = registry.read_bytes() if registry.exists() else None
    capsys.readouterr()

    assert run_rm(argparse.Namespace(target=str(root), purge=False, force=False)) == 1
    err = capsys.readouterr().err
    assert "is not registered; nothing to remove" in err
    assert f"kanibako box rm {root} --purge" in err
    assert (registry.read_bytes() if registry.exists() else None) == before
    assert (root / "box_data").is_dir()

  def test_purge_deletes_the_store_and_adds_no_entry(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True, register=False)
    registry = _std(config_file).registry
    before = registry.read_bytes() if registry.exists() else None
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    assert "from the registry" not in capsys.readouterr().out
    assert not (root / "box_data").exists()
    assert (registry.read_bytes() if registry.exists() else None) == before

  def test_purge_by_path_after_plain_rm_drops_the_parked_entry(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    from kanibako.commands.box._parser import run_rm
    from kanibako.project import registry_store

    root = tmp_home / "sa"
    _create(root, standalone=True)
    registry = _std(config_file).registry
    assert run_rm(argparse.Namespace(target=str(root), purge=False, force=False)) == 0
    assert registry_store.load_deregistered(registry)

    assert _rm(str(root), force=True) == 0
    assert not (root / "box_data").exists()
    assert registry_store.load_deregistered(registry) == {}
    assert registry_store.load_standalone(registry) == {}


class TestStandalonePurge:
  def test_declined_question_keeps_the_registration(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    from kanibako.project import registry_store

    root = tmp_home / "sa"
    _create(root, standalone=True)
    std = _std(config_file)
    before = registry_store.load_standalone(std.registry)
    assert before
    capsys.readouterr()
    _closed_stdin(monkeypatch)

    assert _rm(str(root), force=False) == 2
    assert registry_store.load_standalone(std.registry) == before
    assert (root / "box_data").is_dir()

  def test_symlinked_vault_loses_only_the_link(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    arm = root / "vault" / "rw"
    outside = tmp_home / "elsewhere"
    digest = _symlink_to_canary(arm, outside)
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr().out
    assert f"vault rw (your files): {arm} → {outside} (link only; target kept)" in out
    assert not arm.is_symlink() and not arm.exists()
    assert hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest() == digest

  def test_symlinked_vault_parent_keeps_everything_behind_it(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """A linked ``vault/`` loses only the link; the list is exactly what goes."""
    root = tmp_home / "sa"
    _create(root, standalone=True)
    outside = tmp_home / "extv"
    (root / "vault").rename(outside)
    (root / "vault").symlink_to(outside)
    (outside / "rw" / "canary.txt").write_text("keep me\n")
    digest = hashlib.sha256((outside / "rw" / "canary.txt").read_bytes()).hexdigest()
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert _plan_lines(out.out) == [
      f".gitignore (it holds only kanibako's line): {root / '.gitignore'}",
      f"box canon (kanibako's handbook): {root / 'canon'}",
      f"vault parent folder: {root / 'vault'} → {outside} (link only; target kept)",
      f"box metadata: {root / 'box_data'}",
      # ⚑ LAST, AFTER the anchor: the retry reads these files to re-plan.
      f"workset settings: {root / 'workset.yaml'}",
      f"standalone mark: {root / 'registry.yaml'}",
    ]
    assert f"left the vault at {outside / 'rw'} in place" in out.err
    for gone in ("box_data", "workset.yaml", "registry.yaml", "canon", "vault"):
      assert not (root / gone).exists() and not (root / gone).is_symlink()
    assert (outside / "ro").is_dir()
    assert hashlib.sha256((outside / "rw" / "canary.txt").read_bytes()).hexdigest() == digest

  def test_vault_linked_into_the_project_lists_arms_by_their_real_path(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """The list names ``inner/ro|rw``, which go; the link line keeps ``inner/``."""
    root = tmp_home / "sa"
    _create(root, standalone=True)
    inner = root / "inner"
    (root / "vault").rename(inner)
    (root / "vault").symlink_to(inner)
    (inner / "keep.txt").write_text("mine\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    assert _plan_lines(capsys.readouterr().out) == [
      f".gitignore (it holds only kanibako's line): {root / '.gitignore'}",
      f"box canon (kanibako's handbook): {root / 'canon'}",
      f"vault ro (your files): {inner / 'ro'}",
      f"vault rw (your files): {inner / 'rw'}",
      f"vault parent folder: {root / 'vault'} → {inner} (link only; target kept)",
      f"box metadata: {root / 'box_data'}",
      # ⚑ LAST, AFTER the anchor: the retry reads these files to re-plan.
      f"workset settings: {root / 'workset.yaml'}",
      f"standalone mark: {root / 'registry.yaml'}",
    ]
    assert not (inner / "ro").exists() and not (inner / "rw").exists()
    assert not (root / "vault").is_symlink()
    assert (inner / "keep.txt").read_text() == "mine\n"

  def test_dangling_vault_link_is_listed_and_unlinked(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    nowhere = tmp_home / "nowhere"
    shutil.rmtree(root / "vault")
    (root / "vault").symlink_to(nowhere)
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    assert (
      f"vault parent folder: {root / 'vault'} → {nowhere} (link only; target kept)"
      in _plan_lines(capsys.readouterr().out)
    )
    assert not (root / "vault").is_symlink()

  def test_a_repointed_in_root_arm_is_kept_and_named(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """⚑ task-purgeguard row 1: a box-declared repoint at a folder kanibako did NOT
    create for the vault role is never deleted — it is named as left in place.

    This test used to assert the opposite (``vault rw (your files)`` listed, then gone),
    which is the data loss the dispatch names: the label promised the user's files and
    the purge deleted them.
    """
    root = tmp_home / "sa"
    _create(root, standalone=True)
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  vault_rw: '{meta.workset.path}/data'\n")
    (root / "data").mkdir()
    (root / "data" / "ufile.txt").write_text("mine\n")
    digest = hashlib.sha256((root / "data" / "ufile.txt").read_bytes()).hexdigest()
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    lines = _plan_lines(out.out)
    assert not any(str(root / "data") in line for line in lines), lines
    assert f"left the vault at {root / 'data'} in place" in out.err
    assert (root / "data" / "ufile.txt").read_text() == "mine\n"
    assert hashlib.sha256((root / "data" / "ufile.txt").read_bytes()).hexdigest() == digest
    # ⚑ What genuinely IS kanibako's still goes.
    assert f"workset settings: {root / 'workset.yaml'}" in lines
    assert not (root / "box_data").exists()

  def test_canon_folder_is_listed_and_removed(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """The ``canon/`` create made is the box's: listed, then gone; the user's file stays."""
    root = tmp_home / "sa"
    _create(root, standalone=True)
    assert (root / "canon" / "handbook").is_dir()
    (root / "mine.txt").write_text("mine\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    lines = _plan_lines(capsys.readouterr().out)
    assert f"box canon (kanibako's handbook): {root / 'canon'}" in lines
    listed = [Path(line.split(": ", 1)[1]) for line in lines]
    assert not any(p.exists() or p.is_symlink() for p in listed)
    assert (root / "mine.txt").read_text() == "mine\n"

  def test_symlinked_canon_loses_only_the_link(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    outside = tmp_home / "extcanon"
    (root / "canon").rename(outside)
    (root / "canon").symlink_to(outside)
    (outside / "canary.txt").write_text("keep me\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    assert (
      f"box canon (kanibako's handbook): {root / 'canon'} → {outside} (link only; target kept)"
    ) in _plan_lines(capsys.readouterr().out)
    assert not (root / "canon").is_symlink() and not (root / "canon").exists()
    assert (outside / "canary.txt").read_text() == "keep me\n"

  def test_canon_repointed_outside_the_root_is_kept_and_named(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    outside = tmp_home / "theircanon"
    outside.mkdir()
    (outside / "canary.txt").write_text("keep me\n")
    with (root / "workset.yaml").open("a") as fh:
      fh.write(f"  canon: '{outside}'\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert not any(line.startswith("box canon") for line in _plan_lines(out.out))
    assert f"left the canon folder at {outside} in place" in out.err
    assert (outside / "canary.txt").read_text() == "keep me\n"

  @pytest.mark.parametrize("via_link", [False, True], ids=["at-root", "linked-parent"])
  def test_canon_at_the_root_or_behind_a_linked_parent_is_kept(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    via_link,
  ):
    """Only a tier STRICTLY below the root goes: one AT it, or reached out through a
    linked parent, is kept and named."""
    root = tmp_home / "sa"
    _create(root, standalone=True)
    (root / "mine.txt").write_text("mine\n")
    outside = tmp_home / "ext"
    (outside / "canon").mkdir(parents=True)
    (outside / "canon" / "canary.txt").write_text("keep me\n")
    if via_link:
      (root / "lnk").symlink_to(outside)
    canon, kept = (root / "lnk" / "canon", outside / "canon") if via_link else (root, root)
    with (root / "workset.yaml").open("a") as fh:
      fh.write(f"  canon: '{canon}'\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert not any(line.startswith("box canon") for line in _plan_lines(out.out))
    assert f"left the canon folder at {kept} in place" in out.err
    assert (root / "mine.txt").read_text() == "mine\n"
    assert (outside / "canon" / "canary.txt").read_text() == "keep me\n"
    assert (root / "lnk").is_symlink() == via_link

  def test_canon_left_by_a_null_key_is_kept_and_named(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    (root / "canon" / "mine.txt").write_text("mine\n")
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  canon: null\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert not any(line.startswith("box canon") for line in _plan_lines(out.out))
    assert (
      f"left the canon folder at {root / 'canon'} in place — not the canon tier of this box"
    ) in out.err
    assert (root / "canon" / "mine.txt").read_text() == "mine\n"

  def test_malformed_root_file_is_refused_not_a_miss(
    self,
    config_file,
    tmp_home,
    credentials_dir,
  ):
    from kanibako.errors import ConfigError

    root = tmp_home / "sa"
    _create(root, standalone=True)
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  canon: @meta.workset.path/canon\n")
    before = sorted(p.relative_to(root) for p in root.rglob("*"))

    with pytest.raises(ConfigError, match="is not valid YAML"):
      _rm(str(root), force=True)
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before

  def test_only_kanibakos_gitignore_line_goes_and_is_listed(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n")
    _create(root, standalone=True)
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr().out
    assert f"kanibako's line in: {root / '.gitignore'}" in _plan_lines(out)
    assert "--purge deletes these 7 paths (and 1 edit):" in out
    assert f"Removed kanibako's line in {root / '.gitignore'}" in out
    assert (root / ".gitignore").read_text() == "node_modules/\n"

  def test_gitignore_holding_only_kanibakos_line_is_listed_and_reported_deleted(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    root = tmp_home / "sa"
    _create(root, standalone=True)
    shutil.rmtree(root / "canon")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr().out
    assert "--purge deletes these 7 paths:" in out
    assert f"Deleted {root / '.gitignore'} (it held only kanibako's line)" in out
    assert not (root / ".gitignore").exists()


class TestPurgeGuardRows:
  """The round-2 rows that had no test: the kept list BEFORE the question, the standalone
  anchor kept by an earlier failure and the retry that follows, and the SYSTEM-tier
  exemption — ``workset.boxes`` included, which no test had ever set.

  Each is written from the behavior the row asserts, not from the code now implementing
  it, so a change to that code has to break something here to go unnoticed.
  """

  def test_the_kept_list_prints_before_the_delete_list_and_the_question(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """Row 3: the user sees what SURVIVES before being asked to approve the rest.

    The kept notes used to print only after the run, so the question went out with no
    word about what the purge would not touch — an approval asked blind.
    """
    root = tmp_home / "sa"
    _create(root, standalone=True)
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  vault_rw: '{meta.workset.path}/data'\n")
    (root / "data").mkdir()
    capsys.readouterr()
    _closed_stdin(monkeypatch)

    assert _rm(str(root), force=False) == 2
    out = capsys.readouterr().out
    kept_at = out.index("--purge KEEPS these")
    delete_at = out.index("--purge deletes these")
    asked_at = out.index("Type 'yes' to confirm")
    assert kept_at < delete_at < asked_at, out
    assert f"  kept vault: {root / 'data'}" in out
    # The refusal came only after the whole picture, so nothing moved.
    assert (root / "data").is_dir()
    assert (root / "box_data").is_dir()
    assert (root / "workset.yaml").is_file()

  def test_the_kept_list_is_skipped_when_there_is_nothing_kept(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """The converse half of row 3: no kept block is printed when nothing is kept.

    An empty "KEEPS these 0" header would be noise, and worse, would make the ordering
    rule look satisfied by a purge that kept nothing at all.
    """
    root = tmp_home / "sa"
    _create(root, standalone=True)
    capsys.readouterr()
    _closed_stdin(monkeypatch)

    assert _rm(str(root), force=False) == 2
    out = capsys.readouterr().out
    assert "KEEPS these" not in out
    assert "--purge deletes these" in out

  def test_a_failed_early_delete_keeps_the_standalone_anchor_and_the_retry_finishes(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """Row 1 (standalone): the anchor is KEPT by an earlier failure, then retried.

    ⚑ This is the anchor being SAVED, not the anchor being UNREMOVABLE.  The store is
    listed last, so an earlier step that will not delete stops the anchor from being
    attempted at all — and the box keeps the folder it is still addressable by.
    """
    from kanibako.runtime import container

    root = tmp_home / "sa"
    _create(root, standalone=True)
    store = root / "box_data"
    # ⚑ Block the skeleton PARENT, not an arm: the plan removes ``vault/`` whole when
    # it holds only arms, so blocking ``vault/rw`` alone matches nothing and it goes.
    blocked = [root / "vault"]
    real = container.remove_box_tree
    monkeypatch.setattr(
      container, "remove_box_tree", lambda target: False if target in blocked else real(target)
    )
    capsys.readouterr()

    assert _rm(str(root), force=True) == 1
    err = capsys.readouterr().err
    assert f"Kept metadata: {store}" in err
    assert "re-running the purge can finish" in err
    assert store.is_dir(), "the anchor must survive the aborted run"
    assert (root / "vault").is_dir()

    blocked.clear()
    assert _rm(str(root), force=True) == 0
    capsys.readouterr()
    assert not store.exists()
    assert not (root / "vault").exists()

  def test_a_system_tier_boxes_value_leaves_the_store_removable(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """Row 4: a SYSTEM-tier ``workset.boxes`` is NOT the user's own nomination.

    The keep exists to protect a folder the box's OWN ``workset.yaml`` pointed at —
    one kanibako did not create for the role.  A value inherited from the SYSTEM tier
    is kanibako's own placement, so the containment rule stands and the store goes.
    ``boxes`` is the key no test had ever set at this tier.  The leaf is deliberately
    NOT the default, so the outcome cannot be explained by "it was the default anyway".
    """
    from kanibako.settings.config import system_settings_path
    from kanibako.settings.config_io import dump_doc

    root = tmp_home / "sa"
    dump_doc(system_settings_path(), {"workset": {"boxes": "{meta.workset.path}/store_x"}})
    _create(root, standalone=True)
    store = root / "store_x"
    assert store.is_dir(), "the system value should have placed the store here"
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    err = capsys.readouterr().err
    assert not store.exists()
    assert "left the box store at" not in err

  def test_a_system_tier_vault_value_leaves_the_arm_removable(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """Row 4 again on a vault arm: the exemption turns on WHO declared it, not which key."""
    from kanibako.settings.config import system_settings_path
    from kanibako.settings.config_io import dump_doc

    root = tmp_home / "sa"
    dump_doc(system_settings_path(), {"workset": {"vault_rw": "{meta.workset.path}/vault_w"}})
    _create(root, standalone=True)
    arm = root / "vault_w"
    assert arm.is_dir()
    (arm / "f.txt").write_text("x\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    err = capsys.readouterr().err
    assert not arm.exists()
    assert "left the vault at" not in err

  def test_the_same_store_declared_by_the_box_itself_is_kept(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """The control that makes the two rows above mean something: the SAME non-default
    leaf, declared by the box's OWN ``workset.yaml``, is kept and named.

    Without it both SYSTEM-tier tests could pass on a rule that never looks at who
    declared the value.
    """
    root = tmp_home / "sa"
    _create(root, standalone=True)
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  boxes: '{meta.workset.path}/store_x'\n")
    store = root / "store_x"
    store.mkdir()
    (store / "f.txt").write_text("mine\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    err = capsys.readouterr().err
    assert store.is_dir()
    assert (store / "f.txt").read_text() == "mine\n"
    assert f"left the box store at {store} in place" in err

  def test_a_gone_metadata_path_drops_the_entry_instead_of_claiming_retention(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """Row 2, reachable half one: the parked metadata dir is ALREADY gone, so the
    entry is dropped and nothing is claimed to be kept.

    The old wording risked naming a path that no longer exists as "retained", which
    sends the user looking for a folder that is not there.
    """
    from kanibako.project import registry_store
    from kanibako.runtime import container

    _create(tmp_home / "proj")
    std = _std(config_file)
    meta = std.boxes / "proj"
    blocked = [meta]
    real = container.remove_box_tree
    monkeypatch.setattr(container, "remove_box_tree", lambda t: False if t in blocked else real(t))
    capsys.readouterr()
    assert _rm("proj", force=True) == 1
    assert list(registry_store.load_deregistered(std.registry)) == ["proj"]

    blocked.clear()
    # ⚑ THROUGH THE REAL BOX-TREE REMOVER, not ``shutil.rmtree``: the box home carries
    # the root-owned 555 skeleton ``create`` left, which a bare rmtree cannot take.
    assert real(meta), "the box-tree remover could not clear the metadata"
    capsys.readouterr()

    assert _rm("proj", force=True) == 0
    out = capsys.readouterr().out
    assert "No metadata directory found for 'proj'" in out
    assert "retained" not in out
    assert not registry_store.load_deregistered(std.registry)

  def test_a_kept_anchor_names_the_metadata_path_that_still_exists(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """Row 2, reachable half two: the anchor SURVIVED, so the report names the path it
    is really at and offers the retry that finishes it.
    """
    from kanibako.project import registry_store
    from kanibako.runtime import container

    root = tmp_home / "sa"
    _create(root, standalone=True)
    blocked = [root / "vault"]
    real = container.remove_box_tree
    monkeypatch.setattr(container, "remove_box_tree", lambda t: False if t in blocked else real(t))
    capsys.readouterr()
    assert _rm(str(root), force=True) == 1
    name = list(registry_store.load_deregistered(_std(config_file).registry))[0]

    # ⚑ Now block the ANCHOR itself: the run fails, but the metadata is really there.
    blocked.clear()
    blocked.append(root / "box_data")
    capsys.readouterr()

    assert _rm(name, force=True) == 1
    err = capsys.readouterr().err
    assert f"'{name}' stays deregistered (metadata retained at {root})." in err
    assert "already gone but a delete failed" not in err
    assert f"kanibako box rm {name} --purge" in err


class TestLeftoverDefaultCanon:
  """Item 9: a box that repoints ``workset.canon`` leaves the default ``canon/`` that
  ``create`` stamped.  It used to survive with no Note at all.

  An EMPTY leftover goes, by ``rmdir`` only.  A filled one is the user's content and is
  named, never taken — so the two cases must not be conflated.
  """

  def _repoint_canon(self, root: Path) -> None:
    (root / "mycanon").mkdir()
    with (root / "workset.yaml").open("a") as fh:
      fh.write("  canon: '{meta.workset.path}/mycanon'\n")

  def test_a_create_stamp_left_by_a_repoint_is_removed(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    """⚑ THE REAL ROOT ``canon/`` — what ``kanibako setup`` + ``create`` lay down: the
    workset mold copied into the root, ``handbook/SYS_WORKSET.md`` carrying the template
    text.  ``materialize_canon_skeleton`` is the BOX-HOME skeleton and never runs on a
    root, so building this from it proved a layout no ``create`` makes.
    """
    from kanibako.launch.templates import install_packaged_templates
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    install_packaged_templates(load_std_paths(load_config(config_file)), [])
    root = tmp_home / "sa"
    _create(root, standalone=True)
    self._repoint_canon(root)
    stamped = root / "canon" / "handbook" / "SYS_WORKSET.md"
    assert stamped.is_file() and stamped.stat().st_size > 0, (
      "the real root stamp did not land — this test would prove nothing"
    )
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr().out
    assert f"Removed the default canon folder `create` made: {root / 'canon'}" in out
    assert not (root / "canon").exists()

  def test_one_changed_byte_in_the_template_is_kept_and_named(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """⚑ THE BYTE RULE, on the REAL template: a stamp file the box wrote into is the
    user's even though every PATH in the folder is still a stamp path.  Same length,
    one byte different — so nothing but a content comparison can tell them apart.

    ⚑⚑ The Note reads ``differs from the current template``, NOT ``content kanibako
    did not put there``.  Every path here IS one create installed, so the old claim
    would be false; and kanibako cannot tell this folder from one a box made BEFORE a
    template change — the two are the same bytes on disk.  It says only what it can
    see.  Kept either way; the byte rule that keeps it is unchanged.
    """
    from kanibako.launch.templates import install_packaged_templates
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    install_packaged_templates(load_std_paths(load_config(config_file)), [])
    root = tmp_home / "sa"
    _create(root, standalone=True)
    self._repoint_canon(root)
    stamped = root / "canon" / "handbook" / "SYS_WORKSET.md"
    original = stamped.read_bytes()
    assert len(original) > 1, "setup: the shipped template is not empty"
    flipped = bytes([original[0] ^ 0x01]) + original[1:]
    assert len(flipped) == len(original) and flipped != original
    stamped.write_bytes(flipped)
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    err = capsys.readouterr().err
    assert "differs from the current template" in err, err
    assert "content kanibako did not put there" not in err, err
    assert stamped.read_bytes() == flipped, "the edited template must survive"
    assert (root / "canon").is_dir(), "the folder is kept, not emptied"

  def test_a_filled_default_canon_left_by_a_repoint_is_named_never_taken(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    from kanibako.launch.templates import install_packaged_templates
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    # ⚑ Seed the LIVE mold so the Note can honestly say the template does not hold
    # the added file; with no mold installed the reason is "no template" instead.
    install_packaged_templates(load_std_paths(load_config(config_file)), [])
    root = tmp_home / "sa"
    _create(root, standalone=True)
    self._repoint_canon(root)
    (root / "canon" / "user.txt").write_text("mine\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    err = capsys.readouterr().err
    assert (root / "canon" / "user.txt").read_text() == "mine\n"
    assert f"left the default canon folder {root / 'canon'} in place" not in err
    # ⚑ r3 item 3: reworded to make NO authorship claim. A user file and a file the
    # mold no longer ships are the same fact from here — the template does not have it.
    assert "holds files the current template does not" in err, err

  def test_an_unrepointed_default_canon_is_not_double_handled(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """The ordinary case: the default canon IS the tier, so it is removed once, as the
    handbook tier — not also as a leftover.
    """
    root = tmp_home / "sa"
    _create(root, standalone=True)
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr().out
    assert "empty default canon" not in out
    assert f"box canon (kanibako's handbook): {root / 'canon'}" in out
    assert not (root / "canon").exists()

  def test_the_leftover_never_reaches_the_box_tree_remover(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    """⚑ The plan promises rmdir-only, and this is what proves it: the leftover leaf is
    never handed to the box-tree remover, which is the verb that would recurse into
    whatever had appeared in it since the plan judged it empty.
    """
    from kanibako.launch.templates import install_packaged_templates
    from kanibako.runtime import container
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    # ⚑ Seed the LIVE mold: the stamp is read from it, so with no mold the leftover is
    # never judged removable and this test would prove nothing about the rmdir promise.
    install_packaged_templates(load_std_paths(load_config(config_file)), [])
    root = tmp_home / "sa"
    _create(root, standalone=True)
    self._repoint_canon(root)
    leaf = root / "canon"
    seen: list = []
    real = container.remove_box_tree
    monkeypatch.setattr(container, "remove_box_tree", lambda t: (seen.append(t), real(t))[1])
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    capsys.readouterr()
    assert leaf not in seen, f"the leftover went through the tree remover: {seen}"
    assert not leaf.exists()


class TestCredsWatcherLock:
  """⚑ Measured, not assumed: a standalone's ``proj.metadata_path`` IS its root, so
  the watcher writes ``.kanibako-creds-watcher.lock`` OUTSIDE the store the anchor
  removes — an orphan that outlived every purge.  The plan now owns it, but only an
  UNHELD one: a live watcher's lock is named, never removed.
  """

  def test_an_orphaned_lock_is_listed_and_removed(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    from kanibako.settings.bootstrap import CREDS_WATCHER_LOCK_FILE

    root = tmp_home / "sa"
    _create(root, standalone=True)
    lock = root / CREDS_WATCHER_LOCK_FILE
    lock.write_text("stale\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert any(line.startswith("creds watcher lock:") for line in _plan_lines(out.out)), out.out
    assert not lock.exists()

  def test_a_held_lock_is_kept_named_and_survives(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """A real flock held by another process: the KEPT list shows it BEFORE the
    question, the Note names it after, and the file is still there."""
    from kanibako.settings.bootstrap import CREDS_WATCHER_LOCK_FILE

    root = tmp_home / "sa"
    _create(root, standalone=True)
    lock = root / CREDS_WATCHER_LOCK_FILE
    lock.write_text("held\n")
    holder = subprocess.Popen(
      [
        sys.executable,
        "-c",
        f"import fcntl,sys;fd=open({str(lock)!r},'a');fcntl.flock(fd,fcntl.LOCK_EX);"
        "sys.stdout.write('HELD\\n');sys.stdout.flush();sys.stdin.readline()",
      ],
      stdout=subprocess.PIPE,
      stdin=subprocess.PIPE,
      text=True,
    )
    try:
      assert holder.stdout.readline().strip() == "HELD"
      capsys.readouterr()
      assert _rm(str(root), force=True) == 0
      out = capsys.readouterr()
    finally:
      holder.stdin.write("stop\n")
      holder.stdin.flush()
      holder.wait(timeout=10)

    assert lock.exists(), "a HELD lock was removed"
    assert "kept creds lock" in out.out, out.out
    assert "a live creds watcher holds it" in out.err, out.err
    assert not any(line.startswith("creds watcher lock:") for line in _plan_lines(out.out)), (
      out.out
    )

  def test_a_primary_lock_goes_with_the_anchor_not_as_its_own_step(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
  ):
    """PRIMARY keeps the lock INSIDE the anchor, so the anchor already accounts for
    it — no separate row, and nothing left behind."""
    from kanibako.settings.bootstrap import CREDS_WATCHER_LOCK_FILE

    root = tmp_home / "proj"
    _create(root)
    lock = _std(config_file).boxes / "proj" / CREDS_WATCHER_LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("stale\n")
    capsys.readouterr()

    assert _rm(str(root), force=True) == 0
    out = capsys.readouterr()
    assert not any(line.startswith("creds watcher lock:") for line in _plan_lines(out.out)), (
      out.out
    )
    assert not lock.exists()


class TestEarlyDeleteFailure:
  """⚑ Found by the REAL CLI, not by the suite: with the anchor LAST, the first step is
  the run's first real unlink, and a read-only parent makes ``remove_path`` RAISE
  ``PermissionError`` rather than return ``False``.  Uncaught, the purge died mid-run
  with the registry already rewritten."""

  def test_a_read_only_root_degrades_to_a_kept_anchor_and_the_retry_finishes(
    self,
    config_file,
    tmp_home,
    credentials_dir,
    capsys,
    monkeypatch,
  ):
    from kanibako.runtime.container import ContainerRuntime

    root = tmp_home / "sa"
    _create(root, standalone=True)
    capsys.readouterr()
    os.chmod(root, 0o500)  # readable, but nothing inside can be unlinked
    try:
      # ⚑ A working ``podman unshare rm`` ignores modes and would delete through the
      # read-only root (CI has one): this run models the host where it fails too.
      # The retry below keeps it — the protected canon needs it there.
      with monkeypatch.context() as mp:
        mp.setattr(ContainerRuntime, "unshare_rm", lambda self, path: False)
        rc = _rm(str(root), force=True)
      out = capsys.readouterr()
    finally:
      os.chmod(root, 0o700)

    combined = out.out + out.err
    assert "Traceback" not in combined, combined
    assert rc != 0
    assert "could not fully remove" in out.err
    assert (root / "box_data").is_dir(), "the anchor must survive for the retry"

    assert _rm(str(root), force=True) == 0
    assert not (root / "box_data").exists()
