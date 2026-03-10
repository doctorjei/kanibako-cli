"""Tests for tweakcc configuration and integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanibako.tweakcc import (
    TweakccConfig,
    _deep_merge,
    build_merged_config,
    load_external_config,
    load_tweakcc_section,
    resolve_tweakcc_config,
    write_merged_config,
)


class TestLoadTweakccSection:
    def test_present(self):
        data = {"tweakcc": {"enabled": True, "config": "~/.tweakcc/config.json"}}
        assert load_tweakcc_section(data) == {"enabled": True, "config": "~/.tweakcc/config.json"}

    def test_missing(self):
        assert load_tweakcc_section({}) == {}

    def test_empty(self):
        assert load_tweakcc_section({"tweakcc": {}}) == {}


class TestResolveTweakccConfig:
    def test_disabled_by_default(self):
        cfg = resolve_tweakcc_config({})
        assert cfg.enabled is False
        assert cfg.config_path is None
        assert cfg.overrides == {}

    def test_agent_enables(self):
        cfg = resolve_tweakcc_config({"enabled": True})
        assert cfg.enabled is True

    def test_project_overrides_agent(self):
        cfg = resolve_tweakcc_config(
            {"enabled": False, "config": "/agent/config.json"},
            {"enabled": True, "config": "/project/config.json"},
        )
        assert cfg.enabled is True
        assert cfg.config_path == "/project/config.json"

    def test_inline_overrides_preserved(self):
        cfg = resolve_tweakcc_config({
            "enabled": True,
            "settings": {"misc": {"mcpConnectionNonBlocking": True}},
        })
        assert cfg.overrides == {"settings": {"misc": {"mcpConnectionNonBlocking": True}}}

    def test_config_path_stringified(self):
        cfg = resolve_tweakcc_config({"config": Path("/some/path")})
        assert cfg.config_path == "/some/path"


class TestLoadExternalConfig:
    def test_none_path(self):
        assert load_external_config(None) == {}

    def test_missing_file(self):
        assert load_external_config("/nonexistent/config.json") == {}

    def test_valid_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        result = load_external_config(str(cfg))
        assert result == {"settings": {"misc": {"tableFormat": "unicode"}}}

    def test_invalid_json(self, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("{bad json!")
        assert load_external_config(str(cfg)) == {}

    def test_non_dict_json(self, tmp_path):
        cfg = tmp_path / "array.json"
        cfg.write_text("[1, 2, 3]")
        assert load_external_config(str(cfg)) == {}

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".tweakcc" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"key": "val"}))
        result = load_external_config("~/.tweakcc/config.json")
        assert result == {"key": "val"}


class TestDeepMerge:
    def test_flat(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested(self):
        base = {"settings": {"misc": {"a": 1, "b": 2}}}
        override = {"settings": {"misc": {"b": 3, "c": 4}}}
        result = _deep_merge(base, override)
        assert result == {"settings": {"misc": {"a": 1, "b": 3, "c": 4}}}

    def test_nested_replace_non_dict(self):
        base = {"settings": {"misc": {"a": 1}}}
        override = {"settings": {"misc": "replaced"}}
        result = _deep_merge(base, override)
        assert result == {"settings": {"misc": "replaced"}}

    def test_does_not_mutate(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        result = _deep_merge(base, override)
        assert "c" not in base["a"]
        assert result["a"] == {"b": 1, "c": 2}


class TestBuildMergedConfig:
    def test_empty(self):
        cfg = TweakccConfig()
        assert build_merged_config(cfg) == {}

    def test_kanibako_defaults(self):
        cfg = TweakccConfig()
        defaults = {"settings": {"misc": {"mcpConnectionNonBlocking": True}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result == defaults

    def test_external_overrides_defaults(self, tmp_path):
        ext = tmp_path / "config.json"
        ext.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        cfg = TweakccConfig(config_path=str(ext))
        defaults = {"settings": {"misc": {"tableFormat": "ascii", "a": 1}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result["settings"]["misc"]["tableFormat"] == "unicode"
        assert result["settings"]["misc"]["a"] == 1

    def test_inline_overrides_everything(self, tmp_path):
        ext = tmp_path / "config.json"
        ext.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        cfg = TweakccConfig(
            config_path=str(ext),
            overrides={"settings": {"misc": {"tableFormat": "custom"}}},
        )
        defaults = {"settings": {"misc": {"tableFormat": "ascii"}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result["settings"]["misc"]["tableFormat"] == "custom"


class TestWriteMergedConfig:
    def test_write(self, tmp_path):
        output = tmp_path / "sub" / "config.json"
        config = {"settings": {"misc": {"a": 1}}}
        write_merged_config(config, output)
        assert output.exists()
        assert json.loads(output.read_text()) == config

    def test_creates_parents(self, tmp_path):
        output = tmp_path / "deep" / "nested" / "config.json"
        write_merged_config({"key": "val"}, output)
        assert output.exists()


class TestAgentConfigTweakcc:
    """Test that AgentConfig round-trips the tweakcc section."""

    def test_load_with_tweakcc(self, tmp_path):
        from kanibako.agents import load_agent_config

        toml_content = """\
[agent]
name = "Claude Code"
shell = "standard"
default_args = []

[state]
model = "opus"

[env]

[shared]

[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"
"""
        path = tmp_path / "agent.toml"
        path.write_text(toml_content)
        cfg = load_agent_config(path)
        assert cfg.tweakcc == {"enabled": True, "config": "~/.tweakcc/config.json"}

    def test_load_without_tweakcc(self, tmp_path):
        from kanibako.agents import load_agent_config

        toml_content = """\
[agent]
name = "Claude Code"

[state]
"""
        path = tmp_path / "agent.toml"
        path.write_text(toml_content)
        cfg = load_agent_config(path)
        assert cfg.tweakcc == {}

    def test_write_with_tweakcc(self, tmp_path):
        from kanibako.agents import AgentConfig, load_agent_config, write_agent_config

        cfg = AgentConfig(name="Test", tweakcc={"enabled": True, "config": "/path"})
        path = tmp_path / "agent.toml"
        write_agent_config(path, cfg)

        # Round-trip
        loaded = load_agent_config(path)
        assert loaded.tweakcc["enabled"] is True
        assert loaded.tweakcc["config"] == "/path"

    def test_write_without_tweakcc(self, tmp_path):
        from kanibako.agents import AgentConfig, write_agent_config

        cfg = AgentConfig(name="Test")
        path = tmp_path / "agent.toml"
        write_agent_config(path, cfg)
        content = path.read_text()
        assert "[tweakcc]" in content
        assert "# enabled = false" in content
