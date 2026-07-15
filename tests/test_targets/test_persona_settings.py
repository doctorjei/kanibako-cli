"""Tests for ``Target.read_persona_settings`` (persona-grata store extraction).

The harness-config extraction seam: each plugin parses ITS OWN rendered config
out of a store entry's ``<pid>/<hid>/`` dir into the harness-neutral
:class:`~kanibako.targets.base.PersonaSettings` triple.  Pure reads, fail-soft
``None`` on anything unusable.  Claude parses ``settings.json``
(``env.ANTHROPIC_BASE_URL`` + top-level ``model``, fixed
``ANTHROPIC_AUTH_TOKEN`` auth var); codex parses ``config.toml`` (the inverse
of the ``CodexModelProvider`` shape kanibako emits — ``base_url``/``env_key``
+ top-level ``model``/``model_provider``).  Goose/no_agent inherit the base
``None`` default.
"""

from __future__ import annotations

import json
from pathlib import Path

from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.plugins.codex.target import CodexTarget
from kanibako.plugins.goose.target import GooseTarget
from kanibako.targets.base import PersonaSettings, Target
from kanibako.targets.no_agent import NoAgentTarget


class TestBaseDefault:
    def test_default_returns_none(self, tmp_path):
        assert NoAgentTarget().read_persona_settings(tmp_path) is None

    def test_goose_and_no_agent_inherit_the_default(self):
        for target in (GooseTarget(), NoAgentTarget()):
            assert (
                target.read_persona_settings.__func__
                is Target.read_persona_settings
            ), f"{target.name} should inherit the base no-op"

    def test_claude_and_codex_override(self):
        for target in (ClaudeTarget(), CodexTarget()):
            assert (
                target.read_persona_settings.__func__
                is not Target.read_persona_settings
            ), f"{target.name} must override read_persona_settings"


def _write_claude_settings(config_dir: Path, data) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps(data))


class TestClaudeReadPersonaSettings:
    def test_well_formed(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://api.navigator.example/v1"},
            "model": "gemma4",
        })
        got = ClaudeTarget().read_persona_settings(tmp_path)
        assert got == PersonaSettings(
            endpoint="https://api.navigator.example/v1",
            model="gemma4",
            auth_env="ANTHROPIC_AUTH_TOKEN",
        )

    def test_model_absent_is_none(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://e.example"},
        })
        got = ClaudeTarget().read_persona_settings(tmp_path)
        assert got is not None
        assert got.model is None
        assert got.endpoint == "https://e.example"

    def test_non_string_model_is_none(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://e.example"},
            "model": 7,
        })
        got = ClaudeTarget().read_persona_settings(tmp_path)
        assert got is not None and got.model is None

    def test_missing_base_url_is_unusable(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": {"OTHER": "x"}, "model": "m"})
        assert ClaudeTarget().read_persona_settings(tmp_path) is None

    def test_empty_base_url_is_unusable(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": ""}})
        assert ClaudeTarget().read_persona_settings(tmp_path) is None

    def test_non_dict_env(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": ["not", "a", "dict"]})
        assert ClaudeTarget().read_persona_settings(tmp_path) is None

    def test_non_object_document(self, tmp_path):
        _write_claude_settings(tmp_path, ["not", "an", "object"])
        assert ClaudeTarget().read_persona_settings(tmp_path) is None

    def test_malformed_json(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json")
        assert ClaudeTarget().read_persona_settings(tmp_path) is None

    def test_absent_file(self, tmp_path):
        assert ClaudeTarget().read_persona_settings(tmp_path) is None


def _write_codex_config(config_dir: Path, text: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(text)


_NAVIGATOR_TABLE = """
[model_providers.navigator]
name = "navigator"
base_url = "https://api.navigator.example/v1"
wire_api = "responses"
env_key = "NAVIGATOR_API_KEY"
"""


class TestCodexReadPersonaSettings:
    def test_well_formed_single_table(self, tmp_path):
        _write_codex_config(tmp_path, 'model = "gemma4"\n' + _NAVIGATOR_TABLE)
        got = CodexTarget().read_persona_settings(tmp_path)
        assert got == PersonaSettings(
            endpoint="https://api.navigator.example/v1",
            model="gemma4",
            auth_env="NAVIGATOR_API_KEY",
        )

    def test_model_provider_selects_among_tables(self, tmp_path):
        _write_codex_config(tmp_path, (
            'model = "gemma4"\nmodel_provider = "navigator"\n'
            + _NAVIGATOR_TABLE
            + '\n[model_providers.other]\n'
            'base_url = "https://other.example"\nenv_key = "OTHER_KEY"\n'
        ))
        got = CodexTarget().read_persona_settings(tmp_path)
        assert got is not None
        assert got.endpoint == "https://api.navigator.example/v1"
        assert got.auth_env == "NAVIGATOR_API_KEY"

    def test_multiple_tables_without_selector_is_ambiguous(self, tmp_path):
        _write_codex_config(tmp_path, (
            _NAVIGATOR_TABLE
            + '\n[model_providers.other]\n'
            'base_url = "https://other.example"\nenv_key = "OTHER_KEY"\n'
        ))
        assert CodexTarget().read_persona_settings(tmp_path) is None

    def test_stale_selector_falls_back_to_single_table(self, tmp_path):
        _write_codex_config(
            tmp_path, 'model_provider = "gone"\n' + _NAVIGATOR_TABLE,
        )
        got = CodexTarget().read_persona_settings(tmp_path)
        assert got is not None
        assert got.auth_env == "NAVIGATOR_API_KEY"

    def test_model_absent_is_none(self, tmp_path):
        _write_codex_config(tmp_path, _NAVIGATOR_TABLE)
        got = CodexTarget().read_persona_settings(tmp_path)
        assert got is not None and got.model is None

    def test_missing_env_key_is_unusable(self, tmp_path):
        _write_codex_config(tmp_path, (
            '[model_providers.navigator]\n'
            'base_url = "https://api.navigator.example/v1"\n'
        ))
        assert CodexTarget().read_persona_settings(tmp_path) is None

    def test_missing_base_url_is_unusable(self, tmp_path):
        _write_codex_config(tmp_path, (
            '[model_providers.navigator]\nenv_key = "NAVIGATOR_API_KEY"\n'
        ))
        assert CodexTarget().read_persona_settings(tmp_path) is None

    def test_no_provider_table(self, tmp_path):
        _write_codex_config(tmp_path, 'model = "gemma4"\n')
        assert CodexTarget().read_persona_settings(tmp_path) is None

    def test_malformed_toml(self, tmp_path):
        _write_codex_config(tmp_path, "[model_providers.navigator\nbroken")
        assert CodexTarget().read_persona_settings(tmp_path) is None

    def test_absent_file(self, tmp_path):
        assert CodexTarget().read_persona_settings(tmp_path) is None
