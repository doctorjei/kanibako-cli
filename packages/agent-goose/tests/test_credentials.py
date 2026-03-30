"""Tests for kanibako.plugins.goose.credentials."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from kanibako.plugins.goose.credentials import (
    filter_config,
    read_yaml,
    refresh_secrets,
    write_yaml,
    writeback_secrets,
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


class TestFilterConfig:
    def test_keeps_only_safe_keys(self, tmp_path: Path):
        src = tmp_path / "src.yaml"
        dst = tmp_path / "dst.yaml"
        data = {
            "provider": "anthropic",
            "model": "claude-4",
            "extensions": ["web"],
            "instructions": "be helpful",
            "SECRET_KEY": "should-drop",
            "random_field": "also-dropped",
        }
        src.write_text(yaml.safe_dump(data))
        filter_config(src, dst)
        result = yaml.safe_load(dst.read_text())
        assert set(result.keys()) == {"provider", "model", "extensions", "instructions"}
        assert result["provider"] == "anthropic"

    def test_drops_unknown_keys(self, tmp_path: Path):
        src = tmp_path / "src.yaml"
        dst = tmp_path / "dst.yaml"
        data = {"SECRET": "val", "TOKEN": "val2"}
        src.write_text(yaml.safe_dump(data))
        filter_config(src, dst)
        # All keys are unsafe so filtered dict is empty -> write_yaml writes {}
        result = yaml.safe_load(dst.read_text())
        assert result == {}

    def test_handles_empty_source(self, tmp_path: Path):
        src = tmp_path / "src.yaml"
        dst = tmp_path / "dst.yaml"
        src.write_text("")  # empty file -> safe_load returns None -> read_yaml returns {}
        filter_config(src, dst)
        assert not dst.exists()


class TestRefreshSecrets:
    def test_copies_wholesale_when_project_missing(self, tmp_path: Path):
        host = tmp_path / "host_secrets.yaml"
        project = tmp_path / "sub" / "project_secrets.yaml"
        host.write_text("secret: value\n")

        result = refresh_secrets(host, project)

        assert result is True
        assert project.is_file()
        assert project.read_text() == "secret: value\n"
        mode = project.stat().st_mode & 0o777
        assert mode == 0o600

    def test_skips_when_host_older(self, tmp_path: Path):
        host = tmp_path / "host_secrets.yaml"
        project = tmp_path / "project_secrets.yaml"
        host.write_text("old: data\n")
        # Make project newer
        time.sleep(0.05)
        project.write_text("new: data\n")

        result = refresh_secrets(host, project)

        assert result is False
        assert project.read_text() == "new: data\n"

    def test_updates_when_host_newer(self, tmp_path: Path):
        host = tmp_path / "host_secrets.yaml"
        project = tmp_path / "project_secrets.yaml"
        project.write_text("old: data\n")
        # Make host newer
        time.sleep(0.05)
        host.write_text("updated: data\n")

        result = refresh_secrets(host, project)

        assert result is True
        assert project.read_text() == "updated: data\n"
        mode = project.stat().st_mode & 0o777
        assert mode == 0o600

    def test_noop_when_host_missing(self, tmp_path: Path):
        project = tmp_path / "project_secrets.yaml"
        host = tmp_path / "nonexistent.yaml"

        result = refresh_secrets(host, project)

        assert result is False
        assert not project.exists()


class TestWritebackSecrets:
    def test_calls_cp_if_newer(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "project_secrets.yaml"
        project.write_text("data: value\n")

        fake_host = tmp_path / "fake_host"
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        calls = []
        monkeypatch.setattr(
            "kanibako.plugins.goose.credentials.cp_if_newer",
            lambda src, dst: calls.append((src, dst)) or True,
        )

        writeback_secrets(project)

        assert len(calls) == 1
        assert calls[0][0] == project
        assert calls[0][1] == fake_host / ".config" / "goose" / "secrets.yaml"

    def test_noop_when_project_missing(self, tmp_path: Path, monkeypatch):
        project = tmp_path / "nonexistent.yaml"

        calls = []
        monkeypatch.setattr(
            "kanibako.plugins.goose.credentials.cp_if_newer",
            lambda src, dst: calls.append((src, dst)) or True,
        )

        writeback_secrets(project)

        assert len(calls) == 0
