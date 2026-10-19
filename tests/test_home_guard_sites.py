"""Every ``$HOME`` guard asks :func:`kanibako.utils.is_home_dir`: ``$HOME`` as spelled or resolved,
and a looped link is an ordinary non-home path, never a traceback (``Path.resolve`` raises on 3.11)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.commands.box import _parser
from kanibako.errors import ProjectError
from kanibako.project import import_reconcile, names
from kanibako.settings import paths
from kanibako.settings.config import load_config
from kanibako import utils


@pytest.fixture
def spellings(tmp_home):
  """``$HOME``, a link to it, a looped link, and a sibling directory."""
  home = tmp_home / "home"
  (tmp_home / "to-home").symlink_to(home)
  (tmp_home / "loop-a").symlink_to(tmp_home / "loop-b")
  (tmp_home / "loop-b").symlink_to(tmp_home / "loop-a")
  (tmp_home / "sibling").mkdir()
  return {
    "home": str(home),
    "home/": f"{home}/",
    "link": str(tmp_home / "to-home"),
    "loop": str(tmp_home / "loop-a"),
    "sibling": str(tmp_home / "sibling"),
  }


HOME_SPELLINGS = ("home", "home/", "link")
OTHERS = ("loop", "sibling")


class TestIsHomeDir:
  @pytest.mark.parametrize("which", HOME_SPELLINGS)
  def test_home_in_any_spelling(self, spellings, which):
    assert utils.is_home_dir(spellings[which])

  @pytest.mark.parametrize("which", OTHERS)
  def test_not_home(self, spellings, which):
    assert not utils.is_home_dir(spellings[which])

  def test_home_reached_through_a_link(self, tmp_home, monkeypatch):
    """``$HOME`` itself set to a link: its literal spelling and its target both match."""
    monkeypatch.setenv("HOME", str(tmp_home / "to-home-2"))
    (tmp_home / "to-home-2").symlink_to(tmp_home / "home")
    assert utils.is_home_dir(tmp_home / "to-home-2")
    assert utils.is_home_dir(tmp_home / "home")

  def test_relative_spelling(self, tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home / "home")
    monkeypatch.setenv("PWD", str(tmp_home / "home"))
    assert utils.is_home_dir(".")


def _register_name(registry, p):
  names.register_name(registry, "n", p)


def _register_name_if_absent(registry, p):
  names.register_name_if_absent(registry, "n", p)


@pytest.mark.parametrize(
  "site",
  [_register_name, _register_name_if_absent],
  ids=["register_name", "register_name_if_absent"],
)
class TestNameRegistration:
  @pytest.mark.parametrize("which", HOME_SPELLINGS)
  def test_refuses_home(self, spellings, tmp_home, site, which):
    with pytest.raises(ProjectError, match=r"\$HOME"):
      site(tmp_home / "registry.yaml", spellings[which])

  @pytest.mark.parametrize("which", OTHERS)
  def test_accepts_others(self, spellings, tmp_home, site, which):
    site(tmp_home / "registry.yaml", spellings[which])


@pytest.mark.parametrize("which", HOME_SPELLINGS)
def test_import_declines_home(spellings, tmp_home, which):
  assert (
    import_reconcile.import_named_workset(tmp_home / "registry.yaml", Path(spellings[which]))
    is None
  )


class TestPrimaryNameFree:
  @pytest.mark.parametrize("which", HOME_SPELLINGS)
  def test_refuses_home(self, spellings, tmp_home, which):
    with pytest.raises(ProjectError, match=r"\$HOME"):
      paths.check_primary_box_name_free(tmp_home / "pw", "n", spellings[which])

  @pytest.mark.parametrize("which", OTHERS)
  def test_accepts_others(self, spellings, tmp_home, which):
    paths.check_primary_box_name_free(tmp_home / "pw", "n", spellings[which])


class TestResolveProjectNewHome:
  """The new-project guard compared the LITERAL path to the RESOLVED home: a link to $HOME, or a
  $HOME that is itself a link, created a project at $HOME."""

  def test_refuses_a_link_to_home(self, config_file, spellings):
    std = paths.load_std_paths(load_config(config_file))
    with pytest.raises(ProjectError, match="rooted at .HOME"):
      paths.resolve_project(
        std, load_config(config_file), project_dir=spellings["link"], initialize=True
      )

  def test_refuses_home_when_home_is_a_link(self, config_file, spellings, monkeypatch):
    monkeypatch.setenv("HOME", spellings["link"])
    std = paths.load_std_paths(load_config(config_file))
    with pytest.raises(ProjectError, match="rooted at .HOME"):
      paths.resolve_project(
        std, load_config(config_file), project_dir=spellings["link"], initialize=True
      )


class TestDetectionWalkStop:
  def test_walk_stops_at_a_link_to_home(self, config_file, tmp_home):
    """A standalone marker above a link to $HOME is never adopted: the walk stops at the link."""
    outer = tmp_home / "outer"
    outer.mkdir()
    (outer / "workset.yaml").touch()
    (outer / "registry.yaml").write_text("__STANDALONE__\n")
    (outer / "to-home").symlink_to(tmp_home / "home")
    (tmp_home / "home" / "proj").mkdir()
    config = load_config(config_file)
    result = paths.detect_project_mode(
      outer / "to-home" / "proj", paths.load_std_paths(config), config
    )
    assert result.mode != paths.BoxMode.standalone


class TestCreatePrecheck:
  def _args(self, path, **kw):
    return argparse.Namespace(path=path, name=None, standalone=False, allow_home=False, **kw)

  @pytest.mark.parametrize("which", HOME_SPELLINGS)
  def test_refuses_home(self, spellings, which, capsys):
    assert _parser.precheck_create(self._args(spellings[which])) == 1
    assert "$HOME" in capsys.readouterr().err

  def test_accepts_a_sibling(self, spellings):
    assert _parser.precheck_create(self._args(spellings["sibling"])) is None


class TestAssertDeletable:
  @pytest.mark.parametrize("which", HOME_SPELLINGS)
  def test_refuses_home(self, spellings, which):
    with pytest.raises(ProjectError, match="protected path"):
      _parser._assert_deletable(spellings[which])

  def test_refuses_root(self):
    with pytest.raises(ProjectError, match="protected path"):
      _parser._assert_deletable("/")

  def test_a_looped_link_is_not_a_traceback(self, spellings):
    assert str(_parser._assert_deletable(spellings["loop"])) == spellings["loop"]
