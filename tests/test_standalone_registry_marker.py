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
from kanibako.launch import box_resolve
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.paths import STANDALONE_META_DIR

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


def _info(root: Path, capsys) -> str:
    capsys.readouterr()
    _cli(["box", "info", str(root)])
    out = capsys.readouterr()
    return out.out + out.err


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


class TestDetection:
    def test_created_root_is_standalone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = _create_standalone(tmp_home)
        assert box_resolve.standalone_settings_present(root) is True
        assert "Mode:         standalone" in _info(root, capsys)

    def test_removing_the_line_reads_not_standalone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = _create_standalone(tmp_home)
        settings = root / WORKSET_META_FILE
        kept = [ln for ln in settings.read_text().splitlines() if "registry" not in ln]
        settings.write_text("\n".join(kept) + "\n")
        assert (root / STANDALONE_META_DIR).is_dir()
        assert box_resolve.standalone_settings_present(root) is False
        assert "Mode:         standalone" not in _info(root, capsys)

    def test_box_data_alone_is_not_the_marker(self, tmp_path):
        (tmp_path / STANDALONE_META_DIR).mkdir()
        (tmp_path / WORKSET_META_FILE).write_text("workset:\n  kuid: abcde\n")
        assert box_resolve.standalone_settings_present(tmp_path) is False

    def test_stored_null_is_the_marker_without_box_data(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text("workset:\n  registry: null\n")
        assert box_resolve.standalone_settings_present(tmp_path) is True

    def test_named_stays_named_when_the_system_file_nulls_registry(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        root = tmp_home / "home" / "ws1"
        assert _cli(["workset", "create", str(root), "--name", "ws1"]) == 0
        std = load_std_paths(load_config(config_file))
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text("workset:\n  registry: null\n")
        assert box_resolve.standalone_settings_present(root) is False
