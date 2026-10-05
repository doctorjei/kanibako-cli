"""The WORKSET and SYSTEM doors hold the workset EARLY keys to their reader.

A value the reader cannot resolve, stored by ``workset set`` or ``system set`` (the reader
reads the system file beneath the workset's own), makes every reader of a workset refuse
it: ``workset info``/``show``/``connect`` fail and ``box list`` drops the workset.  The
door runs the reader's own resolve (``workset_dirkeys.early_key_set_error``), so the
verdict here is derived from :func:`resolve_workset_dir_key` rather than listed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project.workset import create_workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.paths import BoxMode, _early_scope, resolve_system_paths
from kanibako.settings.settings_keyspace import DECLARED_WORKSET_CHANNEL_LEAVES
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.workset_dirkeys import (
  WORKSET_EARLY_KEYS, EarlyScope, early_system, resolve_workset_dir_key,
)
from tests.support.filenames import CONFIG_FILENAME

_EARLY = sorted(f"workset.{key}" for key in WORKSET_EARLY_KEYS)
#: Values the reader refuses that the ordering rule alone allows: a ref into the key's own
#: set, and a ``$`` it cannot answer.
_REFUSED = ["@{workset.kuid}/z", "/z/$AGENT"]
#: Values the reader reads: the workset root ref, and a literal.
_ACCEPTED = ["@meta.workset.path/x", "/lit/x"]
#: The spec's own same-set channel defaults (settings-keyspace §2c ALL WORKSETS).
_SPEC_CHANNEL_DEFAULTS = {
  "workset.channels.common": "@workset.channelroot/common",
  "workset.channels.chat": "@workset.channelroot/chat",
  "workset.channels.share": "@workset.channelroot/share",
  "workset.channels.broadcast": "@workset.channels.chat/broadcast.md",
}


def _reader_refuses(key: str, value: str, early: EarlyScope) -> bool:
  try:
    resolve_workset_dir_key(Path("/ws"), value, "", key=key.removeprefix("workset."), early=early)
  except SettingsError:
    return True
  return False


@pytest.fixture
def ws(tmp_path, std):
  return create_workset("earlyws", tmp_path / "ws", std)


def _workset_set(key: str, value: str, ws, std, tmp_path: Path) -> str:
  return set_config_value(
    key, value, config_path=ws.root / WORKSET_META_FILE,
    cascade_system_path=tmp_path / "settings.yaml", command_scope=ConfigLevel.workset,
    std=std, ws=ws,
  )


def _system_set(key: str, value: str, std, tmp_path: Path) -> str:
  settings = tmp_path / "settings.yaml"
  return set_config_value(
    key, value, config_path=tmp_path / CONFIG_FILENAME, system_settings_path=settings,
    cascade_system_path=settings, command_scope=ConfigLevel.system,
    agents_root=tmp_path / "agents", std=std,
  )


class TestTheTableIsTheMembership:
  def test_it_holds_every_declared_channel_leaf(self):
    assert {f"channels.{leaf}" for leaf in DECLARED_WORKSET_CHANNEL_LEAVES} <= WORKSET_EARLY_KEYS

  def test_the_reader_refuses_a_key_outside_it(self, tmp_path):
    record = early_system({}, resolve_system_paths({}, data_home=tmp_path, home=tmp_path))
    with pytest.raises(ValueError, match="workset.kuid"):
      resolve_workset_dir_key(Path("/ws"), "/lit", "kuid", key="kuid", early=EarlyScope(record, "ws"))


@pytest.mark.parametrize("key", _EARLY)
class TestTheWorksetDoorAgreesWithTheReader:
  @pytest.mark.parametrize("value", _REFUSED)
  def test_a_value_the_reader_refuses_is_refused_and_not_written(
    self, key, value, ws, std, tmp_path,
  ):
    assert _reader_refuses(key, value, _early_scope(std, BoxMode.named, ws.name))
    message = _workset_set(key, value, ws, std, tmp_path)
    assert message.startswith("Error: nothing was written"), message
    assert f"{key} is set to {value!r}" in message, message
    assert "which cannot be resolved" in message, message
    stored = ws.root / WORKSET_META_FILE
    assert not stored.exists() or value not in stored.read_text()

  @pytest.mark.parametrize("value", _ACCEPTED)
  def test_a_value_the_reader_reads_is_written(self, key, value, ws, std, tmp_path):
    assert not _reader_refuses(key, value, _early_scope(std, BoxMode.named, ws.name))
    message = _workset_set(key, value, ws, std, tmp_path)
    assert not message.startswith("Error:"), message
    assert value in (ws.root / WORKSET_META_FILE).read_text()


@pytest.mark.parametrize("key", _EARLY)
class TestARefIntoALaterSetIsTheOrderingRulesAtBothDoors:
  def test_the_workset_door(self, key, ws, std, tmp_path):
    message = _workset_set(key, "/z/@{box.image}", ws, std, tmp_path)
    assert 'system-design "Ordering rule"' in message, message
    stored = ws.root / WORKSET_META_FILE
    assert not stored.exists() or "box.image" not in stored.read_text()

  def test_the_system_door(self, key, std, tmp_path):
    message = _system_set(key, "/z/@{meta.box.path}", std, tmp_path)
    assert 'system-design "Ordering rule"' in message, message
    assert "could not be read back" not in message, message
    assert not (tmp_path / "settings.yaml").exists()


def test_a_system_ref_is_refused(ws, std, tmp_path):
  """``@system.agent`` is refused by the cascade probe before this door runs; pinned so
  the key stays refused whichever door answers."""
  message = _workset_set("workset.registry", "/z/@{system.agent}", ws, std, tmp_path)
  assert message.startswith("Error:"), message


@pytest.mark.parametrize("key", _EARLY)
class TestTheSystemDoorAgreesWithTheReader:
  def test_a_value_the_reader_refuses_is_refused_and_not_written(self, key, std, tmp_path):
    message = _system_set(key, "/z/$AGENT", std, tmp_path)
    assert message.startswith("Error: nothing was written"), message
    assert str(tmp_path / "settings.yaml") in message, message
    settings = tmp_path / "settings.yaml"
    assert not settings.exists() or "$AGENT" not in settings.read_text()

  def test_a_value_the_reader_reads_is_written(self, key, std, tmp_path):
    message = _system_set(key, "/lit/x", std, tmp_path)
    assert not message.startswith("Error:"), message
    assert "/lit/x" in (tmp_path / "settings.yaml").read_text()


@pytest.mark.parametrize(("key", "value"), sorted(_SPEC_CHANNEL_DEFAULTS.items()))
def test_the_workset_door_takes_the_spec_channel_defaults(key, value, ws, std, tmp_path):
  message = _workset_set(key, value, ws, std, tmp_path)
  assert not message.startswith("Error:"), message
  assert value in (ws.root / WORKSET_META_FILE).read_text()


def test_a_referent_whose_default_splits_by_mode_is_refused(ws, std, tmp_path):
  """``workset.canon`` is read in every mode, and an unset ``workset.boxes`` is
  ``box_data`` in standalone and ``boxes`` elsewhere: no one answer, so it is refused."""
  message = _workset_set("workset.canon", "@workset.boxes/cn", ws, std, tmp_path)
  assert message.startswith("Error: nothing was written"), message
  assert "'@workset.boxes' is unset" in message, message


def test_the_workset_door_reads_an_unset_referent_as_primary_and_named(ws, std, tmp_path):
  """A named workset's file is read as primary/named, where ``workset.boxes`` is one path."""
  message = _workset_set("workset.logs", "@workset.boxes/lg", ws, std, tmp_path)
  assert not message.startswith("Error:"), message
  assert "@workset.boxes/lg" in (ws.root / WORKSET_META_FILE).read_text()


def test_the_system_door_reads_it_in_each_mode_its_readers_pass(std, tmp_path):
  """Standalone reads the system file too; its ``workset.logs`` reader resolves the ref there."""
  message = _system_set("workset.logs", "@workset.boxes/lg", std, tmp_path)
  assert not message.startswith("Error:"), message
  assert "@workset.boxes/lg" in (tmp_path / "settings.yaml").read_text()


def test_a_referent_split_between_primary_and_named_is_refused(ws, std, tmp_path):
  """An unset ``workset.workspaces`` is null in primary and a dir in named: refused."""
  message = _workset_set("workset.logs", "@workset.workspaces/lg", ws, std, tmp_path)
  assert message.startswith("Error: nothing was written"), message
  assert "'@workset.workspaces' is unset" in message, message
