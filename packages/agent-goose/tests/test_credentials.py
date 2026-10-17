"""Tests for kanibako.plugins.goose.credentials."""

from __future__ import annotations

import pytest
import yaml

from kanibako.plugins.goose.credentials import parse_yaml


class TestParseYaml:
  def test_mapping_is_returned(self):
    data = {"provider": "anthropic", "model": "claude-4"}
    assert parse_yaml(yaml.safe_dump(data)) == data

  def test_bytes_are_accepted(self):
    assert parse_yaml(b"key: value\n") == {"key": "value"}

  @pytest.mark.parametrize(
    "text",
    [
      ":\n  :\n  - :\n    bad: [unterminated",
      yaml.safe_dump(["a", "b"]),
      "",
      "just text",
    ],
    ids=["invalid", "list", "empty", "scalar"],
  )
  def test_anything_but_a_mapping_is_empty(self, text):
    assert parse_yaml(text) == {}


# NOTE: the host config.yaml allowlist filter (filter_config) was removed in
# 1.6.0 along with the host-config import; its tests are deleted.
#
# NOTE: the bespoke ``refresh_secrets`` / ``writeback_secrets`` host<->box
# secrets.yaml copies were folded into the goose descriptor's ``secrets.yaml``
# ``CredFileSpec`` (realized by the credsync engine); their tests moved to the
# engine-level credsync coverage and are deleted here.  ``read_yaml`` and
# ``write_yaml`` went with the ``config.yaml`` sync, their only user.
