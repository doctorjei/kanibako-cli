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
    _FLAT_AGENT_CATEGORIES,
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

    def test_load_present_null_secret_path_is_kept_as_none(self, tmp_path):
        # 2026-08-17 ruling: the token/key MAY hold an explicit null to mean "no
        # key" — this endpoint is deliberately KEYLESS. A ``None`` value must
        # survive the load VERBATIM (never coerced through ``str()``, which
        # would turn it into the four-byte garbage string ``"None"`` —
        # indistinguishable from a typo'd path). Membership (``var in
        # cfg.secret_path``) still says the VAR was configured; only the value
        # differs from a real pointer. (Mutation: restore the old
        # ``str(v)``-for-every-entry load and this comes back as the string
        # ``"None"`` → RED.)
        cfg_path = self._node_file(tmp_path)
        cfg_path.write_text(
            'self:\n'
            '  name: "persona"\n'
            '  secret_path:\n'
            '    ANTHROPIC_AUTH_TOKEN: null\n'
        )
        cfg = load(cfg_path)
        assert "ANTHROPIC_AUTH_TOKEN" in cfg.secret_path
        assert cfg.secret_path["ANTHROPIC_AUTH_TOKEN"] is None
        assert cfg.secret_path["ANTHROPIC_AUTH_TOKEN"] != "None"

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

    def test_round_trip_present_null_secret_path(self, tmp_path):
        # A deliberately-keyless VAR (2026-08-17 ruling) survives save→load as a
        # real ``None``, not the string "None" — and a sparse write still
        # materializes the table (a dict with one None-valued entry is
        # non-empty, so it is NOT dropped as an empty category).
        path = self._node_file(tmp_path)
        original = AgentConfig(
            name="persona",
            secret_path={"ANTHROPIC_AUTH_TOKEN": None},
        )
        save(path, original)
        content = path.read_text()
        assert "secret_path" in content
        loaded = load(path)
        assert loaded.secret_path == {"ANTHROPIC_AUTH_TOKEN": None}


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

    def test_load_present_null_state_is_kept_as_none(self, tmp_path):
        # 2026-08-17 ruling (persona MODEL, same shape as the token key): a flat
        # state scalar (e.g. ``model``) explicitly ``null`` must survive load as
        # a real ``None`` — never coerced through ``str()`` into the four-byte
        # garbage string ``"None"``, which would reach the launch cascade as a
        # BOGUS model id and silently defeat the "this persona needs no model"
        # declaration. (Mutation: restore the unconditional ``str(v)`` and this
        # comes back as the string ``"None"`` → RED.)
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'self:\n'
            '  name: "persona"\n'
            '  model: null\n'
        )
        cfg = load(cfg_path)
        assert "model" in cfg.state
        assert cfg.state["model"] is None
        assert cfg.state["model"] != "None"

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

    def test_round_trip_present_null_state(self, tmp_path):
        # A deliberately-null state scalar (e.g. a persona's ``model: null``)
        # survives save→load as a real ``None``, not the string "None".
        path = tmp_path / "test.yaml"
        original = AgentConfig(name="persona", state={"model": None})
        save(path, original)
        content = path.read_text()
        assert "model:" in content
        loaded = load(path)
        assert loaded.state == {"model": None}

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


class TestCategoryTablesCarryThrough:
    """The CATEGORY tables the record does not model (bindings, caches, seeded, common,
    synced, masks) must survive the load→write round trip OPAQUELY.  AgentConfig does not
    model them (they ride ``_agent_partial`` into the launch cascade), but before the
    ``category_tables`` carry a read-modify-write persist silently DROPPED a user's binds.

    ⚑ RENAMED FROM ``node_tables`` WITH THE S2 FLATTEN, and the name is the fact: there is no
    per-node sub-table any more (``self`` IS ``agent.<node>``), so what the carrier holds is
    exactly the flat categories the record has no field for.
    """

    _FLAT_YAML = (
        "self:\n"
        "  name: Nav\n"
        "  model: gemma4\n"
        "  bindings:\n"
        "    ro:\n"
        "      /box/share: [/host/share]\n"
    )

    def test_load_captures_the_unmodelled_categories(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(self._FLAT_YAML)
        cfg = load(path)
        assert cfg.category_tables == {
            "bindings": {"ro": {"/box/share": ["/host/share"]}}
        }
        # And it is NOT mistaken for flat state (dict-valued entries excluded).
        assert "bindings" not in cfg.state

    def test_round_trip_preserves_the_category_tables(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text(self._FLAT_YAML)
        cfg = load(path)
        cfg.state["endpoint"] = "https://e.example"  # a read-modify-write
        save(path, cfg)

        data = load_doc(path)
        assert data["self"]["bindings"]["ro"] == {"/box/share": ["/host/share"]}
        assert data["self"]["endpoint"] == "https://e.example"

    def test_every_unmodelled_category_rides_the_carrier(self, tmp_path):
        # The carrier is derived from the flat-category tuple MINUS what the record
        # models, so widening one widens the other — no second list to keep in step.
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  caches: {~/.cache/uv: [/store/uv]}\n"
            "  seeded: {~: [/store/template]}\n"
            "  common: {~/.claude/plugins: [/store/plugins]}\n"
            "  synced: {~/.config/x: [/store/x]}\n"
            "  masks: {~/.ssh: true}\n"
        )
        assert set(load(path).category_tables) == {
            "caches", "seeded", "common", "synced", "masks",
        }

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
        assert cfg.category_tables == {}

    def test_empty_category_table_not_materialized(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        save(path, AgentConfig(category_tables={"caches": {}}))
        assert "caches" not in load_doc(path)["self"]

    def test_schema_owned_dict_keys_never_captured(self, tmp_path):
        # Malformed dict-valued identity keys must not ride category_tables (they
        # would clobber the emitted string ``name`` on the next write) — and they are
        # NOT refused as nested sub-tables either: a mistyped scalar is not a nesting.
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  name:\n"
            "    weird: 1\n"
            "  run_args:\n"
            "    weird: 2\n"
        )
        cfg = load(path)
        assert cfg.category_tables == {}

    def test_write_guard_never_clobbers_modeled_tables(self, tmp_path):
        # A hand-built config cannot smuggle a carrier entry named after a modelled
        # key over the real category: ONE set guards both ends, and ``env`` is not in
        # it. Nor can the carrier emit a table ``load`` would refuse.
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        save(path, AgentConfig(
            env={"A": "b"},
            category_tables={"env": {"EVIL": "x"}, "nav℘codex": {"evil": "y"}},
        ))
        data = load_doc(path)
        assert data["self"]["env"] == {"A": "b"}
        assert "nav℘codex" not in data["self"]
        # And what was written loads back without a refusal.
        assert load(path).env == {"A": "b"}

    def test_transform_is_not_a_modelled_key(self):
        # ``transform`` (the tweakcc state knob) is NOT ``transform_settings``: it
        # rides flat state, so it must not be swept into the opaque carrier.
        assert "transform" not in _MODELED_KEYS


class TestSlotRouting:
    """``slot_for`` + read/write/remove — the per-value half of the boundary."""

    _TAILS = ("model", "env.FOO", "secret_path.TOK")

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

    def test_the_bindings_write_arm_is_gone(self, tmp_path):
        # ⚑⚑ THE INVERSION OF THE S2↔S3 WINDOW PIN (rulings 50-52; D-4). Through S2 this
        # arm WROTE a ``self.<node>: bindings:`` sub-table that the flattened read then
        # REFUSED — a value laid down where nothing reads it. S3 does not "flatten" the
        # write arm, it DELETES it: ``bindings`` is dest-keyed, its entries are DATA inside
        # the arm's value, so there is no scalar slot to address and the boundary cannot
        # produce one. The verb refuses by name long before this; this is the backstop.
        slot = slot_for(tmp_path, "claude", "bindings.ro.share")
        with pytest.raises(SettingsError, match=r"no scalar slot"):
            write_leaf(slot, "/host:/box")
        assert not slot.path.exists()

    @pytest.mark.parametrize(
        "tail", ("caches", "masks", "bindings.ro", "transform_settings", "synced"),
    )
    def test_no_write_address_for_a_whole_table(self, tail, tmp_path):
        # The rule is the KEY's value shape, not a list of bad names: every root key
        # holding a TABLE is unaddressable by a scalar write, and a REMOVE is a write.
        slot = slot_for(tmp_path, "claude", tail)
        with pytest.raises(SettingsError, match=r"no scalar slot"):
            write_leaf(slot, "v")
        with pytest.raises(SettingsError, match=r"no scalar slot"):
            remove_leaf(slot)

    def test_read_does_not_re_render(self, tmp_path):
        # ⚑ The two ``read_stored_leaf`` conventions are load-bearing for every
        # ``get`` and the boundary must not layer a second rendering on them.
        slot = slot_for(tmp_path, "claude", "allow_helpers")
        write_leaf(slot, True)
        assert read_leaf(slot) == "true"
        write_leaf(slot, "")
        assert read_leaf(slot) is None


class TestTheDestIsData:
    """D-4: a per-entry DESTINATION is DATA and is never split on ``.`` (rulings 49-52).

    The fifth instance of one root cause ([[dest-is-data-never-split-on-dot]]).  The old bindings
    arm did ``tail.split(".")`` and scattered ``bindings.ro.~/.cache/uv`` across YAML levels, so a
    hand-authored entry read back "(not set)" while the sibling box scope handled the identical
    destination fine.
    """

    _DOTTED = "~/.cache/uv"

    def _file_with(self, tmp_path, body):
        path = tmp_path / "claude" / "settings.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def test_a_dotted_bindings_dest_reads_back_whole(self, tmp_path):
        # MUTATION PROOF: restore ``segs = tail.split(".")`` in the address rule and the
        # read lands on ``self/bindings/ro/~/`` + leaf ``cache/uv`` — a slot no file has.
        self._file_with(
            tmp_path,
            "self:\n"
            "  bindings:\n"
            "    ro:\n"
            f"      {self._DOTTED}: [/store/uv]\n",
        )
        slot = slot_for(tmp_path, "claude", f"bindings.ro.{self._DOTTED}")
        assert read_leaf(slot) == "['/store/uv']"

    @pytest.mark.parametrize("category", ("caches", "seeded", "common", "synced"))
    def test_a_dotted_dest_reads_back_whole_for_every_dest_keyed_category(
        self, category, tmp_path,
    ):
        # The four one-segment-shallower families: the category token IS the whole key,
        # so EVERYTHING after it is the destination.
        self._file_with(
            tmp_path,
            f"self:\n  {category}:\n    {self._DOTTED}: [/store/x]\n",
        )
        slot = slot_for(tmp_path, "claude", f"{category}.{self._DOTTED}")
        assert read_leaf(slot) == "['/store/x']"

    def test_a_dotted_masks_dest_reads_back_whole(self, tmp_path):
        self._file_with(tmp_path, "self:\n  masks:\n    ~/.ssh: true\n")
        assert read_leaf(slot_for(tmp_path, "claude", "masks.~/.ssh")) == "true"

    def test_a_non_category_head_is_a_flat_leaf(self, tmp_path):
        # The fallthrough: a tail whose head is not a category is a root leaf, dots and
        # all — it is NOT exploded into sections a settings_categories claim depends on.
        self._file_with(tmp_path, "self:\n  model: opus\n")
        assert read_leaf(slot_for(tmp_path, "claude", "model")) == "opus"


class TestTableValuedKeysTakeNoScalar:
    """D-7: a declared key whose VALUE is a table takes no scalar, and says so."""

    def test_transform_settings_is_refused_with_its_shape(self, tmp_path):
        from kanibako.settings.agent_file import table_value_error

        msg = table_value_error(
            "transform_settings", path=tmp_path / "settings.yaml", verb="set",
        )
        assert msg is not None
        assert "holds a TABLE" in msg
        # The cure QUOTES the file's own spelling — the allowed residue (ruling 51).
        assert "self.transform_settings" in msg

    def test_a_dotted_arm_renders_whole_in_the_cure(self, tmp_path):
        from kanibako.settings.agent_file import table_value_error

        msg = table_value_error(
            "bindings.ro", path=tmp_path / "settings.yaml", verb="set",
        )
        assert msg is not None and "self.bindings.ro" in msg

    @pytest.mark.parametrize("tail", ("model", "name", "env.FOO", "secret_path.TOK"))
    def test_the_scalar_tails_are_not_refused(self, tail, tmp_path):
        from kanibako.settings.agent_file import table_value_error

        assert table_value_error(
            tail, path=tmp_path / "settings.yaml", verb="set",
        ) is None


class TestLoadSurvivesAMalformedTable:
    """D-7's other half: a wrong-SHAPE value must not kill the verbs that SHOW it.

    A scalar where a table belongs used to raise out of :func:`load` (``dict("foo")``), so every
    caller inherited it — ``agent info``, ``list``, ``show``, and every launch.  The repair verbs
    have to stay reachable, so the READ side coerces and the WRITE side refuses.
    """

    @pytest.mark.parametrize("key", ("transform_settings", "env", "secret_path"))
    def test_a_scalar_at_a_table_key_does_not_raise(self, key, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(f"self:\n  name: Nav\n  {key}: oops\n")
        cfg = load(path)          # must not raise
        assert cfg.name == "Nav"
        assert getattr(cfg, key) == {}
        # ...and the garbage does NOT ride into the launch as an agent-state knob.
        assert key not in cfg.state


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
            "  secret_path:\n"
            "    TOK_A: /a\n"
            "    TOK_B: /b\n"
            "  bindings:\n"
            "    ro:\n"
            "      /box/share: [/h/share]\n"
        )
        # ⚑ FOUR, NOT FIVE, AND THE CHANGE IS DELIBERATE. The old fixture nested these
        # under a ``nav℘codex`` sub-table, which the flatten (S2) refuses; flattened, the
        # per-VAR arm of the count is unreachable, because it only ever counted VARs found
        # inside that sub-table. So: model + access + secret_path + bindings = 4 ROOT keys,
        # each counting once — the rule the docstring states, with nothing special-cased.
        # ⚑ AND THE ``node`` ARGUMENT IS GONE WITH THAT ARM (S3): the count no longer has
        # anything to ask about which node's file this is.
        assert clear_overrides(path) == 4
        assert load_doc(path) == {"self": {"name": "Nav"}}

    def test_prunes_the_root_when_nothing_survives(self, tmp_path):
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "settings.yaml"
        path.write_text("self:\n  model: opus\n")
        assert clear_overrides(path) == 1
        assert load_doc(path) == {}

    def test_no_overrides_is_zero(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text("self:\n  name: Nav\n")
        assert clear_overrides(path) == 0


class TestLevelTable:
    """The cascade half: which table a level reads, and the refusal that guards it."""

    def test_active_level_reads_the_flat_categories(self):
        raw = {"self": {"env": {"A": "b"}, "secret_path": {"T": "/t"}}}
        level = level_table(raw, sub_key="claude", node="claude")
        assert level == AgentFileLevel("claude", {"env": {"A": "b"},
                                                  "secret_path": {"T": "/t"}})

    @pytest.mark.parametrize("category", _FLAT_AGENT_CATEGORIES)
    def test_every_flat_category_reaches_the_active_level(self, category):
        # ⚑ PARAMETRIZED OFF THE CONSTANT, so widening the tuple widens the pin and a
        # narrowing shows up as a missing case rather than as silence. ``bindings`` rides
        # as ONE token — its ``{ro, rw}`` table is re-rooted whole.
        table = {"ro": {"/box/x": ["/h/x"]}} if category == "bindings" else {"X": "y"}
        raw = {"self": {category: table}}
        level = level_table(raw, sub_key="claude", node="claude")
        assert level.table == {category: table}

    def test_default_level_is_structurally_empty(self):
        # The flat tables are THIS node's, never every agent's — and since the flatten
        # the file has NO spelling for the all-agents tier at all; that tier is the
        # SYSTEM file's ``agent: default:`` table.
        raw = {"self": {"env": {"A": "b"}, "bindings": {"ro": {"/x": ["/y"]}}}}
        assert level_table(raw, sub_key="default", node="claude").table == {}

    def test_missing_root_is_an_empty_level(self):
        assert level_table({"system": {}}, sub_key="claude").table == {}
        assert level_table("not-a-dict", sub_key="claude").table == {}

    @pytest.mark.parametrize("category", _FLAT_AGENT_CATEGORIES)
    @pytest.mark.parametrize("sub", ("claude", "default"))
    def test_nested_category_refuses_by_name(self, category, sub):
        # rulings 50-52 — ``self.<sub>.<category>`` reads
        # ``agent.<agent>.<sub>.<category>``, which is never syntactically correct.
        # ⚑ EVERY category, from the constant: the refusal is ONE PREDICATE over the root
        # table, so it cannot be true of some categories and not others.
        raw = {"self": {sub: {category: {"X": "y"}}}}
        with pytest.raises(SettingsError) as exc:
            level_table(raw, sub_key=sub, node="claude")
        assert file_spelling(sub, category) in str(exc.value)

    def test_nested_state_refuses_too(self):
        # ⚑ THE STATE CASE (defect D-3), which a per-category loop could not express:
        # ``self: claude: model:`` is a scalar carrier, not a category table. It used to
        # resolve, and to LOSE silently to the flat spelling. One predicate closes it.
        raw = {"self": {"claude": {"model": "opus"}}}
        with pytest.raises(SettingsError) as exc:
            level_table(raw, sub_key="claude", node="claude")
        message = str(exc.value)
        assert "self.claude" in message
        assert "model" in message
        # No category to point at, so the cure is the rule itself — never a verb.
        assert "kanibako agent set" not in message

    def test_the_refusal_indicts_the_nested_table_not_the_legal_flat_one(self):
        # ⚑⚑ THE MESSAGE IS WHAT DISCRIMINATES, NOT THE RAISE. A file carrying a LEGAL
        # flat table beside a nested sub-table must be refused for the nested one and
        # must not name the flat table's entries — a predicate written over the wrong
        # table would indict the innocent half.
        raw = {"self": {
            "env": {"ONLY_FLAT": "b"},
            "claude": {"env": {"ONLY_NESTED": "y"}},
        }}
        with pytest.raises(SettingsError) as exc:
            level_table(raw, sub_key="claude", node="claude")
        message = str(exc.value)
        assert "self.claude.env" in message
        assert "ONLY_NESTED" in message
        assert "ONLY_FLAT" not in message

    def test_an_unknown_root_table_refuses_even_with_no_category_in_it(self):
        # The closed keyspace, not a list of known-bad names: a table nobody declared
        # refuses by name, and the cure states the rule rather than guessing an intent.
        raw = {"self": {"claude": {"whatever": {"X": "y"}}}}
        with pytest.raises(SettingsError, match=r"self\.claude"):
            level_table(raw, sub_key="claude", node="claude")

    def test_a_bare_sub_key_leaf_is_not_a_table(self):
        # ``claude:`` with nothing under it parses to None. It carries nothing and
        # delivers nothing, so it is a stray scalar, not a nested sub-table.
        assert level_table(
            {"self": {"claude": None}}, sub_key="claude", node="claude",
        ).table == {}


class TestStateLevel:
    def test_empty_state_is_no_level(self):
        assert state_level(None, node="claude") is None
        assert state_level({}, node="claude") is None

    def test_discriminator_attaches_at_the_boundary(self):
        assert state_level({"model": "opus"}, node="claude") == AgentFileLevel(
            "claude", {"model": "opus"}
        )


class TestTheForwardCompatPassthroughIsClosed:
    """S3b / D-5's other end: an undeclared scalar no longer rides into the launch.

    ``_agent_state_partial`` documented that "undeclared agent-scope scalar keys ride through
    verbatim (forward-compat)" — which is the *"old ``agent.<name>.<anyleaf>`` behaviour"* spec §0
    SPECIFICALLY EXCLUDES. Together with the ungated ``agent set`` (D-5) it meant stored garbage
    was not merely dead: it reached the box.
    """

    def test_an_undeclared_scalar_refuses_the_launch_by_name(self):
        with pytest.raises(SettingsError) as exc:
            state_level({"model": "opus", "junk": "x"}, node="claude")
        message = str(exc.value)
        assert "'junk'" in message
        assert "agents/claude/settings.yaml" in message
        # The repair route stays NAMED — the verb that still works on a poisoned file.
        assert "agent info claude" in message

    def test_the_self_alias_cannot_ride_in_either(self):
        # ruling 55: nothing past the parse boundary recognises ``self``. A hand-authored
        # ``self.model:`` root leaf is a scalar the loader sweeps into state — and it stops
        # here rather than becoming ``agent.claude.self.model`` in the snapshot.
        with pytest.raises(SettingsError, match=r"self\.model"):
            state_level({"self.model": "opus"}, node="claude")

    def test_every_core_declared_leaf_still_launches(self):
        # The CONTROL. A refusal that also refused the real keys would be caught by the
        # rest of the suite, but not by anything that says WHY this list is the list.
        state = {
            "model": "opus", "access": "full", "endpoint": "https://e",
            "allow_helpers": "true", "continue_mode": "true", "bootstrap": "x",
            "template": "t", "canon": "c", "transform": "tweakcc",
        }
        assert state_level(state, node="claude").table == state

    def test_a_plugin_declared_leaf_still_launches(self):
        """THE POSITIVE CONTROL, and the mutation proof for the union.

        ``provider`` is declared by the goose target via ``setting_descriptors()``, not by core's
        §2d table. MUTATION: drop ``agent_leaves=`` from ``config_keys.agent_key_reason``'s
        ``key_validity`` call and this reddens — a refusal without the union kills a working box.
        """
        from kanibako.settings.settings_prefs import default_valid_agents

        leaves = getattr(default_valid_agents(), "leaves", None) or ()
        if "provider" not in leaves:
            pytest.skip("no installed plugin declares 'provider' in this environment")
        assert state_level({"provider": "ollama"}, node="goose").table == {
            "provider": "ollama",
        }

    def test_the_repair_verbs_do_not_go_through_here(self, tmp_path):
        # ⚑ WHY THE REFUSAL IS AT THE LAUNCH BOUNDARY AND NOT IN ``load``: a poisoned file
        # must still be clearable. ``clear_overrides`` reads raw YAML and never builds a
        # level, so this is the escape hatch, pinned rather than assumed.
        path = tmp_path / "settings.yaml"
        path.write_text("self:\n  name: Nav\n  junk: x\n")
        assert load(path).state == {"junk": "x"}   # the SHOW verbs still see it
        assert clear_overrides(path) == 1


class TestFileSpelling:
    def test_the_root_alone(self):
        assert file_spelling() == "self"

    def test_one_segment_is_the_flat_table(self):
        # What every CURE names since the flatten: the file IS the node's, so the
        # category table sits directly under the root.
        assert file_spelling("caches") == "self.caches"

    def test_segments_join_under_the_root(self):
        # What every REFUSAL names: the nested shape the user actually wrote.
        assert file_spelling("claude", "bindings") == "self.claude.bindings"

    def test_empty_segments_are_dropped(self):
        # Lets the refusal pass an optional category without a branch of its own.
        assert file_spelling("claude", "") == "self.claude"


class TestLoadSharesTheRefusal:
    """Two readers of ONE file must not disagree about what the file means (call (b))."""

    def test_load_refuses_what_the_cascade_refuses(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  name: Nav\n"
            "  claude:\n"
            "    env:\n"
            "      EDITOR: vim\n"
        )
        with pytest.raises(SettingsError) as exc:
            load(path)
        message = str(exc.value)
        assert "self.claude.env" in message
        assert str(path) in message

    def test_a_flat_file_loads(self, tmp_path):
        # The control: the predicate must not refuse the shape the flatten blesses.
        path = tmp_path / "settings.yaml"
        path.write_text(
            "self:\n"
            "  name: Nav\n"
            "  env:\n"
            "    EDITOR: vim\n"
            "  bindings:\n"
            "    ro:\n"
            "      /box/x: [/h/x]\n"
        )
        cfg = load(path)
        assert cfg.env == {"EDITOR": "vim"}
        assert cfg.category_tables == {"bindings": {"ro": {"/box/x": ["/h/x"]}}}
