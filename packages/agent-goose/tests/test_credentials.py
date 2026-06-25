"""Tests for kanibako.plugins.goose.credentials."""

from __future__ import annotations

from pathlib import Path

import yaml

from kanibako.plugins.goose.credentials import (
    read_yaml,
    write_yaml,
)


class TestReadYaml:
    def test_file_not_found_returns_empty(self, tmp_path: Path):
        result = read_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_invalid_yaml_returns_empty(self, tmp_path: Path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n  :\n  - :\n    bad: [unterminated")
        result = read_yaml(bad)
        assert result == {}

    def test_valid_yaml_returns_dict(self, tmp_path: Path):
        f = tmp_path / "good.yaml"
        data = {"provider": "anthropic", "model": "claude-4"}
        f.write_text(yaml.safe_dump(data))
        result = read_yaml(f)
        assert result == data

    def test_non_dict_yaml_returns_empty(self, tmp_path: Path):
        f = tmp_path / "list.yaml"
        f.write_text(yaml.safe_dump(["a", "b", "c"]))
        result = read_yaml(f)
        assert result == {}


class TestWriteYaml:
    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c" / "out.yaml"
        write_yaml(target, {"key": "value"})
        assert target.parent.is_dir()

    def test_writes_valid_yaml(self, tmp_path: Path):
        target = tmp_path / "out.yaml"
        data = {"provider": "openai", "model": "gpt-4"}
        write_yaml(target, data)
        loaded = yaml.safe_load(target.read_text())
        assert loaded == data


# NOTE: the host config.yaml allowlist filter (filter_config) was removed in
# 1.6.0 along with the host-config import; its tests are deleted.
#
# NOTE: the bespoke ``refresh_secrets`` / ``writeback_secrets`` host<->box
# secrets.yaml copies were folded into the goose descriptor's ``secrets.yaml``
# ``CredFileSpec`` (realized by the credsync engine); their tests moved to the
# engine-level credsync coverage and are deleted here.
