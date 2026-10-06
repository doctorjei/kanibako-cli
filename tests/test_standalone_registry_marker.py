"""The stored ``workset.registry: <None>`` marker DEFINES a standalone root.

``system-design-1.8.0.md`` § Detection & import and the keyspec's standalone
``workset.registry`` row: create writes the marker beside ``workset.kuid`` in the
root ``workset.yaml``, detection reads THAT file's own key (never the cascade), and
``workset set workset.registry=<None>`` on a NAMED workset is refused unless ``--force``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError
from kanibako.settings.config import WORKSET_META_FILE

MARKER_LINE = "  registry: null  # REMOVING THIS WILL BREAK A STANDALONE BOX!"


def _cli(argv: list[str]) -> int:
    from kanibako.cli import main

    try:
        main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _create_standalone(tmp_home: Path) -> Path:
    root = tmp_home / "home" / "sa"
    root.mkdir()
    assert _cli(["create", str(root), "--standalone", "--no-vault"]) == 0
    return root


class TestCreateWritesMarker:
    def test_root_file_carries_the_commented_null_line(
        self, config_file, tmp_home, credentials_dir,
    ):
        root = _create_standalone(tmp_home)
        text = (root / WORKSET_META_FILE).read_text()
        assert MARKER_LINE in text.splitlines()
        doc = yaml.safe_load(text)
        assert doc["workset"]["registry"] is None
        assert doc["workset"]["kuid"]


class TestDumpDocCommented:
    def test_comment_lands_on_the_key_line_and_round_trips(self, tmp_path):
        from kanibako.settings.config_io import dump_doc_commented

        path = tmp_path / "w.yaml"
        data = {"box": {"registry": None}, "workset": {"kuid": "abcde", "registry": None}}
        dump_doc_commented(path, data, ("workset",), "registry", "KEEP ME")
        text = path.read_text()
        assert "  registry: null  # KEEP ME" in text.splitlines()
        assert text.count("# KEEP ME") == 1
        assert yaml.safe_load(text) == data

    def test_absent_anchor_raises_and_writes_nothing(self, tmp_path):
        from kanibako.settings.config_io import dump_doc_commented

        path = tmp_path / "w.yaml"
        with pytest.raises(ConfigError):
            dump_doc_commented(path, {"workset": {"kuid": "abcde"}}, ("workset",),
                               "registry", "KEEP ME")
        assert not path.exists()
