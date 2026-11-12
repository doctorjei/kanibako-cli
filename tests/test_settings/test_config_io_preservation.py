"""Legal settings and registry document roundtrips."""

import pytest

from kanibako.settings.config_io import dump_doc, load_doc


@pytest.mark.parametrize(
  "value",
  [
    pytest.param("None", id="null-like-label"),
    pytest.param("on", id="boolean-like-label"),
    pytest.param("2026-11-02", id="date-text"),
    pytest.param("2026-11-02T03:04:05.123456", id="timestamp-text"),
    pytest.param("2026-11-02T03:04:05.123456+05:30", id="offset-timestamp-text"),
    pytest.param("2026-11-02T03:04:05Z", id="utc-timestamp-text"),
    pytest.param("2026-11-02 03:04:05", id="space-separated-timestamp-text"),
  ],
)
def test_owned_writer_roundtrips_quoted_labels(tmp_path, value):
  path = tmp_path / "settings.yaml"
  path.write_text(f'agent:\n  default:\n    label: "{value}"\n')
  document = {"agent": {"default": {"label": value}}}
  assert load_doc(path) == document

  dump_doc(path, document)

  assert load_doc(path) == document


@pytest.mark.parametrize(
  ("value", "expected"),
  [
    (("{meta.box.workspace}",), ["{meta.box.workspace}"]),
    (("{$HOME}/source", "z"), ["{$HOME}/source", "z"]),
    (("~/source", ""), ["~/source", ""]),
  ],
)
def test_owned_writer_normalizes_binding_tuples_to_lists(tmp_path, value, expected):
  path = tmp_path / "settings.yaml"
  dump_doc(path, {"box": {"bindings": {"rw": {"/workspace": value}}}})

  actual = load_doc(path)["box"]["bindings"]["rw"]["/workspace"]
  assert isinstance(actual, list)
  assert actual == expected


def test_owned_writer_preserves_env_keys_and_order(tmp_path):
  path = tmp_path / "settings.yaml"
  env = {"LABEL": "None", "DATE": "2026-11-02", "SOURCE": "{$HOME}/source"}
  document = {"box": {"env": env}}
  dump_doc(path, document)

  actual = load_doc(path)
  assert list(actual["box"]["env"].items()) == list(env.items())
  assert actual == document


def test_owned_writer_preserves_workset_names_and_order(tmp_path):
  path = tmp_path / "registry.yaml"
  worksets = {"Research": str(tmp_path / "research"), "Tools": str(tmp_path / "tools")}
  document = {"worksets": worksets}
  dump_doc(path, document)

  actual = load_doc(path)
  assert list(actual["worksets"].items()) == list(worksets.items())
  assert actual == document
