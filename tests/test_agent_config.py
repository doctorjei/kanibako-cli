"""Tests for kanibako.agent_config: AgentConfig, load/write agent YAML."""

from __future__ import annotations

from kanibako.agent_config import (
    AgentConfig,
    agent_config_path,
    agent_settings_path,
    agents_dir,
    load_agent_config,
    write_agent_config,
)


class TestAgentConfigDefaults:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.name == ""
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}

    def test_custom_values(self):
        cfg = AgentConfig(
            name="Claude Code",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
        )
        assert cfg.name == "Claude Code"
        assert cfg.run_args == ["--verbose"]
        assert cfg.state == {"access": "permissive"}
        assert cfg.env == {"FOO": "bar"}


class TestAgentConfigSecretPath:
    """The secret_path POINTER family (VAR -> host path; secret stays in the file).

    RENAMED from ``env_file`` (rc0-rc2, clean break). Stored DIRECTLY under
    ``self.secret_path.<VAR>`` — ``self`` IS ``agent.<node>`` (the per-agent store dir
    ``agents/<node>/settings.yaml``), so there is NO second ``<node>`` embedding; the
    whole ``self`` table is what ``_agent_partial`` re-roots into the launch cascade.
    The value is a PATH only (never the secret contents).
    """

    def _node_file(self, tmp_path, node="nav℘claude"):
        p = tmp_path / node / "settings.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_default_empty(self):
        assert AgentConfig().secret_path == {}

    def test_custom_value(self):
        cfg = AgentConfig(
            secret_path={"ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"}
        )
        assert cfg.secret_path == {
            "ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"
        }

    def test_load_secret_path_section(self, tmp_path):
        cfg_path = self._node_file(tmp_path)
        cfg_path.write_text(
            'self:\n'
            '  name: "persona"\n'
            '  secret_path:\n'
            '    ANTHROPIC_AUTH_TOKEN: "~/.config/claude/nav/token"\n'
        )
        cfg = load_agent_config(cfg_path)
        # Only the PATH is loaded (a pointer), never any secret value. secret_path
        # sits DIRECTLY under self (self IS agent.<node>) and does NOT leak into
        # flat state (it is a dict, not a scalar knob).
        assert cfg.secret_path == {
            "ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"
        }
        assert "secret_path" not in cfg.state

    def test_load_missing_secret_path_section(self, tmp_path):
        cfg_path = self._node_file(tmp_path)
        cfg_path.write_text('self:\n  name: "x"\n')
        assert load_agent_config(cfg_path).secret_path == {}

    def test_round_trip_secret_path(self, tmp_path):
        path = self._node_file(tmp_path)
        original = AgentConfig(
            name="persona",
            secret_path={"ANTHROPIC_AUTH_TOKEN": "/secure/token"},
        )
        write_agent_config(path, original)
        # The written file stores the PATH (directly under self.secret_path), not
        # any token contents, and NO legacy env_file section.
        content = path.read_text()
        assert "/secure/token" in content
        assert "env_file" not in content
        loaded = load_agent_config(path)
        assert loaded.secret_path == {"ANTHROPIC_AUTH_TOKEN": "/secure/token"}


class TestAgentsDir:
    def test_default(self, tmp_path):
        result = agents_dir(tmp_path)
        assert result == tmp_path / "agents"

    def test_custom(self, tmp_path):
        result = agents_dir(tmp_path, "my-crabs")
        assert result == tmp_path / "my-crabs"

    def test_empty_fallback(self, tmp_path):
        result = agents_dir(tmp_path, "")
        assert result == tmp_path / "agents"


class TestAgentConfigPath:
    def test_path(self, tmp_path):
        # Per-agent settings live INSIDE the store dir: agents/<agent>/settings.yaml
        result = agent_config_path(tmp_path, "claude")
        assert result == tmp_path / "agents" / "claude" / "settings.yaml"

    def test_custom_agents_dir(self, tmp_path):
        result = agent_config_path(tmp_path, "claude", "my-crabs")
        assert result == tmp_path / "my-crabs" / "claude" / "settings.yaml"

    def test_general_agent(self, tmp_path):
        result = agent_config_path(tmp_path, "general")
        assert result == tmp_path / "agents" / "general" / "settings.yaml"


class TestAgentSettingsPath:
    def test_path(self, tmp_path):
        agents_root = tmp_path / "agents"
        result = agent_settings_path(agents_root, "claude")
        assert result == agents_root / "claude" / "settings.yaml"

    def test_general(self, tmp_path):
        agents_root = tmp_path / "agents"
        result = agent_settings_path(agents_root, "general")
        assert result == agents_root / "general" / "settings.yaml"


class TestLoadAgentConfig:
    def test_nonexistent_file_returns_defaults(self, tmp_path):
        cfg = load_agent_config(tmp_path / "missing.yaml")
        assert cfg.name == ""
        assert cfg.run_args == []

    def test_load_all_sections(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  name: "Claude Code"\n'
            '  run_args: ["--verbose", "--debug"]\n'
            '  model: "opus"\n'
            '  access: "permissive"\n'
            '  env:\n'
            '    MY_VAR: "hello"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == "Claude Code"
        assert cfg.run_args == ["--verbose", "--debug"]
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.env == {"MY_VAR": "hello"}

    def test_load_agent_section_only(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  name: "Shell"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == "Shell"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}

    def test_load_state_keys_without_identity(self, tmp_path):
        # [self] with only state keys (no identity keys) → all land in state.
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  access: "safe"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {"access": "safe"}

    def test_load_missing_agent_section(self, tmp_path):
        # A ``self`` section holding only env (no identity/state keys): env still
        # loads, name/state stay empty.
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  env:\n'
            '    FOO: "bar"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {}
        assert cfg.env == {"FOO": "bar"}

    def test_load_empty_file(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text("")
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""

    def test_run_args_must_be_list(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  run_args: "not-a-list"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.run_args == []


class TestWriteAgentConfig:
    def test_write_defaults(self, tmp_path):
        path = tmp_path / "agents" / "test.yaml"
        cfg = AgentConfig()
        write_agent_config(path, cfg)

        assert path.exists()
        content = path.read_text()
        assert 'self:' in content
        assert 'state:' not in content
        # Sparse write: an empty env is NOT materialized (no phantom override).
        assert 'env:' not in content

    def test_write_with_values(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = AgentConfig(
            name="Claude Code",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
        )
        write_agent_config(path, cfg)

        loaded = load_agent_config(path)
        assert loaded.name == "Claude Code"
        assert loaded.run_args == ["--verbose"]
        assert loaded.state == {"access": "permissive"}
        assert loaded.env == {"FOO": "bar"}

    def test_state_folded_into_agent_section(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = AgentConfig(state={"access": "permissive"})
        write_agent_config(path, cfg)

        content = path.read_text()
        # No separate state section; state knobs live under agent.
        assert 'state:' not in content
        loaded = load_agent_config(path)
        assert loaded.state == {"access": "permissive"}

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "agent.yaml"
        write_agent_config(path, AgentConfig())
        assert path.exists()


class TestRoundTrip:
    def test_write_then_load(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig(
            name="Claude Code",
            run_args=["--verbose", "--debug"],
            state={"model": "opus", "access": "permissive"},
            env={"MY_VAR": "hello"},
        )
        write_agent_config(path, original)
        loaded = load_agent_config(path)

        assert loaded.name == original.name
        assert loaded.run_args == original.run_args
        assert loaded.state == original.state
        assert loaded.env == original.env

    def test_round_trip_empty_config(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig()
        write_agent_config(path, original)
        loaded = load_agent_config(path)

        assert loaded.name == ""
        assert loaded.run_args == []
        assert loaded.state == {}
        assert loaded.env == {}

    def test_state_folded_into_single_agent_section(self, tmp_path):
        # Writing state must produce ONE agent section (identity + state),
        # with no separate state section, and load back intact.
        path = tmp_path / "test.yaml"
        original = AgentConfig(
            name="Claude Code",
            run_args=["--verbose"],
            state={"model": "sonnet"},
        )
        write_agent_config(path, original)
        content = path.read_text()
        assert 'state:' not in content
        assert content.count("self:") == 1
        assert 'name: Claude Code' in content
        assert 'model: sonnet' in content

        loaded = load_agent_config(path)
        assert loaded.state == {"model": "sonnet"}
        assert loaded.name == "Claude Code"
        assert loaded.run_args == ["--verbose"]

    def test_round_trip_multiple_run_args(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig(run_args=["--foo", "--bar", "baz"])
        write_agent_config(path, original)
        loaded = load_agent_config(path)
        assert loaded.run_args == ["--foo", "--bar", "baz"]


class TestNodeTablesCarryThrough:
    """The discriminated ``self.<node>.*`` sub-table (node binds) must survive
    the load→write round-trip OPAQUELY.  AgentConfig does not model it (it
    rides ``_agent_partial`` into the launch cascade), but before the
    ``node_tables`` carry every read-modify-write persist (launch adopt,
    persona import) silently DROPPED a user's node binds."""

    _NODE_YAML = (
        "self:\n"
        "  name: Nav\n"
        "  model: gemma4\n"
        "  \"nav℘codex\":\n"
        "    bindings:\n"
        "      ro:\n"
        "        share: /host/share:/box/share\n"
    )

    def test_default_empty(self):
        assert AgentConfig().node_tables == {}

    def test_load_captures_node_sub_table(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(self._NODE_YAML)
        cfg = load_agent_config(path)
        assert cfg.node_tables == {
            "nav℘codex": {"bindings": {"ro": {"share": "/host/share:/box/share"}}}
        }
        # And it is NOT mistaken for flat state (dict-valued entries excluded).
        assert "nav℘codex" not in cfg.state

    def test_round_trip_preserves_node_sub_table(self, tmp_path):
        from kanibako.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text(self._NODE_YAML)
        cfg = load_agent_config(path)
        cfg.state["endpoint"] = "https://e.example"  # a read-modify-write
        write_agent_config(path, cfg)

        data = load_doc(path)
        assert data["self"]["nav℘codex"]["bindings"]["ro"]["share"] == (
            "/host/share:/box/share"
        )
        assert data["self"]["endpoint"] == "https://e.example"

    def test_env_secret_transform_not_double_captured(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  env:\n"
            "    A: b\n"
            "  secret_path:\n"
            "    TOK: /t\n"
            "  transform_settings:\n"
            "    theme: dark\n"
        )
        cfg = load_agent_config(path)
        assert cfg.node_tables == {}

    def test_empty_node_table_not_materialized(self, tmp_path):
        from kanibako.config_io import load_doc

        path = tmp_path / "settings.yaml"
        write_agent_config(path, AgentConfig(node_tables={"nav℘codex": {}}))
        assert "nav℘codex" not in load_doc(path)["self"]

    def test_schema_owned_dict_keys_never_captured(self, tmp_path):
        # Malformed dict-valued identity keys must not ride node_tables (they
        # would clobber the emitted string ``name`` on the next write).
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  name:\n"
            "    weird: 1\n"
            "  run_args:\n"
            "    weird: 2\n"
        )
        cfg = load_agent_config(path)
        assert cfg.node_tables == {}

    def test_write_guard_never_clobbers_modeled_tables(self, tmp_path):
        # A hand-built config cannot smuggle a "node table" named after a
        # modelled key over the real category (guarded at BOTH ends).
        from kanibako.config_io import load_doc

        path = tmp_path / "settings.yaml"
        write_agent_config(path, AgentConfig(
            env={"A": "b"},
            node_tables={"env": {"EVIL": "x"}, "name": {"evil": "y"}},
        ))
        data = load_doc(path)
        assert data["self"]["env"] == {"A": "b"}
        assert data["self"]["name"] == ""
