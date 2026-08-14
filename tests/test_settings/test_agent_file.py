"""Tests for kanibako.settings.agent_file: the per-agent settings file's SHAPE.

Migrated wholesale from ``test_agent_config.py`` when the load/write/route bodies moved into
the boundary module (S1).  What stayed behind there is what does NOT touch a file: the
``AgentConfig`` record's own defaults and the agent store's path helpers.
"""

from __future__ import annotations

import pytest

from kanibako.settings.agent_config import AgentConfig
from kanibako.settings.agent_file import (
    AgentFileLevel,
    _MODELED_KEYS,
    clear_overrides,
    file_spelling,
    level_table,
    load,
    read_leaf,
    remove_leaf,
    save,
    slot_for,
    state_level,
    write_leaf,
)
from kanibako.settings.settings_resolve import SettingsError


class TestSecretPathSection:
    """The secret_path POINTER family (VAR -> host path; secret stays in the file).

    RENAMED from ``env_file`` (rc0-rc2, clean break). Stored DIRECTLY under the file's root
    (``self.secret_path.<VAR>``) — ``self`` IS ``agent.<node>`` (the per-agent store dir
    ``agents/<node>/settings.yaml``), so there is NO second ``<node>`` embedding; the whole root
    table is what ``_agent_partial`` re-roots into the launch cascade.  The value is a PATH only
    (never the secret contents).
    """

    def _node_file(self, tmp_path, node="nav℘claude"):
        p = tmp_path / node / "settings.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_load_secret_path_section(self, tmp_path):
        cfg_path = self._node_file(tmp_path)
        cfg_path.write_text(
            'self:\n'
            '  name: "persona"\n'
            '  secret_path:\n'
            '    ANTHROPIC_AUTH_TOKEN: "~/.config/claude/nav/token"\n'
        )
        cfg = load(cfg_path)
        # Only the PATH is loaded (a pointer), never any secret value. secret_path
        # sits DIRECTLY under the root (self IS agent.<node>) and does NOT leak into
        # flat state (it is a dict, not a scalar knob).
        assert cfg.secret_path == {
            "ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"
        }
        assert "secret_path" not in cfg.state

    def test_load_missing_secret_path_section(self, tmp_path):
        cfg_path = self._node_file(tmp_path)
        cfg_path.write_text('self:\n  name: "x"\n')
        assert load(cfg_path).secret_path == {}

    def test_round_trip_secret_path(self, tmp_path):
        path = self._node_file(tmp_path)
        original = AgentConfig(
            name="persona",
            secret_path={"ANTHROPIC_AUTH_TOKEN": "/secure/token"},
        )
        save(path, original)
        # The written file stores the PATH (directly under the root's secret_path
        # table), not any token contents, and NO legacy env_file section.
        content = path.read_text()
        assert "/secure/token" in content
        assert "env_file" not in content
        loaded = load(path)
        assert loaded.secret_path == {"ANTHROPIC_AUTH_TOKEN": "/secure/token"}


class TestLoad:
    def test_nonexistent_file_returns_defaults(self, tmp_path):
        cfg = load(tmp_path / "missing.yaml")
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
        cfg = load(cfg_path)
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
        cfg = load(cfg_path)
        assert cfg.name == "Shell"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}

    def test_load_state_keys_without_identity(self, tmp_path):
        # A root table with only state keys (no identity keys) → all land in state.
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  access: "safe"\n'
        )
        cfg = load(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {"access": "safe"}

    def test_load_missing_agent_section(self, tmp_path):
        # A root table holding only env (no identity/state keys): env still
        # loads, name/state stay empty.
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  env:\n'
            '    FOO: "bar"\n'
        )
        cfg = load(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {}
        assert cfg.env == {"FOO": "bar"}

    def test_load_empty_file(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text("")
        cfg = load(cfg_path)
        assert cfg.name == ""

    def test_run_args_must_be_list(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  run_args: "not-a-list"\n'
        )
        cfg = load(cfg_path)
        assert cfg.run_args == []


class TestSave:
    def test_write_defaults(self, tmp_path):
        path = tmp_path / "agents" / "test.yaml"
        cfg = AgentConfig()
        save(path, cfg)

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
        save(path, cfg)

        loaded = load(path)
        assert loaded.name == "Claude Code"
        assert loaded.run_args == ["--verbose"]
        assert loaded.state == {"access": "permissive"}
        assert loaded.env == {"FOO": "bar"}

    def test_state_folded_into_agent_section(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = AgentConfig(state={"access": "permissive"})
        save(path, cfg)

        content = path.read_text()
        # No separate state section; state knobs live under the root table.
        assert 'state:' not in content
        loaded = load(path)
        assert loaded.state == {"access": "permissive"}

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "agent.yaml"
        save(path, AgentConfig())
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
        save(path, original)
        loaded = load(path)

        assert loaded.name == original.name
        assert loaded.run_args == original.run_args
        assert loaded.state == original.state
        assert loaded.env == original.env

    def test_round_trip_empty_config(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig()
        save(path, original)
        loaded = load(path)

        assert loaded.name == ""
        assert loaded.run_args == []
        assert loaded.state == {}
        assert loaded.env == {}

    def test_state_folded_into_single_agent_section(self, tmp_path):
        # Writing state must produce ONE root table (identity + state),
        # with no separate state section, and load back intact.
        path = tmp_path / "test.yaml"
        original = AgentConfig(
            name="Claude Code",
            run_args=["--verbose"],
            state={"model": "sonnet"},
        )
        save(path, original)
        content = path.read_text()
        assert 'state:' not in content
        assert content.count("self:") == 1
        assert 'name: Claude Code' in content
        assert 'model: sonnet' in content

        loaded = load(path)
        assert loaded.state == {"model": "sonnet"}
        assert loaded.name == "Claude Code"
        assert loaded.run_args == ["--verbose"]

    def test_round_trip_multiple_run_args(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig(run_args=["--foo", "--bar", "baz"])
        save(path, original)
        loaded = load(path)
        assert loaded.run_args == ["--foo", "--bar", "baz"]


class TestNodeTablesCarryThrough:
    """The discriminated per-node sub-table (node binds) must survive the load→write
    round trip OPAQUELY.  AgentConfig does not model it (it rides ``_agent_partial``
    into the launch cascade), but before the ``node_tables`` carry, a read-modify-write
    persist silently DROPPED a user's node binds."""

    _NODE_YAML = (
        "self:\n"
        "  name: Nav\n"
        "  model: gemma4\n"
        "  \"nav℘codex\":\n"
        "    bindings:\n"
        "      ro:\n"
        "        share: /host/share:/box/share\n"
    )

    def test_load_captures_node_sub_table(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(self._NODE_YAML)
        cfg = load(path)
        assert cfg.node_tables == {
            "nav℘codex": {"bindings": {"ro": {"share": "/host/share:/box/share"}}}
        }
        # And it is NOT mistaken for flat state (dict-valued entries excluded).
        assert "nav℘codex" not in cfg.state

    def test_round_trip_preserves_node_sub_table(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text(self._NODE_YAML)
        cfg = load(path)
        cfg.state["endpoint"] = "https://e.example"  # a read-modify-write
        save(path, cfg)

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
        cfg = load(path)
        assert cfg.node_tables == {}

    def test_empty_node_table_not_materialized(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        save(path, AgentConfig(node_tables={"nav℘codex": {}}))
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
        cfg = load(path)
        assert cfg.node_tables == {}

    def test_write_guard_never_clobbers_modeled_tables(self, tmp_path):
        # A hand-built config cannot smuggle a "node table" named after a
        # modelled key over the real category (guarded at BOTH ends).
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        save(path, AgentConfig(
            env={"A": "b"},
            node_tables={"env": {"EVIL": "x"}, "name": {"evil": "y"}},
        ))
        data = load_doc(path)
        assert data["self"]["env"] == {"A": "b"}
        assert data["self"]["name"] == ""

    def test_transform_is_not_a_modelled_key(self):
        # ``transform`` (the tweakcc state knob) is NOT ``transform_settings``: it
        # rides flat state, so it must not be swept into the opaque carrier.
        assert "transform" not in _MODELED_KEYS


class TestSlotRouting:
    """``slot_for`` + read/write/remove — the per-value half of the boundary."""

    _TAILS = ("model", "env.FOO", "secret_path.TOK", "bindings.ro.share")

    @pytest.mark.parametrize("tail", _TAILS)
    def test_write_read_remove_round_trip(self, tail, tmp_path):
        slot = slot_for(tmp_path, "claude", tail)
        assert slot.path == tmp_path / "claude" / "settings.yaml"
        assert read_leaf(slot) is None
        write_leaf(slot, "v")
        assert read_leaf(slot) == "v"
        assert remove_leaf(slot) is True
        assert read_leaf(slot) is None
        assert remove_leaf(slot) is False

    def test_leaf_lands_where_the_cascade_reads_it(self, tmp_path):
        # The WRITE side and the cascade READ side are one fact; a flat category
        # written through a slot must be the table ``level_table`` splices back.
        from kanibako.settings.config_io import load_doc

        slot = slot_for(tmp_path, "claude", "env.NAV_X")
        write_leaf(slot, "from-the-file")
        level = level_table(load_doc(slot.path), sub_key="claude", node="claude")
        assert level.table["env"] == {"NAV_X": "from-the-file"}

    def test_bindings_land_in_the_discriminated_sub_table(self, tmp_path):
        # ⚑ Bindings are the ONE category still nested (S2 flattens it): the slot
        # writes the node sub-table, which is where ``level_table`` reads it.
        from kanibako.settings.config_io import load_doc

        slot = slot_for(tmp_path, "claude", "bindings.ro.share")
        write_leaf(slot, "/host:/box")
        level = level_table(load_doc(slot.path), sub_key="claude", node="claude")
        assert level.table["bindings"]["ro"] == {"share": "/host:/box"}

    def test_read_does_not_re_render(self, tmp_path):
        # ⚑ The two ``read_stored_leaf`` conventions are load-bearing for every
        # ``get`` and the boundary must not layer a second rendering on them.
        slot = slot_for(tmp_path, "claude", "allow_helpers")
        write_leaf(slot, True)
        assert read_leaf(slot) == "true"
        write_leaf(slot, "")
        assert read_leaf(slot) is None


class TestClearOverrides:
    """``agent reset --all``'s read-modify-write, now owned by the boundary."""

    def test_preserves_name_and_counts(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  name: Nav\n"
            "  model: opus\n"
            "  access: full\n"
            "  \"nav℘codex\":\n"
            "    secret_path:\n"
            "      TOK_A: /a\n"
            "      TOK_B: /b\n"
            "    bindings:\n"
            "      ro:\n"
            "        share: /h:/b\n"
        )
        # model + access + (2 secret_path VARs + 1 for the other node content).
        assert clear_overrides(path, "nav℘codex") == 5
        assert load_doc(path) == {"self": {"name": "Nav"}}

    def test_prunes_the_root_when_nothing_survives(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text("self:\n  model: opus\n")
        assert clear_overrides(path, "claude") == 1
        assert load_doc(path) == {}

    def test_no_overrides_is_zero(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text("self:\n  name: Nav\n")
        assert clear_overrides(path, "claude") == 0


class TestLevelTable:
    """The cascade half: which table a level reads, and the refusal that guards it."""

    def test_active_level_splices_the_flat_categories(self):
        raw = {"self": {"env": {"A": "b"}, "secret_path": {"T": "/t"}}}
        level = level_table(raw, sub_key="claude", node="claude")
        assert level == AgentFileLevel("claude", {"env": {"A": "b"},
                                                  "secret_path": {"T": "/t"}})

    def test_default_level_gets_no_flat_splice(self):
        # The flat tables are THIS node's, never every agent's — the all-agents
        # tier is the SYSTEM file's ``agent: default:`` table.
        raw = {"self": {"env": {"A": "b"}}}
        assert level_table(raw, sub_key="default", node="claude").table == {}

    def test_missing_root_is_an_empty_level(self):
        assert level_table({"system": {}}, sub_key="claude").table == {}
        assert level_table("not-a-dict", sub_key="claude").table == {}

    @pytest.mark.parametrize("category", ("env", "secret_path"))
    @pytest.mark.parametrize("sub", ("claude", "default"))
    def test_nested_category_refuses_by_name(self, category, sub):
        # rulings 49c / 50 — ``self.<sub>.<category>`` reads
        # ``agent.<agent>.<sub>.<category>``, which is never syntactically correct.
        raw = {"self": {sub: {category: {"X": "y"}}}}
        with pytest.raises(SettingsError) as exc:
            level_table(raw, sub_key=sub, node="claude")
        assert file_spelling(sub, category) in str(exc.value)

    def test_refusal_precedes_the_flat_splice(self):
        # ⚑ ORDERING IS THE CONTRACT: a file carrying BOTH spellings must still be
        # refused for the nested one, not silently resolved off the flat one.
        # ⚑⚑ THE MESSAGE IS WHAT DISCRIMINATES, NOT THE RAISE. Both orderings raise
        # on this input — after the splice ``node_tbl["env"]`` is the FLAT table, which
        # is still a present ``env`` — and they differ ONLY in which entries the refusal
        # NAMES: refusal-first indicts the nested ``X``, splice-first the flat ``A``.
        # A bare ``pytest.raises`` here would pin nothing.
        raw = {"self": {"env": {"A": "b"}, "claude": {"env": {"X": "y"}}}}
        with pytest.raises(SettingsError) as exc:
            level_table(raw, sub_key="claude", node="claude")
        entries = str(exc.value).split("entries: ", 1)[1].splitlines()[0]
        assert "X" in entries
        assert "A" not in entries

    def test_bindings_still_nest(self):
        # ⚑ NOT refused: nested is bindings' only spelling until S2 lands the flat
        # route.  Refusing first would delete a live delivery path.
        raw = {"self": {"claude": {"bindings": {"ro": {"share": "/h:/b"}}}}}
        level = level_table(raw, sub_key="claude", node="claude")
        assert level.table == {"bindings": {"ro": {"share": "/h:/b"}}}


class TestStateLevel:
    def test_empty_state_is_no_level(self):
        assert state_level(None, node="claude") is None
        assert state_level({}, node="claude") is None

    def test_discriminator_attaches_at_the_boundary(self):
        assert state_level({"model": "opus"}, node="claude") == AgentFileLevel(
            "claude", {"model": "opus"}
        )


class TestFileSpelling:
    def test_node_only(self):
        assert file_spelling("claude") == "self.claude"

    def test_node_and_tail(self):
        assert file_spelling("claude", "bindings.ro") == "self.claude.bindings.ro"
