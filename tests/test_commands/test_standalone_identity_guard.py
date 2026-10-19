"""One standalone identity, two registered spellings, is refused at every door (S6, S5b).

``solo`` and ``b`` are registered standalone boxes; ``b`` is then replaced by a link to
``solo``.  Before the guard, ``b``'s verbs acted on ``solo``'s tree: a purge deleted its
``box_data``, canon, and vaults, a move carried it away, and a snapshot moved its store.
A hard-linked ``workset.yaml`` is the same identity by another spelling, so ``create``
refuses it too; a genuine copy keeps the warn-and-re-mint.

⚑ Every case goes through the production parser (``cli.build_parser`` + ``args.func``).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
from pathlib import Path

import pytest

from kanibako.project.import_reconcile import ImportConflictError

SECOND = "is another path to standalone box"


def _door(*argv: str) -> int:
  from kanibako import cli

  args = cli.build_parser().parse_args(list(argv))
  return int(args.func(args) or 0)


def _std(config_file):
  from kanibako.settings.config import load_config
  from kanibako.settings.paths import load_std_paths

  return load_std_paths(load_config(config_file))


def _create(path: Path, *, register: bool = True) -> int:
  from kanibako.commands.box._parser import run_create

  path.mkdir(parents=True, exist_ok=True)
  return run_create(
    argparse.Namespace(
      path=str(path),
      standalone=True,
      no_vault=False,
      name=None,
      image=None,
      agent=None,
      allow_home=False,
      recover=False,
      register=register,
    )
  )


def _digest(root: Path) -> str:
  """Every path under *root* with its content (a link by its text): any change shows."""
  h = hashlib.sha256()
  for p in sorted(root.rglob("*")):
    h.update(str(p.relative_to(root)).encode())
    if p.is_symlink():
      h.update(os.readlink(p).encode())
    elif p.is_file():
      h.update(p.read_bytes())
  return h.hexdigest()


def _registered(std) -> dict[str, str]:
  from kanibako.project import registry_store

  return registry_store.load_standalone(std.registry)


def _make_twin(base: Path, config_file, capsys):
  """``solo`` and ``b`` registered under *base*; ``b`` replaced by ``ln -s solo b``."""
  solo, b = base / "solo", base / "b"
  assert _create(solo) == 0
  assert _create(b) == 0
  (solo / "box_data" / "solo-file.txt").write_text("data")
  (solo / "vault" / "rw").mkdir(parents=True, exist_ok=True)
  (solo / "vault" / "rw" / "solo-vault.txt").write_text("v")
  shutil.move(str(b), str(base / "aside-b"))
  b.symlink_to(solo)
  std = _std(config_file)
  b_name = next(n for n, r in _registered(std).items() if r == str(b))
  capsys.readouterr()
  return std, solo, b, b_name


@pytest.fixture
def twin(config_file, tmp_home, credentials_dir, capsys):
  return _make_twin(tmp_home, config_file, capsys)


class TestTheSecondSpellingIsRefused:
  """S6: every verb that reaches ``solo`` through ``b`` refuses, writing nothing."""

  def test_info_by_path(self, twin):
    std, solo, b, _ = twin
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND) as exc:
      _door("box", "info", str(b))
    assert f"registered at {solo}" in str(exc.value)
    assert _digest(solo) == before

  def test_info_by_name(self, twin):
    _std_, solo, _b, b_name = twin
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "info", b_name)

  def test_start_resolution_from_cwd(self, twin, monkeypatch):
    """``start``'s resolver (``resolve_any_project``) from ``cd b``."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import resolve_any_project

    std, solo, b, _ = twin
    monkeypatch.chdir(b)
    monkeypatch.setenv("PWD", str(b))
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND):
      resolve_any_project(std, load_config(user_config_file()), None, initialize=True)
    assert _digest(solo) == before

  def test_vault_snapshot(self, twin):
    std, solo, b, _ = twin
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "vault", "snapshot", str(b))
    assert _digest(solo) == before

  def test_rm_purge_by_name(self, twin, capsys):
    std, solo, _b, b_name = twin
    before, rows = _digest(solo), _registered(std)
    rc = _door("box", "rm", b_name, "--purge", "--force")
    assert rc == 1
    err = capsys.readouterr().err
    assert SECOND in err
    assert f"or drop this registration with 'kanibako box rm {b_name}'." in err
    assert _digest(solo) == before
    assert _registered(std) == rows

  def test_rm_purge_by_path(self, twin):
    std, solo, b, _ = twin
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "rm", str(b), "--purge", "--force")
    assert _digest(solo) == before

  def test_purge(self, twin):
    std, solo, b, _ = twin
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "purge", "--force", str(b))
    assert _digest(solo) == before

  def test_move(self, twin, tmp_home):
    std, solo, b, _ = twin
    before = _digest(solo)
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "move", "--force", str(b), str(tmp_home / "moved"))
    assert _digest(solo) == before
    assert not (tmp_home / "moved").exists()

  def test_plain_rm_unregisters_the_second_spelling(self, twin):
    """CONTROL (the cure): ``rm`` without ``--purge`` drops ``b`` and deletes nothing,
    and a purge by ``b``'s name cannot reach ``solo`` afterwards."""
    std, solo, _b, b_name = twin
    before = _digest(solo)
    assert _door("box", "rm", b_name) == 0
    assert b_name not in _registered(std)
    assert _door("box", "rm", b_name, "--purge", "--force") == 1
    assert _digest(solo) == before

  def test_plain_rm_by_path_unregisters_the_second_spelling(self, twin):
    """The cure works by PATH too: ``rm <b>`` drops ``b``'s row and deletes nothing."""
    std, solo, b, b_name = twin
    before = _digest(solo)
    assert _door("box", "rm", str(b)) == 0
    assert b_name not in _registered(std)
    assert _digest(solo) == before
    assert _door("box", "info", str(solo)) == 0

  def test_the_real_spelling_works_under_a_linked_ancestor(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    """Only the root's OWN leaf decides: under ``anc -> real`` (``/home -> /var/home``)
    ``solo`` still works and only ``b`` is refused."""
    (tmp_home / "real").mkdir()
    (tmp_home / "anc").symlink_to(tmp_home / "real")
    std, solo, b, _ = _make_twin(tmp_home / "anc", config_file, capsys)
    assert _door("box", "info", str(solo)) == 0
    with pytest.raises(ImportConflictError, match=SECOND):
      _door("box", "info", str(b))

  def test_the_physical_spelling_still_works(self, twin, capsys):
    """``solo`` is the directory itself, so it is not refused; ``b`` is the link."""
    std, solo, _b, _ = twin
    assert _door("box", "info", str(solo)) == 0
    assert "_solo" in capsys.readouterr().out


class TestTheStoreAnotherBoxHoldsIsNotAdopted:
  """``solo`` never adopts ``.versions/<kuid>_b``: registered ``b`` composes that name."""

  def test_vault_list_leaves_the_held_store(self, twin, caplog):
    std, solo, _b, b_name = twin
    from kanibako.settings.config import read_workset_kuid
    from kanibako.snapshots import _versions_dir

    held = f"{read_workset_kuid(solo / 'workset.yaml')}_b"
    store = _versions_dir(solo / "vault" / "rw") / held / "20260101T000000Z"
    store.mkdir(parents=True)
    (store / "f.txt").write_text("snap")

    with caplog.at_level(logging.WARNING):
      assert _door("box", "vault", "list", str(solo)) == 0

    assert store.is_dir(), "solo adopted the store registered box b composes"
    assert f"store of the box registered at {_b}" in caplog.text

  def test_unit_held_candidate_is_not_moved(self, tmp_path):
    from kanibako import kuid
    from kanibako.snapshots import _versions_dir, adopt_standalone_store

    k, rw = kuid.generate(), tmp_path / "rw"
    (_versions_dir(rw) / f"{k}_b" / "20260101T000000Z").mkdir(parents=True)
    assert adopt_standalone_store(rw, box_name=f"{k}_solo", held={f"{k}_b": "/h/b"}) is None
    assert (_versions_dir(rw) / f"{k}_b").is_dir()
    assert not (_versions_dir(rw) / f"{k}_solo").exists()

  def test_unit_unheld_candidate_is_still_adopted(self, tmp_path):
    """CONTROL: a renamed box's own store (nobody holds it) is adopted as before."""
    from kanibako import kuid
    from kanibako.snapshots import _versions_dir, adopt_standalone_store

    k, rw = kuid.generate(), tmp_path / "rw"
    (_versions_dir(rw) / f"{k}_old" / "20260101T000000Z").mkdir(parents=True)
    assert adopt_standalone_store(rw, box_name=f"{k}_new", held={f"{k}_b": "/h/b"}) is not None
    assert (_versions_dir(rw) / f"{k}_new" / "20260101T000000Z").is_dir()


class TestCreateOverAnotherSpellingOfTheKuidFile:
  """S5b: the copy guard keys on identity, so a hard-linked ``workset.yaml`` refuses."""

  def test_hard_linked_workset_yaml_is_refused_writing_nothing(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    from kanibako.settings.config import read_workset_kuid

    solo, c = tmp_home / "solo", tmp_home / "c"
    assert _create(solo) == 0
    c.mkdir()
    os.link(solo / "workset.yaml", c / "workset.yaml")
    std = _std(config_file)
    rows, kuid_before = _registered(std), read_workset_kuid(solo / "workset.yaml")
    capsys.readouterr()

    rc = _create(c, register=False)

    assert rc == 1
    err = capsys.readouterr().err
    assert f"{c} {SECOND}" in err
    assert "drop this registration" not in err  # c has no row to drop
    assert sorted(p.name for p in c.iterdir()) == ["workset.yaml"]
    assert (solo / "workset.yaml").stat().st_nlink == 2
    assert read_workset_kuid(solo / "workset.yaml") == kuid_before
    assert _registered(std) == rows

  def test_a_genuine_copy_still_re_mints(self, config_file, tmp_home, credentials_dir, caplog):
    from kanibako.settings.config import read_workset_kuid

    solo, g = tmp_home / "solo", tmp_home / "g"
    assert _create(solo) == 0
    g.mkdir()
    shutil.copy2(solo / "workset.yaml", g / "workset.yaml")
    kuid_solo = read_workset_kuid(solo / "workset.yaml")

    with caplog.at_level(logging.WARNING):
      assert _create(g) == 0

    assert read_workset_kuid(g / "workset.yaml") != kuid_solo
    assert read_workset_kuid(solo / "workset.yaml") == kuid_solo
    assert "this box gets a new kuid" in caplog.text

  def test_the_resolver_copy_guard_refuses_before_any_write(
    self, config_file, tmp_home, credentials_dir
  ):
    """The same refusal inside ``resolve_standalone_project``, which every create route
    reaches whether or not its door ran ``check_standalone`` first."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import resolve_standalone_project

    solo, c = tmp_home / "solo", tmp_home / "c"
    assert _create(solo) == 0
    c.mkdir()
    os.link(solo / "workset.yaml", c / "workset.yaml")
    std = _std(config_file)

    with pytest.raises(ImportConflictError, match=SECOND):
      resolve_standalone_project(std, load_config(config_file), str(c), initialize=True)

    assert sorted(p.name for p in c.iterdir()) == ["workset.yaml"]


class TestAPlainRmOfTheSecondSpellingParksNothing:
  """The second spelling's files are ``solo``'s: there is nothing to restore or purge,
  so no entry is parked and no register/purge advice is printed."""

  @pytest.mark.parametrize("by", ["name", "path"])
  def test_no_entry_and_no_advice(self, twin, capsys, by):
    from kanibako.project import registry_store

    std, solo, b, b_name = twin
    before = _digest(solo)
    assert _door("box", "rm", b_name if by == "name" else str(b)) == 0
    out = capsys.readouterr().out

    assert registry_store.lookup_deregistered(std.registry, b_name) is None
    assert "kanibako box register" not in out and "--purge" not in out
    assert "its files are standalone box '" in out and f"at {solo}" in out
    assert _digest(solo) == before

  def test_a_stale_entry_offers_only_the_register_that_drops_it(self, twin, capsys):
    """An entry parked at ``solo``'s directory (an older kanibako) cannot be purged
    (the I4 guard); the one printed route drops it."""
    import shlex

    from kanibako.project import registry_store

    std, solo, b, b_name = twin
    assert _door("box", "rm", b_name) == 0
    registry_store.register_deregistered(
      std.registry, b_name, kind="standalone", workspace=str(b), metadata=str(b)
    )
    capsys.readouterr()
    assert _door("box", "rm", b_name) == 0
    cures = [
      shlex.split(line)
      for line in capsys.readouterr().out.splitlines()
      if line.startswith("  kanibako ")
    ]

    assert cures == [["kanibako", "box", "register", b_name]]
    assert _door(*cures[0][1:]) == 0
    assert registry_store.lookup_deregistered(std.registry, b_name) is None

  def test_a_stale_entry_at_an_unmarked_owner_names_the_marker_first(self, twin, capsys):
    """``register`` checks the marker before the owner: with the marker gone it would
    drop the entry and fail, so the advice restores the marker first."""
    import shlex

    from kanibako.project import registry_store

    std, solo, b, b_name = twin
    assert _door("box", "rm", b_name) == 0
    registry_store.register_deregistered(
      std.registry, b_name, kind="standalone", workspace=str(b), metadata=str(b)
    )
    marker = solo / "registry.yaml"
    kept = marker.read_text()
    marker.unlink()
    capsys.readouterr()
    assert _door("box", "rm", b_name) == 0
    out = capsys.readouterr().out
    cures = [shlex.split(line) for line in out.splitlines() if line.startswith("  kanibako ")]

    owner = registry_store.standalone_name_for_same_dir(std.registry, solo)
    assert (
      f"Its files are standalone box '{owner}', and {b / 'registry.yaml'} is missing; create "
      "that file holding only '__STANDALONE__', then drop "
      "this stale entry:"
    ) in out
    assert cures == [["kanibako", "box", "register", b_name]]
    marker.write_text(kept)
    assert _door(*cures[0][1:]) == 0
    assert registry_store.lookup_deregistered(std.registry, b_name) is None

  def test_the_purge_note_does_not_offer_a_refused_purge(self, twin, capsys):
    """A stale entry parked at a live box's root cannot be purged by name (I4), so
    the purge's note points at ``rm <name>`` instead."""
    from kanibako.project import registry_store

    std, solo, _b, _b_name = twin
    registry_store.register_deregistered(
      std.registry, "stale", kind="standalone", workspace=str(solo), metadata=str(solo)
    )
    assert _door("box", "rm", "stale", "--purge", "--force") == 1  # the I4 refusal
    registry_store.register_deregistered(
      std.registry, "stale", kind="standalone", workspace=str(solo), metadata=str(solo)
    )
    capsys.readouterr()
    assert _door("box", "rm", str(solo), "--purge", "--force") == 0
    err = capsys.readouterr().err

    assert "still parked at this path: 'stale' (standalone)." in err
    assert "Purge one by name" not in err
    assert "For the commands that restore or delete one: kanibako box rm <name>" in err
