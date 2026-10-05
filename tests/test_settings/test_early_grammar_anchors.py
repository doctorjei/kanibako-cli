"""The early reader's anchors before a snapshot: the scope's name and the ``system.*`` paths.

DESIGN § 7 step 3: ``{meta.workset.name}`` resolves from ``EarlyScope.workset_name`` and a
``{system.*}`` path from ``EarlySystem.system_paths``, so the spec's own
``workset.channels.mailboxes`` default (keyspec § 2c) resolves for every partition.  Each
check runs in the old and the new reference spelling, both still read (braced-refs plan).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from kanibako.settings.paths import BoxMode, resolve_system_paths
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.workset_dirkeys import EarlyScope, resolve_workset_dir_key
from tests.support.early import early_record

#: The spec's ``workset.channels.mailboxes`` default, in both spellings.
_MAILBOXES_DEFAULT = [
  "{system.channels.mailboxes}/{meta.workset.name}",
  "@system.channels.mailboxes/@meta.workset.name",
]
_PARTITIONS = [
  (BoxMode.primary, None, "__PRIMARY__"),
  (BoxMode.named, "kento", "kento"),
  (BoxMode.standalone, None, "__STANDALONE__"),
]


def _read(value: str, early: EarlyScope, key: str = "channels.mailboxes") -> Path:
  return resolve_workset_dir_key(Path("/ws"), value, "", key=key, early=early)


@pytest.mark.parametrize("value", _MAILBOXES_DEFAULT)
@pytest.mark.parametrize(("mode", "name", "token"), _PARTITIONS)
def test_the_spec_mailboxes_default_resolves_per_partition(value, mode, name, token, tmp_path):
  mailboxes = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)["system.channels.mailboxes"]
  early = early_record(tmp_path, mode=mode, name=name)
  assert _read(value, early) == Path(mailboxes) / token


@pytest.mark.parametrize("value", ["/srv/mb/{meta.workset.name}", "/srv/mb/@meta.workset.name"])
def test_the_name_alone_spells_the_partition(value, tmp_path):
  assert _read(value, early_record(tmp_path, mode=BoxMode.named, name="kento")) == Path("/srv/mb/kento")


@pytest.mark.parametrize("value", ["{system.cache}/x", "@system.cache/x"])
def test_a_system_path_is_read_verbatim_from_the_record(value, tmp_path):
  early = early_record(tmp_path / "a $b {c}", mode=BoxMode.primary)
  cache = resolve_system_paths({}, data_home=tmp_path / "a $b {c}", home=tmp_path / "a $b {c}")
  assert _read(value, early) == Path(cache["system.cache"]) / "x"


@pytest.mark.parametrize("value", _MAILBOXES_DEFAULT)
def test_a_dropped_system_table_refuses_the_ref_naming_why(value, tmp_path):
  early = early_record(tmp_path, mode=BoxMode.primary)
  failed = EarlyScope(
    replace(early.system, system_paths={}, system_refusal="system.cache is null"), early.workset_name,
  )
  with pytest.raises(SettingsError, match="'@system.channels.mailboxes' cannot be read: system.cache is null"):
    _read(value, failed)


@pytest.mark.parametrize("value", ["{system.channels.mailboxes}/x", "@system.channels.mailboxes/x"])
def test_a_null_system_path_is_refused(value, tmp_path):
  early = early_record(tmp_path, mode=BoxMode.primary)
  paths = {**early.system.system_paths, "system.channels.mailboxes": None}
  null = EarlyScope(replace(early.system, system_paths=paths), early.workset_name)
  with pytest.raises(SettingsError, match="'@system.channels.mailboxes' is null in"):
    _read(value, null)


@pytest.mark.parametrize("value", ["/z/{system.agent}", "/z/@system.agent", "/z/{config.data}"])
def test_no_other_anchor_is_admitted(value, tmp_path):
  with pytest.raises(SettingsError, match="cannot be resolved here"):
    _read(value, early_record(tmp_path, mode=BoxMode.primary))
