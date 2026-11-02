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


@pytest.mark.parametrize(
  "text",
  [
    "value: None\n",
    "value: null\n",
    "on: yes\nvalue: off\n",
    "value: first\nvalue: last\n",
    "base: &base {x: 1}\ncopy: *base\n",
    "base: &base {x: 1}\nvalue: {<<: *base, y: 2}\n",
    "value: !!binary YWJj\n",
    "value: 2026-11-02\n",
    "value: 2026-11-02T12:00:00Z\n",
    "value: !!python/object:builtins.object {}\n",
    "value: [unterminated\n",
    "",
    "---\nvalue: 1\n---\nvalue: 2\n",
    "﻿value: café\n",
  ],
)
@pytest.mark.parametrize("encoding", [None, "utf-8-sig", "utf-16", "utf-32"])
def test_native_yaml_outcome_matches_safe_load(text, encoding):
  payload = text if encoding is None else text.encode(encoding)
  try:
    loaded = yaml.safe_load(payload)
  except yaml.YAMLError:
    expected = {}
  else:
    expected = loaded if isinstance(loaded, dict) else {}
  actual = parse_yaml(payload)
  assert actual == expected
  assert [(type(k), type(v)) for k, v in actual.items()] == [
    (type(k), type(v)) for k, v in expected.items()
  ]


def test_native_alias_identity_cycles_and_depth_remain_supported():
  shared = parse_yaml("first: &base {value: 1}\nsecond: *base\n")
  assert shared["first"] is shared["second"]
  cycle = parse_yaml("value: &loop [*loop]\n")["value"]
  assert cycle[0] is cycle
  text = "value: " + "[" * 70 + "1" + "]" * 70
  value = parse_yaml(text)["value"]
  for _ in range(70):
    value = value[0]
  assert value == 1


def test_native_nonfinite_values_remain_floats():
  import math

  data = parse_yaml("positive: .inf\nnegative: -.inf\nnan: .nan\n")
  assert data["positive"] == float("inf")
  assert data["negative"] == float("-inf")
  assert math.isnan(data["nan"])
