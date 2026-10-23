"""A PARKED standalone is refused at another spelling of its root, as a registered one is.

``box rm <name>`` parks a standalone box: its row moves to ``deregistered`` with the root
spelling it was registered at.  The resolver's [R188] check consulted only REGISTERED
rows, so a launch at another spelling of the same directory (a link, or the directory
behind one) passed, and composed a second box name for one tree.
``refuse_standalone_spelling`` now refuses that spelling.  ``box register <spelling>`` stays the re-registration route.

⚑ Every door case goes through the production parser (``cli.build_parser`` +
``args.func``).
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import pytest

from kanibako.project import registry_store
from kanibako.project.import_reconcile import ImportConflictError

PARKED = "is another path to deregistered standalone box"


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
  monkeypatch.setattr("kanibako.commands.start.seed_new_box", lambda std, config, proj, **kw: None)


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


def _registry_bytes(std) -> bytes | None:
  return std.registry.read_bytes() if std.registry.exists() else None


def _journal_state(std) -> list[str]:
  j = Path(std.journal)
  if not j.exists():
    return []
  return sorted(str(p.relative_to(j)) for p in j.rglob("*")) if j.is_dir() else [j.read_text()]


def _park(config_file, capsys, stored: Path, real: Path):
  """Register the box at *stored*, then park it with a plain ``box rm <name>``."""
  assert _create(stored) == 0
  std = _std(config_file)
  name = next(iter(registry_store.load_standalone(std.registry)))
  assert _door("box", "rm", name) == 0
  assert registry_store.load_standalone(std.registry) == {}
  assert list(registry_store.load_deregistered(std.registry)) == [name]
  (real / "box_data" / "keep.txt").write_text("data")
  capsys.readouterr()
  return std, name


@pytest.fixture
def parked_at_link(config_file, tmp_home, credentials_dir, capsys):
  """Box created and parked at ``lnk``, a link to ``real/sa2``."""
  real, lnk = tmp_home / "real" / "sa2", tmp_home / "lnk"
  real.mkdir(parents=True)
  lnk.symlink_to(real)
  std, name = _park(config_file, capsys, lnk, real)
  return std, name, real, lnk


@pytest.fixture
def parked_at_dir(config_file, tmp_home, credentials_dir, capsys):
  """Box created and parked at ``real/sa2``; ``lnk`` links to it afterwards."""
  real, lnk = tmp_home / "real" / "sa2", tmp_home / "lnk"
  std, name = _park(config_file, capsys, real, real)
  lnk.symlink_to(real)
  return std, name, real, lnk


def _resolve(std, where: Path, monkeypatch):
  """``start``'s resolver (``resolve_any_project``) from ``cd <where>``."""
  from kanibako.settings.config import load_config, user_config_file
  from kanibako.settings.paths import resolve_any_project

  monkeypatch.chdir(where)
  monkeypatch.setenv("PWD", str(where))
  return resolve_any_project(std, load_config(user_config_file()), None, initialize=True)


class TestTheOtherSpellingIsRefused:
  """Nothing is written on a refusal: tree, registry, and journal are byte-identical."""

  @pytest.mark.parametrize(
    "fixture,spelling",
    [
      ("parked_at_link", "real"),
      ("parked_at_dir", "lnk"),
    ],
  )
  def test_start_resolution(self, request, fixture, spelling, monkeypatch):
    std, name, real, lnk = request.getfixturevalue(fixture)
    other = real if spelling == "real" else lnk
    stored = lnk if spelling == "real" else real
    before = (_digest(real), _registry_bytes(std), _journal_state(std))
    with pytest.raises(ImportConflictError, match=PARKED) as exc:
      _resolve(std, other, monkeypatch)
    msg = str(exc.value)
    assert f"'{name}' (kept at {stored})" in msg
    assert f"Work from {stored}, or register it at this path: kanibako box register {other}" in msg
    assert (_digest(real), _registry_bytes(std), _journal_state(std)) == before

  def test_info_by_path(self, parked_at_link):
    std, _name, real, _lnk = parked_at_link
    before = (_digest(real), _registry_bytes(std))
    with pytest.raises(ImportConflictError, match=PARKED):
      _door("box", "info", str(real))
    assert (_digest(real), _registry_bytes(std)) == before

  def test_box_purge_deletes_nothing(self, parked_at_link):
    std, _name, real, _lnk = parked_at_link
    before = (_digest(real), _registry_bytes(std))
    with pytest.raises(ImportConflictError, match=PARKED):
      _door("box", "purge", "--force", str(real))
    assert (_digest(real), _registry_bytes(std)) == before

  def test_box_rm_purge_by_path_deletes_nothing(self, parked_at_link):
    std, _name, real, _lnk = parked_at_link
    before = (_digest(real), _registry_bytes(std))
    with pytest.raises(ImportConflictError, match=PARKED):
      _door("box", "rm", str(real), "--purge", "--force")
    assert (_digest(real), _registry_bytes(std)) == before

  def test_create_at_the_other_spelling(self, parked_at_link, capsys):
    std, _name, real, _lnk = parked_at_link
    before = (_digest(real), _registry_bytes(std))
    assert _create(real) == 1
    assert PARKED in capsys.readouterr().err
    assert (_digest(real), _registry_bytes(std)) == before


class TestTheRoutesThatStayOpen:
  def test_stored_spelling_resolves(self, parked_at_link, monkeypatch):
    std, _name, _real, lnk = parked_at_link
    from kanibako.settings.paths import BoxMode

    proj = _resolve(std, lnk, monkeypatch)
    assert proj.mode is BoxMode.standalone

  def test_box_register_at_the_other_spelling_re_registers(self, parked_at_link):
    std, name, real, _lnk = parked_at_link
    assert _door("box", "register", str(real)) == 0
    rows = registry_store.load_standalone(std.registry)
    assert list(rows.values()) == [str(real)]
    assert name not in rows
    assert registry_store.load_deregistered(std.registry) == {}

  def test_rm_purge_by_name_still_purges(self, parked_at_link):
    std, name, real, _lnk = parked_at_link
    assert _door("box", "rm", name, "--purge", "--force") == 0
    assert not (real / "box_data").exists()
    assert registry_store.load_deregistered(std.registry) == {}

  def test_a_gone_stored_spelling_refuses_nothing(self, parked_at_link, monkeypatch):
    """The link the box was parked at is removed: no directory is that root any more."""
    from kanibako.settings.paths import BoxMode

    std, _name, real, lnk = parked_at_link
    lnk.unlink()
    assert _resolve(std, real, monkeypatch).mode is BoxMode.standalone

  def test_a_root_registered_at_this_spelling_refuses_nothing(self, parked_at_link, monkeypatch):
    """An active row AT the spelling is that box; the parked row is not consulted."""
    from kanibako.settings.paths import BoxMode

    std, _name, real, _lnk = parked_at_link
    registry_store.register_standalone(std.registry, "other_sa2", real)
    assert _resolve(std, real, monkeypatch).mode is BoxMode.standalone


@pytest.fixture
def parked_with_hard_linked_copy(config_file, tmp_home, credentials_dir, capsys):
  """Box created and parked at ``real/sa2``; ``hl`` is a copy of it whose ``workset.yaml``
  is a hard link to the original's, the one file the identity check reads.

  ⚑ Not ``cp -al``: GitHub runners refuse hard-linking the seeded canon files
  (``fs.protected_hardlinks``), so only ``workset.yaml`` is linked, and it must be ours.
  """
  import shutil

  real, hl = tmp_home / "real" / "sa2", tmp_home / "hl"
  std, name = _park(config_file, capsys, real, real)
  shutil.copytree(real, hl, symlinks=True)
  meta = real / "workset.yaml"
  st = meta.lstat()
  assert meta.is_file() and not meta.is_symlink()
  assert st.st_uid == os.getuid() and st.st_mode & 0o600 == 0o600
  (hl / "workset.yaml").unlink()
  os.link(meta, hl / "workset.yaml")
  return std, name, real, hl


class TestAHardLinkedCopyOfAParkedRoot:
  """A hard-linked ``workset.yaml`` (as ``cp -al`` makes) is one identity ([R188]) in another
  directory.  Registered, [R188] refuses it; parked, the identity pass does."""

  def test_start_resolution_is_refused(self, parked_with_hard_linked_copy, monkeypatch):
    std, name, real, hl = parked_with_hard_linked_copy
    before = (_digest(real), _digest(hl), _registry_bytes(std), _journal_state(std))
    with pytest.raises(ImportConflictError, match=PARKED) as exc:
      _resolve(std, hl, monkeypatch)
    assert f"'{name}' (kept at {real})" in str(exc.value)
    assert (_digest(real), _digest(hl), _registry_bytes(std), _journal_state(std)) == before

  def test_box_purge_of_the_copy_deletes_nothing(self, parked_with_hard_linked_copy):
    std, _name, real, hl = parked_with_hard_linked_copy
    before = (_digest(real), _digest(hl), _registry_bytes(std))
    with pytest.raises(ImportConflictError, match=PARKED):
      _door("box", "purge", "--force", str(hl))
    assert (_digest(real), _digest(hl), _registry_bytes(std)) == before

  def test_the_stored_root_still_resolves(self, parked_with_hard_linked_copy, monkeypatch):
    from kanibako.settings.paths import BoxMode

    std, _name, real, _hl = parked_with_hard_linked_copy
    assert _resolve(std, real, monkeypatch).mode is BoxMode.standalone

  def test_a_plain_copy_is_not_this_identity(
    self, config_file, tmp_home, credentials_dir, capsys, monkeypatch
  ):
    """``cp -a`` writes a new ``workset.yaml`` file: the identity pass is silent."""
    import shutil

    real, cp = tmp_home / "real" / "sa2", tmp_home / "cp"
    std, _name = _park(config_file, capsys, real, real)
    shutil.copytree(real, cp, symlinks=True)
    try:
      _resolve(std, cp, monkeypatch)
    except ImportConflictError as e:
      assert PARKED not in str(e), e
