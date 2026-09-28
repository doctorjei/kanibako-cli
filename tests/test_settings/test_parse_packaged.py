"""``config_io.parse_packaged`` reads kanibako's shipped YAML with libyaml where PyYAML has it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings import config_io
from kanibako.settings.core_defaults import PACKAGED_SETTINGS_PARTS, packaged_data_dir


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML built without libyaml")
def test_the_c_parser_is_used_where_pyyaml_has_it():
  assert config_io._PACKAGED_LOADER is yaml.CSafeLoader


@pytest.mark.parametrize("name", ["core-defaults.yaml", "keyspace-manifest.yaml", "image-baseline.yaml"])
def test_the_shipped_files_parse_to_the_same_document_as_safe_load(name):
  text = Path(str(packaged_data_dir(*PACKAGED_SETTINGS_PARTS, name))).read_text()
  assert config_io.parse_packaged(text) == yaml.safe_load(text)


def test_no_arbitrary_python_object_is_built():
  with pytest.raises(yaml.YAMLError):
    config_io.parse_packaged("!!python/object/apply:os.system ['true']")
