"""Owned codec roundtrips, not downstream setting-type permissions."""

from datetime import date, datetime, timedelta, timezone
import math

import pytest

from kanibako.settings.config_io import dump_doc, load_doc


@pytest.mark.parametrize(
  "value",
  [
    pytest.param(math.nan, id="nan"),
    pytest.param(math.inf, id="positive-infinity"),
    pytest.param(-math.inf, id="negative-infinity"),
    pytest.param(date(2026, 11, 2), id="date"),
    pytest.param(datetime(2026, 11, 2, 3, 4, 5, 123456), id="naive-datetime"),
    pytest.param(
      datetime(2026, 11, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30))),
      id="offset-datetime",
    ),
    pytest.param(b"abc\x00\xff\r\n", id="bytes"),
  ],
)
def test_owned_writer_roundtrips_typed_values(tmp_path, value):
  path = tmp_path / "document.yaml"
  dump_doc(path, {"data": {"value": value}})

  actual = load_doc(path)["data"]["value"]
  assert type(actual) is type(value)
  if isinstance(value, float):
    assert actual.hex() == value.hex()
  else:
    assert actual == value
  if isinstance(value, datetime):
    assert actual.isoformat() == value.isoformat()


@pytest.mark.parametrize(
  ("value", "expected"),
  [
    ((), []),
    (("{system.state}/source",), ["{system.state}/source"]),
    ((("{$HOME}/source",),), [["{$HOME}/source"]]),
  ],
)
def test_owned_writer_normalizes_tuples_to_lists(tmp_path, value, expected):
  path = tmp_path / "document.yaml"
  dump_doc(path, {"data": {"sequence": value}})

  actual = load_doc(path)["data"]["sequence"]
  assert type(actual) is list
  assert actual == expected


def test_owned_writer_preserves_exact_nested_keys_and_order(tmp_path):
  path = tmp_path / "document.yaml"
  data = {
    "": "{meta.box.home}",
    "@cache": "{$HOME}/cache",
    "Ａ": "fullwidth",
    "A": "ascii",
    "k" * 1025: "long",
  }
  registry = {"__cache__": "~/synthetic/workset", "Cache": "{config.data}/workset"}
  document = {"data": [{"nested": data}], "worksets": registry}
  dump_doc(path, document)

  actual = load_doc(path)
  assert list(actual) == list(document)
  assert list(actual["data"][0]["nested"].items()) == list(data.items())
  assert list(actual["worksets"].items()) == list(registry.items())
  assert actual == document
