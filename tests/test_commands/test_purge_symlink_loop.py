"""purgeloop (kanibako `re-purgeloop` / `re-purgeloop-base`): a purge never crashes on a
symlink loop, never calls one "outside", and never deletes one.

The layout is a standalone box whose ``vault/`` folder is a link to its own name.  Every
path under it -- ``vault/.versions/<box>``, ``vault/rw`` -- resolves through the loop, so
:func:`Path.resolve` raises ``RuntimeError`` on 3.11 and returns the literal path on 3.13.
The two doors took the crash differently; both are pinned here under the 3.11 shape.

⚑ The cure has two parts: the in-tree predicates answer the RETAIN direction
for anything they cannot resolve, and every site that REPORTS a keep names the loop with
``SYMLINK_LOOP_WHY`` instead of a location phrase the user cannot act on.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kanibako.project import workset as W

_LOOP_WHY = (
  "it is a symlink loop that kanibako cannot resolve, so it is left in place for you to look at."
)


def _cli(*argv: str) -> int:
  from kanibako.cli import main

  with pytest.raises(SystemExit) as info:
    main(list(argv))
  return int(info.value.code or 0)


def _standalone(root):
  root.mkdir(parents=True, exist_ok=True)
  assert _cli("create", "--standalone", "--register", str(root)) == 0


class TestTheInTreePredicatesNeverRaiseOnALoop:
  """Cure (a): one helper, both predicates, the RETAIN direction -- not a try/except
  scattered over the call sites that happen to have been reported."""

  @pytest.fixture
  def tree(self, tmp_path):
    root = tmp_path / "boxroot"
    root.mkdir()
    (root / "plain").mkdir()
    loop = root / "vault"
    loop.mkdir()
    shutil.rmtree(loop)
    loop.symlink_to(loop.name)
    return root, loop

  def test_a_looped_path_is_not_in_tree(self, tree):
    root, loop = tree
    assert W._path_in_tree(loop, root) is False

  def test_a_path_under_a_loop_is_not_in_tree(self, tree):
    root, loop = tree
    assert W._path_in_tree(loop / ".versions" / "me", root) is False

  def test_a_looped_path_is_not_STRICTLY_in_tree(self, tree):
    """The sharp one: a link AT *path* is judged at its literal leaf, which DOES sit
    below *root* -- so before the cure a looped link read REMOVABLE here."""
    root, loop = tree
    assert W._strictly_in_tree(loop, root) is False

  def test_a_path_under_a_loop_is_not_STRICTLY_in_tree(self, tree):
    root, loop = tree
    assert W._strictly_in_tree(loop / ".versions" / "me", root) is False

  def test_a_looped_ROOT_is_not_in_tree_either(self, tmp_path):
    """Both arguments resolve, so both can be the loop."""
    looped = tmp_path / "up"
    looped.mkdir()
    shutil.rmtree(looped)
    looped.symlink_to(looped.name)
    inside = looped / "down"
    assert W._path_in_tree(inside, looped) is False
    assert W._strictly_in_tree(inside, looped) is False

  def test_the_hoist_narrows_nothing_a_real_tree_still_answers(self, tree):
    """⚑ Control: the loop branch must not have widened the predicate.  An ordinary
    in-tree path still reads in-tree on both, or this "fix" is a disable."""
    root, _loop = tree
    plain = root / "plain"
    assert W._path_in_tree(plain, root) is True
    assert W._strictly_in_tree(plain, root) is True
    assert W._path_in_tree(root, root) is True
    assert W._strictly_in_tree(root, root) is False


class TestAKeptLoopIsNamedNotPlaced:
  """Cure (b): no Note may describe an unresolvable path by its LOCATION."""

  def _early(self, config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import BoxMode, _early_scope, load_std_paths

    return _early_scope(load_std_paths(load_config(config_file)), BoxMode.standalone)

  def test_the_canon_reason_names_the_loop(self, config_file, tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    canon = root / "canon"
    canon.mkdir()
    shutil.rmtree(canon)
    canon.symlink_to(canon.name)
    assert (
      W.canon_keep_reason(root, canon, early=self._early(config_file)) == W.CANON_SYMLINK_LOOP_WHY
    )

  def test_the_canon_note_does_not_say_left_in_place_twice(self, capsys):
    """``report_retained_canon`` appends ", so it is yours to remove", so the loop
    phrase must be the SHORT one or the sentence repeats itself."""
    W.report_retained_canon(Path("/x/canon"), Path("/x"), W.CANON_SYMLINK_LOOP_WHY)
    err = capsys.readouterr().err
    assert err.count("in place") == 1, err
    assert (
      "— it is a symlink loop that kanibako cannot resolve, so it is yours to remove."
    ) in err, err

  def test_a_kept_vault_is_never_reported_as_outside(self, tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    vault = root / "vault"
    vault.mkdir()
    shutil.rmtree(vault)
    vault.symlink_to(vault.name)
    why = W.retained_vault_reason(root, vault)
    assert why == _LOOP_WHY
    assert "outside" not in why


class TestPurgeDoorAKeepsAndNamesTheLoop:
  """``box purge --force`` on a standalone whose ``vault/`` is a loop: this was the
  reported crash -- ``_purge_one`` -> ``_strictly_in_tree(vault/.versions/<box>)``."""

  def test_the_whole_vault_folder_looped(
    self, config_file, tmp_home, credentials_dir, resolve_as_on_311, capsys
  ):
    root = tmp_home / "p" / "k"
    _standalone(root)
    loop = root / "vault"
    shutil.rmtree(loop)
    loop.symlink_to(loop.name)
    capsys.readouterr()

    assert _cli("box", "purge", "--force", str(root)) == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert f"Note: left the vault at {loop} in place — {_LOOP_WHY}" in err, err
    assert loop.is_symlink() and os.readlink(loop) == "vault"

  def test_the_inner_rw_arm_looped(
    self, config_file, tmp_home, credentials_dir, resolve_as_on_311, capsys
  ):
    root = tmp_home / "p" / "kc"
    _standalone(root)
    loop = root / "vault" / "rw"
    shutil.rmtree(loop)
    loop.symlink_to(loop.name)
    capsys.readouterr()

    assert _cli("box", "purge", "--force", str(root)) == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert f"Note: left the vault at {loop} in place — {_LOOP_WHY}" in err, err
    assert loop.is_symlink()


class TestPurgeDoorBKeepsAndNamesTheLoop:
  """``box rm --purge --force`` -- the other door, same layout, same answer."""

  def test_the_whole_vault_folder_looped(
    self, config_file, tmp_home, credentials_dir, resolve_as_on_311, capsys
  ):
    root = tmp_home / "p" / "kb"
    _standalone(root)
    loop = root / "vault"
    shutil.rmtree(loop)
    loop.symlink_to(loop.name)
    capsys.readouterr()

    assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert f"Note: left the vault at {loop} in place — {_LOOP_WHY}" in err, err
    assert loop.is_symlink() and os.readlink(loop) == "vault"

  def test_the_inner_rw_arm_looped(
    self, config_file, tmp_home, credentials_dir, resolve_as_on_311, capsys
  ):
    root = tmp_home / "p" / "kd"
    _standalone(root)
    loop = root / "vault" / "rw"
    shutil.rmtree(loop)
    loop.symlink_to(loop.name)
    capsys.readouterr()

    assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert f"Note: left the vault at {loop} in place — {_LOOP_WHY}" in err, err
    assert loop.is_symlink()
