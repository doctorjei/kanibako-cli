"""Tests for the persona-grata store: discovery, resolve, and import mapping.

Covers ``kanibako.persona_store``: the discovery root builder, entry location
(store PRESENCE decides persona-vs-plain), the ``.secret_path`` token pointer
resolution (expansion + the relative→persona-dir anchor), and the import
mapping (store entry → candidate ``AgentConfig`` → persisted agent settings,
flat ``self.secret_path.<VAR>`` shape).  Everything here is filesystem-only
under ``tmp_home`` — no container, no start/create wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.persona_store import (
    PersonaEntry,
    build_candidate,
    import_persona_entry,
    locate_entry,
    persist_candidate,
    persona_store_root,
    resolve_secret_path,
)


def _make_store_entry(tmp_home: Path, persona: str = "navigator", harness: str = "codex") -> Path:
    """Lay down ``$XDG_CONFIG_HOME/personas/<persona>/<harness>/``; return persona dir."""
    persona_dir = tmp_home / "config" / "personas" / persona
    (persona_dir / harness).mkdir(parents=True)
    return persona_dir


class TestPersonaStoreRoot:
    def test_honors_absolute_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        assert persona_store_root() == (tmp_path / "cfg" / "personas").resolve()

    def test_unset_falls_back_to_home_dot_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert persona_store_root() == tmp_path / ".config" / "personas"

    def test_relative_xdg_value_ignored_per_spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
        assert persona_store_root() == tmp_path / ".config" / "personas"


class TestLocateEntry:
    def test_hit_with_plus_ref(self, tmp_home):
        persona_dir = _make_store_entry(tmp_home)
        entry = locate_entry("navigator+codex")
        assert entry is not None
        assert entry.node == "navigator℘codex"
        assert entry.persona == "navigator"
        assert entry.harness == "codex"
        assert entry.persona_dir == persona_dir
        assert entry.config_dir == persona_dir / "codex"

    def test_hit_with_canonical_ref(self, tmp_home):
        _make_store_entry(tmp_home)
        entry = locate_entry("navigator℘codex")
        assert entry is not None
        assert entry.node == "navigator℘codex"

    def test_absent_store_dir_is_none(self, tmp_home):
        assert locate_entry("navigator+codex") is None

    def test_persona_dir_without_harness_dir_is_none(self, tmp_home):
        _make_store_entry(tmp_home, harness="codex")
        assert locate_entry("navigator+claude") is None

    def test_bare_ref_is_never_a_persona(self, tmp_home):
        # Even a store dir shaped like a bare name must not turn a bare agent
        # into a persona: a bare ref has no persona segment at all.
        _make_store_entry(tmp_home, persona="claude", harness="claude")
        assert locate_entry("claude") is None

    def test_config_dir_is_a_file_not_dir(self, tmp_home):
        persona_dir = tmp_home / "config" / "personas" / "navigator"
        persona_dir.mkdir(parents=True)
        (persona_dir / "codex").write_text("not a dir\n")
        assert locate_entry("navigator+codex") is None

    def test_dot_dot_persona_never_escapes_store_root(self, tmp_home):
        # "..+claude" would resolve <root>/../claude == $XDG_CONFIG_HOME/claude
        # — an ordinary harness config dir, NOT a store entry.
        # ⚑ CHANGED 2026-08-04: this used to return None from a dot-check inside
        # locate_entry.  '.' is no longer a legal segment character at all, so
        # parse_agent_ref refuses the ref outright and the separate check is
        # gone.  Refusing LOUDLY is strictly better than a silent "not a
        # persona" — but either way the traversal never reaches the filesystem.
        (tmp_home / "config" / "claude").mkdir(parents=True)
        (tmp_home / "config" / "personas").mkdir()
        with pytest.raises(ConfigError):
            locate_entry("..+claude")
        with pytest.raises(ConfigError):
            locate_entry(".+claude")

    def test_dot_dot_harness_never_escapes_persona_dir(self, tmp_home):
        # "navigator+.." would resolve <root>/navigator/.. == the store root.
        # Refused at parse time as above.
        _make_store_entry(tmp_home)
        with pytest.raises(ConfigError):
            locate_entry("navigator+..")

    def test_malformed_ref_raises_config_error(self, tmp_home):
        with pytest.raises(ConfigError):
            locate_entry("navi/gator+codex")
        with pytest.raises(ConfigError):
            locate_entry("")


class TestResolveSecretPath:
    def _entry(self, tmp_home, pointer_line: str | None = None) -> PersonaEntry:
        persona_dir = _make_store_entry(tmp_home)
        if pointer_line is not None:
            (persona_dir / ".secret_path").write_text(pointer_line)
        entry = locate_entry("navigator+codex")
        assert entry is not None
        return entry

    # --- the resolution rule table, one case per row ------------------------

    def test_absolute_path_used_as_is(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == Path("/data/tok")

    def test_tilde_expands_to_home(self, tmp_home):
        entry = self._entry(tmp_home, "~/creds/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == tmp_home / "home" / "creds" / "tok"

    def test_env_var_expands(self, tmp_home):
        # tmp_home sets XDG_DATA_HOME to <tmp>/data
        entry = self._entry(tmp_home, "$XDG_DATA_HOME/p/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == tmp_home / "data" / "p" / "tok"

    def test_dot_relative_anchors_to_persona_dir(self, tmp_home):
        entry = self._entry(tmp_home, "./token\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == entry.persona_dir / "token"

    def test_bare_relative_anchors_to_persona_dir(self, tmp_home):
        entry = self._entry(tmp_home, "token\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == entry.persona_dir / "token"

    # --- anchor + pointer semantics -----------------------------------------

    def test_relative_anchor_is_persona_dir_not_cwd(self, tmp_home, monkeypatch):
        entry = self._entry(tmp_home, "./token\n")
        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        path, _ = resolve_secret_path(entry)
        assert path == entry.persona_dir / "token"
        assert not str(path).startswith(str(elsewhere))

    def test_result_is_always_absolute(self, tmp_home):
        entry = self._entry(tmp_home, "./token\n")
        path, _ = resolve_secret_path(entry)
        assert path is not None and path.is_absolute()

    def test_pointer_resolves_even_when_token_file_absent(self, tmp_home):
        # A pointer, not a read: the token file need not exist yet.
        entry = self._entry(tmp_home, "/nonexistent/place/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == Path("/nonexistent/place/tok")
        assert not path.exists()

    def test_never_reads_the_token_file(self, tmp_home):
        # The token exists but resolve must not open it; unreadable perms would
        # raise on any read attempt.
        entry = self._entry(tmp_home, "./token\n")
        token = entry.persona_dir / "token"
        token.write_text("SECRET")
        token.chmod(0o000)
        try:
            path, error = resolve_secret_path(entry)
        finally:
            token.chmod(0o600)
        assert error is None
        assert path == token

    # --- malformed pointer cases (tolerant: (None, reason), never raises) ---

    def test_missing_pointer_file(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and ".secret_path" in error

    def test_empty_file(self, tmp_home):
        entry = self._entry(tmp_home, "")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "empty" in error

    def test_whitespace_only_file(self, tmp_home):
        entry = self._entry(tmp_home, "   \n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "empty" in error

    def test_multiline_file_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n/other/tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "one line" in error

    def test_blank_second_line_is_still_multiline(self, tmp_home):
        # Two trailing newlines = a (blank) second line -> malformed.
        entry = self._entry(tmp_home, "/data/tok\n\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "one line" in error

    def test_single_trailing_newline_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_no_trailing_newline_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_crlf_single_line_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\r\n")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_not_utf8_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        (entry.persona_dir / ".secret_path").write_bytes(b"\xff\xfe\x00bad")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_pointer_is_a_directory_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        (entry.persona_dir / ".secret_path").mkdir()
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_oversized_pointer_file_is_an_error(self, tmp_home):
        # Tolerance without a slurp: a runaway "pointer" is rejected, not read.
        entry = self._entry(tmp_home, "/data/" + "x" * (17 * 1024))
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "too large" in error

    def test_unresolvable_tilde_user_is_an_error(self, tmp_home):
        # Path.expanduser raises RuntimeError for an unknown ~user; the
        # never-raises-through contract turns it into a warnable reason.
        entry = self._entry(tmp_home, "~kanibako_no_such_user/tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_embedded_nul_is_an_error(self, tmp_home):
        # An embedded NUL raises ValueError inside resolve(); warnable, no raise.
        entry = self._entry(tmp_home, "/data/\x00tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None


class TestImportMapping:
    """Store entry -> candidate AgentConfig -> persisted agent settings (Phase 2).

    Uses the REAL codex/claude readers for the round-trips (the extraction seam
    has its own tests in test_targets/test_persona_settings.py) and a stub
    Target for the contract edges.  Asserts the persisted shape at the FILE
    level: flat ``self.secret_path.<VAR>`` (``self`` IS ``agent.<node>`` — no
    second node embedding) and preservation of everything the import does not
    own (name, run_args, env, transform_settings, node-bind sub-tables).
    """

    _ENDPOINT = "https://api.navigator.example/v1"
    _CODEX_TOML = (
        'model = "gemma4"\n'
        "[model_providers.navigator]\n"
        'name = "navigator"\n'
        f'base_url = "{_ENDPOINT}"\n'
        'wire_api = "responses"\n'
        'env_key = "NAVIGATOR_API_KEY"\n'
    )

    def _codex_entry(self, tmp_home, *, pointer: str | None = "./token",
                     config: str | None = None) -> PersonaEntry:
        persona_dir = _make_store_entry(tmp_home)
        toml = self._CODEX_TOML if config is None else config
        if toml:
            (persona_dir / "codex" / "config.toml").write_text(toml)
        if pointer is not None:
            (persona_dir / ".secret_path").write_text(pointer + "\n")
        entry = locate_entry("navigator+codex")
        assert entry is not None
        return entry

    def _agents_root(self, tmp_home) -> Path:
        return tmp_home / "data" / "agents"

    def _settings_path(self, tmp_home) -> Path:
        from kanibako.settings.agent_config import agent_settings_path

        return agent_settings_path(self._agents_root(tmp_home), "navigator℘codex")

    # --- full-entry round-trip ----------------------------------------------

    def test_full_codex_import_round_trip(self, tmp_home):
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home)
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.error is None and result.warning is None

        path = self._settings_path(tmp_home)
        data = load_doc(path)
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert data["self"]["model"] == "gemma4"
        # FLAT shape: self.secret_path.<VAR>, absolute host path, anchored to
        # the persona dir (./token) — and NO discriminated node sub-table.
        assert data["self"]["secret_path"]["NAVIGATOR_API_KEY"] == str(
            entry.persona_dir / "token"
        )
        assert "navigator℘codex" not in path.read_text()

    def test_full_claude_import_round_trip(self, tmp_home):
        import json

        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.claude.target import ClaudeTarget

        persona_dir = _make_store_entry(tmp_home, harness="claude")
        (persona_dir / "claude" / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_BASE_URL": self._ENDPOINT},
            "model": "gemma4",
        }))
        (persona_dir / ".secret_path").write_text("./token\n")
        entry = locate_entry("navigator+claude")
        assert entry is not None

        result = import_persona_entry(self._agents_root(tmp_home), entry, ClaudeTarget())
        assert result.error is None and result.warning is None
        from kanibako.settings.agent_config import agent_settings_path

        data = load_doc(agent_settings_path(self._agents_root(tmp_home), "navigator℘claude"))
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert data["self"]["secret_path"]["ANTHROPIC_AUTH_TOKEN"] == str(
            entry.persona_dir / "token"
        )

    # --- build/persist split (the S9 verify seam) ----------------------------

    def test_build_candidate_does_not_persist(self, tmp_home):
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home)
        result = build_candidate(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.candidate is not None
        assert result.candidate.state["endpoint"] == self._ENDPOINT
        assert not self._settings_path(tmp_home).exists()

    def test_persist_candidate_commits_the_candidate(self, tmp_home):
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home)
        result = build_candidate(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.candidate is not None
        written = persist_candidate(self._agents_root(tmp_home), entry, result.candidate)
        assert written == self._settings_path(tmp_home)
        assert written.exists()

    # --- partial-store cases --------------------------------------------------

    def test_unresolvable_secret_still_imports_endpoint_model(self, tmp_home):
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home, pointer=None)  # no .secret_path
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.error is None
        assert result.warning is not None and ".secret_path" in result.warning

        data = load_doc(self._settings_path(tmp_home))
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert data["self"]["model"] == "gemma4"
        assert "secret_path" not in data["self"]  # sparse: nothing to write

    def test_unresolvable_secret_keeps_existing_token(self, tmp_home):
        from kanibako.settings.agent_config import AgentConfig, write_agent_config
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        path = self._settings_path(tmp_home)
        write_agent_config(path, AgentConfig(
            secret_path={"NAVIGATOR_API_KEY": "/old/known-good/tok"},
        ))
        entry = self._codex_entry(tmp_home, pointer=None)
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.warning is not None

        data = load_doc(path)
        assert data["self"]["secret_path"]["NAVIGATOR_API_KEY"] == "/old/known-good/tok"
        assert data["self"]["endpoint"] == self._ENDPOINT

    def test_unusable_store_config_is_hard_error_and_writes_nothing(self, tmp_home):
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home, config="")  # dir exists, no config.toml
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.candidate is None
        assert result.error is not None and "codex" in result.error
        assert not self._settings_path(tmp_home).exists()

    def test_endpointless_settings_is_hard_error(self, tmp_home):
        from kanibako.targets.base import AgentInstall, PersonaSettings, Target

        class _EndpointlessTarget(Target):
            @property
            def name(self) -> str:
                return "stub"

            @property
            def display_name(self) -> str:
                return "Stub"

            def detect(self) -> AgentInstall | None:
                return None

            def read_persona_settings(self, config_dir):
                return PersonaSettings(endpoint=None, model="m", auth_env="K")

        entry = self._codex_entry(tmp_home)
        result = import_persona_entry(
            self._agents_root(tmp_home), entry, _EndpointlessTarget(),
        )
        assert result.candidate is None
        assert result.error is not None and "endpoint" in result.error
        assert not self._settings_path(tmp_home).exists()

    def test_model_absent_in_store_leaves_existing_model(self, tmp_home):
        from kanibako.settings.agent_config import AgentConfig, write_agent_config
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        path = self._settings_path(tmp_home)
        write_agent_config(path, AgentConfig(state={"model": "user-set"}))
        toml_no_model = self._CODEX_TOML.replace('model = "gemma4"\n', "")
        entry = self._codex_entry(tmp_home, config=toml_no_model)
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.error is None

        data = load_doc(path)
        assert data["self"]["model"] == "user-set"  # no store value to sync
        assert data["self"]["endpoint"] == self._ENDPOINT

    # --- non-destructive read-modify-write ------------------------------------

    def test_preserves_values_the_import_does_not_own(self, tmp_home):
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        path = self._settings_path(tmp_home)
        path.parent.mkdir(parents=True)
        path.write_text(
            "self:\n"
            "  name: My Codex\n"
            "  run_args:\n"
            "  - --verbose\n"
            "  access: restricted\n"
            "  endpoint: https://stale.example\n"
            "  model: stale-model\n"
            "  env:\n"
            "    KEEP_ME: 'yes'\n"
            "  transform_settings:\n"
            "    theme: dark\n"
            "  secret_path:\n"
            "    OTHER_TOKEN: /keep/this/tok\n"
            "  \"navigator℘codex\":\n"
            "    bindings:\n"
            "      ro:\n"
            "        share: /host/share:/box/share\n"
        )
        entry = self._codex_entry(tmp_home)
        result = import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())
        assert result.error is None and result.warning is None

        data = load_doc(path)
        sec = data["self"]
        # Owned values replaced:
        assert sec["endpoint"] == self._ENDPOINT
        assert sec["model"] == "gemma4"
        assert sec["secret_path"]["NAVIGATOR_API_KEY"] == str(entry.persona_dir / "token")
        # Unowned values preserved verbatim:
        assert sec["name"] == "My Codex"
        assert sec["run_args"] == ["--verbose"]
        assert sec["access"] == "restricted"
        assert sec["env"] == {"KEEP_ME": "yes"}
        assert sec["transform_settings"] == {"theme": "dark"}
        assert sec["secret_path"]["OTHER_TOKEN"] == "/keep/this/tok"
        assert sec["navigator℘codex"]["bindings"]["ro"]["share"] == (
            "/host/share:/box/share"
        )

    def test_reimport_of_unchanged_store_is_idempotent(self, tmp_home):
        from kanibako.plugins.codex.target import CodexTarget

        entry = self._codex_entry(tmp_home)
        root = self._agents_root(tmp_home)
        import_persona_entry(root, entry, CodexTarget())
        first = self._settings_path(tmp_home).read_bytes()
        import_persona_entry(root, entry, CodexTarget())
        assert self._settings_path(tmp_home).read_bytes() == first

    def test_reimport_replaces_stale_owned_values(self, tmp_home):
        from kanibako.settings.agent_config import AgentConfig, write_agent_config
        from kanibako.settings.config_io import load_doc
        from kanibako.plugins.codex.target import CodexTarget

        path = self._settings_path(tmp_home)
        write_agent_config(path, AgentConfig(
            state={"endpoint": "https://old.example", "model": "old"},
            secret_path={"NAVIGATOR_API_KEY": "/old/tok"},
        ))
        entry = self._codex_entry(tmp_home)
        import_persona_entry(self._agents_root(tmp_home), entry, CodexTarget())

        data = load_doc(path)
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert data["self"]["model"] == "gemma4"
        assert data["self"]["secret_path"]["NAVIGATOR_API_KEY"] == str(
            entry.persona_dir / "token"
        )
