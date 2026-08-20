"""Tests for the unified config interface engine."""

from __future__ import annotations

import pytest
import yaml

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.config_keys import ConfigLevel, is_known_key
from kanibako.settings.config_interface import (
    ConfigAction,
    get_config_value,
    parse_config_arg,
    reset_config_value,
    set_config_value,
    show_config,
    reset_all,
)


# ---------------------------------------------------------------------------
# parse_config_arg
# ---------------------------------------------------------------------------

class TestParseConfigArg:
    """Tests for argument parsing logic."""

    def test_none_returns_show(self):
        action, key, value = parse_config_arg(None)
        assert action == ConfigAction.show
        assert key == ""
        assert value == ""

    def test_key_only_returns_get(self):
        action, key, value = parse_config_arg("image")
        assert action == ConfigAction.get
        assert key == "image"
        assert value == ""

    def test_key_equals_value_returns_set(self):
        action, key, value = parse_config_arg("image=ghcr.io/foo:latest")
        assert action == ConfigAction.set
        assert key == "image"
        assert value == "ghcr.io/foo:latest"

    def test_key_equals_empty_value(self):
        action, key, value = parse_config_arg("model=")
        assert action == ConfigAction.set
        assert key == "model"
        assert value == ""

    def test_env_key_get(self):
        action, key, value = parse_config_arg("env.MY_VAR")
        assert action == ConfigAction.get
        assert key == "env.MY_VAR"

    def test_env_key_set(self):
        action, key, value = parse_config_arg("env.MY_VAR=hello")
        assert action == ConfigAction.set
        assert key == "env.MY_VAR"
        assert value == "hello"


# ---------------------------------------------------------------------------
# is_known_key
# ---------------------------------------------------------------------------

class TestIsKnownKey:
    """Tests for the known-key heuristic."""

    def test_known_static_key(self):
        assert is_known_key("box.image") is True
        assert is_known_key("model") is True
        assert is_known_key("box.auth.global_enabled") is True

    def test_short_aliases_no_longer_known(self):
        """W2a: the image/agent short-name aliases were removed (canonical only)."""
        assert is_known_key("image") is False
        assert is_known_key("agent") is False

    def test_known_dotted_key(self):
        assert is_known_key("box.enable_vault") is True
        # P2 clean break: the retired ``vault.enabled`` alias is NOT known.
        assert is_known_key("vault.enabled") is False
        assert is_known_key("config.data") is True
        assert is_known_key("config.agents") is True
        # ⮕ P7: renamed (spec §2g); the retired spelling is NOT a key.
        assert is_known_key("system.agent") is True
        assert is_known_key("system.default_agent") is False
        # ⮕ P7: ``box.agent_name`` is RETIRED (spec §2b) — the agent SELECTION is
        # the §2h request ``pref.system.agent``.
        assert is_known_key("box.agent_name") is False
        # P3: the per-workset registry key is a known settable key.
        assert is_known_key("workset.registry") is True
        # P6a: the workset LAYOUT anchors are now known settable keys.
        for k in (
            "workset.auth.path",
            "workset.boxes",
            "workset.vault_ro",
            "workset.vault_rw",
            "workset.logs",
            "workset.channels.common",
            "workset.channels.chat",
            "workset.channels.share",
        ):
            assert is_known_key(k) is True, k
        # P6d: the workset kuid + advisory-check toggle are known settable keys.
        assert is_known_key("workset.kuid") is True
        assert is_known_key("workset.skip_kuid_check") is True

    def test_dead_keys_no_longer_known(self):
        """W4: paths.shell/paths.vault, layout, persistence were deleted.

        The shared-cache surgery additionally retired paths.shared and the
        shared.* dynamic prefix (replaced by the ``caches`` category).
        """
        assert is_known_key("paths.shell") is False
        assert is_known_key("paths.vault") is False
        assert is_known_key("paths.shared") is False
        assert is_known_key("shared.cargo-git") is False
        assert is_known_key("layout") is False
        assert is_known_key("persistence") is False

    def test_box_shell_is_known(self):
        """box.shell must be a known GET key (set/--reset bypass is_known_key)."""
        assert is_known_key("box.shell") is True

    def test_bootstrap_is_known(self):
        """bootstrap is an agent-scope behavior key — a known GET key (spec §2d).

        The old box-scope ``box.bootstrap_program`` is RETIRED (relocated to the
        agent scope, 1.7.0-rc clean break — no alias)."""
        assert is_known_key("bootstrap") is True
        # Per-agent override form (persona key), mirroring model/access.
        assert is_known_key("agent.claude.bootstrap") is True
        # The retired box-scope key is no longer known.
        assert is_known_key("box.bootstrap_program") is False

    def test_dynamic_env_prefix(self):
        assert is_known_key("env.MY_VAR") is True

    def test_unknown_key(self):
        assert is_known_key("my-project") is False
        assert is_known_key("foobar") is False

    def test_multifaceted_terminals_answer_false_quarantined(self):
        """⚑ PINS THE QUARANTINE, NOT A DESIRED BEHAVIOUR (Jei, 2026-08-08).

        The six bind-shaped category TERMINALS and ``<scope>.masks`` are DECLARED
        keys whose values are multi-faceted (a dest-keyed map; a list for
        ``masks``).  ``is_known_key`` is a HAND-MAINTAINED set and answers
        ``False`` for every one of them, so the system noun's ``get`` refuses a
        key that exists.  Jei ruled QUARANTINE rather than fix: an individual
        read/write of a multi-faceted key is not supported, and the readable form
        is a promise whose shape is undecided.

        ⚑⚑ THIS TEST EXISTS TO STOP THE OBVIOUS FIX. Deriving ``is_known_key``
        from the declaration SoT would flip these to ``True`` and thereby build
        the surface the ruling has not chosen — it was proposed and DECLINED.
        When the promised form lands, DELETE this test with the quarantine block
        above ``config_keys.KNOWN_CONFIG_KEYS``; do not silently retune it.
        """
        for scope in ("system", "workset", "box"):
            for category in (
                "bindings.ro", "bindings.rw", "caches",
                "common", "seeded", "synced", "masks",
            ):
                key = f"{scope}.{category}"
                assert is_known_key(key) is False, key
        # The AGENT-scope terminal spelling is in the same state.
        assert is_known_key("agent.claude.caches") is False
        assert is_known_key("agent.claude.bindings.ro") is False
        # ⚑ The PER-ENTRY spellings stay recognised — they are refused BY NAME
        # (spec §0 refuses loudly), which requires the disambiguator to read them
        # as keys.  Only the terminals are quarantined.
        assert is_known_key("box.caches.npm") is True
        assert is_known_key("agent.claude.caches.npm") is True


# ---------------------------------------------------------------------------
# get / set / reset for regular config keys
# ---------------------------------------------------------------------------

class TestRegularConfigKeys:
    """Tests for regular (KanibakoConfig) config keys."""

    def test_plain_get_box_image_stored_at_another_tier_is_not_set(
        self, tmp_path,
    ):
        """F6 get model: box.image set ONLY in the GLOBAL config file is
        "(not set)" for a plain get at the box noun (global is not the box
        noun's file).  The resolved value shows under ``--effective``, not here.
        (Was ``test_get_default_image``, which asserted the pre-F6 merged-view
        lie that a plain get returns another tier's value.)"""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "my-image:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        val = get_config_value(
            "box.image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val is None

    def test_set_and_get_image(self, tmp_path):
        """Setting a config key writes it and subsequent get returns it."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "default:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        msg = set_config_value(
            "box.image", "custom:v2",
            config_path=project_toml,
        )
        assert "Set" in msg
        assert "custom:v2" in msg

        val = get_config_value(
            "box.image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "custom:v2"

    def test_reset_image(self, tmp_path):
        """Resetting a key removes the project-level override."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "default:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        set_config_value("box.image", "custom:v2", config_path=project_toml)
        msg = reset_config_value("box.image", config_path=project_toml)
        # F7: honest wording — the override is CLEARED (no fabricated "reverts
        # to default: <built-in>" claim). command_scope=None → scope-neutral.
        assert "cleared" in msg.lower()
        assert "reverts to default" not in msg

    def test_reset_nonexistent_key(self, tmp_path):
        """Resetting a key that has no override returns informative message."""
        project_toml = tmp_path / "settings.yaml"
        msg = reset_config_value("box.image", config_path=project_toml)
        assert "No override" in msg

    def test_get_box_shell_unset_returns_none(self, tmp_path):
        """box.shell defaults to empty → get returns None (rendered as not set)."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        val = get_config_value(
            "box.shell",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val is None

    def test_set_and_get_box_shell(self, tmp_path):
        """Setting box.shell and reading it back returns the value (no error)."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        set_config_value("box.shell", "/bin/zsh", config_path=project_toml)
        val = get_config_value(
            "box.shell",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "/bin/zsh"

    def test_get_bootstrap_unset_is_not_set(self, tmp_path):
        """An UNSET agent-scope ``bootstrap`` is "(not set)" — a plain get never
        fabricates the consumer default ("tmux").  The built-in still applies at
        LAUNCH + ``--effective`` (spec §2d agent.default.bootstrap=tmux)."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        val = get_config_value(
            "bootstrap",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val is None

    def test_set_and_get_bootstrap_agent_default_tier(self, tmp_path):
        """Setting the bare agent-scope ``bootstrap`` writes the reserved
        ``agent.default`` tier (mirrors ``model``) and reads back the value."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        msg = set_config_value("bootstrap", "screen", config_path=project_toml)
        assert "Set bootstrap=screen" in msg
        # The agent-agnostic CLI writes the reserved agent.default tier.
        data = load_doc(project_toml)
        assert data["agent"]["default"]["bootstrap"] == "screen"

        val = get_config_value(
            "bootstrap",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "screen"

    def test_set_per_agent_bootstrap_override(self, tmp_path):
        """A per-agent ``agent.<agent>.bootstrap`` override is a PER-PERSONA setting
        routed to the agent's OWN ``agents/<node>/settings.yaml`` flat slot the launch
        reads — mirroring ``agent.<agent>.model``."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.bootstrap", "none",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "self": {"bootstrap": "none"},
        }


class TestContinueMode:
    """``continue_mode`` — an agent-scope BOOL behavior key (spec §2d
    ``agent.default.continue_mode | true``; "continue vs fresh; resume removed").
    Wired EXACTLY like ``access``/``bootstrap``; REPLACES the dead
    ``start_mode`` leaf (spec §3, 1.7.0-rc clean break — no alias)."""

    def test_continue_mode_is_known(self):
        assert is_known_key("continue_mode") is True
        # Per-agent override form (persona key), mirroring model/access.
        assert is_known_key("agent.claude.continue_mode") is True

    def test_start_mode_is_retired(self):
        """The dead ``start_mode`` leaf is GONE — not a known key (no reader at
        launch; fully covered by continue_mode + access, spec §3)."""
        assert is_known_key("start_mode") is False
        assert is_known_key("agent.claude.start_mode") is False

    def test_start_mode_bare_set_refused_as_unknown(self, tmp_path):
        """A bare ``start_mode`` set no longer takes the agent.default route — it
        is refused as an unknown key and nothing is written."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("start_mode", "fresh", config_path=project_toml)
        assert msg == "Error: unknown config key: start_mode"
        assert not project_toml.exists()

    def test_set_and_get_continue_mode_agent_default_tier(self, tmp_path):
        """Setting the bare agent-scope ``continue_mode`` writes the reserved
        ``agent.default`` tier (mirrors ``model``) and reads back the value."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        msg = set_config_value("continue_mode", "false", config_path=project_toml)
        assert "Set continue_mode=false" in msg
        data = load_doc(project_toml)
        assert data["agent"]["default"]["continue_mode"] == "false"

        val = get_config_value(
            "continue_mode",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "false"

    def test_set_explicit_agent_default_continue_mode_refused(self, tmp_path):
        """``agent.default.continue_mode`` is refused (the any-agent default is the
        BARE key), mirroring model/access."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "agent.default.continue_mode", "false", config_path=project_toml,
        )
        assert msg.startswith("Error:"), msg

    def test_set_continue_mode_per_agent_override(self, tmp_path):
        """A per-agent ``agent.<agent>.continue_mode`` override is a PERSONA key: it
        lands on the agent's OWN ``agents/<agent>/settings.yaml`` flat slot the
        launch reader picks over ``agent.default`` (§2d active-over-default)."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.continue_mode", "false",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "self": {"continue_mode": "false"},
        }

    def test_bare_continue_mode_refused_at_box_scope(self, tmp_path):
        """The box/workset bare-key refusal now COVERS continue_mode (it is in
        ``_is_agent_setting``): a bare set at BOX scope is refused (redirected to
        the box.agent.<key> mirror), nothing lands in a top-level agent table."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "continue_mode", "false",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        if f.exists():
            assert "agent" not in load_doc(f)


# ---------------------------------------------------------------------------
# workset.kuid / workset.skip_kuid_check (P6d)
# ---------------------------------------------------------------------------

class TestWorksetKuidKeys:
    """The kuid + advisory-check keys are settable workset.* keys routed to the
    ``workset:`` table of the command-scope (workset-tier) settings file."""

    def test_set_kuid_routes_to_workset_slot(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.kuid", "abcde", config_path=project_toml,
        )
        assert "Set" in msg and "abcde" in msg
        # Landed in the nested workset.kuid slot (mutation: unregister the key
        # from _KEY_ROUTES → set_config_value returns an "unknown key" error
        # instead of writing → this assertion goes RED).
        data = load_doc(project_toml)
        assert data["workset"]["kuid"] == "abcde"
        # And the reader sees it.
        from kanibako.settings.config import read_workset_kuid
        assert read_workset_kuid(project_toml) == "abcde"

    def test_set_skip_kuid_check_coerces_bool(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        set_config_value(
            "workset.skip_kuid_check", "false", config_path=project_toml,
        )
        data = load_doc(project_toml)
        # KEY_TYPES bool coercion: stored as a real False, not the string "false".
        assert data["workset"]["skip_kuid_check"] is False
        from kanibako.settings.config import read_workset_skip_kuid_check
        assert read_workset_skip_kuid_check(project_toml) is False

    def test_kuid_default_is_sentinel_for_absent_file(self, tmp_path):
        # #3: primary/named (and any unset box) default workset.kuid = "00000".
        from kanibako import kuid
        from kanibako.settings.config import (
            read_workset_kuid,
            read_workset_skip_kuid_check,
        )
        absent = tmp_path / "nope.yaml"
        assert read_workset_kuid(absent) == kuid.SENTINEL == "00000"
        # And skip_kuid_check defaults TRUE (advisory is opt-in strictness).
        assert read_workset_skip_kuid_check(absent) is True


# ---------------------------------------------------------------------------
# The env family: bare RETIRED (R-39), scoped LIVE
# ---------------------------------------------------------------------------

class TestEnvKeys:
    """The engine's half of the R-39 retirement + the scoped replacement.

    The FULL family behaviour (every scope arm, the reserved-name floor, the
    round trip) lives in ``tests/test_env_cmd.py``; these are the seams this
    module already owned.
    """

    def test_set_bare_env_var_is_refused(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.MY_VAR", "hello",
            config_path=tmp_path / "settings.yaml",
            env_path=env_path,
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.MY_VAR" in msg
        assert not env_path.exists()

    def test_get_bare_env_var_reads_nothing(self, tmp_path):
        """The docker ``.env`` file is not a store any more (RQ-1)."""
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        val = get_config_value(
            "env.FOO",
            global_config_path=tmp_path / "kanibako_config.yaml",
            env_project=env_path,
        )
        assert val is None

    def test_get_env_var_not_set(self, tmp_path):
        val = get_config_value(
            "env.MISSING",
            global_config_path=tmp_path / "kanibako_config.yaml",
        )
        assert val is None

    def test_reset_bare_env_var_is_refused(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        msg = reset_config_value(
            "env.FOO", config_path=tmp_path / "p.yaml", env_path=env_path,
        )
        assert msg.startswith("Error:"), msg
        assert env_path.read_text() == "FOO=bar\n"

    def test_scoped_env_var_round_trips(self, tmp_path):
        f = tmp_path / "settings.yaml"
        assert set_config_value(
            "box.env.MY_VAR", "hello",
            config_path=f, command_scope=ConfigLevel.box,
        ) == "Set box.env.MY_VAR=hello"
        assert get_config_value(
            "box.env.MY_VAR",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f, command_scope=ConfigLevel.box,
        ) == "hello"

    def test_reset_scoped_env_var_missing(self, tmp_path):
        msg = reset_config_value(
            "box.env.MISSING",
            config_path=tmp_path / "p.yaml",
            command_scope=ConfigLevel.box,
        )
        assert "No override" in msg

    @pytest.mark.parametrize("verb", ["set", "reset", "read"])
    def test_the_agent_command_scope_never_cures_toward_a_bare_agent_key(
        self, verb,
    ):
        """N-1. The cure is derived from the command scope, and the AGENT scope
        is DISCRIMINATED (spec §0): ``agent.env.<VAR>`` is not a key at all —
        ``ENV_KEY_RE`` spells the agent scope ``agent.<node>`` and refuses the
        bare form. A cure naming it would hand the user a second illegal
        spelling to replace the first.
        """
        from kanibako.settings.config_keys import bare_env_retired_error
        from kanibako.settings.settings_categories import ENV_KEY_RE

        msg = bare_env_retired_error(
            "env.FOO", verb=verb, command_scope=ConfigLevel.agent,
        )
        assert msg is not None and msg.startswith("Error:"), msg
        # The illegal spelling must not appear anywhere in the message …
        assert "'agent.env.FOO'" not in msg, msg
        assert "agent.env.FOO" not in msg, msg
        # … and the cure that IS named must be the discriminated family.
        assert "agent.<agent>.env.FOO" in msg, msg
        # Ground truth for both halves, read off the keyspace itself rather
        # than restated here: bare is not a key, discriminated is.
        assert ENV_KEY_RE.match("agent.env.FOO") is None
        assert ENV_KEY_RE.match("agent.myagent.env.FOO") is not None

    @pytest.mark.parametrize(
        "level,cure",
        [
            (None, "box.env.FOO"),
            (ConfigLevel.box, "box.env.FOO"),
            (ConfigLevel.workset, "workset.env.FOO"),
            (ConfigLevel.system, "system.env.FOO"),
        ],
    )
    def test_the_other_scopes_still_cure_toward_their_own_scoped_key(
        self, level, cure,
    ):
        """The agent carve-out must not disturb the three CONFIG nouns."""
        from kanibako.settings.config_keys import bare_env_retired_error

        msg = bare_env_retired_error(
            "env.FOO", verb="set", command_scope=level,
        )
        assert msg is not None and f"'{cure}'" in msg, msg


# ---------------------------------------------------------------------------
# Target settings (model, continue_mode, autonomous)
# ---------------------------------------------------------------------------

class TestTargetSettings:
    """Tests for target settings keys."""

    def test_set_model(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("model", "sonnet", config_path=project_toml)
        assert "Set model=sonnet" in msg

        # The agent-agnostic CLI writes the reserved agent.default tier.
        data = load_doc(project_toml)
        assert data["agent"]["default"]["model"] == "sonnet"

    def test_get_model(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"model": "opus"}}})

        val = get_config_value(
            "model",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "opus"

    def test_reset_model(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"model": "opus"}}})

        msg = reset_config_value("model", config_path=project_toml)
        assert msg == (
            "Cleared model set on this scope; it now falls back through the "
            "cascade."
        ), msg

    def test_endpoint_is_known_key(self):
        # Block B: endpoint is a settable agent setting, a sibling of model.
        assert is_known_key("endpoint") is True

    def test_set_endpoint(self, tmp_path):
        # endpoint accepted the SAME way as model — writes agent.default tier.
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "endpoint", "http://localhost:8080", config_path=project_toml
        )
        assert "Set endpoint=http://localhost:8080" in msg
        data = load_doc(project_toml)
        assert data["agent"]["default"]["endpoint"] == "http://localhost:8080"

    def test_get_endpoint(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(
            project_toml,
            {"agent": {"default": {"endpoint": "http://ep:9000"}}},
        )
        val = get_config_value(
            "endpoint",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "http://ep:9000"


# ---------------------------------------------------------------------------
# show_config
# ---------------------------------------------------------------------------

class TestShowConfig:
    """Tests for the show_config display function."""

    def test_show_no_overrides(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
        )
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "my:img"\n')
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
        )
        captured = capsys.readouterr()
        assert "box_image" in captured.out
        assert "my:img" in captured.out

    def test_show_with_override(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "default"\n')
        project_toml = tmp_path / "settings.yaml"
        project_toml.write_text('box:\n  image: "custom"\n')

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
        )
        captured = capsys.readouterr()
        assert "box_image" in captured.out
        assert "custom" in captured.out

    def test_effective_new_params_default_none_is_byte_identical(
        self, tmp_path, capsys,
    ):
        """With workset_path/agent_state/env_resolved=None, output is unchanged."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "my:img"\n')
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
        )
        baseline = capsys.readouterr().out

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            workset_path=None,
            agent_state=None,
            env_resolved=None,
        )
        with_none = capsys.readouterr().out

        assert with_none == baseline

    def test_effective_workset_path_overlays(self, tmp_path, capsys):
        """A value set only at the workset level is reflected when supplied."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "sys:img"\n')
        project_toml = tmp_path / "settings.yaml"
        workset_cfg = tmp_path / "config.yaml"
        workset_cfg.write_text('box:\n  image: "ws:img"\n')

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            workset_path=workset_cfg,
        )
        captured = capsys.readouterr()
        assert "box_image" in captured.out
        assert "ws:img" in captured.out
        assert "sys:img" not in captured.out

    def test_effective_agent_state_renders_with_override_marker(
        self, tmp_path, capsys,
    ):
        """agent_state is rendered; only box-level keys get the override marker."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"
        project_toml.write_text('agent:\n  default:\n    model: "sonnet"\n')

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            agent_state={"model": "sonnet", "continue_mode": "true"},
        )
        captured = capsys.readouterr()
        # model is set at box level -> marked override
        assert "model = sonnet (override)" in captured.out
        # continue_mode comes from a lower level -> no marker
        assert "continue_mode = true\n" in captured.out
        assert "continue_mode = true (override)" not in captured.out

    def test_effective_env_resolved_used_when_supplied(self, tmp_path, capsys):
        """env_resolved is the source dict for the env section when given."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            env_resolved={"RESOLVED_VAR": "yes"},
        )
        captured = capsys.readouterr()
        # ⚑ Rendered ``env <VAR>``, NOT ``env.<VAR>``: every other row in this
        # display is a KEY and ``env.<VAR>`` is now a REFUSED spelling (R-39),
        # while these rows are the MERGE the box receives, which no single key
        # names.
        assert "env RESOLVED_VAR = yes" in captured.out
        assert "env.RESOLVED_VAR" not in captured.out

    def test_show_hides_legacy_resource_overrides_table(self, tmp_path):
        # The dropped resource.* surface (spec §3 D-M7) may leave an inert
        # ``resource_overrides`` table in a pre-1.7.x system file; it must NOT
        # render in the show/effective view (display-only legacy filter) while a
        # real nested scope table still does.
        from kanibako.settings.config_display import _nested_settings_overrides
        sys_file = tmp_path / "system-settings.yaml"
        dump_doc(sys_file, {
            "resource_overrides": {"plugins": "/legacy"},   # dead legacy table
            "workset": {"auth": {"share_allowed": False}},  # a real nested table
        })
        out = _nested_settings_overrides(sys_file)
        assert not any(k.startswith("resource_overrides") for k in out), out
        assert out.get("workset.auth.share_allowed") == "false"


# ---------------------------------------------------------------------------
# reset_all
# ---------------------------------------------------------------------------

class TestResetAll:
    """Tests for the reset-all operation."""

    def test_reset_all_with_force(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        project_toml.write_text('box:\n  image: "custom"\n')
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")

        msg = reset_all(config_path=project_toml, env_path=env_path, force=True)
        assert "Reset" in msg

    def test_reset_all_nothing_to_reset(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = reset_all(config_path=project_toml, force=True)
        assert "No overrides" in msg

    def test_reset_all_clears_nested_scope_tables(self, tmp_path):
        # Residuals item 3: --all clears NESTED scope tables (box.auth /
        # box.bindings), not only the flat KanibakoConfig fields. Baseline-RED at
        # 6340dad: these tables survived reset --all.
        box_file = tmp_path / "box-settings.yaml"
        dump_doc(box_file, {
            "box": {
                "image": "custom",  # a flat field (already handled)
                "auth": {"global_enabled": False, "workset_enabled": True},
                "bindings": {"ro": {"vault": ["/h", "~/vault/ro"]}},
            },
        })
        msg = reset_all(
            config_path=box_file, force=True, command_scope=ConfigLevel.box,
        )
        assert "Reset" in msg and "No overrides" not in msg, msg
        # The WHOLE box table is gone (flat + nested).
        assert "box" not in load_doc(box_file), load_doc(box_file)

    def test_reset_all_preserves_upward_table(self, tmp_path):
        # Residuals item 3 guard (Editor's yardstick): a table whose single reset
        # would be REFUSED as upward (a hostile ``system:`` hand-edited into a
        # BOX file) must NOT be cleared by --all either. The downward/own table
        # IS cleared; the upward one is left intact.
        box_file = tmp_path / "box-settings.yaml"
        dump_doc(box_file, {
            "box": {"auth": {"global_enabled": False}},   # own scope → cleared
            "system": {"auth": {"share_allowed": False}},  # UPWARD → preserved
        })
        reset_all(config_path=box_file, force=True, command_scope=ConfigLevel.box)
        doc = load_doc(box_file)
        assert "box" not in doc, doc          # own table cleared
        assert doc.get("system", {}).get("auth", {}).get("share_allowed") is False

    def test_reset_all_workset_clears_downward_box_defaults(self, tmp_path):
        # A workset file may hold DOWNWARD box.* defaults (spec §0). --all at the
        # workset scope clears them (workset contains box), plus its own
        # workset.auth table.
        ws_file = tmp_path / "workset-settings.yaml"
        dump_doc(ws_file, {
            "workset": {"auth": {"share_allowed": False}},
            "box": {"image": "ws-default:img"},  # downward default
        })
        reset_all(
            config_path=ws_file, force=True, command_scope=ConfigLevel.workset,
        )
        doc = load_doc(ws_file)
        assert "workset" not in doc, doc
        assert "box" not in doc, doc

    def test_reset_all_count_is_real_removals_not_phantom(self, tmp_path):
        # Editor F2: load_project_overrides reports a phantom ``config_paths``
        # field for any file carrying a [system]/[config] table, and
        # unset_project_config_key returns False for a flat key naming no real
        # top-level entry. The flat pass must count ONLY real removals — a file
        # with ONLY a structural [system] table (no overrides) says
        # "No overrides to reset.", not "Reset N".
        f = tmp_path / "settings.yaml"
        dump_doc(f, {"system": {"cache": "/x"}})  # structural-ish, no override
        msg = reset_all(config_path=f, force=True)  # no command_scope
        assert "No overrides to reset." in msg, msg
        # And with ONE real flat override alongside the [system] table, count is 1.
        dump_doc(f, {"system": {"cache": "/x"}, "box": {"image": "img"}})
        msg2 = reset_all(config_path=f, force=True)
        assert "Reset 1 override(s)." in msg2, msg2

    def test_reset_all_without_scope_leaves_nested_tables(self, tmp_path):
        # Backward-compat: command_scope=None (no scope context) does NOT touch a
        # nested scope table (the guard can't be evaluated) — flat/agent/env
        # clears still run. This pins the None fall-through as deliberate.
        box_file = tmp_path / "box-settings.yaml"
        dump_doc(box_file, {"box": {"auth": {"global_enabled": False}}})
        reset_all(config_path=box_file, force=True)  # no command_scope
        assert load_doc(box_file)["box"]["auth"]["global_enabled"] is False


# ---------------------------------------------------------------------------
# ConfigLevel enum
# ---------------------------------------------------------------------------

class TestConfigLevel:
    """Verify ConfigLevel enum values exist."""

    def test_levels(self):
        assert ConfigLevel.box.value == "box"
        assert ConfigLevel.workset.value == "workset"
        assert ConfigLevel.agent.value == "agent"
        assert ConfigLevel.system.value == "system"


# ---------------------------------------------------------------------------
# H1 regression — typed writer routes every advertised key (never crashes)
# ---------------------------------------------------------------------------

class TestH1NoCrashOnAdvertisedKeys:
    """H1: ``config set`` must NEVER raise for advertised keys.

    Before 2d these keys fell through to ``_split_config_key`` which raised an
    uncaught ``ValueError`` (the CLI dumped a traceback). The typed writer
    routes every known key and returns an error string (never raises) for
    unknown keys.
    """

    def test_set_box_auth_global_enabled_no_crash(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        # Auth 3-tier SHARING (2026-07-01 redesign): the box's per-tier opt-out
        # knob, a typed bool routed to the box.auth section. Must not raise.
        msg = set_config_value(
            "box.auth.global_enabled", "false", config_path=project_toml
        )
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["box"]["auth"]["global_enabled"] is False

    def test_set_box_auth_workset_enabled_no_crash(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "box.auth.workset_enabled", "false", config_path=project_toml
        )
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["box"]["auth"]["workset_enabled"] is False

    def test_set_allow_helpers_lands_in_agent_default_tier(self, tmp_path):
        """allow_helpers moved to the AGENT keyspace (spec §2d): the bare
        key is the any-agent ``agent.default`` tier (mirrors ``model``), NOT a
        flat top-level scalar. Clean break — nothing lands at the old scopeless
        ``allow_helpers`` key."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("allow_helpers", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        # Agent-scope scalar: lands in [agent][default]allow_helpers (like model).
        assert data["agent"]["default"]["allow_helpers"] == "false"
        # Clean break: the old flat top-level scalar is gone.
        assert "allow_helpers" not in data

    def test_set_access_lands_in_agent_default_tier(self, tmp_path):
        """``access`` is an AGENT-scope enum key (spec §2d, R-41): the bare key is
        the any-agent ``agent.default`` tier (mirrors ``model``/``allow_helpers``),
        written VERBATIM (no KEY_TYPES coercion — validated at set AND at launch)."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("access", "restricted", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["agent"]["default"]["access"] == "restricted"
        # No flat top-level scalar leaks out.
        assert "access" not in data

    def test_set_explicit_agent_default_access_refused(self, tmp_path):
        """An explicit ``agent.default.access`` write is REFUSED — the any-agent
        default is the BARE key (``default`` is the reserved tier, never a persona
        node)."""
        cf = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "agent.default.access", "restricted",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=tmp_path / "agents",
        )
        assert msg.startswith("Error:")
        assert "reserved any-agent tier" in msg

    def test_set_access_off_enum_value_is_rejected(self, tmp_path):
        """``access`` is AUTH-CRITICAL: a value outside the enum must be REJECTED
        at set time, never stored to be re-read at launch.

        Mutation proof: dropping the ``is_access_key`` write-guard lets this
        through and this assertion reddens."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("access", "fll", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "restricted | editing | full" in msg
        # NOT written: the typo never lands in the file.
        assert not project_toml.exists() or "access" not in (
            load_doc(project_toml).get("agent", {}).get("default", {})
        )

    def test_set_access_rejects_the_retired_boolean_literals(self, tmp_path):
        """⚑ Muscle memory: ``access=true`` is NOT ``full``.  The boolean
        spelling is retired, and silently mapping it at SET time would put two
        vocabularies on one key."""
        for literal in ("true", "false", "1", "0", "yes", "no"):
            project_toml = tmp_path / f"settings_{literal}.yaml"
            msg = set_config_value("access", literal, config_path=project_toml)
            assert msg.startswith("Error:"), literal
            assert not project_toml.exists()

    def test_set_access_is_case_sensitive(self, tmp_path):
        """EXACT matching: the stored value is what the launch resolver reads
        back, and that resolver is exact too."""
        project_toml = tmp_path / "settings.yaml"
        assert set_config_value(
            "access", "FULL", config_path=project_toml,
        ).startswith("Error:")

    def test_set_access_accepts_every_declared_tier(self, tmp_path):
        """Happy path: each declared tier is accepted + written VERBATIM."""
        for tier in ("restricted", "editing", "full"):
            project_toml = tmp_path / f"settings_{tier}.yaml"
            msg = set_config_value("access", tier, config_path=project_toml)
            assert msg.startswith("Set"), tier
            data = load_doc(project_toml)
            assert data["agent"]["default"]["access"] == tier

    def test_set_box_enable_vault_lands_in_box_table(self, tmp_path):
        """P2: ``box.enable_vault`` routes to the ``box:`` table nested slot
        ``enable_vault`` as a real bool (NOT the [project] section, NOT a
        string)."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("box.enable_vault", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["box"]["enable_vault"] is False
        # Real bool, not the string "false" (mutation guard on KEY_TYPES).
        assert isinstance(data["box"]["enable_vault"], bool)
        # Clean break: nothing lands in [project].
        assert "enable_vault" not in data.get("project", {})

    def test_set_box_enable_vault_preserves_other_box_keys(self, tmp_path):
        """The nested write merges — a pre-existing ``box.image`` survives."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"box": {"image": "img:1"}})
        set_config_value("box.enable_vault", "false", config_path=project_toml)
        data = load_doc(project_toml)
        assert data["box"]["image"] == "img:1"
        assert data["box"]["enable_vault"] is False

    def test_reset_box_enable_vault_removes_it(self, tmp_path):
        """Reset clears the box-scope override (sparse store)."""
        project_toml = tmp_path / "settings.yaml"
        set_config_value("box.enable_vault", "false", config_path=project_toml)
        assert load_doc(project_toml)["box"]["enable_vault"] is False
        reset_config_value(
            "box.enable_vault", config_path=project_toml,
            command_scope=ConfigLevel.box,
        )
        assert "enable_vault" not in load_doc(project_toml).get("box", {})

    def test_set_workset_registry_lands_in_workset_table_as_string(self, tmp_path):
        """P3: ``workset.registry`` routes to the ``workset:`` table nested slot
        ``registry`` as a real STRING path (NOT bool-coerced, NOT [project])."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.registry", "/custom/reg.yaml", config_path=project_toml
        )
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["workset"]["registry"] == "/custom/reg.yaml"
        # A real string path — NOT coerced to a bool (no KEY_TYPES entry).
        assert isinstance(data["workset"]["registry"], str)
        # Sparse: nothing lands in [project] or elsewhere.
        assert "registry" not in data.get("project", {})

    def test_set_workset_registry_preserves_other_workset_keys(self, tmp_path):
        """The nested write merges — a pre-existing ``workset:`` key survives."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"workset": {"auth": {"share_allowed": True}}})
        set_config_value(
            "workset.registry", "/custom/reg.yaml", config_path=project_toml
        )
        data = load_doc(project_toml)
        assert data["workset"]["auth"]["share_allowed"] is True
        assert data["workset"]["registry"] == "/custom/reg.yaml"

    def test_reset_workset_registry_removes_it(self, tmp_path):
        """Reset clears the workset-scope override (sparse store)."""
        project_toml = tmp_path / "settings.yaml"
        set_config_value(
            "workset.registry", "/custom/reg.yaml", config_path=project_toml
        )
        assert load_doc(project_toml)["workset"]["registry"] == "/custom/reg.yaml"
        reset_config_value(
            "workset.registry", config_path=project_toml,
            command_scope=ConfigLevel.workset,
        )
        assert "registry" not in load_doc(project_toml).get("workset", {})

    def test_set_workset_boxes_lands_in_workset_table_as_string(self, tmp_path):
        """P6a: ``workset.boxes`` routes to the ``workset:`` table nested slot
        ``boxes`` as a real STRING path (NOT bool-coerced, NOT [project])."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.boxes", "/srv/boxes", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        data = load_doc(project_toml)
        assert data["workset"]["boxes"] == "/srv/boxes"
        assert isinstance(data["workset"]["boxes"], str)
        assert "boxes" not in data.get("project", {})

    def test_set_workset_auth_path_nests_under_auth(self, tmp_path):
        """P6a: ``workset.auth.path`` nests under ``workset.auth`` (the same nested
        pattern as ``workset.auth.share_allowed``)."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.auth.path", "/srv/auth", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        assert load_doc(project_toml)["workset"]["auth"]["path"] == "/srv/auth"

    def test_set_workset_channels_common_nests_under_channels(self, tmp_path):
        """P6a: ``workset.channels.common`` nests under ``workset.channels``."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.channels.common", "/srv/common", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        assert (
            load_doc(project_toml)["workset"]["channels"]["common"] == "/srv/common"
        )

    def test_set_workset_anchor_preserves_other_workset_keys(self, tmp_path):
        """The nested write merges — a pre-existing ``workset:`` key survives."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"workset": {"registry": "/reg.yaml"}})
        set_config_value("workset.boxes", "/srv/boxes", config_path=project_toml)
        data = load_doc(project_toml)
        assert data["workset"]["registry"] == "/reg.yaml"
        assert data["workset"]["boxes"] == "/srv/boxes"

    def test_reset_workset_boxes_removes_it(self, tmp_path):
        """Reset clears the workset-scope override (sparse store)."""
        project_toml = tmp_path / "settings.yaml"
        set_config_value("workset.boxes", "/srv/boxes", config_path=project_toml)
        assert load_doc(project_toml)["workset"]["boxes"] == "/srv/boxes"
        reset_config_value(
            "workset.boxes", config_path=project_toml,
            command_scope=ConfigLevel.workset,
        )
        assert "boxes" not in load_doc(project_toml).get("workset", {})

    def test_set_workset_boxes_at_box_scope_refused(self, tmp_path):
        """P6a: a workset anchor is UPWARD from the box scope — refused (matches the
        sibling ``workset.auth.share_allowed`` direction). Nothing is written."""
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "workset.boxes", "/srv/boxes",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "workset" in msg and "box" in msg
        assert not f.exists()

    def test_set_workset_boxes_at_workset_scope_allowed(self, tmp_path):
        """P6a: the SAME-scope (workset) write is accepted and lands in the workset
        settings file (the sibling behavior for ``workset.auth.share_allowed``)."""
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "workset.boxes", "/srv/boxes",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["workset"]["boxes"] == "/srv/boxes"

    def test_set_workset_workspaces_lands_in_workset_table_as_string(
        self, tmp_path
    ):
        """Bifrost A1: ``workset.workspaces`` is CLI-settable (manifest ``set:
        cli+file``), routed to the ``workset:`` nested slot ``workspaces`` — a
        real STRING path (NOT bool-coerced).  Pre-fix, the key was declared and
        consumed live but refused by the verbs ("unknown config key"), forcing
        a settings-file edit."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.workspaces", "/srv/pods", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        data = load_doc(project_toml)
        assert data["workset"]["workspaces"] == "/srv/pods"
        assert isinstance(data["workset"]["workspaces"], str)

    def test_set_workset_channelroot_lands_in_workset_table_as_string(
        self, tmp_path
    ):
        """``workset.channelroot`` — the sibling resolved workset dir key
        (§3.3), same route shape as ``workset.workspaces``."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.channelroot", "/srv/comms", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        assert load_doc(project_toml)["workset"]["channelroot"] == "/srv/comms"

    def test_set_workset_workspaces_read_back_by_the_live_resolver(
        self, tmp_path
    ):
        """End-to-end with the CONSUMER: the slot ``config set`` writes is the
        one ``resolve_workset_workspaces`` reads the repoint from — one
        location, no drift."""
        from pathlib import Path

        from kanibako.project.workset import (
            load_workset_settings_doc,
            resolve_workset_workspaces,
        )

        project_toml = tmp_path / "settings.yaml"
        set_config_value(
            "workset.workspaces", "/srv/pods", config_path=project_toml
        )
        assert resolve_workset_workspaces(
            tmp_path, load_workset_settings_doc(tmp_path)
        ) == Path("/srv/pods")

    def test_reset_workset_workspaces_removes_it(self, tmp_path):
        """Reset clears the workset-scope override (sparse store)."""
        project_toml = tmp_path / "settings.yaml"
        set_config_value(
            "workset.workspaces", "/srv/pods", config_path=project_toml
        )
        reset_config_value(
            "workset.workspaces", config_path=project_toml,
            command_scope=ConfigLevel.workset,
        )
        assert "workspaces" not in load_doc(project_toml).get("workset", {})

    def test_set_workset_workspaces_at_box_scope_refused(self, tmp_path):
        """UPWARD from the box scope — refused like the sibling anchors (the
        bifrost A1 probe was ``box set workset.workspaces``: it now refuses
        with the DIRECTION cure, not "unknown config key")."""
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "workset.workspaces", "/srv/pods",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "workset" in msg and "box" in msg
        assert not f.exists()

    def test_set_mode_rejected_not_settable(self, tmp_path):
        """``mode`` is no longer settable via config set (block B1, spec §2b /
        §0): the project mode is the RO identity anchor ``meta.box.mode``, set by
        the bootstrap layer at box creation, NOT overridable. ``config set mode``
        is now an unknown-key error; nothing is written.
        """
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("mode", "primary", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "unknown config key" in msg
        # The bootstrap [project].mode identity write is untouched, but config set
        # never created one.
        assert not project_toml.exists() or "mode" not in load_doc(project_toml).get(
            "project", {}
        )

    def test_set_dead_layout_key_rejected(self, tmp_path):
        """W4: layout is a deleted dead key — now an unknown-key error."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("layout", "robust", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "unknown config key" in msg

    def test_set_unknown_key_returns_error_not_raise(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        # No exception: an UNKNOWN key returns an error string.
        msg = set_config_value("totally-bogus-key", "x", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "unknown config key" in msg
        # Nothing was written.
        assert not project_toml.exists() or "totally-bogus-key" not in load_doc(project_toml)

    def test_reset_unknown_key_returns_error_not_raise(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = reset_config_value("totally-bogus-key", config_path=project_toml)
        assert msg.startswith("Error:")

    def test_get_set_share_the_same_known_key_table(self, tmp_path):
        """Every routed key set by the writer reads back (no asymmetry)."""
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"
        for key, val in [
            ("box.auth.global_enabled", "false"),
            ("box.image", "custom:latest"),
            ("workset.vault_ro", "/ro"),
        ]:
            set_config_value(key, val, config_path=project_toml)
            got = get_config_value(
                key,
                global_config_path=global_cfg,
                project_toml=project_toml,
            )
            assert got is not None, f"get returned None for {key} after set"


# ---------------------------------------------------------------------------
# H2 regression — boolean keys coerce to real bools (load back as bool)
# ---------------------------------------------------------------------------

class TestH2BoolCoercion:
    """H2: bool keys must store a real bool, not the string ``'false'``."""

    def test_set_box_share_images_false_loads_as_real_bool(self, tmp_path):
        from kanibako.settings.config import load_config

        project_toml = tmp_path / "settings.yaml"
        set_config_value("box.share_images", "false", config_path=project_toml)

        # On-disk: a real YAML bool, not the string 'false'.
        data = load_doc(project_toml)
        assert data["box"]["share_images"] is False

        # Loader reads back a real bool -> a truthiness check sees it disabled.
        cfg = load_config(project_toml)
        assert cfg.box_share_images is False
        assert not cfg.box_share_images  # consumer disable-check honored

    def test_set_box_share_images_various_truthy_falsy(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        for raw, expected in [
            ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
            ("false", False), ("0", False), ("no", False), ("off", False),
        ]:
            set_config_value("box.share_images", raw, config_path=project_toml)
            assert load_doc(project_toml)["box"]["share_images"] is expected

    def test_get_allow_helpers_round_trips_agent_default(self, tmp_path):
        """The bare ``allow_helpers`` get reads the value STORED at the any-agent
        ``agent.default`` tier (symmetric with set; mirrors ``model``)."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"allow_helpers": "false"}}})
        val = get_config_value(
            "allow_helpers",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "false"

    def test_set_allow_helpers_per_agent_override(self, tmp_path):
        """A per-agent override ``agent.<agent>.allow_helpers`` is a PERSONA key
        (like ``agent.<agent>.model``): it lands on the agent's OWN
        ``agents/<agent>/settings.yaml`` flat ``agent:`` slot the launch reads."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.allow_helpers", "false",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "self": {"allow_helpers": "false"},
        }

    def test_get_access_round_trips_agent_default(self, tmp_path):
        """The bare ``access`` get reads the value STORED at the any-agent
        ``agent.default`` tier (symmetric with set; mirrors ``model``)."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"access": "restricted"}}})
        val = get_config_value(
            "access",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "restricted"

    def test_set_access_per_agent_override(self, tmp_path):
        """A per-agent override ``agent.<agent>.access`` is a PERSONA key: it
        lands on the agent's OWN ``agents/<agent>/settings.yaml`` flat slot the
        launch reader picks over ``agent.default`` (§2d active-over-default)."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.access", "restricted",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "self": {"access": "restricted"},
        }

    def test_retired_autonomous_and_auto_approve_do_not_route(self, tmp_path):
        """The dead ``autonomous`` leaf and the RETIRED ``auto_approve`` spelling
        (R-41: superseded by ``access``) are neither known keys; a bare set of
        either is refused as unknown (never lands in agent.default).

        ⚑ ``access`` moved the OTHER way in the same ruling — it is now the
        declared key, so it is asserted KNOWN here, in the same test, so the two
        halves of the swap can never drift apart."""
        assert is_known_key("autonomous") is False
        assert is_known_key("auto_approve") is False
        assert is_known_key("access") is True
        project_toml = tmp_path / "settings.yaml"
        for dead in ("autonomous", "auto_approve"):
            msg = set_config_value(dead, "true", config_path=project_toml)
            assert msg == f"Error: unknown config key: {dead}", dead
            assert not project_toml.exists()

    def test_bool_key_rejects_garbage(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("box.share_images", "maybe", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "boolean" in msg


def _seed_system_agent(path, name):
    """Programmatically write ``system.agent`` (the path ``setup`` uses).

    ⮕ P7: writes the ``system:`` table's ``agent`` leaf — where
    ``config.read_system_agent`` reads back, where the CLI ``set`` writes, AND
    where ``assemble_levels`` reads the SYSTEM tier. (Was the ``agent.default``
    table's ``default_agent`` leaf, spec §2g renamed + relocated it.)
    """
    from kanibako.settings.config_io import write_nested_key

    write_nested_key(path, ("system",), "agent", name)


class TestSystemAgent:
    """``system.agent``: an ordinary system-scope SETTINGS key (spec §2g).

    ⮕ P7 renamed it from ``system.default_agent`` and RELOCATED it out of the
    reserved ``agent.default`` table into the ``system:`` table, which deleted the
    four-site special case: set/get/reset now route through ``_KEY_ROUTES`` like
    every other scope-prefixed settings key, and the launch reads it as the
    ordinary SYSTEM tier of the cascade.
    """

    def test_set_writes_the_settings_tier(self, tmp_path):
        from kanibako.settings.config import read_system_agent

        f = tmp_path / "settings.yaml"
        msg = set_config_value("system.agent", "claude", config_path=f)
        assert not msg.startswith("Error:"), msg
        # Stored exactly where the shipped reader AND the cascade's system tier read.
        assert load_doc(f)["system"]["agent"] == "claude"
        assert read_system_agent(f) == "claude"

    def test_reset_removes_the_setting(self, tmp_path):
        from kanibako.settings.config import read_system_agent

        f = tmp_path / "settings.yaml"
        _seed_system_agent(f, "claude")
        msg = reset_config_value("system.agent", config_path=f)
        assert not msg.startswith("Error:"), msg
        assert read_system_agent(f) is None

    def test_get_reads_programmatic_write(self, tmp_path):
        from kanibako.settings.config import read_system_agent

        # Residuals item 2: the key lives in the system SETTINGS file
        # (@config.settings, where read_system_agent + set/reset all agree), so a
        # system-scope get reads it via ``system_settings_path`` — NOT the
        # kanibako_config.yaml CONFIG file.
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        _seed_system_agent(ssp, "goose")
        # The interface getter and the launch-time reader agree on the settings file.
        assert get_config_value(
            "system.agent", global_config_path=cf, system_settings_path=ssp,
        ) == "goose"
        assert read_system_agent(ssp) == "goose"

    def test_get_unset_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        cf.touch()
        assert get_config_value("system.agent", global_config_path=cf) is None

    def test_box_get_does_not_leak_the_global_system_agent(self, tmp_path):
        # Residuals item 2 (spec §2a Read verbs, clause 5): a plain get at a
        # box/workset noun must NOT surface the GLOBAL value — that is another
        # (containing) tier's value, reserved for --effective.
        cf = tmp_path / "kanibako_config.yaml"
        _seed_system_agent(cf, "claude")  # a global default exists
        box_file = tmp_path / "box-settings.yaml"
        box_file.touch()  # the box noun stores nothing
        assert (
            get_config_value(
                "system.agent",
                global_config_path=cf,
                project_toml=box_file,
            )
            is None
        )


class TestSystemConfigFileOnly:
    """The STRUCTURAL system.* path-tier family is FILE-ONLY (F2 narrowing:
    the old ALL-system.* catch-all is deliberately flipped).

    Only the ``SYSTEM_PATH_DEFAULTS`` family (+ ``system.setup_completed``,
    whose shipped reader reads the config file) is refused, pointing at the
    REAL resolved bootstrap config file.  Retired key names (``system.data``
    → ``config.data``) and settings keys are NOT this family.
    """

    def test_set_structural_system_key_refused(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        for key in (
            "system.cache", "system.backup", "system.channelroot",
            # M-11: ``system.base_template`` is RETIRED; ``system.template`` and
            # the new ``system.canon`` are the structural spellings now.
            "system.template", "system.canon", "system.runtime",
            "system.channels.common", "system.setup_completed",
        ):
            msg = set_config_value(key, "x", config_path=cf)
            assert msg.startswith("Error:"), key
            assert "structural config key" in msg
            # The advice names the REAL bootstrap config file (the [system]
            # table resolve_system_paths reads) — never the command scope's
            # settings file.
            assert "kanibako_config.yaml" in msg
        # Nothing was written to the file.
        assert not cf.exists()

    def test_reset_structural_system_key_refused(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        for key in ("system.cache", "system.setup_completed"):
            msg = reset_config_value(key, config_path=cf)
            assert msg.startswith("Error:"), key
            assert "structural config key" in msg

    def test_retired_system_key_is_unknown_not_structural(self, tmp_path):
        """``system.data`` was RENAMED to ``config.data`` (block #3a): it is
        not in the structural family, so it is an unknown key — pointing a
        user at the config file for a key the resolver drops would be the
        exact wrong-file advice F2 eliminates."""
        cf = tmp_path / "kanibako_config.yaml"
        msg = set_config_value("system.data", "x", config_path=cf)
        assert msg.startswith("Error: unknown config key"), msg
        assert not cf.exists()

    def test_set_config_foundation_key_refused_every_scope(self, tmp_path):
        """Block B2: ``config.*`` keys are refused via ``config set`` at EVERY
        command scope with the ruled bootstrap-file message (NOT the older generic
        ``system_key_refusal`` that named ``setup``)."""
        for scope in (None, ConfigLevel.system, ConfigLevel.box, ConfigLevel.workset):
            cf = tmp_path / "kanibako_config.yaml"
            for key in (
                "config.data", "config.settings", "config.agents",
                "config.primary_workset", "config.registry", "config.journal",
            ):
                msg = set_config_value(key, "x", config_path=cf, command_scope=scope)
                assert msg.startswith(
                    "Error: config.* keys can only be set by editing"
                ), (key, scope)
                assert "structural config key" not in msg
                assert "kanibako setup" not in msg
            assert not cf.exists()  # nothing written

    def test_reset_config_foundation_key_refused_every_scope(self, tmp_path):
        """Block B2: ``config.*`` keys are refused via ``--reset`` at EVERY command
        scope with the ruled message (verb "changed" — a reset is a change)."""
        for scope in (None, ConfigLevel.system, ConfigLevel.box, ConfigLevel.workset):
            cf = tmp_path / "kanibako_config.yaml"
            for key in ("config.data", "config.registry", "config.journal"):
                msg = reset_config_value(key, config_path=cf, command_scope=scope)
                assert msg.startswith(
                    "Error: config.* keys can only be changed by editing"
                ), (key, scope)
                assert "structural config key" not in msg
                assert "kanibako setup" not in msg

    def test_get_system_config_key_still_reads(self, tmp_path):
        """Reads/shows are unaffected — only writes are refused."""
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        write_nested_key(cf, ("system",), "cache", "/custom/cache")
        assert (
            get_config_value("system.cache", global_config_path=cf)
            == "/custom/cache"
        )


class TestConfigJournalRecognition:
    """B2 (§3.3 ruling "needs to be recognized"): ``config.journal`` is a
    recognized ``config.*`` key with EXACT sibling parity — the known-key
    heuristic treats it as a key (not a project name), the get path reads the
    raw stored ``[config]`` value like its five siblings, and set/reset refuse
    it with the ruled bootstrap-file message.  (It was already resolved and
    consumed as ``std.journal``; only recognition was missing.)"""

    def test_journal_is_a_known_key(self):
        assert is_known_key("config.journal") is True

    def test_get_reads_stored_value_like_the_registry_sibling(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        dump_doc(cf, {"config": {
            "registry": "/srv/kani/registry.yaml",
            "journal": "/srv/kani/journal.yaml",
        }})
        settings = tmp_path / "settings.yaml"
        assert get_config_value(
            "config.registry", global_config_path=cf, project_toml=settings,
        ) == "/srv/kani/registry.yaml"
        assert get_config_value(
            "config.journal", global_config_path=cf, project_toml=settings,
        ) == "/srv/kani/journal.yaml"

    def test_get_unset_is_none_like_the_sibling(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        settings = tmp_path / "settings.yaml"
        for key in ("config.registry", "config.journal"):
            assert get_config_value(
                key, global_config_path=cf, project_toml=settings,
            ) is None

    def test_set_and_reset_refused_with_the_ruled_message(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        msg = set_config_value("config.journal", "/x/j.yaml", config_path=cf)
        assert msg.startswith(
            "Error: config.* keys can only be set by editing"
        ), msg
        msg = reset_config_value("config.journal", config_path=cf)
        assert msg.startswith(
            "Error: config.* keys can only be changed by editing"
        ), msg
        assert not cf.exists()

    def test_non_system_key_still_settable_at_global_tier(self, tmp_path):
        """Narrow scope (a): only system.*-prefixed keys are refused.  A
        regular key still sets fine via the (global) config path."""
        cf = tmp_path / "kanibako_config.yaml"
        msg = set_config_value("box.image", "ghcr.io/foo:bar", config_path=cf)
        assert msg.startswith("Set")
        assert load_doc(cf)["box"]["image"] == "ghcr.io/foo:bar"

    def test_write_system_value_round_trips(self, tmp_path):
        """The programmatic helper bypasses the guard and round-trips, while
        preserving other keys (what setup relies on)."""
        from kanibako.settings.config import read_setup_completed
        from kanibako.settings.config_interface import write_system_value
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        write_nested_key(cf, ("system",), "data", "/keep/me")
        write_system_value(cf, "setup_completed", "1.6.0")

        data = load_doc(cf)
        assert data["system"]["setup_completed"] == "1.6.0"
        assert data["system"]["data"] == "/keep/me"  # other keys preserved
        # The raw reader and a typed-loader-agnostic read agree.
        assert read_setup_completed(cf) == "1.6.0"


class TestSystemSettingsTierSplit:
    """SYSTEM scope: SETTINGS route to @system.settings (global/settings.yaml),
    while system.* CONFIG keys stay in kanibako_config.yaml — the config/settings split.

    The interface fns take an optional ``system_settings_path``; when set (the
    SYSTEM scope) SETTINGS reads/writes go there, NOT to ``config_path`` /
    ``global_config_path`` (which remain the CONFIG file for ``system.*``).
    """

    def test_system_agent_set_routes_to_settings_file(self, tmp_path):
        """F3 flip: the set SUCCEEDS and lands in the system SETTINGS file's
        ``system:`` table — never the kanibako_config.yaml CONFIG file.
        (⮕ P7: the table moved from ``agent.default`` to ``system``, §2g.)"""
        cf = tmp_path / "kanibako_config.yaml"        # CONFIG file
        ssp = tmp_path / "global" / "settings.yaml"  # SETTINGS file
        ssp.parent.mkdir(parents=True, exist_ok=True)
        msg = set_config_value(
            "system.agent", "claude",
            config_path=cf, system_settings_path=ssp,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ssp)["system"]["agent"] == "claude"
        assert not cf.exists()

    def test_system_agent_reads_from_settings_file(self, tmp_path):
        from kanibako.settings.config import read_system_agent
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        # setup writes it programmatically into the settings file's table.
        write_nested_key(ssp, ("system",), "agent", "goose")
        # Read back via interface getter (system scope) + launch-time reader.
        assert get_config_value(
            "system.agent", global_config_path=cf, system_settings_path=ssp,
        ) == "goose"
        # The launch-time reader points at the SETTINGS file, not kanibako_config.yaml.
        assert read_system_agent(ssp) == "goose"
        # A stale value in kanibako_config.yaml does NOT feed the tier.
        assert read_system_agent(cf) is None

    def test_agent_setting_routes_to_settings_file(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        set_config_value(
            "model", "gpt-5", config_path=cf, system_settings_path=ssp,
        )
        # Agent SETTING lands in the settings file, not the CONFIG file.
        assert load_doc(ssp)["agent"]["default"]["model"] == "gpt-5"
        assert not cf.exists()
        assert get_config_value(
            "model", global_config_path=cf, system_settings_path=ssp,
        ) == "gpt-5"

    def test_system_config_key_stays_in_config_file(self, tmp_path):
        """STRUCTURAL system.* CONFIG read uses global_config_path
        (kanibako_config.yaml), even when a settings file is supplied —
        config/settings stay separate."""
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        write_nested_key(cf, ("system",), "cache", "/custom/cache")
        assert get_config_value(
            "system.cache", global_config_path=cf, system_settings_path=ssp,
        ) == "/custom/cache"
        # The settings file was never touched by a CONFIG read.
        assert not ssp.exists()

    def test_reset_system_agent_removes_from_settings_file(self, tmp_path):
        """F3 flip: reset removes the setting from the SETTINGS file (where the
        launch reader looks), leaving the CONFIG file untouched."""
        from kanibako.settings.config import read_system_agent
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        write_nested_key(ssp, ("system",), "agent", "claude")
        msg = reset_config_value(
            "system.agent", config_path=cf, system_settings_path=ssp,
        )
        # Honest cleared-form (F7). ⮕ P7: the message now names the key in the
        # GENERIC branch's flat spelling (``system_agent``), because the key lost
        # its four-site special case and routes like every other scope-prefixed
        # settings key — one branch, one message style.
        assert msg == (
            "Cleared system_agent set on this scope; it now falls back "
            "through the cascade."
        ), msg
        assert read_system_agent(ssp) is None
        assert not cf.exists()

    def test_reset_all_clears_settings_and_config_separately(self, tmp_path):
        from kanibako.settings.config_io import write_nested_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        # A SETTING (settings file) + a config override (config file).
        set_config_value(
            "model", "opus",
            config_path=cf, system_settings_path=ssp,
        )
        write_nested_key(cf, ("box",), "image", "ghcr.io/x:1")
        reset_all(config_path=cf, force=True, system_settings_path=ssp)
        # The SETTING is gone from the settings file.
        assert not load_doc(ssp).get("agent")

    def test_absent_settings_file_is_graceful(self, tmp_path):
        """Missing global/settings.yaml → empty system tier, no error."""
        from kanibako.settings.config import read_system_agent

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"  # never created
        assert read_system_agent(ssp) is None
        assert get_config_value(
            "system.agent", global_config_path=cf, system_settings_path=ssp,
        ) is None


# ---------------------------------------------------------------------------
# Category `config set` — the source-only RAW host_src repoint (block 7c).
# Drives the REAL `set_config_value` router (not the unit `settings_configset`).
# Spec §2a / design §6d / SEAMS S24/S25.
# ---------------------------------------------------------------------------

class TestCategoryConfigSet:
    """`config set <category-key>` through the live CLI setter — REFUSED BY NAME.

    ⚑⚑ THIS CLASS USED TO PIN THE SOURCE-ONLY REPOINT (S24/S25): a category set
    swapped ``host_src`` and preserved ``box_dest`` + options RAW, WARNed on a
    not-yet-existent literal, and hard-ERRORed on the ``:`` notation / a dangling
    ``@``-ref / an unknown ``$VAR`` / a key absent from the cascade. **DS-BL1 = (a)
    (Jei, 2026-08-07g — "accept the loss uniformly") retired that route for EVERY
    bind-shaped category**, so what is pinned here now is the REFUSAL: by name, with
    a cure, in the verb preamble before any write machinery, at every scope, leaving
    the stored tuple untouched.

    ⚑ The repoint MECHANISM (``settings_configset.repoint_host_src``) survived this
    pass with no CLI caller and was DELETED in QA′ (2026-08-08, on Jei's word),
    together with R-8's three-element stale-shape refusal and the test pinning the
    DECLINED 2-element option — see the graveyard block in
    ``test_settings_configset.py``. Nothing here changed: this class pins the
    REFUSAL, which is upstream of any write machinery. Do not re-pin a repoint
    through this door; there is no longer anything to pin.
    """

    def _seed(self, tmp_path, key_path, tuple_val):
        """Write an existing category bind tuple into a scope file; return path."""
        f = tmp_path / "settings.yaml"
        data: dict = {}
        node = data
        for seg in key_path[:-1]:
            node = node.setdefault(seg, {})
        node[key_path[-1]] = tuple_val
        dump_doc(f, data)
        return f

    @pytest.mark.parametrize("category", ["caches", "seeded", "common", "synced"])
    def test_every_bind_shaped_category_set_is_refused_by_name(
        self, tmp_path, category,
    ):
        """All four, not one specimen: DS-BL1 = (a) is UNIFORM, and a per-category
        exception would be exactly the split option (c) that was declined."""
        f = self._seed(tmp_path, ["box", category, "x"], ["/old", "/dest"])
        msg = set_config_value(
            f"box.{category}.x", "/newsrc",
            config_path=f, command_scope=ConfigLevel.box, cascade_box_path=f,
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg, msg
        assert f"box.{category}.<name>" in msg, msg          # names the SPELLING
        assert "settings file" in msg, msg                    # names the CURE
        # The read still works — and the message names a command that EXISTS:
        # there is no ``config`` noun (bifrost row 66, defect 2), and the box noun
        # takes its subject first.
        assert f"kanibako box get <box> box.{category}.x" in msg, msg
        # ⚑ Refused BEFORE any write machinery — the stored tuple is byte-identical.
        assert load_doc(f)["box"][category]["x"] == ["/old", "/dest"]

    def test_the_refusal_clause_follows_the_CATEGORY_not_the_door(self, tmp_path):
        """⚑⚑ THE TWO REASONS HAVE CONVERGED, AND THIS TEST NO LONGER PINS THEM
        APART ON TRUTH. It used to, under the name
        ``test_the_refusal_states_the_RULING_not_the_shape``, and the justification
        was: the four kept their per-entry key and lost only the ROUTE (DS-BL1),
        while the ``bindings`` arms lost the KEY itself (R-5/R-6 — terminal,
        dest-keyed), so telling a ``caches`` user "a per-name key no longer exists"
        would send them hunting for a key ``config get`` read back fine.

        **That premise dissolved on 2026-08-08c.** All four went terminal and
        dest-keyed too, so the SHAPE clause is now true of all six and neither clause
        can mislead anybody. What survives of the split is PROVENANCE — which
        retirement arrived first — not a live difference in what a user can do, and
        ``config_keys._retired_because``'s own docstring says exactly that.

        So the guarantee is REPLACED, not dropped, and re-posed on the property that
        is still real: **the clause is selected by the CATEGORY alone.** It does not
        vary by DOOR (file scope vs agent scope, two separate error builders) and it
        does not vary by VERB — which is precisely what ``_retired_because`` exists
        to guarantee, "so neither door invents its own story". All four combinations
        are driven here, because a single-specimen check could not tell a per-category
        clause from a per-door one.

        ⚑ The distinctness half is kept for ONE reason, and it is no longer the old
        one: while the two clauses are two strings, collapsing them into one is a
        DELIBERATE, VISIBLE edit at this test rather than drift somewhere else.
        """
        stored = ["/old", "/dest"]
        box_file = self._seed(tmp_path, ["box", "caches", "x"], list(stored))
        agent_file = tmp_path / "kanibako_config.yaml"
        SHAPE = "per-name key no longer exists"      # the bindings-arm clause
        RULING = "authored in YAML only"             # the other four's clause

        def file_door(key, verb):
            if verb == "set":
                return set_config_value(
                    key, "/newsrc", config_path=box_file,
                    command_scope=ConfigLevel.box, cascade_box_path=box_file,
                )
            return reset_config_value(
                key, config_path=box_file, command_scope=ConfigLevel.box,
            )

        def agent_door(key, verb):
            if verb == "set":
                return set_config_value(
                    key, "/newsrc", config_path=agent_file, is_system=True,
                    command_scope=ConfigLevel.system,
                )
            return reset_config_value(
                key, config_path=agent_file, command_scope=ConfigLevel.system,
            )

        doors = (("box.{}.x", file_door), ("agent.claude.{}.x", agent_door))
        seen = 0
        for verb in ("set", "reset"):
            for spelling, door in doors:
                four = door(spelling.format("caches"), verb)
                arm = door(spelling.format("bindings.ro"), verb)
                assert RULING in four and SHAPE not in four, (verb, four)
                assert SHAPE in arm and RULING not in arm, (verb, arm)
                seen += 1
        # ⚑ NON-VACUITY: four combinations, not one specimen — the whole claim is
        # that the clause is invariant across them.
        assert seen == 4

        # A refused write is a refused write at either door: nothing moved.
        assert load_doc(box_file)["box"]["caches"]["x"] == stored
        assert not agent_file.exists()

    def test_agent_scope_category_set_is_refused_through_its_own_door(self, tmp_path):
        """The AGENT-scope spelling is refused too, and by the NODE door
        (``agent_node_bind_retired_error``) — so the cure names the node's OWN
        settings file, the file ``_agent_partial`` actually reads, not a scope
        table."""
        f = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "agent.claude.common.plugins", "/newsrc",
            config_path=f, is_system=True, command_scope=ConfigLevel.system,
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg, msg
        assert "agent.<node>.common.<name>" in msg, msg
        # ⚑ The NODE left the file spelling with the S2 flatten
        # ([spec:15-21, "self"]): the file IS claude's, so the table sits DIRECTLY under ``self:``. The node stays
        # in the PATH, which is what tells the user which file to open.
        assert "self.common" in msg, msg
        assert "self.claude" not in msg, msg
        assert "agents/claude/settings.yaml" in msg, msg
        assert not f.exists()  # a refused write creates nothing

    def test_reset_is_refused_symmetrically(self, tmp_path):
        """A reset is a WRITE. "No override for …" would imply the spelling could
        have been written from the CLI, while the hand-authored tuple sits in the
        file untouched — the same double lie the bindings preamble already avoids."""
        f = self._seed(tmp_path, ["box", "caches", "x"], ["/old", "/dest"])
        msg = reset_config_value(
            "box.caches.x", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg, msg
        assert "No override" not in msg, msg
        # NOT removed — a refused reset must not clear a hand-authored entry.
        assert load_doc(f)["box"]["caches"]["x"] == ["/old", "/dest"]

    def test_null_on_a_category_gets_the_retirement_too(self, tmp_path):
        """``--null <scope>.<category>.<name>`` used to hit a bespoke "a repoint has
        no null form" guard. That guard is GONE with the route; the retirement
        refusal is earlier and better. RED if the null path found another door."""
        f = self._seed(tmp_path, ["box", "caches", "x"], ["/old", "/dest"])
        msg = set_config_value(
            "box.caches.x", None,
            config_path=f, command_scope=ConfigLevel.box, cascade_box_path=f,
        )
        assert "RETIRED" in msg, msg
        assert "--null is not yet supported" not in msg, msg

    def test_the_read_survives_the_write(self, tmp_path):
        """Refuse the write, keep the read honest: the cure the refusal prescribes
        (hand-edit the YAML) is only verifiable if ``config get`` still reads it."""
        f = self._seed(tmp_path, ["box", "caches", "x"], ["/src", "/dest"])
        # (the reader stringifies a structured value for display)
        assert get_config_value(
            "box.caches.x", global_config_path=f, project_toml=f,
            command_scope=ConfigLevel.box,
        ) == str(["/src", "/dest"])

    def test_structural_system_key_still_refused(self, tmp_path):
        """A real structural system.* config key is still file-only refused.

        (``system.data`` is a RETIRED name — ``config.data`` — so the pin uses
        a live SYSTEM_PATH_DEFAULTS member; F2 narrowed the family.)"""
        f = tmp_path / "kanibako_config.yaml"
        dump_doc(f, {"system": {"cache": "/x"}})
        msg = set_config_value("system.cache", "/tmp", config_path=f, is_system=True)
        assert msg.startswith("Error:")
        assert "structural" in msg

    def test_category_key_is_known(self):
        """`is_known_key` recognizes category keys (D1 — get/set symmetry)."""
        assert is_known_key("system.caches.x")
        assert is_known_key("workset.common.plugins")
        assert is_known_key("box.synced.x")
        assert not is_known_key("some-project-name")

    def test_the_retired_scope_bind_spelling_is_still_key_shaped(self):
        """⚑ ``True`` here for a DIFFERENT REASON since R-9. This is the
        positional-vs-key disambiguator: if the retired spelling stopped reading
        as a key, the verbs would take it for a PROJECT NAME and the user would
        get a project error instead of the retirement message that tells them
        what happened. (It is also still readable.) Same recognise-to-refuse role
        the bare ``env.<VAR>`` spelling has."""
        assert is_known_key("box.bindings.rw.home")
        assert is_known_key("system.bindings.ro.helper")


# ---------------------------------------------------------------------------
# Cross-scope @-ref resolution at set-time — the (b) FULL CASCADE rework
# (Jei ruling 2026-06-29). The first cut built the set-time snapshot from the
# command-scope file + the system.* floor ONLY, which FALSE-BLOCKED a value that
# @-refs a higher non-system scope key. These exercise the full cascade: a box-
# scope set whose new value references a key set ONLY at the workset scope must
# RESOLVE (allowed); a genuinely dangling cross-scope ref still BLOCKS.
# Spec §2a "layer the target's settings in precedence order".
# ---------------------------------------------------------------------------

class TestCrossScopeCascadeConfigSet:
    """`config set` set-time E3 over the FULL launch cascade (not cmd-file only).

    ⚑ THE VEHICLE CHANGED, THE MECHANISM DID NOT. These used to drive the probe
    through a CATEGORY repoint (``box.synced.foo``); DS-BL1 = (a) retired that route,
    so they now drive it through a routed SCALAR (``box.canon`` / ``workset.boxes``),
    which is the surviving caller of the very same
    ``_category_set_lookups`` → lenient-``expand`` seam (``set_config_value`` runs it
    for every key ``_probes_at_set_time`` claims). What is pinned is unchanged: the
    snapshot is the FULL cascade, so a cross-scope ``@``-ref RESOLVES instead of
    false-blocking, and a genuinely dangling one still BLOCKS by name.
    """

    def _seed_workset(self, tmp_path, leaf, value):
        """Write a workset-scope key into a workset settings file."""
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {leaf: value}})
        return f

    def test_cross_scope_ref_resolves_with_full_cascade(self, tmp_path):
        """A box-scope set whose value @-refs a key set ONLY at the workset scope
        is ALLOWED (the false-block the first cut produced is GONE)."""
        box_f = tmp_path / "box-settings.yaml"
        ws_f = self._seed_workset(tmp_path, "vault_ro", "/srv/vault/ro")
        msg = set_config_value(
            "box.canon", "@workset.vault_ro/bar",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_workset_path=ws_f,
            cascade_box_path=box_f,
        )
        # ALLOWED: @workset.vault_ro is visible in the full cascade -> resolves.
        assert not msg.startswith("Error:"), msg
        # stored RAW (the @-ref, NOT a literal — §0 files unresolved).
        assert load_doc(box_f)["box"]["canon"] == "@workset.vault_ro/bar"

    def test_cross_scope_ref_false_blocked_without_workset_file(self, tmp_path):
        """Control: the SAME @workset.* ref with NO workset file in the cascade
        is dangling -> BLOCKED. Proves the prior test's pass is the cascade's
        doing (the workset key really is the only thing that resolves it)."""
        box_f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.canon", "@workset.vault_ro/bar",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f,
        )
        assert msg.startswith("Error:"), msg
        assert "dangling" in msg
        assert "workset.vault_ro" in msg  # names the broken upstream dep

    def test_cross_scope_genuinely_dangling_still_blocks(self, tmp_path):
        """A value @-ref to a key set NOWHERE in the cascade still BLOCKS, naming
        the dangling target (E3 upstream rule holds over the full cascade)."""
        box_f = tmp_path / "box-settings.yaml"
        ws_f = self._seed_workset(tmp_path, "vault_ro", "/srv/vault/ro")
        msg = set_config_value(
            "box.canon", "@workset.nope_absent/bar",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_workset_path=ws_f,
            cascade_box_path=box_f,
        )
        assert msg.startswith("Error:"), msg
        assert "dangling" in msg
        assert "workset.nope_absent" in msg

    def test_workset_scope_set_refs_system_floor_and_sibling(self, tmp_path):
        """A workset-scope set referencing a sibling workset key in the same file
        resolves -> ALLOWED (the system.* floor is folded in by
        ``_category_set_lookups`` regardless of cascade files)."""
        ws_f = tmp_path / "ws-settings.yaml"
        dump_doc(ws_f, {"workset": {"vault_ro": "/srv/vault/ro", "boxes": "/old"}})
        msg = set_config_value(
            "workset.boxes", "@workset.vault_ro/sub",
            config_path=ws_f, command_scope=ConfigLevel.workset,
            cascade_workset_path=ws_f,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ws_f)["workset"]["boxes"] == "@workset.vault_ro/sub"


# ---------------------------------------------------------------------------
# ⚑⚑ ``TestRepointFromCascade`` LIVED HERE AND IS GONE (DS-BL1 = (a), 2026-08-07g).
#
# It drove F10 — spec §2a's "the key MUST ALREADY EXIST in the cascade; the CLI can
# only REPOINT an existing bind, never CREATE one" — through the CLI: a box set
# repointing a bind set only at the system scope, the downward workset write, the
# nowhere-in-the-cascade refusal, the command-file-wins tuple, and the reset
# symmetry. **There is no CLI category repoint any more**, so every one of those
# cases is now the SAME refusal, pinned once in ``TestCategoryConfigSet``.
#
# ⚑⚑ F10 IS NOW UNTESTED, AND SAYING SO IS THE POINT. When this block was written
# the rule still lived in ``repoint_host_src`` and was unit-tested directly in
# ``test_settings_configset.py`` (``test_repoint_cascade_fallback_*`` /
# ``test_repoint_missing_key_raises``). QA′ (2026-08-08, on Jei's word) deleted that
# function as an orphan, so those cases went too and F10 has NO implementation and
# NO test anywhere in the tree.
#
# ⚑ THAT IS CORRECT, NOT A GAP TO PATCH: F10 is a rule about what ``config set`` may
# do to a category, and ``config set`` may not touch a category at all. There is
# nothing left for the rule to constrain. Do NOT re-add a CLI-level copy — it could
# only pass by resurrecting the retired route. If a category write route is ever
# rebuilt, F10 comes back WITH it, spec §2a and all.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scope-direction guard (block B4, spec §0 directional view/set + §2a)
# ---------------------------------------------------------------------------

class TestScopeDirectionGuard:
    """A ``config set`` writes keys of the command scope's OWN namespace and of
    any scope it CONTAINS (``system ⊃ agent ⊃ workset ⊃ box``, command-scope ≥
    key-scope) — a downward write lands in the COMMAND scope's file, scope token
    kept, as an overridable default; an UPWARD write (and any ``meta.*`` write)
    is REFUSED (spec §0 "Directional view/set" + §2a "Scope-direction guard",
    repaired 2026-07-02).

    These exercise ``set_config_value`` directly with an explicit
    ``command_scope`` (the token each command handler threads). When
    ``command_scope`` is None the guard is skipped (back-compat) — covered by
    the many pre-existing tests that omit it.
    """

    # --- UPWARD writes are REFUSED ----------------------------------------

    def test_box_scope_refuses_workset_key(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "workset.vault_ro", "/srv/x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "workset" in msg and "box" in msg
        # The file is NOT written (refused before dispatch).
        assert not f.exists()

    def test_box_scope_refuses_agent_key(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "agent.claude.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "agent" in msg
        assert not f.exists()

    def test_box_scope_refuses_system_key(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "system.cache", "/srv/cache",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "system" in msg
        assert not f.exists()

    def test_workset_scope_refuses_system_key(self, tmp_path):
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "system.cache", "/srv/cache",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:"), msg
        assert "system" in msg and "workset" in msg
        assert not f.exists()

    def test_workset_scope_allows_box_key_downward(self, tmp_path):
        """DOWNWARD (workset ⊃ box): ``workset config set box.image`` is
        ACCEPTED and stored in the WORKSET file, nested under the key's own
        scope token (``box:``) — the form ``assemble_levels`` mirrors as an
        overridable workset-level default. NOT remapped to any box file."""
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "box.image", "img:1",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["box"]["image"] == "img:1"
        # The workset file is the ONLY file written (no key-scope remap).
        assert [p.name for p in tmp_path.iterdir()] == ["ws-settings.yaml"]

    def test_workset_scope_refuses_agent_key(self, tmp_path):
        # UPWARD: agent CONTAINS workset (system ⊃ agent ⊃ workset ⊃ box).
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.x", "/srv/x",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:"), msg
        assert "agent" in msg
        assert not f.exists()

    def test_agent_scope_refuses_system_key(self, tmp_path):
        """UPWARD (system CONTAINS agent): an agent-scope command may not write
        a system.* key. (No command handler threads ConfigLevel.agent today —
        commands/agent_cmd.py bypasses the engine, a noted gap — but the
        containment table carries the row, so pin its direction.)"""
        f = tmp_path / "agent-settings.yaml"
        msg = set_config_value(
            "system.cache", "/srv/cache",
            config_path=f, command_scope=ConfigLevel.agent,
        )
        assert msg.startswith("Error:"), msg
        assert "system" in msg and "agent" in msg
        assert not f.exists()

    def test_system_scope_allows_box_key_downward(self, tmp_path):
        """DOWNWARD (system ⊃ box): accepted, and stored in the system
        SETTINGS file (``@config.settings``) with the ``box:`` scope token kept
        — NOT in the Layer-1 kanibako_config.yaml (spec §1: settings keys never
        live in the bootstrap config file)."""
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "settings.yaml"
        msg = set_config_value(
            "box.image", "img:1",
            config_path=cf, is_system=True, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ssp)["box"]["image"] == "img:1"
        # The Layer-1 config file is untouched.
        assert not cf.exists()

    def test_system_scope_allows_workset_key_downward(self, tmp_path):
        """DOWNWARD (system ⊃ workset): a REGISTERED workset key is accepted
        and nests under ``workset:`` in the system SETTINGS file."""
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.auth.share_allowed", "false",
            config_path=cf, is_system=True, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ssp)["workset"]["auth"]["share_allowed"] is False
        assert not cf.exists()

    def test_system_scope_passes_guard_for_agent_key(self, tmp_path):
        """DOWNWARD (system ⊃ agent): the direction guard PERMITS an agent.*
        key from the system scope. Block B1 makes ``agent.<node>.<key>`` a real
        PER-PERSONA setting routed to the agent's OWN
        ``agents/<node>/settings.yaml`` — so a system-scope write now SUCCEEDS
        (past the guard) when the agents root is threaded, landing sparsely at
        the flat ``agent:`` slot the launch reads."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.model", "opus",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert "cannot be set from the system scope" not in msg
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "self": {"model": "opus"},
        }

    def test_downward_unknown_key_still_rejected_by_registry(self, tmp_path):
        """A downward write of an UNREGISTERED key passes the guard but is
        still rejected as an unknown config key (registry rejection is not
        relaxed by the containment rule)."""
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "box.no_such_key", "x",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error: unknown config key"), msg
        assert not f.exists()

    # --- meta.* is read-only from EVERY scope -----------------------------

    def test_box_scope_refuses_meta_key(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "meta.box.name", "fred",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "read-only" in msg and "meta" in msg
        assert not f.exists()

    def test_workset_scope_refuses_meta_key(self, tmp_path):
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "meta.workset.path", "/srv/ws",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:"), msg
        assert "read-only" in msg

    def test_system_scope_refuses_meta_key(self, tmp_path):
        f = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "meta.runtime.project_type", "primary",
            config_path=f, is_system=True, command_scope=ConfigLevel.system,
        )
        assert msg.startswith("Error:"), msg
        assert "read-only" in msg

    def test_meta_refused_even_without_command_scope(self, tmp_path):
        """meta.* is RO regardless of command scope — refused even when no
        command_scope is threaded (it is a top-level RO namespace, not a
        directional check)."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value("meta.box.name", "fred", config_path=f)
        assert msg.startswith("Error:"), msg
        assert "read-only" in msg

    # --- same-scope writes SUCCEED ----------------------------------------

    def test_box_scope_allows_box_key(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.image", "img:1",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["box"]["image"] == "img:1"

    def test_box_scope_refuses_the_retired_box_agent_key(self, tmp_path):
        """⮕ P7: ``box.agent.<key>`` is RETIRED (spec §2b) — the settable mirror
        is gone and the cure is the §2h request. The refusal must come from the
        RETIREMENT (naming the replacement), NOT from the scope-direction guard
        (which would send the user looking for the wrong problem), and NOTHING
        may be written."""
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
            cascade_agent_name="claude",
        )
        assert "cannot be set from the box scope" not in msg
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg
        assert "pref.agent.claude.model" in msg
        # ⚑ The pointer names what --effective ACTUALLY renders (the pref block:
        # request + result). It must NOT promise ``meta.box.agent.model``, which no
        # renderer emits — a cure pointing at output the user cannot find is worse
        # than no pointer.
        assert "meta.box.agent" not in msg
        assert "--effective" in msg
        assert not f.exists()

    def test_workset_scope_allows_workset_key(self, tmp_path):
        # ⚑ VEHICLE: a routed workset SCALAR. This row is about the DIRECTION guard
        # (own-namespace write is allowed), and it used to ride on a category
        # repoint — a route DS-BL1 = (a) retired, so a category key here would now
        # be refused for a reason that has nothing to do with direction.
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {"boxes": "/old"}})
        msg = set_config_value(
            "workset.boxes", "/new",
            config_path=f, cascade_workset_path=f,
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["workset"]["boxes"] == "/new"

    def test_system_config_key_refused_with_ruled_message(self, tmp_path):
        """Block B2: ``config.*`` foundation keys are NEVER CLI-settable — refused
        from EVERY scope (including SYSTEM, which the B4 direction guard would
        otherwise own) with the ruled bootstrap-file message, BEFORE the scope
        guard. Not the direction-guard message, not the older generic
        ``system_key_refusal`` (which named ``setup``)."""
        f = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "config.data", "/srv/data",
            config_path=f, is_system=True, command_scope=ConfigLevel.system,
        )
        assert msg.startswith("Error: config.* keys can only be set by editing"), msg
        # Refused by B2, not by the direction guard nor the generic refusal.
        assert "cannot be set from the system scope" not in msg
        assert "structural config key" not in msg
        assert "kanibako setup" not in msg
        # File untouched (refused before any write).
        assert not f.exists()

    def test_system_scope_allows_system_category_key(self, tmp_path):
        f = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "system.caches.x", "/srv/cache",
            config_path=f, is_system=True,
            cascade_system_path=f, command_scope=ConfigLevel.system,
        )
        assert "cannot be set from the system scope" not in msg

    # --- the env family under the guard -----------------------------------
    #
    # ⚑ The bare ``env.<VAR>`` spelling used to be the SCOPELESS specimen here.
    # R-39 retired it (refused in the verb preamble, before this guard ever
    # runs), and the live family ``<scope>.env.<VAR>`` is scope-TOKENED — so it
    # is GUARDED like any other scope key rather than exempt from the guard.

    def test_own_scope_env_key_allowed_at_box(self, tmp_path):
        msg = set_config_value(
            "box.env.FOO", "bar",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg

    def test_downward_env_key_allowed_at_workset(self, tmp_path):
        # workset ⊃ box, so a workset may store a box-scope default.
        msg = set_config_value(
            "box.env.FOO", "bar",
            config_path=tmp_path / "ws-settings.yaml",
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg

    def test_upward_env_key_refused_at_box(self, tmp_path):
        msg = set_config_value(
            "workset.env.FOO", "bar",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "cannot be set from the box scope" in msg

    def test_removed_bare_vault_keys_are_unknown(self, tmp_path):
        # Bug 4: the old bare ``vault.ro``/``vault.rw`` keys routed to the
        # ``project:`` section P8 DELETED — a silent dead write. They are REMOVED;
        # a set/reset/flat-form now returns the unknown-key error and writes
        # nothing (the vault override surface is ``box.bindings.{ro,rw}.vault``).
        f = tmp_path / "settings.yaml"
        for key in ("vault.ro", "vault.rw", "vault_ro", "vault_rw"):
            msg = set_config_value(
                key, "/x", config_path=f, command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "unknown config key" in msg, (key, msg)
        rmsg = reset_config_value("vault.ro", config_path=f)
        assert rmsg.startswith("Error:") and "unknown config key" in rmsg, rmsg
        # Nothing was written by the dead set.
        assert not f.exists() or "project" not in load_doc(f)

    # --- reset follows the same directional rule ---------------------------

    def test_box_scope_reset_refuses_workset_key(self, tmp_path):
        """UPWARD reset is refused, symmetric with set."""
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"workset": {"auth": {"share_allowed": False}}})
        msg = reset_config_value(
            "workset.auth.share_allowed",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "workset" in msg and "box" in msg
        # The pre-existing entry is untouched (refused before dispatch).
        assert load_doc(f)["workset"]["auth"]["share_allowed"] is False

    def test_workset_scope_reset_removes_downward_key_from_workset_file(
        self, tmp_path
    ):
        """DOWNWARD reset removes the key from the COMMAND scope's file."""
        f = tmp_path / "ws-settings.yaml"
        set_config_value(
            "box.image", "img:1",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        msg = reset_config_value(
            "box.image", config_path=f, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        doc = load_doc(f)
        assert "image" not in doc.get("box", {})

    # --- downward CATEGORY writes: guard passes, must-exist still bites -----

    def test_downward_category_key_is_refused_by_the_retirement_not_direction(
        self, tmp_path,
    ):
        """A downward category write (``workset set box.synced.X``) PASSES the
        direction guard (workset ⊃ box) and is then refused by the RETIREMENT.

        ⚑ IT USED TO BE REFUSED BY THE SOURCE-ONLY MUST-EXIST RULE ("the key MUST
        ALREADY EXIST in the cascade", F10) — DS-BL1 = (a) removed that route, so the
        refusal now comes from a different rule and says so. The property this row
        actually guards is unchanged and still worth pinning: the DIRECTION guard did
        not fire, and NOTHING was written."""
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {"foo": "bar"}})  # file exists, key absent
        msg = set_config_value(
            "box.synced.newmount", str(tmp_path),
            config_path=f, cascade_workset_path=f,
            command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg, msg
        # NOT the direction refusal (that would be the wrong diagnosis).
        assert "cannot be set from the workset scope" not in msg, msg
        # Nothing was created in the file.
        assert load_doc(f) == {"workset": {"foo": "bar"}}


# ---------------------------------------------------------------------------
# workset-defaults-box cascade (the §0 downward-default ruling, end-to-end)
# ---------------------------------------------------------------------------

class TestWorksetDefaultsBoxCascade:
    """The §0 ruling end-to-end (Jei 2026-07-02): ``workset config set
    box.image`` stores the key in the WORKSET yaml (scope token kept) → the
    launch snapshot resolves it for the workset's boxes → a box-level set of
    the same key OVERRIDES it (contained scope wins) → a box-level reset falls
    back to the workset default.

    Mutation-proof: step 1 asserts the workset file is the ONLY file written,
    and the final fallback assertion fails if the workset-scope write had
    landed in the box file (the box reset would then drop it to the floor
    default, not the workset value)."""

    @staticmethod
    def _snapshot_image(ws, box):
        """Resolve ``box.image`` through the REAL launch snapshot (assemble →
        merge → expand) over the two settings files, with a floor default
        underneath (so a wrong-file write is distinguishable from fallback)."""
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        ctx = ResolveCtx(
            agent_name="claude", workset_name=None,
            host_home="/home/host", xdg={},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx,
            system_path=None, agent_path=None,
            workset_path=ws, box_path=box,
            default_categories={"box.image": "floor-img:0"},
        )
        return snap.box.image

    def test_workset_default_resolves_overrides_and_falls_back(self, tmp_path):
        ws = tmp_path / "ws-settings.yaml"
        box = tmp_path / "box-settings.yaml"

        # 1. workset-scope downward set → stored in the WORKSET yaml under the
        #    key's own scope token; the box file is NOT created (no remap).
        msg = set_config_value(
            "box.image", "ws-img:1",
            config_path=ws, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ws)["box"]["image"] == "ws-img:1"
        assert not box.exists()

        # 2. The launch snapshot resolves the workset value for the box
        #    (workset level beats the floor default).
        assert self._snapshot_image(ws, box) == "ws-img:1"

        # 3. A box-level set of the SAME key overrides the workset default
        #    (box is the most-specific cascade level).
        msg = set_config_value(
            "box.image", "box-img:2",
            config_path=box, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(box)["box"]["image"] == "box-img:2"
        assert self._snapshot_image(ws, box) == "box-img:2"

        # 4. Box-level reset → falls BACK to the workset default (not the
        #    floor). Fails if step 1's write had landed in the box file.
        msg = reset_config_value(
            "box.image", config_path=box, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert self._snapshot_image(ws, box) == "ws-img:1"


# ---------------------------------------------------------------------------
# box.agent.* mirror config-set (block B5 — spec §2b, JC-B5-2)
# ---------------------------------------------------------------------------

class TestBoxAgentMirrorConfigSet:
    """``box.agent.<key>`` is a settable BOX-scope key (the §2b B5 downward-tweak
    mirror): recognized by ``is_known_key``, set/reset land in the box settings
    file at the nested ``box.agent.<key>`` location, and the B4 guard permits it
    as a same-scope box write (covered above + here)."""

    def test_box_agent_key_is_recognised_so_it_can_be_refused(self):
        """The retired spelling must still be RECOGNISED — a user with it in
        muscle memory gets the cure, not "unknown config key"."""
        from kanibako.settings.config_keys import _is_box_agent_key
        assert _is_box_agent_key("box.agent.model") is True
        assert _is_box_agent_key("box.agent.bindings.ro.share") is True
        # It is no longer a SETTABLE key.
        assert is_known_key("box.agent.model") is True  # recognised…
        # …but every WRITE verb refuses it (below).

    def test_the_retired_box_agent_name_scalar_is_not_a_key(self):
        # ``box.agent_name`` (the flat scalar) is RETIRED (spec §2b) and is not the
        # mirror either (it has no dotted tail).
        from kanibako.settings.config_keys import _is_box_agent_key
        assert _is_box_agent_key("box.agent_name") is False
        assert is_known_key("box.agent_name") is False

    def test_set_box_agent_scalar_is_refused_with_the_pref_cure(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "sonnet",
            config_path=f, command_scope=ConfigLevel.box,
            cascade_agent_name="claude",
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg
        assert "pref.agent.claude.model" in msg
        # NOTHING written — a silent write to a key nothing reads is the failure
        # this refusal exists to prevent.
        assert not f.exists()

    def test_set_box_agent_deep_category_key_is_refused(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.bindings.ro.share", "/user/share",
            config_path=f, command_scope=ConfigLevel.box,
            cascade_agent_name="claude",
        )
        assert msg.startswith("Error:"), msg
        assert "pref.agent.claude.bindings.ro.share" in msg
        assert not f.exists()

    def test_set_box_agent_names_a_placeholder_when_no_agent_resolves(self, tmp_path):
        # With no resolvable agent the cure still teaches the SHAPE.
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "sonnet",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert "pref.agent.<agent>.model" in msg, msg

    def test_reset_box_agent_key_is_refused_and_clears_nothing(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"box": {"agent": {"model": "sonnet"}}})
        msg = reset_config_value(
            "box.agent.model", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg
        # Symmetric with set: refuse rather than silently clear a key that no
        # longer does anything — the file is untouched.
        assert load_doc(f)["box"]["agent"]["model"] == "sonnet"

    def test_get_box_agent_key_reports_not_set(self, tmp_path):
        # A hand-written legacy leaf is NOT reported as a value: it has no effect
        # on the launch, so surfacing it would be worse than "(not set)". The
        # effective value is readable at meta.box.agent.<key> via --effective.
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"box": {"agent": {"model": "sonnet"}}})
        assert get_config_value(
            "box.agent.model", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=ConfigLevel.box,
        ) is None


class TestBareAgentKeyAtBoxScope:
    """A BARE agent behavior key (model / access / bootstrap / endpoint /
    allow_helpers / continue_mode) at BOX command scope targets the any-agent
    ``agent.default`` tier — an UPWARD write a box cannot make (spec L440: a box
    tweaks its agent through its own ``box.agent.*`` mirror; §0 directional rule).

    The OLD code wrote ``agent.default.<key>`` into the box settings file, which
    ``settings_assemble._drop_upward_scopes`` then DROPPED at launch — a silent
    no-op the CLI still reported as "Set". SET now REFUSES; GET REDIRECTS the read.
    ⮕ **P7 RETARGETED the redirect** from the retired ``box.agent.<key>`` mirror to
    the §2h request ``pref.agent.<active>.<key>`` — a redirect that named a spelling
    the write verbs now refuse would be a cure that does not work.
    """

    def test_redirect_helper_fires_only_for_bare_key_at_box_scope(self):
        from kanibako.settings.config_keys import box_agent_redirect_key
        # Bare agent keys at box scope → the box's §2h REQUEST.
        assert box_agent_redirect_key(
            "bootstrap", ConfigLevel.box, "claude") == "pref.agent.claude.bootstrap"
        assert box_agent_redirect_key(
            "model", ConfigLevel.box, "claude") == "pref.agent.claude.model"
        assert box_agent_redirect_key(
            "access", ConfigLevel.box, "nav℘claude",
        ) == "pref.agent.nav℘claude.access"
        # No resolvable agent → NO redirect (the request targets a DISCRIMINATED
        # agent slot; there is no bare ``agent.<key>``, §0).
        assert box_agent_redirect_key("bootstrap", ConfigLevel.box) is None
        # Non-box scopes: NO redirect (a bare key is a legit downward write there).
        assert box_agent_redirect_key("bootstrap", ConfigLevel.system, "claude") is None
        assert box_agent_redirect_key("bootstrap", ConfigLevel.workset, "claude") is None
        assert box_agent_redirect_key("bootstrap", None, "claude") is None
        # Already-qualified / per-agent forms are NOT bare agent keys.
        assert box_agent_redirect_key(
            "box.agent.bootstrap", ConfigLevel.box, "claude") is None
        assert box_agent_redirect_key(
            "agent.claude.bootstrap", ConfigLevel.box, "claude") is None

    def test_set_bare_bootstrap_at_box_scope_refused_nothing_written(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "bootstrap", "none", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "can't be set bare" in msg, msg
        # names the correct REQUEST form (P7 — was the retired mirror).
        assert "pref.agent.<agent>.bootstrap" in msg, msg
        # NOTHING written — the refused set never creates the box file (no dropped
        # agent.default.bootstrap). This is the mutation guard vs the old no-op write.
        assert not f.exists(), "the refused set must not write the box file"

    def test_set_bare_model_and_access_at_box_scope_refused(self, tmp_path):
        # Uniformity: the SAME refusal for other agent behavior keys (proves the
        # fix is keyed on the _is_agent_setting family, not a single per-key branch).
        cases = {"model": "sonnet", "access": "restricted"}
        for key, value in cases.items():
            f = tmp_path / f"box-{key}.yaml"
            msg = set_config_value(
                key, value, config_path=f, command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "can't be set bare" in msg, (key, msg)
            assert f"pref.agent.<agent>.{key}" in msg, (key, msg)
            assert not f.exists(), (key, "refused set must not write")

    def test_the_pref_request_form_works(self, tmp_path):
        # The cure the refusal teaches must actually WORK (P7): the §2h request
        # writes to the box file at the nested pref path.
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.bootstrap", "none",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["pref"]["agent"]["claude"]["bootstrap"] == "none"

    def test_get_bare_bootstrap_at_box_scope_redirects_to_the_request(self, tmp_path):
        # The box file carries the REQUEST (pref.agent.claude.bootstrap); a bare
        # `get bootstrap` at box scope REDIRECTS the read to it (P7).
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"pref": {"agent": {"claude": {"bootstrap": "screen"}}}})
        assert get_config_value(
            "bootstrap", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=ConfigLevel.box, active_agent="claude",
        ) == "screen"
        # Mutation guard: WITHOUT box scope the bare read hits agent.default
        # (absent here) → None — proving the redirect is what surfaced the value.
        assert get_config_value(
            "bootstrap", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=None, active_agent="claude",
        ) is None
        # And without a resolvable agent there is no redirect either.
        assert get_config_value(
            "bootstrap", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=ConfigLevel.box,
        ) is None

    def test_get_bare_model_and_access_redirect(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"pref": {"agent": {"claude": {
            "model": "sonnet", "access": "restricted",
        }}}})
        assert get_config_value(
            "model", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=ConfigLevel.box, active_agent="claude",
        ) == "sonnet"
        assert get_config_value(
            "access", global_config_path=tmp_path / "cfg.yaml",
            project_toml=f, command_scope=ConfigLevel.box, active_agent="claude",
        ) == "restricted"

    def test_bare_agent_key_at_system_scope_unaffected(self, tmp_path):
        # A bare agent key at SYSTEM scope is a legit DOWNWARD write to
        # agent.default — still works, lands in the file's agent.default table.
        f = tmp_path / "sys-settings.yaml"
        msg = set_config_value(
            "bootstrap", "none", config_path=f, command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["agent"]["default"]["bootstrap"] == "none"
        # And the system-scope get reads it back (no box redirect applies).
        assert get_config_value(
            "bootstrap", global_config_path=tmp_path / "cfg.yaml",
            system_settings_path=f, command_scope=ConfigLevel.system,
        ) == "none"

    def test_reset_bare_bootstrap_at_box_scope_refused_value_not_removed(
        self, tmp_path,
    ):
        # RESET is a WRITE, so a bare reset at box scope REFUSES (symmetric with
        # SET). The mutation this guards: the OLD path removed the (absent)
        # agent.default.bootstrap and reported "No override" while the real value
        # at box.agent.bootstrap stayed STUCK. Prove the mirror value SURVIVES.
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"pref": {"agent": {"claude": {"bootstrap": "screen"}}}})
        msg = reset_config_value(
            "bootstrap", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "can't be reset bare" in msg, msg
        # teaches the REQUEST form (P7 — was the retired mirror).
        assert "reset pref.agent.<agent>.bootstrap" in msg, msg
        # The stuck-value mutation guard: the stored value is NOT removed.
        assert load_doc(f)["pref"]["agent"]["claude"]["bootstrap"] == "screen"

    def test_reset_bare_model_and_access_at_box_scope_refused(self, tmp_path):
        # Uniformity: the SAME reset refusal for other agent behavior keys.
        for key in ("model", "access"):
            f = tmp_path / f"box-{key}.yaml"
            dump_doc(f, {"pref": {"agent": {"claude": {key: "x"}}}})
            msg = reset_config_value(
                key, config_path=f, command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "can't be reset bare" in msg, (key, msg)
            assert f"reset pref.agent.<agent>.{key}" in msg, (key, msg)
            assert load_doc(f)["pref"]["agent"]["claude"][key] == "x", (
                key, "must survive",
            )

    def test_reset_the_pref_request_form_clears(self, tmp_path):
        # The qualified reset of the REQUEST is UNAFFECTED — it clears (P7).
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"pref": {"agent": {"claude": {"bootstrap": "screen"}}}})
        msg = reset_config_value(
            "pref.agent.claude.bootstrap",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert "cleared" in msg.lower(), msg

    def test_reset_bare_bootstrap_at_system_scope_unaffected(self, tmp_path):
        # A bare reset at SYSTEM scope is a legit DOWNWARD reset — still clears
        # agent.default (not refused, no box redirect).
        f = tmp_path / "sys-settings.yaml"
        dump_doc(f, {"agent": {"default": {"bootstrap": "none"}}})
        msg = reset_config_value(
            "bootstrap", config_path=f, command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        assert "cleared" in msg.lower(), msg
        assert "default" not in load_doc(f).get("agent", {})

    def test_per_agent_key_at_box_scope_unaffected(self, tmp_path):
        # A per-agent agent.<name>.<key> is NOT a bare agent key — it is refused at
        # box scope by the UPWARD agent-scope directional guard (agent ⊃ box), NOT
        # by the bare-key redirect. The message must be the directional one.
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "agent.claude.bootstrap", "none",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "can't be set bare" not in msg, msg  # not the bare-key message
        assert not f.exists(), "the refused per-agent write must not touch the box"


class TestBareAgentKeyAtWorksetScope:
    """A BARE agent behavior key at WORKSET command scope has the SAME upward-drop
    bug as box (a workset file's top-level ``agent`` table is dropped at launch —
    agent ⊃ workset). UNLIKE box, a workset spans MULTIPLE boxes/agents, so there is
    deliberately NO ``workset.agent.*`` mirror (no single "the agent"). The
    conformant fix is therefore to REFUSE — uniformly for set / get / reset — with a
    message pointing at system scope (all agents) or the per-box §2h request
    ``pref.agent.<agent>.<key>`` (P7 — was the retired ``box.agent.*`` mirror).
    Uniform over the whole ``_is_agent_setting`` family (NOT a per-key list).
    """

    def test_scope_error_helper_workset_refuses_no_mirror(self):
        from kanibako.settings.config_keys import bare_agent_key_scope_error
        for verb in ("set", "read", "reset"):
            msg = bare_agent_key_scope_error(
                "bootstrap", ConfigLevel.workset, verb=verb,
            )
            assert msg is not None and msg.startswith("Error:"), (verb, msg)
            assert f"can't be {verb} at workset scope" in msg, (verb, msg)
            assert "system scope" in msg, (verb, msg)
            # No workset.agent.* mirror is invented — it points per-box instead.
            assert "workset.agent" not in msg, (verb, msg)
            # P7: the per-box cure is the §2h REQUEST, not the retired mirror.
            assert "pref.agent.<agent>.bootstrap" in msg, (verb, msg)
        # A per-agent / already-qualified form is NOT a bare agent key.
        assert bare_agent_key_scope_error(
            "agent.claude.bootstrap", ConfigLevel.workset, verb="set") is None

    def test_set_bare_agent_keys_at_workset_scope_refused(self, tmp_path):
        # Uniformity: bootstrap + model + access all refused, nothing written.
        cases = {"bootstrap": "none", "model": "sonnet", "access": "restricted"}
        for key, value in cases.items():
            f = tmp_path / f"ws-{key}.yaml"
            msg = set_config_value(
                key, value, config_path=f, command_scope=ConfigLevel.workset,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "can't be set at workset scope" in msg, (key, msg)
            assert not f.exists(), (key, "the refused set must not write")

    def test_reset_bare_agent_keys_at_workset_scope_refused(self, tmp_path):
        for key in ("bootstrap", "model", "access"):
            f = tmp_path / f"ws-{key}.yaml"
            # Even a pre-existing (mis-written) agent.default value stays put.
            dump_doc(f, {"agent": {"default": {key: "x"}}})
            msg = reset_config_value(
                key, config_path=f, command_scope=ConfigLevel.workset,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "can't be reset at workset scope" in msg, (key, msg)
            assert load_doc(f)["agent"]["default"][key] == "x", (key, "must survive")

    def test_workset_scope_keys_unaffected(self, tmp_path):
        # A legitimate workset.* key is NOT an agent key — still writes normally.
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "workset.boxes", "/srv/boxes",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["workset"]["boxes"] == "/srv/boxes"


# ---------------------------------------------------------------------------
# F5/F6/F7 get-side truthfulness + the F2/F3-class sibling (get reads where set
# wrote) + the honest reset message.  These are the block's REGRESSION tests —
# baseline-RED at 523e2f0 (each fails against the current wrong behavior).
#
# GET SEMANTICS (director's model; spec §2a is SILENT on read semantics —
# Writer's finding, posted to the pair chat before implementing):
#   * plain ``get <key>`` at a noun = the value STORED at that noun's file
#     (including a downward key it stored), else "(not set)".  NEVER a
#     fabricated built-in default, and never another tier's value.
#   * ``--effective`` = the resolved cascade (unchanged; the ``show`` path).
# ---------------------------------------------------------------------------

class TestF5BoxAgentGet:
    """⮕ **P7 INVERTED THIS CLASS, deliberately.**

    F5 pinned ``box get box.agent.<key>`` reading back what ``box set
    box.agent.<key>`` wrote. Spec §2b RETIRED that settable mirror: the write
    verbs now refuse with the ``pref.agent.<agent>.<key>`` cure, so there is
    nothing to read back and a plain get reports "(not set)" — reporting a
    hand-written legacy leaf would name a value that has NO effect on the launch,
    which is strictly worse. The effective value lives at ``meta.box.agent.<key>``
    (RO) and is read through ``--effective``.
    """

    def test_box_agent_scalar_set_is_refused_so_get_is_not_set(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        val = get_config_value(
            "box.agent.model",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
        )
        assert val is None

    def test_box_agent_deep_key_set_is_refused_so_get_is_not_set(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.bindings.ro.share", "/user/share",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        val = get_config_value(
            "box.agent.bindings.ro.share",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
        )
        assert val is None

    def test_box_agent_get_unset_is_not_set(self, tmp_path):
        # An UNSET box.agent.<key> is "(not set)" at the box noun — the branch
        # must not fabricate a value, and must not raise.
        f = tmp_path / "box-settings.yaml"
        val = get_config_value(
            "box.agent.model",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
        )
        assert val is None


class TestF6NoFabricatedDefaultOnPlainGet:
    """F6 — a plain ``get <key>`` at a noun must NOT print a fabricated built-in
    default when nothing is stored at that noun.  Today the flat-field branch
    returns ``getattr(cfg, flat)`` = the ``_DEFAULTS`` built-in even when the box
    file is empty, so ``box get box.image`` lies with the default image."""

    def test_plain_get_unset_flat_field_is_not_set(self, tmp_path):
        # Nothing stored at the box noun.
        global_cfg = tmp_path / "kanibako_config.yaml"
        project_toml = tmp_path / "settings.yaml"
        # RED at baseline: returns "ghcr.io/doctorjei/kanibako-oci:latest".
        val = get_config_value(
            "box.image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val is None

    def test_plain_get_returns_value_stored_at_the_noun(self, tmp_path):
        # A value stored AT the box noun's own file IS returned by plain get.
        global_cfg = tmp_path / "kanibako_config.yaml"
        project_toml = tmp_path / "settings.yaml"
        set_config_value("box.image", "custom:v2", config_path=project_toml)
        val = get_config_value(
            "box.image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "custom:v2"

    def test_plain_get_does_not_show_another_tiers_value(self, tmp_path):
        # box.image set ONLY in the global config file — a plain box get is
        # "(not set)" (global is not the box noun's file); --effective shows it.
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "global:img"\n')
        project_toml = tmp_path / "settings.yaml"
        # RED at baseline: returns "global:img" (merged default view).
        val = get_config_value(
            "box.image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val is None

    def test_plain_get_scopeless_key_roundtrips_at_box(self, tmp_path):
        # A SCOPELESS key (allow_helpers) is stored in — and read from — the
        # command's own config file (get mirrors set's dest selection): set at
        # the box noun → get at the box noun returns it; unset → "(not set)".
        global_cfg = tmp_path / "kanibako_config.yaml"
        project_toml = tmp_path / "settings.yaml"
        assert get_config_value(
            "allow_helpers",
            global_config_path=global_cfg,
            project_toml=project_toml,
        ) is None
        set_config_value("allow_helpers", "true", config_path=project_toml)
        assert get_config_value(
            "allow_helpers",
            global_config_path=global_cfg,
            project_toml=project_toml,
        ) == "true"

    def test_effective_view_unchanged_still_shows_resolved(
        self, tmp_path, capsys,
    ):
        # --effective must still show the resolved (merged) value — unchanged.
        global_cfg = tmp_path / "kanibako_config.yaml"
        global_cfg.write_text('box:\n  image: "global:img"\n')
        project_toml = tmp_path / "settings.yaml"
        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
        )
        out = capsys.readouterr().out
        assert "box_image = global:img" in out


class TestF7HonestResetMessage:
    """F7 (Jei-ruled 07-02d) — ``reset`` must NOT claim "reverts to default:
    <built-in>" when the value falls back to a higher-tier stored default.  The
    behavior (fallback) is right; the MESSAGE must be honest: say it cleared the
    value set on THIS box (and, where cheap, the now-effective value + source)."""

    def test_reset_message_is_not_the_builtin_default_claim(self, tmp_path):
        # box.image reset with the box override present.
        project_toml = tmp_path / "settings.yaml"
        set_config_value("box.image", "custom:v2", config_path=project_toml)
        msg = reset_config_value(
            "box.image", config_path=project_toml,
            command_scope=ConfigLevel.box,
        )
        # RED at baseline: msg is "Reset box_image (reverts to default: <builtin>)".
        # Mutation guard: this test must FAIL if the old text returns.
        assert "reverts to default" not in msg, msg
        # Non-vacuous the other way: a truthful phrase is PRESENT.
        assert "cleared" in msg.lower(), msg

    def test_reset_message_says_cleared_on_this_noun(self, tmp_path):
        # Box scope (the box handler threads command_scope=ConfigLevel.box):
        # honest wording direction (Jei) — cleared the value set on THIS box.
        project_toml = tmp_path / "settings.yaml"
        set_config_value("box.image", "custom:v2", config_path=project_toml)
        msg = reset_config_value(
            "box.image", config_path=project_toml,
            command_scope=ConfigLevel.box,
        )
        assert "cleared" in msg.lower()
        assert "box" in msg.lower()  # names the command noun at box scope

    def test_reset_message_names_the_command_noun_not_hardcoded_box(
        self, tmp_path,
    ):
        # Editor check #2: the message names the COMMAND's noun. At workset scope
        # it must NOT say "box" — it names the workset. (system set/reset of a
        # downward key lands in the system file; here we prove non-hardcoding.)
        ws = tmp_path / "workset-settings.yaml"
        set_config_value(
            "box.image", "ws:img", config_path=ws,
            command_scope=ConfigLevel.workset,
        )
        msg = reset_config_value(
            "box.image", config_path=ws,
            command_scope=ConfigLevel.workset,
        )
        assert "cleared" in msg.lower()
        assert "workset" in msg.lower()
        assert "reverts to default" not in msg

    def test_reset_message_shows_effective_value_and_source_tier(self, tmp_path):
        # Residuals item 1: threading the cascade lets the honest message APPEND
        # the now-effective value + its source tier. workset holds a downward
        # box.image default; box overrides it; resetting the box override falls
        # back to the WORKSET value. Baseline-RED at 6340dad: reset_config_value
        # had no cascade kwargs, so the message could never name the tier.
        ws = tmp_path / "ws.yaml"
        box = tmp_path / "box.yaml"
        set_config_value(
            "box.image", "ws-img", config_path=ws,
            command_scope=ConfigLevel.workset,
        )
        set_config_value(
            "box.image", "box-img", config_path=box,
            command_scope=ConfigLevel.box,
        )
        msg = reset_config_value(
            "box.image", config_path=box, command_scope=ConfigLevel.box,
            cascade_workset_path=ws, cascade_box_path=box,
        )
        # The effective value + its source tier are named (the F7 "where cheap").
        assert "cleared" in msg.lower(), msg
        assert "ws-img" in msg, msg
        assert "workset" in msg.lower(), msg
        # Mutation guard: the old lie is absent AND the bare cleared-only tail is
        # NOT used when the effective value IS available.
        assert "reverts to default" not in msg, msg
        assert "falls back through the cascade" not in msg, msg

    def test_reset_without_cascade_keeps_cleared_only_form(self, tmp_path):
        # Item 1 evidence-honesty: with NO cascade inputs supplied, keep the
        # cleared-only form (do not guess an effective value).
        ws = tmp_path / "ws.yaml"
        box = tmp_path / "box.yaml"
        set_config_value(
            "box.image", "ws-img", config_path=ws,
            command_scope=ConfigLevel.workset,
        )
        set_config_value(
            "box.image", "box-img", config_path=box,
            command_scope=ConfigLevel.box,
        )
        msg = reset_config_value(
            "box.image", config_path=box, command_scope=ConfigLevel.box,
        )
        assert "cleared" in msg.lower(), msg
        assert "falls back through the cascade" in msg, msg
        # No effective value guessed.
        assert "effective is now" not in msg, msg

    def test_reset_absent_below_keeps_cleared_only_form(self, tmp_path):
        # Item 1: even WITH cascade inputs, a key with no lower-tier setter (so it
        # is absent from the post-reset cascade) keeps the cleared-only form — no
        # fabricated built-in default (the Editor's "no built-in guess").
        box = tmp_path / "box.yaml"
        set_config_value(
            "box.image", "box-only", config_path=box,
            command_scope=ConfigLevel.box,
        )
        msg = reset_config_value(
            "box.image", config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box,
        )
        assert "cleared" in msg.lower(), msg
        assert "effective is now" not in msg, msg
        assert "falls back through the cascade" in msg, msg

    def test_scopeless_key_never_claims_cascade_effective(self, tmp_path):
        # Editor F1: a SCOPELESS key (allow_helpers) is read from a SINGLE
        # settings file / the flat KanibakoConfig — NOT the settings cascade.
        # So even with cascade inputs holding a lower-tier allow_helpers,
        # the reset must NOT claim a cascade-derived "effective" (a value from a
        # tier nothing reads). It keeps the cleared-only form.
        # NOTE: exercised at SYSTEM scope — a BARE agent behavior key (it targets
        # ``agent.default``) is a legit DOWNWARD write from system, whereas setting
        # it BARE at BOX scope is now REFUSED (redirected to the box.agent.* mirror;
        # see TestBareAgentKeyAtBoxScope). The F7 honest-reset property this guards
        # (a scopeless key is a single-file read, never a cascade claim) is
        # scope-independent.
        ws = tmp_path / "ws.yaml"
        box = tmp_path / "box.yaml"
        dump_doc(ws, {"allow_helpers": False})  # a lower-tier value
        set_config_value(
            "allow_helpers", "true", config_path=box,
            command_scope=ConfigLevel.system,
        )
        msg = reset_config_value(
            "allow_helpers", config_path=box, command_scope=ConfigLevel.system,
            cascade_workset_path=ws, cascade_box_path=box,
        )
        assert "cleared" in msg.lower(), msg
        # The wrong claim MUST be absent (mutation guard) — cleared-only stands.
        assert "effective is now" not in msg, msg
        assert "falls back through the cascade" in msg, msg


class TestSiblingDownwardKeyGetAtNoun:
    """Sibling (F2/F3 pair, 07-02d) — a downward flat key SET at a higher noun
    (the containment relax: ``system set box.image`` lands in the system
    settings file) must be READ BACK by that same noun's ``get``.  Same class as
    F5/F6: get must read where set wrote."""

    def test_system_set_downward_key_then_get_roundtrips(self, tmp_path):
        # system scope: set box.image → lands in the system SETTINGS file (ssp).
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True, exist_ok=True)
        msg = set_config_value(
            "box.image", "sys-default:img",
            config_path=cf, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        # RED at baseline: get reads cf/project_toml, never ssp → not the value.
        val = get_config_value(
            "box.image",
            global_config_path=cf,
            system_settings_path=ssp,
        )
        assert val == "sys-default:img"

    def test_workset_set_downward_key_then_get_roundtrips(self, tmp_path):
        # workset scope: set box.image → lands in the workset settings file.
        ws = tmp_path / "workset-settings.yaml"
        msg = set_config_value(
            "box.image", "ws-default:img",
            config_path=ws, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        # The workset noun's own file is project_toml here — a plain get at the
        # workset noun must read its own stored downward key.
        val = get_config_value(
            "box.image",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=ws,
        )
        assert val == "ws-default:img"


class TestSetTimeCtxUsesHostXdgMap:
    """Dedup — ``_set_time_ctx`` must route its ``$XDG_*`` map through the
    canonical :func:`kanibako.settings.paths.host_xdg_map` builder (spec §1 XDG clause +
    L2 §3 single-source-of-truth: ONE builder supplies every host-side context),
    not a hand-rolled 5-var dict.  (The builder's own resolution coverage lives
    in test_system_paths.py ``TestHostXdgMap`` — this only proves the wiring.)"""

    def test_set_time_ctx_xdg_equals_host_xdg_map(self):
        from kanibako.settings.config_interface import _set_time_ctx
        from kanibako.settings.paths import host_xdg_map

        ctx = _set_time_ctx()
        assert ctx.xdg == host_xdg_map()

    def test_set_time_ctx_calls_host_xdg_map(self, monkeypatch):
        # Non-vacuous: prove the wiring routes THROUGH the builder (so a revert
        # to a hand-rolled map that happens to produce equal output still fails).
        # RED at baseline: the inline dict never calls host_xdg_map → sentinel
        # is not observed in ctx.xdg.
        import kanibako.settings.config_interface as ci

        sentinel = {"XDG_DATA_HOME": "/SENTINEL"}
        monkeypatch.setattr(
            # ⚑ NO raising=False: with it, a _host_xdg_map that moved out of
            # this module would make the patch a silent no-op and leave the
            # test "passing" while proving nothing.
            ci, "_host_xdg_map", lambda *a, **k: dict(sentinel),
        )
        ctx = ci._set_time_ctx()
        assert ctx.xdg == sentinel


# ---------------------------------------------------------------------------
# ⚑ ``TestF10CoreFloorRegistry`` USED TO LIVE HERE (F10 — expose the launch-only
# CORE box-mount floor to config-set). It pinned the SHAPE of
# ``core_defaults.core_default_bind_keys``: the context-light SET-TIME floor
# registry of core box-mount entries with a placeholder host_src, folded into the
# category set-time cascade so a source-only repoint of a launch-only bind would
# pass the must-exist gate.
#
# R-9 retired every bind CLI write route, which made the registry unable to change
# an outcome; it was kept only because it still reached ``dotted_partial`` and so
# had to keep emitting a live shape. The whole thread — the producer, its
# ``FLOOR_PLACEHOLDER_SRC`` sentinel, the ``default_categories`` parameter on five
# ``config_interface`` entry points, ``config_keys._floor_bind_display`` and the
# three handler call sites — was then deleted in one subtractive pass, and these
# tests went with their subject. Nothing about the LAUNCH floor is affected: its
# producers (``core_default_categories`` and siblings) are host-probed, feed
# ``build_launch_snapshot``, and are pinned by ``test_defaults_golden.py`` /
# ``test_categories_live.py``.
# ---------------------------------------------------------------------------


class TestScopeBindRouteRetired:
    """R-9 — the SCOPE-level bind CLI route
    ``{system,workset,box}.bindings.{ro,rw}.<name>`` is RETIRED.

    This class REPLACES ``TestF10CoreFloorRepoint``, which pinned the opposite
    behaviour: a source-only repoint of a launch-only CORE bind, enabled by
    threading the floor registry. That surface is a KNOWN, ACCEPTED LOSS (Jei:
    *"unfortunate, but this is going to have to be a cost we'll pay"*), boarded
    for review as DS-BL1. It is NOT a regression to restore.

    What must hold instead: the refusal is LOUD, NAMES THE KEY, and WRITES
    NOTHING — spec §0 refuses, never silently accepts and never fabricates.
    ⚑ These calls used to thread the floor registry on purpose, so the refusal
    could not be an artefact of the caller omitting it. The registry and its
    parameter are gone, so there is nothing left to omit — the refusal fires in the
    verb PREAMBLE, before any cascade is assembled at all.
    """

    @pytest.mark.parametrize(
        "key",
        [
            "box.bindings.ro.vault",
            "box.bindings.rw.home",
            "system.bindings.ro.helper",
            "workset.bindings.rw.share",
        ],
    )
    def test_set_is_refused_and_names_the_key(self, tmp_path, key):
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            key, "/newsrc",
            config_path=box, command_scope=ConfigLevel.system,
            cascade_box_path=box,
        )
        assert msg.startswith("Error:"), msg
        # §0: the error NAMES the offending key — not a generic "unknown key".
        assert key in msg, msg
        assert "RETIRED" in msg, msg
        # Nothing was written: a refused write creates no file.
        assert not box.exists()

    def test_reset_is_refused_symmetrically(self, tmp_path):
        box = tmp_path / "box.yaml"
        dump_doc(box, {"box": {"bindings": {"ro": {"vault": [
            "/old", "/custom/dest", "ro",
        ]}}}})
        msg = reset_config_value(
            "box.bindings.ro.vault",
            config_path=box, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "box.bindings.ro.vault" in msg
        # ⚑ "RETIRED" is NOT decoration. Without it this assertion survived a
        # mutation that deleted the refusal entirely: the reset then fell through
        # to the routing table and returned "unknown config key: box.bindings.ro
        # .vault" — which starts with "Error:", names the key, and writes nothing,
        # so every other assertion here still passed. Pin the RIGHT error, not
        # merely an error. (Found by the M1 mutation run, not by inspection.)
        assert "RETIRED" in msg, msg
        # ⚑ NOT "No override for …": that would be a lie twice over — it implies
        # the spelling was CLI-writable, and a hand-authored tuple is sitting
        # right there. Prove the reset did not remove it.
        assert "No override" not in msg, msg
        assert load_doc(box)["box"]["bindings"]["ro"]["vault"] == [
            "/old", "/custom/dest", "ro",
        ]

    def test_null_is_refused_by_the_retirement_not_the_category_guard(self, tmp_path):
        """``--null`` on the retired spelling gets the RETIREMENT message, not the
        "category has no null form" one. Ordering matters: a user who typed a key
        that no longer exists must be told THAT, not handed a rule about a route
        they cannot reach."""
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "box.bindings.rw.home", None,
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box,
        )
        assert "RETIRED" in msg, msg
        assert "--null is not yet supported" not in msg, msg

    def test_the_retirement_now_covers_every_bind_shaped_category(self, tmp_path):
        """⚑ THIS ROW INVERTED, ON A RULING. It used to pin the retirement as
        SURGICAL — two tokens at three scopes, with ``box.synced.x`` still settable
        as the control. DS-BL1 = (a) (Jei, 2026-08-07g) made the loss UNIFORM, so the
        control became a second retired spelling. What is pinned now is the same
        no-over/under-reach property from the other side: the SAME door refuses the
        arms and the other four, and it does NOT reach a neighbouring scalar."""
        box = tmp_path / "box.yaml"
        dump_doc(box, {"box": {"synced": {"x": ["/old", "/dest"]}}, })
        for key in ("box.bindings.ro.x", "box.synced.x", "box.caches.x",
                    "box.seeded.x", "box.common.x"):
            msg = set_config_value(
                key, "/newsrc",
                config_path=box, command_scope=ConfigLevel.box,
                cascade_box_path=box,
            )
            assert msg.startswith("Error:") and "RETIRED" in msg, (key, msg)
        # ...and the value is untouched by any of them.
        assert load_doc(box)["box"]["synced"]["x"] == ["/old", "/dest"]
        # NOT over-reached: a scalar at the same scope still writes.
        assert not set_config_value(
            "box.shell", "/bin/zsh",
            config_path=box, command_scope=ConfigLevel.box, cascade_box_path=box,
        ).startswith("Error:")

    def test_the_two_retirements_keep_their_own_recognisers(self):
        """The agent-scope route is retired TOO (the second R-9 step), but by its
        OWN recogniser — the node-splitting parser, not this one. Pin the split so
        neither predicate quietly grows to cover the other's keys."""
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_path_category_key,
            _is_scope_bind_key,
        )

        # Neither retired form is a settable category key any more.
        assert not _is_path_category_key("agent.claude.bindings.ro.launcher")
        assert not _is_path_category_key("box.bindings.ro.vault")
        # ...and each is claimed by exactly ONE recogniser.
        assert _is_scope_bind_key("box.bindings.ro.vault")
        assert not _is_agent_node_bind_key("box.bindings.ro.vault")
        assert _is_agent_node_bind_key("agent.claude.bindings.ro.launcher")
        assert not _is_scope_bind_key("agent.claude.bindings.ro.launcher")


class TestCoreFloorStillMergesAtLaunch:
    """The floor registry itself is UNAFFECTED by R-9: only the CLI route died.
    A box-scope tuple still beats the base floor when the launch cascade merges,
    which is what makes hand-editing the settings file — the cure the refusal
    prescribes — actually work."""

    def test_written_box_tuple_overrides_floor_at_launch(self, tmp_path):
        # Take-effect (reconcile precedence): a box-scope written tuple sits at the
        # box level and BEATS the base floor when the launch cascade merges.
        from kanibako.settings.settings_assemble import assemble_levels
        from kanibako.settings.settings_merge import merge

        # ⚑ DEST-KEYED on BOTH sides (R-3/R-5): the box file's arm and the floor's
        # arm are the SAME terminal key ``box.bindings.rw``, and the entry they
        # contend over is identified by its DESTINATION. The floor writes ``~`` and
        # the file writes ``~/``; R-11 normalizes both to ``/home/agent``, so they
        # land on ONE entry and the merge really does have a contest to decide.
        # Before R-11 those were two entries and the "override" would have been two
        # binds at one mountpoint instead.
        box = tmp_path / "box.yaml"
        dump_doc(box, {"box": {"bindings": {"rw": {
            "~/": ["/BOXWIN", "Z,U"],
        }}}})
        floor = {"box.bindings.rw": {"~": ("/FLOOR", "Z,U")}}
        snap = merge(assemble_levels(agent_name="", box_path=box, floor=floor))
        node = snap
        for seg in ("box", "bindings", "rw", "/home/agent"):
            node = dict.get(node, seg)
        assert node.src == "/BOXWIN"  # box beats the base floor
        # And with NO box file the floor value is the fallback (proves reachability).
        snap2 = merge(assemble_levels(agent_name="", floor=floor))
        n2 = snap2
        for seg in ("box", "bindings", "rw", "/home/agent"):
            n2 = dict.get(n2, seg)
        assert n2.src == "/FLOOR"


# ---------------------------------------------------------------------------
# item-0 — per-node DESCRIPTOR bind repoint (agent.<node>.bindings.{ro,rw}.<name>)
# ---------------------------------------------------------------------------

class TestAgentNodeBindRouting:
    """The ``agent.<node>.bindings.{ro,rw}.<name>`` predicate + its routing order:
    it is a per-node DESCRIPTOR bind (item-0), NOT a persona scalar, NOT a box.agent
    mirror. (There is no bare ``agent.bindings.*`` form to distinguish it from — the
    agent tier is DISCRIMINATED, spec §2d / §0.)

    ⚑ Since R-9 the predicate's job is RECOGNISE-TO-REFUSE plus routing the
    surviving ``config get``; it is no longer a set route. What it must still match
    is unchanged, which is exactly why these rows are kept.
    """

    def test_predicate_matches_node_bind_only(self):
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_agent_scope_bind_key,
            _is_box_agent_key,
            _is_persona_agent_key,
        )
        # A node bind key: node-bind True, and the neighbours False (no mis-capture).
        k = "agent.claude.bindings.ro.launcher"
        assert _is_agent_node_bind_key(k)
        assert not _is_box_agent_key(k)
        assert not _is_persona_agent_key(k)  # launcher is not a state leaf
        # ⚑ THIS LINE USED TO READ ``assert not _is_path_category_key(k)`` and it had
        # gone VACUOUS: since 2026-08-08c ``BIND_KEY_RE``'s non-terminal complement
        # is empty, so that predicate compiles ``(?!)`` and answers False for EVERY
        # string — the assertion would have held for ``""``. The live neighbour is
        # the agent-scope RECOGNISER, and it deliberately answers TRUE here: it is a
        # SUPERSET of the node parser (recognition may be broad, resolution may
        # not), and the narrow one is checked FIRST wherever both matter.
        assert _is_agent_scope_bind_key(k)

    def test_the_node_regex_is_pinned_to_the_bindings_arms_as_a_proper_subset(self):
        """⚑ ``_AGENT_NODE_BIND_RE`` spells the two ``bindings`` arms LITERALLY (it
        has to, to split the node non-greedily around them) instead of importing an
        alternation, so it must be pinned against the single source. **It is pinned
        as a PROPER SUBSET, and the REASON MOVED AT S3 (the fact did not):** since the
        agent file's address rule reads every category flat with the destination whole,
        a widened parser would no longer MIS-ADDRESS the other four — it would ADMIT
        them. These two arms are the only per-entry spellings whose READ survived R-9,
        which is exactly what ``config_keys.agent_read_key_error`` carves out of the §0
        read gate and what ``is_known_key`` claims as key-shaped; widening this parser
        would hand the other four a read spec §0 says is not a key at any scope.

        ⚑⚑ THE ARMS ARE SPELLED OUT HERE ON PURPOSE, AND THAT IS THE FIX. This half
        used to loop over ``_TERMINAL_BIND_CATEGORIES``, which was the two arms when
        it was written and became ALL SIX on 2026-08-08c — so the test had inverted
        into a DEMAND for exactly the widening the parser must never have, while its
        second loop (``RETIRED − TERMINAL``) ran zero times and proved nothing. A pin
        against a set whose membership MOVES is not a pin. The literal is what is
        being guarded, so the literal is what is written.

        The other four are refused at the agent scope through
        ``_is_agent_scope_bind_key`` / ``AGENT_BIND_KEY_RE`` instead — the SAME door
        (``agent_node_bind_retired_error``), a different parser — which the second
        half pins, so neither half can quietly stop covering its share.
        """
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_agent_scope_bind_key,
            agent_node_bind_retired_error,
        )
        from kanibako.settings.settings_categories import (
            RETIRED_BIND_CATEGORIES,
            SETTABLE_BIND_CATEGORIES,
        )

        arms = ("bindings.ro", "bindings.rw")
        # PROPER subset: covered by the derived set, and STRICTLY smaller — which is
        # what makes "the other four" below a non-empty set rather than a promise.
        assert set(arms) < set(RETIRED_BIND_CATEGORIES)

        # RESOLUTION — the node parser claims exactly the two arms...
        for cat in arms:
            assert _is_agent_node_bind_key(f"agent.claude.{cat}.x"), cat

        others = sorted(set(RETIRED_BIND_CATEGORIES) - set(arms))
        # ⚑ NON-VACUITY GUARD: this loop is the whole point of the second half, and
        # its predecessor iterated ZERO times. Assert it has work before doing it.
        assert others, "the complement is EMPTY — the loop below would prove nothing"
        for cat in others:
            assert not _is_agent_node_bind_key(f"agent.claude.{cat}.x"), cat
            # RECOGNITION — ...and the other four are claimed by the agent-scope
            # recogniser, so the DOOR still covers all six at the agent scope.
            assert _is_agent_scope_bind_key(f"agent.claude.{cat}.x"), cat

        for cat in RETIRED_BIND_CATEGORIES:
            assert agent_node_bind_retired_error(
                f"agent.claude.{cat}.x", verb="set",
            ) is not None, cat
        # ⚑ NOT tautological on an empty tuple: SETTABLE is empty by ruling, so
        # assert THAT directly rather than looping over nothing.
        assert SETTABLE_BIND_CATEGORIES == ()

    def test_bind_named_model_is_a_bind_not_a_persona_scalar(self):
        # COLLISION: a bind literally NAMED ``model`` — the ``bindings.ro`` segment
        # disambiguates it from the persona state leaf ``agent.claude.model``. Both
        # predicates fire, but the node-bind is checked FIRST in the dispatch.
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_persona_agent_key,
        )
        k = "agent.claude.bindings.ro.model"
        assert _is_agent_node_bind_key(k)
        assert _is_persona_agent_key(k)  # would mis-capture if checked first

    def test_persona_scalar_is_not_a_node_bind(self):
        from kanibako.settings.config_keys import _is_agent_node_bind_key
        assert not _is_agent_node_bind_key("agent.claude.model")
        assert not _is_agent_node_bind_key("agent.claude.endpoint")

    def test_box_agent_bind_is_not_a_node_bind(self):
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_box_agent_key,
        )
        assert not _is_agent_node_bind_key("box.agent.bindings.ro.x")
        assert _is_box_agent_key("box.agent.bindings.ro.x")

    def test_bare_agent_category_is_not_a_node_bind(self):
        # The BARE ``agent.<category>.<name>`` (no node) is NOT A KEY: the keyspace
        # is CLOSED (spec §0) and the agent tier is DISCRIMINATED (§2d / §0), so
        # BOTH the node-bind PARSER and the agent-scope RECOGNISER refuse it — and
        # the recogniser's refusal is the load-bearing one, because a match there
        # would hand an undeclared spelling a "route is RETIRED" message implying it
        # had once been a key.
        #
        # ⚑ The second term used to be ``_is_path_category_key``; that predicate
        # answers False for every string since 2026-08-08c, so this pair of
        # assertions had gone vacuous on one side and false on the other.
        from kanibako.settings.config_keys import (
            _is_agent_node_bind_key,
            _is_agent_scope_bind_key,
        )
        assert not _is_agent_node_bind_key("agent.bindings.ro.foo")
        assert not _is_agent_scope_bind_key("agent.bindings.ro.foo")
        assert not _is_agent_scope_bind_key("agent.caches.foo")
        # A DISCRIMINATED key is claimed: the ``bindings`` arms by BOTH (the node
        # parser resolves, the recogniser is its deliberate superset), and the other
        # four by the recogniser alone.
        assert _is_agent_node_bind_key("agent.claude.bindings.ro.foo")
        assert _is_agent_scope_bind_key("agent.claude.bindings.ro.foo")
        assert not _is_agent_node_bind_key("agent.default.caches.foo")
        assert _is_agent_scope_bind_key("agent.default.caches.foo")

    def test_resolve_key_canonicalizes_node_plus_form(self):
        from kanibako.settings.config_keys import resolve_key
        assert (
            resolve_key("agent.navigator+claude.bindings.rw.plugins")
            == "agent.navigator℘claude.bindings.rw.plugins"
        )
        # A bind named ``model`` under a persona keeps its bind shape (NOT the
        # persona-scalar re-root).
        assert (
            resolve_key("agent.nav+claude.bindings.ro.model")
            == "agent.nav℘claude.bindings.ro.model"
        )


class TestAgentNodeBindWriteRouteRetired:
    """R-9 (second step) — the per-node descriptor bind CLI WRITE route
    ``agent.<node>.bindings.{ro,rw}.<name>`` is RETIRED.

    This class REPLACES ``TestAgentNodeBindRepoint``, which pinned the opposite
    behaviour: a source-only repoint that wrote the RAW tuple into the node file,
    sourcing box_dest/opts from a detect-free descriptor floor registry. That
    surface is a KNOWN, ACCEPTED LOSS with no replacement spelling (R-9), boarded
    for review as DS-BL1. It is NOT a regression to restore.

    What must hold instead: the refusal is LOUD, NAMES THE KEY, and WRITES NOTHING
    — spec §0 refuses, never silently accepts and never fabricates.
    """

    @pytest.mark.parametrize(
        "key,shown",
        [
            ("agent.claude.bindings.ro.launcher",
             "agent.claude.bindings.ro.launcher"),
            ("agent.claude.bindings.rw.plugins",
             "agent.claude.bindings.rw.plugins"),
            # A persona node: the message hands the key back in its USER-FACING
            # ``+`` spelling, never the ``℘`` canonical one.
            ("agent.navigator℘claude.bindings.ro.launcher",
             "agent.navigator+claude.bindings.ro.launcher"),
            # A bind literally NAMED after a persona state leaf: the refusal must
            # claim it as a BIND, not fall through to the persona scalar branch and
            # write "/newsrc" into ``self.model``.
            ("agent.claude.bindings.ro.model",
             "agent.claude.bindings.ro.model"),
        ],
    )
    def test_set_is_refused_and_names_the_key(self, tmp_path, key, shown):
        node = tmp_path / "settings.yaml"
        msg = set_config_value(
            key, "/newsrc",
            config_path=node, command_scope=ConfigLevel.system,
            cascade_agent_path=node, cascade_agent_name="claude",
        )
        assert msg.startswith("Error:"), msg
        # §0: the error NAMES the offending key — not a generic "unknown key".
        assert shown in msg, msg
        # ⚑ "RETIRED" is NOT decoration — it is the ONLY token this refusal
        # produces that a fall-through error does not. Without the preamble guard a
        # set of this key lands on the routing table's "unknown config key: <key>",
        # which also starts with "Error:", also names the key, and also writes
        # nothing. Pin the RIGHT error, not merely an error. (P1 shipped a vacuous
        # assertion of exactly this shape and its mutation run caught it.)
        assert "RETIRED" in msg, msg
        # Nothing was written: a refused write creates no file.
        assert not node.exists()

    def test_reset_is_refused_symmetrically(self, tmp_path):
        """A reset is a WRITE. "No override for …" would be a lie twice over: it
        implies the spelling was CLI-writable, and a hand-authored tuple is sitting
        right there in the node file."""
        agents = tmp_path / "agents"
        (agents / "claude").mkdir(parents=True)
        node_file = agents / "claude" / "settings.yaml"
        seeded = {"self": {"bindings": {"ro": {
            "launcher": ["/old", "/box/launcher", "ro"]}}}}
        dump_doc(node_file, seeded)
        msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        assert msg.startswith("Error:"), msg
        assert "agent.claude.bindings.ro.launcher" in msg, msg
        assert "RETIRED" in msg, msg
        assert "No override" not in msg, msg
        # The hand-authored tuple survives the refusal untouched.
        assert load_doc(node_file) == seeded

    def test_null_is_refused_by_the_retirement_not_the_category_guard(self, tmp_path):
        """``--null`` on the retired spelling gets the RETIREMENT message, not the
        "category has no null form" one. A user who typed a route that no longer
        exists must be told THAT, not handed a rule about a route they cannot
        reach."""
        node = tmp_path / "settings.yaml"
        msg = set_config_value(
            "agent.claude.bindings.rw.plugins", None,
            config_path=node, command_scope=ConfigLevel.system,
        )
        assert "RETIRED" in msg, msg
        assert "--null is not yet supported" not in msg, msg

    def test_the_cure_names_the_node_file_not_a_command(self, tmp_path):
        """R-9 accepted the loss, so the message must NOT prescribe a CLI verb that
        does not exist. It names the file the launch actually reads — and for this
        route that is the NODE's own file, not a scope table."""
        msg = set_config_value(
            "agent.navigator℘claude.bindings.ro.launcher", "/newsrc",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.system,
        )
        assert "agents/navigator+claude/settings.yaml" in msg, msg
        # ⚑ Flat since S2: the file is that node's, so ``self:`` IS
        # ``agent.navigator+claude`` and the bindings arm sits directly under it. The
        # node is carried by the PATH asserted above, not by the table spelling.
        assert "self.bindings.ro" in msg, msg
        assert "self.navigator+claude" not in msg, msg
        # ⚑ ``℘`` is a keyspace-INTERNAL separator and must NEVER reach a message —
        # including the read the message hands back for the user to run.
        assert "℘" not in msg, msg
        # The read is the one verb still offered, and it really works — spelled on
        # the AGENT noun, which takes the node as its SUBJECT and the rest as the
        # TAIL. It used to be spelled ``config get <full key>``, which is wrong
        # twice: there is no ``config`` noun, and the full key double-prefixes the
        # node (bifrost row 66, defect 2).
        assert "kanibako agent get navigator+claude bindings.ro.launcher" in msg, msg
        assert "config get" not in msg, msg

    def test_the_plus_form_is_refused_too(self, tmp_path):
        """The user types ``+``; ``resolve_key`` canonicalizes before the refusal,
        so the guard must fire on the spelling the user actually typed."""
        msg = set_config_value(
            "agent.navigator+claude.bindings.ro.launcher", "/newsrc",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.system,
        )
        assert "RETIRED" in msg, msg

    def test_box_scope_repoint_is_still_refused_upward_first(self, tmp_path):
        """The §0 directional guard runs BEFORE the retirement (it is the scope
        rule, and it was already the answer at box scope). Unchanged by R-9."""
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.launcher", "/new",
            config_path=box, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:") and "cannot be set" in msg
        assert not box.exists()  # nothing written

    def test_the_other_four_agent_categories_are_still_recognised(self, tmp_path):
        """The narrowing is SURGICAL: ``_AGENT_NODE_BIND_RE`` claims the two
        ``bindings`` arms and nothing else, and the other four are still RECOGNISED
        at the agent scope by ``_is_agent_scope_bind_key`` — so their refusal names
        the key instead of degrading to "unknown config key" (spec §0) or, worse,
        being mistaken for a project name.

        ⚑ THIS TEST WAS CALLED ``test_the_still_settable_agent_categories_are_untouched``
        and asserted ``_is_path_category_key``. Both halves of that name are now
        false: nothing bind-shaped is settable (DS-BL1 = (a)) and that predicate
        answers False for every string (its non-terminal complement emptied on
        2026-08-08c), so the assertion had become a red that could only be reached by
        widening the node parser — the one change it exists to forbid. The guarantee
        is unchanged and re-posed on the live recogniser; only the term moved.
        """
        from kanibako.settings.config_keys import _is_agent_scope_bind_key

        for cat in ("common", "caches", "seeded", "synced"):
            assert _is_agent_scope_bind_key(f"agent.claude.{cat}.x"), cat

    def test_written_tuple_still_overrides_descriptor_floor_at_launch(self, tmp_path):
        """⚑ THE CURE ACTUALLY WORKS. The route died; the KEY did not. A tuple
        hand-authored in the node file — the only way left to write one, and exactly
        what the refusal prescribes — still beats the descriptor default at launch.
        Without this, the refusal would be pointing users at a dead end."""
        from kanibako.settings.agent_representation import agent_default_partial
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.targets.base import (
            AgentInstall, BindKind, BindScope, Binding, HostSrcOrigin,
            PluginDescriptor,
        )

        install = AgentInstall(
            name="claude", binary=tmp_path / "b",
            launcher=tmp_path / "orig-launcher", install_dir=tmp_path / "share",
        )
        binding = Binding(
            key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/box/launcher",
            kind=BindKind.FILE, scope=BindScope.AGENT_CRITICAL, ro=True,
        )
        desc = PluginDescriptor(command=("claude",), bindings=(binding,), mode={})
        partial = agent_default_partial(desc, install, node_name="claude")

        # Authored the ONLY way left: directly in the node's settings file.
        # ⚑ DEST-KEYED (R-5/R-10): the arm is a flat map from box DESTINATION to
        # ``[src[, options]]``. The retired ``{"launcher": [src, dest, opts]}``
        # spelling is refused by arity now, which is what makes the two shapes
        # distinguishable at all — a 2-element list is NOT (R-9's accepted loss).
        node = tmp_path / "settings.yaml"
        dump_doc(node, {"self": {"bindings": {"ro": {
            "/box/launcher": ["/REPOINT", "ro"]}}}})

        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=_bind_launch_ctx(),
            system_path=None, agent_path=node, workset_path=None, box_path=None,
            agent_partial=partial,
        )
        assert snap.agent.claude.bindings.ro["/box/launcher"].src == "/REPOINT"


def _bind_launch_ctx():
    from kanibako.settings.settings_resolve import ResolveCtx
    return ResolveCtx(
        agent_name="claude", workset_name=None, host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


# ---------------------------------------------------------------------------
# What remains of the per-node bind verb surface after R-9: the READ
# ---------------------------------------------------------------------------

class TestAgentNodeBindGetSurvives:
    """⚑ THE READ SURVIVED THE WRITE, for the agent scope exactly as for the file
    scopes (``TestCoreBindGetReset`` below).

    The set/reset route is retired, but the key is still DECLARED, still authored
    by hand in ``agents/<node>/settings.yaml``, and still delivered at launch — and
    hand-editing that file is exactly the cure the refusal prescribes. So ``config
    get`` must keep returning the stored tuple. A get that answered "(not set)" for
    a bind the launch is actually mounting would be a silent lie, and would make the
    prescribed cure unverifiable.
    """

    def _agents_root(self, tmp_path):
        # A node file under an agents root: agents/<node>/settings.yaml.
        root = tmp_path / "agents"
        (root / "claude").mkdir(parents=True)
        return root

    def test_get_reads_a_hand_authored_bind_after_the_route_retired(self, tmp_path):
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        # Authored the ONLY way left: directly in the node's settings file.
        dump_doc(node_file, {"self": {"bindings": {"ro": {
            "launcher": ["/newsrc", "/box/launcher", "ro"]}}}})
        val = get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        )
        assert val == str(["/newsrc", "/box/launcher", "ro"])

    def test_the_write_verbs_refuse_and_leave_the_file_alone(self, tmp_path):
        """The other half of the same round-trip: set and reset both refuse, and the
        hand-authored tuple the get above reads is still there afterwards."""
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        seeded = {"self": {"bindings": {"ro": {
            "launcher": ["/old", "/box/launcher", "ro"]}}}}
        dump_doc(node_file, seeded)
        set_msg = set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node_file, command_scope=ConfigLevel.system,
            cascade_agent_path=node_file, cascade_agent_name="claude",
            agents_root=agents,
        )
        reset_msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        assert load_doc(node_file) == seeded
        # ...and the read is unaffected by either refusal.
        assert get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        ) == str(["/old", "/box/launcher", "ro"])

    def test_get_unset_node_bind_is_not_set(self, tmp_path):
        # An unset bind → None ("(not set)"), non-crashing (mutation: RED if the get
        # branch fabricated the floor default instead of the stored-at-noun value).
        agents = self._agents_root(tmp_path)
        val = get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        )
        assert val is None

    def test_get_without_agents_root_is_not_set(self, tmp_path):
        # A box/workset-scope get (no agents_root threaded) → None, never crashes.
        val = get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml",
        )
        assert val is None

    def test_get_bind_named_after_state_leaf_routes_to_bind(self, tmp_path):
        # Collision: a bind literally NAMED ``model`` must route to the node-bind
        # get path (the bindings.ro segment), NOT the persona ``model`` scalar.
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        dump_doc(node_file, {"self": {"bindings": {"ro": {
            "model": ["/hostmodel", "/box/model", "ro"]}}}})
        val = get_config_value(
            "agent.claude.bindings.ro.model",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        )
        assert val == str(["/hostmodel", "/box/model", "ro"])
        # The PERSONA scalar ``agent.claude.model`` is a DIFFERENT key (flat slot),
        # unaffected by the bind entry.
        assert get_config_value(
            "agent.claude.model",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        ) is None


class TestPersonaScalarGetResetUnchanged:
    """Neither the surviving node-bind GET branch nor the node-bind write REFUSAL
    may divert a persona SCALAR key (``agent.<node>.model`` / ``.endpoint``) — it
    still routes stored-at-noun to the flat ``agent:`` slot (byte-unchanged
    collision guard). ⚑ Load-bearing after R-9: the refusal runs in the set
    PREAMBLE, ahead of every branch, so an over-wide guard here would take the
    persona scalars with it and there would be no later branch to save them."""

    def test_persona_model_get_reset_unchanged(self, tmp_path):
        agents = tmp_path / "agents"
        (agents / "claude").mkdir(parents=True)
        node_file = agents / "claude" / "settings.yaml"
        set_config_value(
            "agent.claude.model", "opus",
            config_path=tmp_path / "x", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        # Stored at the FLAT persona slot ``self.model`` (NOT nested bindings).
        assert load_doc(node_file) == {"self": {"model": "opus"}}
        assert get_config_value(
            "agent.claude.model",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        ) == "opus"
        assert reset_config_value(
            "agent.claude.model",
            config_path=tmp_path / "x", command_scope=ConfigLevel.system,
            agents_root=agents,
        ) == (
            "Cleared agent.claude.model set on the system scope; it now falls "
            "back through the cascade."
        )


class TestCoreBindGetReset:
    """item 4 — what remains of the CORE bind
    (``{system,workset,box}.bindings.{ro,rw}.<name>``) verb surface after R-9.

    ⚑ THE READ SURVIVED THE WRITE. The set/reset route is retired, but the key is
    still DECLARED, still authored by hand in the settings YAML, and still
    delivered at launch — and hand-editing that file is exactly the cure the
    refusal prescribes. So ``config get`` must keep returning the stored tuple. A
    get that answered "(not set)" for a bind the launch is actually mounting
    would be a silent lie, and would make the prescribed cure unverifiable.
    """

    def test_get_reads_a_hand_authored_bind_after_the_route_retired(self, tmp_path):
        box_f = tmp_path / "box.yaml"
        # Authored the ONLY way left: directly in the settings file.
        dump_doc(box_f, {"box": {"bindings": {"ro": {
            "vault_ro": ["/newvault", "/vault/ro", "ro"]}}}})
        val = get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        )
        assert val == str(["/newvault", "/vault/ro", "ro"])

    def test_the_write_verbs_refuse_and_leave_the_file_alone(self, tmp_path):
        """The other half of the same round-trip: set and reset both refuse, and
        the hand-authored tuple the get above reads is still there afterwards."""
        box_f = tmp_path / "box.yaml"
        seeded = {"box": {"bindings": {"ro": {
            "vault_ro": ["/old", "/vault/ro", "ro"]}}}}
        dump_doc(box_f, seeded)
        set_msg = set_config_value(
            "box.bindings.ro.vault_ro", "/newvault",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f,
        )
        reset_msg = reset_config_value(
            "box.bindings.ro.vault_ro", config_path=box_f,
            command_scope=ConfigLevel.box,
        )
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        assert load_doc(box_f) == seeded
        # ...and the read is unaffected by either refusal.
        assert get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        ) == str(["/old", "/vault/ro", "ro"])

    def test_core_bind_get_unset_is_none(self, tmp_path):
        box_f = tmp_path / "box.yaml"
        dump_doc(box_f, {"box": {"image": "x"}})
        assert get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        ) is None


def test_meta_box_path_is_read_only_from_every_scope():
    """The RO box root is not settable from ANY command scope (spec §0 meta-RO).

    ``meta.box.path`` is a mount SOURCE — the box home binds from it — so a
    settable box root would let a config set redirect the box home to an arbitrary
    host directory. It is RO by NAMESPACE (nothing per-key registers it), and this
    pins that the namespace rule actually covers it, including the no-command-scope
    path. The FILE half (a top-level ``meta:`` table being dropped at assembly) is
    pinned in ``tests/test_settings_launch.py``.
    """
    from kanibako.settings.config_keys import (
        ConfigLevel,
        _scope_direction_error,
        is_known_key,
    )

    for scope in (*ConfigLevel, None):
        err = _scope_direction_error("meta.box.path", scope)
        assert err is not None, scope
        assert "meta.box.path" in err, scope
        assert "read-only" in err, scope
    # And it is never mistaken for a project name / settable key.
    assert is_known_key("meta.box.path") is False


# ---------------------------------------------------------------------------
# The SET-TIME resolution probe (spec §2a E3 / Q9) and what it does NOT touch
# ---------------------------------------------------------------------------

class TestSetTimeResolutionProbe:
    """T16 — a dangling ``@``-ref is REFUSED in a value the EXPANDER will see.

    Before this, the E3 probe was wired only at the CATEGORY set path, so a set
    accepted a value whose ref does not resolve. For an expanded value that is
    not inert: an embedded dangling ref is substituted with the EMPTY STRING
    (§6b) and the key silently resolves to something else entirely.
    """

    def test_dangling_embedded_ref_is_refused_and_names_the_referent(self, tmp_path):
        msg = set_config_value(
            "workset.boxes", "@meta.nope.key/boxes",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:")
        assert "meta.nope.key" in msg
        assert not (tmp_path / "settings.yaml").exists()  # nothing written

    def test_a_resolvable_ref_is_accepted(self, tmp_path):
        cfg = tmp_path / "settings.yaml"
        cfg.write_text("workset:\n  template: /ws/template\n")
        msg = set_config_value(
            "workset.boxes", "@workset.template/boxes",
            config_path=cfg, cascade_workset_path=cfg,
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg

    def test_an_unrelated_pre_existing_defect_still_allows_the_set(self, tmp_path):
        """``config set`` must stay usable to REPAIR a broken config: the probe
        blocks only on the EDITED value's own transitive upstream chain."""
        cfg = tmp_path / "settings.yaml"
        cfg.write_text("workset:\n  logs: '@meta.also.nope/logs'\n")
        msg = set_config_value(
            "workset.boxes", "/abs/boxes",
            config_path=cfg, cascade_workset_path=cfg,
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg

    def test_a_colon_in_a_scalar_value_is_ordinary_content(self, tmp_path):
        """The ``src:dest`` refusal is a CATEGORY rule about the BIND SHAPE.

        A scalar has no such shape — ``box.image = ghcr.io/…:latest`` is the
        obvious case — so leaving that check ungated would have made the probe
        unshippable the moment it reached a non-category key.
        """
        msg = set_config_value(
            "box.image", "ghcr.io/doctorjei/kanibako-oci:latest",
            config_path=tmp_path / "settings.yaml",
        )
        assert not msg.startswith("Error:"), msg
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in msg

    def test_reset_is_untouched(self, tmp_path):
        """Removing an override cannot introduce a dangling ref in the removed
        value, so ``--reset`` does not run the probe."""
        cfg = tmp_path / "settings.yaml"
        cfg.write_text("box:\n  image: 'custom:v2'\n")
        msg = reset_config_value("box.image", config_path=cfg)
        assert not msg.startswith("Error:"), msg

    def test_the_docker_env_exclusion_died_with_the_spelling(self, tmp_path):
        """⚑ FLIPPED by R-39. The bare ``env.<VAR>`` family was the probe's third
        exclusion: it was written VERBATIM to a docker ``.env`` file the expander
        never saw, so ``@``/``$`` in its value were ordinary characters and
        probing would have refused legitimate input with no correct spelling
        available (the escape hatch could not rescue it — ``\\@`` passed the
        probe but landed in the file WITH the backslash).

        The spelling is now RETIRED, refused in the verb preamble before the
        probe is ever consulted, so the exclusion has nothing left to exclude —
        and the LIVE ``<scope>.env.<VAR>`` arm probes, because its value IS
        host-expanded at launch.
        """
        from kanibako.settings.config_keys import _probes_at_set_time

        assert not _probes_at_set_time("env.EMAIL")   # never reaches the probe
        assert _probes_at_set_time("box.env.EMAIL")   # the live arm is LOUD

        env_path = tmp_path / ".env"
        msg = set_config_value(
            "env.EMAIL", "jei@example.com",
            config_path=tmp_path / "settings.yaml", env_path=env_path,
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.EMAIL" in msg
        assert not env_path.exists()

    def test_a_dangling_ref_in_a_scoped_env_value_is_refused(self, tmp_path):
        """The live env arm is host-expanded at launch, so it gets the check.

        This is the direction the retired arm could not take: an unresolvable
        ``@``-ref here would otherwise resolve to ``""`` silently at launch.
        """
        msg = set_config_value(
            "box.env.MY_PATH", "@nope.not.a.key/bin",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg

    def test_an_expanded_scalar_key_still_refuses_a_dangling_ref(self, tmp_path):
        """The exclusion is the DOCKER family and nothing more.

        A scalar whose value the expander DOES see stays LOUD — otherwise the
        ruling would have quietly disarmed the check it was meant to narrow.
        """
        for key in ("box.shell", "box.secret_path.TOK"):
            msg = set_config_value(
                key, "@meta.nope.key/x",
                config_path=tmp_path / f"{key.replace('.', '_')}.yaml",
                command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (key, msg)
            assert "meta.nope.key" in msg

    def test_an_unknown_key_is_named_as_an_unknown_KEY(self, tmp_path):
        """SHOULD-3 / spec §0: the error must NAME the key.

        Probing first would diagnose the VALUE of a key that does not exist —
        ``Unknown variable: $BAR`` instead of ``unknown config key: run_args`` —
        which sends the reader after the wrong thing entirely.
        """
        msg = set_config_value(
            "run_args", "--env FOO=$BAR", config_path=tmp_path / "settings.yaml",
        )
        assert msg == "Error: unknown config key: run_args"


class TestSetDispatchCoverage:
    """``_has_dedicated_route`` MIRRORS the ``set_config_value`` dispatch chain.

    It exists so the E3 probe stays off keys nothing handles. If a dispatch
    branch is added without a matching term, this drifts silently — so every
    routing-table key and one representative of each special family is pinned.
    """

    def test_every_routing_table_key_has_a_route(self):
        from kanibako.settings.config_keys import _KEY_ROUTES, _has_dedicated_route

        for key in _KEY_ROUTES:
            assert _has_dedicated_route(key), key

    def test_each_special_family_is_claimed(self):
        from kanibako.settings.config_keys import _has_dedicated_route

        for key in (
            "agent.claude.secret_path.TOK",       # per-node secret
            "box.secret_path.TOK",                # scope secret
            "box.env.FOO",                        # scope env
            "agent.claude.model",                 # persona setting
            "model",                              # bare agent setting
            "box.agent.model",                    # box agent mirror
            "system.cache",                       # system path tier
        ):
            assert _has_dedicated_route(key), key

    def test_a_fabricated_key_is_claimed_by_nothing(self):
        from kanibako.settings.config_keys import _has_dedicated_route

        # ⚑ ``env.FOO`` (the RETIRED bare spelling) is claimed by nothing on
        # purpose: R-39 refuses it in the verb PREAMBLE, and a preamble guard is
        # not a dispatch branch — a term for it here would be a second spelling
        # of the refusal. ``box.env.FOO`` moved the OTHER way, into the claimed
        # list above, when the scoped arm got its route.
        #
        # ⚑ EVERY retired bind-shaped route is in the same position, and each moved
        # OUT of the claimed list above when its branch was deleted: the two
        # ``bindings`` arms with R-9, and ``caches``/``seeded``/``common``/``synced``
        # with DS-BL1 = (a). All are refused in the preamble; none reaches a
        # dispatch branch.
        for key in (
            "run_args", "env.FOO", "nonsense.key",
            "agent.claude.bindings.ro.launcher",
            "box.bindings.ro.vault",
            "box.common.plugins",
            "agent.claude.caches.x",
        ):
            assert not _has_dedicated_route(key), key

    def test_the_probe_is_off_for_the_category_and_unclaimed_families(self):
        from kanibako.settings.config_keys import _probes_at_set_time

        # ``env.FOO`` is off because NOTHING claims it any more (R-39), not
        # because of a docker-family exclusion — that exclusion is gone.
        # ``agent.claude.bindings.ro.launcher`` is off because NOTHING claims it
        # any more (R-9's second step), the same way ``env.FOO`` is — not because
        # of a category exclusion, which is why its term was deleted.
        for off in ("env.FOO", "box.common.plugins",
                    "agent.claude.bindings.ro.launcher", "run_args"):
            assert not _probes_at_set_time(off), off
        for on in ("box.shell", "box.secret_path.TOK", "box.env.FOO",
                   "model", "box.image"):
            assert _probes_at_set_time(on), on


# ---------------------------------------------------------------------------
# `--effective` renders the declaration AND the binding it derives (spec §0)
# ---------------------------------------------------------------------------

class TestEffectiveCategoryBlock:
    """T15 — the materialisation is observable end-to-end (D6, box scope)."""

    # ⚑⚑ BUILT THROUGH THE PRODUCTION ROUTE, ON PURPOSE (2026-08-08f). The
    # previous fixture was hand-assembled from 3-element ``Bind`` leaves under
    # NAME keys and handed straight to ``show_config``, which renders whatever it
    # is given and validates no key — so it survived the 2026-08-08c dest-keying
    # flip unchanged while having stopped meaning what it says, and
    # ``test_concrete_bindings_are_listed_too`` stayed GREEN against a renderer
    # that emitted ZERO binding rows on every real snapshot. A fixture that
    # cannot meet production is worse than no test, so this one is assembled by
    # the launch itself: settings FILES -> ``build_launch_snapshot`` (assemble ->
    # merge -> expand) -> ``snapshot_category_entries`` -> ``derive_binding_keys``
    # -> ``_install_derived_bindings``. Nothing about the value shapes is
    # asserted here; they are whatever the producers make.
    #
    # ⚑ One consequence that is the PRODUCERS', not this fixture's, and is left
    # visible rather than papered over: destinations arrive R-11-ABSOLUTIZED
    # (``~/.claude/plugins`` is stored ``/home/agent/.claude/plugins``), which is
    # what retires the deferred-``~``-vs-resolved-dest contrast the first skipped
    # test below states. It belongs to the collapse rewrite that test is gated on.
    #
    # ⚑ The dest here CONTAINS DOTS, deliberately: ``binding_derivations.*`` is
    # keyed by SEGMENTS and installed with ``insert_segments``, so the dest is one
    # node. It used to be a dotted key handed to ``insert_dotted`` and SHATTERED
    # into nested nodes; this fixture drives the real chain that proves it no
    # longer does.
    @staticmethod
    def _snapshot(tmp_path):
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.settings_categories import derive_binding_keys
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx

        # The agent file is rooted at ``self:``, which IS ``agent.<node>`` — the file is
        # that node's own, so the category tables sit DIRECTLY under the root and a
        # second ``<node>`` level refuses (``settings_assemble._agent_partial``, the S2
        # flatten). Every category below is DEST-KEYED — the map key is the box
        # destination, the value is ``[src[, opts]]``.
        agent_file = tmp_path / "agent-settings.yaml"
        agent_file.write_text(yaml.safe_dump({"self": {
            "common": {
                "~/.claude/plugins": ["/store/agents/claude/common/plugins"],
            },
            "seeded": {"~": ["/store/template"]},
            "bindings": {"ro": {"~/ref": ["/store/ref"]}},
        }}))
        box_file = tmp_path / "box-settings.yaml"
        box_file.write_text(yaml.safe_dump({"box": {"bindings": {
            "rw": {"~": ["/boxes/mybox/home", "Z,U"]},
        }}}))
        ctx = ResolveCtx(
            agent_name="claude", workset_name=None, host_home="/home/host", xdg={},
        )
        snapshot = build_launch_snapshot(
            agent_name="claude", ctx=ctx,
            system_path=None, agent_path=agent_file,
            workset_path=None, box_path=box_file,
        )
        _install_derived_bindings(snapshot, derive_binding_keys(
            snapshot_category_entries(
                snapshot, active_agent="claude", box_ctx=ctx,
            ),
        ))
        return snapshot

    def _render(self, tmp_path):
        import io

        buf = io.StringIO()
        show_config(
            global_config_path=tmp_path / "kanibako_config.yaml",
            config_path=tmp_path / "settings.yaml",
            effective=True,
            file=buf,
            category_snapshot=self._snapshot(tmp_path),
        )
        return buf.getvalue()

    @pytest.mark.skip(
        reason="Asserts the ABSTRACT half of the `--effective` block renders the "
               "declaration and its binding_derivations line ADJACENTLY. That "
               "half is DISABLED while "
               "settings_categories.effective_bindings_and_template_sources is a "
               "deliberate stub, so the block prints a notice instead of pairs. "
               "To be REWRITTEN against that function once the collapse function "
               "lands — NOT deleted: the adjacency, and the deferred `~` vs "
               "resolved guest dest it contrasts, are the point of the display."
    )
    def test_declaration_and_derived_binding_print_adjacently(self, tmp_path):
        lines = self._render(tmp_path).splitlines()
        decl = next(
            i for i, ln in enumerate(lines)
            if ln.strip().startswith("agent.claude.common.plugins =")
        )
        assert lines[decl + 1].strip().startswith(
            "binding_derivations.agent.claude.common.plugins ="
        )
        # The declaration carries the DEFERRED box-side ``~``; the derivation
        # carries the resolved guest dest. Seeing both is the point.
        assert "~/.claude/plugins" in lines[decl]
        assert "/home/agent/.claude/plugins" in lines[decl + 1]

    @pytest.mark.skip(
        reason="Asserts the ABSTRACT half of the `--effective` block states each "
               "derivation's DELIVERY ((copy) vs (mount)). That half is DISABLED "
               "while settings_categories.effective_bindings_and_template_sources "
               "is a deliberate stub, so the block prints a notice instead of "
               "pairs. To be REWRITTEN against that function once the collapse "
               "function lands — NOT deleted: `seeded` deriving a COPY rather "
               "than a mount is exactly what the rewrite may not quietly drop "
               "(config_display._declaration_delivery is retained, caller-less, "
               "for the same reason)."
    )
    def test_the_derivation_line_states_its_DELIVERY(self, tmp_path):
        """N2 — ``seeded`` derives a COPY, not a mount (spec §0), and the two are
        not interchangeable: a mount is live and shadows the dest, a copy runs
        once at create and is then the box's own file. A reader who cannot tell
        them apart cannot answer the question this display exists for."""
        text = self._render(tmp_path)
        assert "binding_derivations.agent.claude.seeded.template" in text
        seeded_line = next(
            ln for ln in text.splitlines()
            if "binding_derivations.agent.claude.seeded.template" in ln
        )
        assert seeded_line.rstrip().endswith("(copy)")
        common_line = next(
            ln for ln in text.splitlines()
            if "binding_derivations.agent.claude.common.plugins" in ln
        )
        assert common_line.rstrip().endswith("(mount)")

    def test_concrete_bindings_are_listed_too(self, tmp_path):
        """The row is assembled from the map KEY (the destination) and the
        2-element ``BindEntry`` leaf TOGETHER — the leaf carries no destination
        at all (R-6). Before 2026-08-08f this block emitted nothing on a real
        snapshot: the guard tested ``isinstance(leaf, Bind)``, which is False for
        every ``BindEntry``, so ``--effective`` listed no bindings whatsoever."""
        text = self._render(tmp_path)
        assert (
            "box.bindings.rw./home/agent = /boxes/mybox/home -> /home/agent  [Z,U]"
            in text
        )

    def test_the_agent_tier_is_listed_under_its_DISCRIMINATED_name(self, tmp_path):
        """An agent-scope arm prints as ``agent.<node>.bindings.*`` — the only
        agent key form the spec defines (§2d), and the reason ``_iter_agent_tiers``
        exists at all."""
        text = self._render(tmp_path)
        assert (
            "agent.claude.bindings.ro./home/agent/ref = /store/ref -> /home/agent/ref"
            in text
        )

    def test_the_bare_agent_form_is_never_printed(self, tmp_path):
        """``agent.<category>`` is not a key (spec §0), so a display that
        spelled it would teach a shape the keyspace forbids."""
        text = self._render(tmp_path)
        assert "agent.common" not in text
        assert "agent.bindings" not in text

    def test_a_collision_is_REPORTED_rather_than_raised(self, tmp_path):
        """This display is M-7's own detection recipe ("resolve the snapshot and
        look for duplicate dests"), so dying on the fault it exists to surface
        would be backwards."""
        import io

        buf = io.StringIO()
        rc = show_config(
            global_config_path=tmp_path / "kanibako_config.yaml",
            config_path=tmp_path / "settings.yaml",
            effective=True,
            file=buf,
            category_error=(
                "Two bindings target the same box destination '/home/agent':\n"
                "    system.bindings.ro.home  ->  /a\n"
            ),
        )
        assert rc == 0
        assert "Two bindings target the same box destination" in buf.getvalue()


# ---------------------------------------------------------------------------
# ``pref.*`` — the verb surface (spec §2h read verbs + write site)
# ---------------------------------------------------------------------------

class TestPrefWriteSite:
    """§2h — a pref may be WRITTEN at workset and box scope ONLY.
    *"This is what BOUNDS the recursion, so it is a hard rule."*"""

    @pytest.mark.parametrize("scope", [ConfigLevel.system, ConfigLevel.agent])
    def test_set_at_an_illegal_scope_is_refused(self, tmp_path, scope):
        """INVERT: remove ``_pref_write_site_error`` -> reddens."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.system.agent", "goose",
            config_path=f, system_settings_path=f, command_scope=scope,
        )
        assert msg.startswith("Error:")
        assert "workset or box settings file" in msg
        assert "bounds the resolution recursion" in msg
        assert not f.exists()  # nothing was written

    @pytest.mark.parametrize("scope", [ConfigLevel.system, ConfigLevel.agent])
    def test_reset_at_an_illegal_scope_is_refused(self, tmp_path, scope):
        f = tmp_path / "settings.yaml"
        msg = reset_config_value(
            "pref.system.agent",
            config_path=f, system_settings_path=f, command_scope=scope,
        )
        assert msg.startswith("Error:")
        assert "workset or box settings file" in msg

    def test_the_write_site_is_checked_before_the_target_filters(self, tmp_path):
        """A user at the system scope must be told the FILE is wrong regardless
        of the target's quality — fixing the target first would only surface
        this error afterwards."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.zippity.model", "x",
            config_path=f, system_settings_path=f,
            command_scope=ConfigLevel.system,
        )
        assert "workset or box settings file" in msg
        assert "not a valid agent" not in msg


class TestPrefSetGetReset:
    """§2h — set / get / reset all operate on prefs."""

    def test_set_writes_a_nested_table_and_get_returns_the_request(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.system.agent", "goose",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "Set pref.system.agent=goose"
        # NESTED on disk, never a dotted literal (settings_prefs D5).
        assert yaml.safe_load(f.read_text()) == {"pref": {"system": {"agent": "goose"}}}
        got = get_config_value(
            "pref.system.agent",
            global_config_path=tmp_path / "global.yaml", project_toml=f,
            command_scope=ConfigLevel.box,
        )
        assert got == "goose"

    def test_a_deep_agent_pref_round_trips(self, tmp_path):
        f = tmp_path / "settings.yaml"
        set_config_value(
            "pref.agent.claude.model", "opus",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert yaml.safe_load(f.read_text()) == {
            "pref": {"agent": {"claude": {"model": "opus"}}}
        }
        assert get_config_value(
            "pref.agent.claude.model",
            global_config_path=tmp_path / "g.yaml", project_toml=f,
            command_scope=ConfigLevel.workset,
        ) == "opus"

    def test_reset_clears_exactly_where_set_wrote(self, tmp_path):
        f = tmp_path / "settings.yaml"
        set_config_value(
            "pref.system.agent", "goose",
            config_path=f, command_scope=ConfigLevel.box,
        )
        msg = reset_config_value(
            "pref.system.agent", config_path=f, command_scope=ConfigLevel.box,
        )
        assert "Cleared pref.system.agent" == msg
        assert get_config_value(
            "pref.system.agent",
            global_config_path=tmp_path / "g.yaml", project_toml=f,
            command_scope=ConfigLevel.box,
        ) is None

    def test_reset_of_an_absent_pref_is_honest(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = reset_config_value(
            "pref.system.agent", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "No override for pref.system.agent"


class TestPrefTargetFiltersAtSetTime:
    """The SAME three filters the launch applies — so a request ``config set``
    accepts is one the launch honours, and one refused here can never become a
    stored request that fails every future launch."""

    def test_an_undeclared_target_is_refused(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.notakey", "x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:")
        assert "not a declared" in msg
        assert not f.exists()

    def test_a_non_allowlisted_target_is_refused(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.box.image", "x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert "not requestable" in msg
        assert "directly at the box scope" in msg

    def test_a_meta_target_is_refused(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.meta.box.path", "/x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert "categorical tier" in msg

    def test_a_new_name_in_a_parametric_family_is_accepted(self, tmp_path):
        """VALIDITY, not existence (§2h)."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set")


class TestPrefAccessEnumGuard:
    """The auth-critical ``access`` enum is enforced through the PREF spelling.

    ``pref.agent.<agent>.access=<tier>`` is exactly the command the RQ-2
    retired-key refusal PRESCRIBES to box/workset users, so it is the spelling
    they are most likely to type.  ``is_access_key`` answers False for it by
    design (it names TARGET keys), so the guard has to be applied at the TARGET
    — which is what ``_pref_value_error`` is for.

    Not a permissive hole (the launch resolver refuses the stored typo too), but
    without this the refusal arrives at every future LAUNCH of the box instead of
    at the write that caused it, and the config file records a value no validator
    ever accepted.
    """

    @pytest.mark.parametrize("bogus", ["fll", "FULL", "true", "", "yolo"])
    def test_an_off_enum_value_is_refused_at_set_time(self, tmp_path, bogus):
        """Mutation proof: drop the ``is_access_key(target)`` arm in
        ``_pref_value_error`` → the write succeeds and this reddens."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.access", bogus,
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), (bogus, msg)
        assert "restricted | editing | full" in msg
        # The message names the key the USER typed, not the extracted target.
        assert "pref.agent.claude.access" in msg
        assert not f.exists()  # the typo never lands in the file

    @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
    def test_every_declared_tier_is_accepted(self, tmp_path, tier):
        f = tmp_path / f"settings_{tier}.yaml"
        msg = set_config_value(
            "pref.agent.claude.access", tier,
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set"), (tier, msg)
        assert yaml.safe_load(f.read_text()) == {
            "pref": {"agent": {"claude": {"access": tier}}}
        }

    def test_the_persona_node_spelling_is_guarded_too(self, tmp_path):
        """A ``+``/``℘`` persona node is still an ``access`` target."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.nav+claude.access", "fll",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "restricted | editing | full" in msg

    def test_the_suppression_request_is_still_legal(self, tmp_path):
        """``--null`` is §2h's suppression channel and is legal at ANY leaf —
        the enum guard must not swallow it (present-``None`` is not a value)."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.access", None,
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set"), msg


class TestPrefIsKnownKey:
    def test_a_pref_key_is_recognised_as_a_key_not_a_project_name(self):
        """The positional-vs-key disambiguator — otherwise ``box config
        pref.system.agent`` is read as a PROJECT called ``pref.system.agent``."""
        assert is_known_key("pref.system.agent")
        assert is_known_key("pref.agent.claude.model")
        assert not is_known_key("someproject")

    def test_the_scope_env_arm_now_has_a_route(self, tmp_path):
        """⚑ FLIPPED by B9/R-39. ``<scope>.env.<VAR>`` is a DIFFERENT key from the
        bare ``env.<VAR>`` and used to have NO dispatch route — it was reported as
        an unknown key. It has one now, and it MUST: the R-39 refusal names it as
        the cure, and the same change retired the ``.env`` launch read that was
        the bare spelling's only delivery. Without this the cure would be a
        dead end and no config verb could set a container env var at all.
        """
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "box.env.FOO", "x", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "Set box.env.FOO=x", msg
        assert is_known_key("box.env.FOO")


class TestPrefShow:
    def test_show_lists_prefs_at_box_scope(self, tmp_path, capsys):
        """§2h — 'config show lists prefs'.

        ⚑ RE-POSED ON THE DESTINATION (2026-08-08c): the suppression request used
        to name the entry ``plugins``. It stayed green through the dest-keying
        flip because this listing is a plain nested walk that validates no key —
        green was not evidence the spelling existed.
        """
        f = tmp_path / "settings.yaml"
        f.write_text(yaml.safe_dump({
            "pref": {"system": {"agent": "goose"},
                     "agent": {"claude": {"common": {
                         "/home/agent/.claude/plugins": None,
                     }}}},
        }))
        show_config(
            global_config_path=tmp_path / "g.yaml", config_path=f, effective=False,
        )
        out = capsys.readouterr().out
        assert "pref.system.agent = goose" in out
        # A suppression REQUEST must be visible as such, not blank.
        assert (
            "pref.agent.claude.common./home/agent/.claude/plugins = null" in out
        )

    def test_effective_shows_request_and_result(self, tmp_path, capsys):
        """§2h — '--effective shows BOTH the request and the resulting value'."""
        from kanibako.settings.keystore import KeyStore

        snap = KeyStore({
            "pref": {"system": {"agent": "goose"},
                     "agent": {"claude": {"template": "@workset.template/x"}}},
            "system": {"agent": "goose"},
            "agent": {"claude": {"template": "/ws/tpl/x"}},
        })
        show_config(
            global_config_path=tmp_path / "g.yaml",
            config_path=tmp_path / "s.yaml",
            effective=True, category_snapshot=snap,
        )
        out = capsys.readouterr().out
        assert "pref.system.agent = goose" in out
        assert "-> system.agent = goose" in out
        # ⚑ The REQUEST is shown as WRITTEN while the RESULT is resolved — the
        # whole reason expand carries the pref subtree through unexpanded.
        assert "pref.agent.claude.template = @workset.template/x" in out
        assert "-> agent.claude.template = /ws/tpl/x" in out

    def test_effective_distinguishes_suppressed_from_unset(self, tmp_path, capsys):
        """⚑ RE-POSED ON THE DESTINATION (2026-08-08c). The pref used to suppress
        ``common.plugins`` — an entry NAME — and stayed green through the
        dest-keying flip because ``show_config`` renders whatever snapshot it is
        handed and validates no key. Green was not evidence the spelling existed.

        The per-ENTRY suppression itself is unchanged and still expressible: a
        present-None INSIDE the dest-keyed map is the per-entry omit, so the
        request is now keyed by the DESTINATION.
        """
        from kanibako.settings.keystore import KeyStore

        snap = KeyStore({
            "pref": {"agent": {"claude": {
                "common": {"/home/agent/.claude/plugins": None},
                "model": None,
            }}},
            # the common ENTRY was OMITTED by the merge (suppressed); model is a
            # scalar and was KEPT as None (unset).
            "agent": {"claude": {"common": KeyStore(), "model": None}},
        })
        show_config(
            global_config_path=tmp_path / "g.yaml",
            config_path=tmp_path / "s.yaml",
            effective=True, category_snapshot=snap,
        )
        out = capsys.readouterr().out
        assert "(omitted — the entry is suppressed; no mount." in out
        assert "(unset — the consumer applies its default)" in out
        # B-6: suppression has no verb of its own, so the message that reports a
        # suppression is where the user learns what UNDOES it — and WHERE, since
        # an edit at the wrong noun removes nothing.
        #
        # ⚑ THE CURE NAMES THE FILE, NOT A VERB (Jei, 2026-08-08e). This used to
        # pin ``reset pref.agent.claude.common./home/agent/.claude/plugins`` — a
        # command that does not work and is not going to, because addressing one
        # facet of a dest-keyed key individually is not a thing the CLI does. The
        # assertion was deliberate then and is wrong now; the negative below is
        # what keeps a non-working verb from creeping back in.
        assert "Undo by removing this entry from the 'pref:' table" in out
        assert "at the scope that set it" in out
        assert "reset pref." not in out

    def test_a_dotted_DESTINATION_is_not_shattered(self, tmp_path, capsys):
        """A dest-keyed entry that WORKED was reported as SUPPRESSED.

        The block used to walk the ``pref`` subtree with its own recursion, which
        descended PAST a terminal dest-keyed arm and then split the target on
        ``.`` to find the result — so ``…caches./home/agent/.cache/uv`` was cut at
        the dot inside ``.cache``, the lookup missed, and a present, mounted entry
        printed as "(omitted — the entry is suppressed; no mount…)" with an
        instruction to reset it. Built through the LAUNCH (files -> assemble ->
        merge -> expand), because a hand-built snapshot is exactly what hid this.

        INVERT: restore the private walk (or split the target on ``.``) -> the
        result half becomes the suppression message and this reddens.
        """
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        box_file = tmp_path / "box-settings.yaml"
        box_file.write_text(yaml.safe_dump({"pref": {"agent": {"claude": {
            "caches": {"~/.cache/uv": ["/host/caches/uv"]},
        }}}}))
        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=ResolveCtx(
                agent_name="claude", workset_name=None,
                host_home="/home/host", xdg={},
            ),
            system_path=None, agent_path=None,
            workset_path=None, box_path=box_file,
        )
        show_config(
            global_config_path=tmp_path / "g.yaml",
            config_path=tmp_path / "s.yaml",
            effective=True, category_snapshot=snap,
        )
        out = capsys.readouterr().out
        dest = "/home/agent/.cache/uv"
        assert f"pref.agent.claude.caches.{dest} = /host/caches/uv -> {dest}" in out
        assert (
            f"-> agent.claude.caches.{dest} = /host/caches/uv -> {dest}" in out
        )
        assert "suppressed" not in out


# ---------------------------------------------------------------------------
# Editor review — the pref VALUE surface (MUST-1) and the read verb (SHOULD-2)
# ---------------------------------------------------------------------------

class TestPrefValueValidation:
    """MUST-1(a) — the VALUE is validated against the TARGET, not the pref path."""

    def test_a_scalar_at_a_bind_shaped_target_is_refused(self, tmp_path):
        """Before this, the set was ACCEPTED and the LAUNCH died with 'category
        agent.claude.common is str, expected a Bind' — naming a key the user
        never wrote. INVERT: drop the shape check -> reddens.

        ⚑ THE TARGET IS THE BARE CATEGORY (2026-08-08c). ``common`` went TERMINAL
        and DEST-KEYED with the other three, so ``pref.agent.claude.common.x`` is
        no longer a key at all and would be refused a step EARLIER, by the target
        validity check — which would prove nothing about the VALUE-shape guard
        this test exists for. The bare category is the only ``common`` target a
        pref can name, and it is claimed by ``is_terminal_category_key``, the
        same term that claims a ``bindings`` arm in the twin below.
        """
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.common", "just-a-string",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:")
        assert "STRUCTURED" in msg
        assert "--null" in msg          # the suppression spelling is offered
        assert not f.exists()

    def test_a_scalar_at_a_TERMINAL_bind_arm_is_still_refused(self, tmp_path):
        """⚑ ``pref`` is NOT a retired route — a box may still REQUEST a change to
        an agent bind it can no longer set directly — so the shape check must keep
        firing on a bindings target. That key left ``BIND_KEY_RE`` when its CLI
        route died (R-9), which is exactly how this hole would open: the guard would
        stop recognising the very key that lost its direct route, and the LAUNCH
        would die naming a key the user never wrote.

        ⚑ REWRITTEN AT P4′. The target used to be
        ``pref.agent.claude.bindings.ro.launcher``; under R-5/R-10 that is no
        longer a KEY (see the twin below), so the bind-shaped target a pref can
        actually name is the BARE ARM — and none of the four pre-existing terms in
        ``_pref_value_error`` match it, because they all require a trailing
        ``.<name>``. This is the FIFTH term.

        ⚑ MUTATION: delete ``or is_terminal_category_key(target)``
        from ``_pref_value_error`` -> the scalar falls through to the E3 scalar
        probe, is ACCEPTED, the file is written, and both the ``STRUCTURED``
        assertion and ``not f.exists()`` die. Nothing else refuses this target:
        the four regex terms are pinned absent by ``test_..._is_not_a_key`` below.

        ⚑ The AGENT form is the one pinned here because it is the only bind target
        that REACHES this check: the §2h allowlist refuses ``pref.<scope>.…`` for a
        file scope several steps earlier ("only 'system.agent' and
        'agent.<agent>.<key>' may be requested")."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.bindings.ro", "just-a-string",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "STRUCTURED" in msg, msg
        # The skeleton must offer the MAP form — the arm is dest-keyed, so the
        # bare pair would be a shape that gets refused all over again.
        assert "{<box_dest>:" in msg, msg
        assert not f.exists()

    def test_the_none_of_the_four_regex_terms_match_the_bare_arm(self):
        """The discriminator for the test above: it is NOT green by accident.

        If any pre-existing term matched the bare arm, the fifth term would be
        dead weight and its mutation proof vacuous.
        """
        from kanibako.settings.settings_categories import (
            BIND_KEY_RE,
            MASK_KEY_RE,
            SCOPE_BIND_KEY_RE,
        )
        from kanibako.settings.config_keys import _is_agent_node_bind_key

        for target in ("agent.claude.bindings.ro", "box.bindings.rw"):
            assert BIND_KEY_RE.match(target) is None, target
            assert MASK_KEY_RE.match(target) is None, target
            assert SCOPE_BIND_KEY_RE.match(target) is None, target
            assert not _is_agent_node_bind_key(target), target

    def test_a_name_under_a_bind_arm_is_not_a_key_at_all(self, tmp_path):
        """R-5/R-10 — the retired spelling is refused EARLIER and for a DIFFERENT
        reason: it is not a key, so no value shape applies to it. The message must
        say so rather than talking about tuple shape."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.bindings.ro.launcher", "just-a-string",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "TERMINAL" in msg, msg
        assert "not a declared key" in msg, msg
        assert not f.exists()

    def test_an_unresolvable_scalar_value_is_refused(self, tmp_path):
        """The E3 probe must run AT THE TARGET: probing at the pref path is a
        no-op because expand skips the pref subtree, so `@typo` used to be
        accepted and then silently DROPPED the target at launch."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.template", "@nope.nothing/x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:")
        assert "does not resolve at its target" in msg
        assert "agent.claude.template" in msg

    def test_a_resolvable_scalar_value_is_accepted(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.template", "/plain/path",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set")

    def test_a_reserved_leaf_returns_an_error_and_never_raises(self, tmp_path):
        """set_config_value's contract is 'returns an error string, NEVER
        raises'. ReservedKeyError is a KeyError and used to escape.

        ⚑ THE SPECIMEN MOVED TO ``env`` (2026-08-08c). The row used to read
        ``pref.agent.claude.common.get``; ``common`` is TERMINAL and dest-keyed
        now, so ``get`` there is a DESTINATION — data inside the value — and the
        reserved-NAME rule never reaches it. ``env.<VAR>`` is a surviving
        name-keyed family, so a reserved leaf is still expressible and the
        never-raises contract is still exercised rather than proved vacuously.
        """
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.env.get", "x",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:") and "RESERVED" in msg

    def test_pref_system_agent_value_is_NOT_checked_against_installed_agents(
        self, tmp_path,
    ):
        """⚑ DELIBERATE (§2h): the agent test is 'is it a VALID
        agent' about the KEY's discriminator, not about this VALUE. An unknown
        name surfaces at agent RESOLUTION (P7), not here."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.system.agent", "zippity",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set")


class TestNullSpelling:
    """MUST-1(c) — the suppression request needs a working CLI spelling."""

    def test_parse_config_arg_null_flag_yields_a_None_value(self):
        action, key, value = parse_config_arg(
            "pref.agent.claude.common", set_null=True,
        )
        assert action == ConfigAction.set
        assert key == "pref.agent.claude.common"
        assert value is None

    def test_the_string_null_is_NOT_magic(self, tmp_path):
        """No pref-only dialect: `config set` stores scalars verbatim, so the
        literal text 'null' must stay a string wherever it is legal."""
        f = tmp_path / "settings.yaml"
        set_config_value(
            "pref.agent.claude.template", "null",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert yaml.safe_load(f.read_text())["pref"]["agent"]["claude"][
            "template"
        ] == "null"

    def test_null_writes_a_real_yaml_null(self, tmp_path):
        # ⚑ The suppression is spelled at the CATEGORY: ``common`` is TERMINAL and
        # dest-keyed (2026-08-08c), so there is no per-entry key to null.
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.common", None,
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "Set pref.agent.claude.common=null"
        doc = yaml.safe_load(f.read_text())
        assert doc["pref"]["agent"]["claude"]["common"] is None

    def test_no_write_mechanism_refuses_null_on_its_own_any_more(self, tmp_path):
        """⚑⚑ THE SPECIMEN RAN OUT. This row pinned the ONE mechanism that could not
        express a null: first the docker ``env.<VAR>`` arm ("the env file is a plain
        string store with no null value"), which went with the spelling itself (R-39,
        ``--null`` included); then the CATEGORY source-only repoint, which went with
        DS-BL1 = (a). **Both are now refused as RETIRED SPELLINGS in the verb
        preamble, before ``--null`` is looked at at all**, so the bespoke
        "not yet supported" guard is gone and every surviving write path carries
        ``None`` natively through a nested YAML write.

        What is pinned now is that absence, from both sides: the retired spelling
        gets the RETIREMENT (not a null-mechanism lecture), and a live nested target
        actually stores a null."""
        f = tmp_path / "settings.yaml"
        f.write_text(yaml.safe_dump({"box": {"common": {"plugins": ["/a", "~/b"]}}}))
        msg = set_config_value(
            "box.common.plugins", None,
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert "RETIRED" in msg, msg
        assert "not yet supported" not in msg, msg
        # ⚑ And the stored tuple is untouched: refused before any write.
        assert yaml.safe_load(f.read_text())["box"]["common"]["plugins"] == [
            "/a", "~/b",
        ]
        # The LIVE null route (the §2h request) still writes a real YAML null —
        # so "nothing refuses --null" is not "nothing accepts it".
        g = tmp_path / "pref.yaml"
        assert set_config_value(
            "pref.agent.claude.common", None,
            config_path=g, command_scope=ConfigLevel.box,
        ) == "Set pref.agent.claude.common=null"

    def test_null_on_the_retired_bare_env_gets_the_retirement_cure(self, tmp_path):
        """The R-39 refusal runs BEFORE the --null route guard, so a user who
        writes ``--null env.FOO`` is told the spelling is retired — not offered a
        null-mechanics explanation for a key that does not exist."""
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "env.FOO", None, config_path=f, env_path=tmp_path / "env",
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.FOO" in msg
        assert "no null value" not in msg


class TestPrefRefusalDoesNotPrescribeAMissingCommand:
    """A pref refusal appends "Set '<target>' directly at the <scope> scope instead"
    — TRUE for a scalar, and a LIE for a YAML-only target.

    ⚑⚑ THIS WAS ALREADY WRONG FOR THE ``bindings`` ARMS SINCE R-9; DS-BL1 = (a)
    widened it to ``caches``/``seeded``/``common``/``synced`` as well, which is what
    made it worth fixing rather than inheriting. Both message sites now ask ONE
    predicate (``config_keys.has_no_cli_write_route``): the write-site guard
    (``_pref_write_site_error``) and the allowlist reason
    (``settings_prefs.not_requestable_reason``).

    ⚑ ``masks`` is in scope for the same reason and was never settable at all.
    """

    @pytest.mark.parametrize("target", [
        "box.bindings.ro.vault", "box.common.x", "box.caches.x",
        "box.seeded.x", "box.synced.x", "box.masks",
    ])
    def test_no_direct_set_is_prescribed_for_a_yaml_only_target(
        self, tmp_path, target,
    ):
        f = tmp_path / "settings.yaml"
        # SITE 1 — the write-site guard (a pref at a scope that may not hold one).
        site1 = set_config_value(
            f"pref.{target}", "v",
            config_path=f, command_scope=ConfigLevel.system,
        )
        assert site1.startswith("Error:")
        assert "directly at the" not in site1, site1
        # SITE 2 — the §2h allowlist reason (a legal pref scope, illegal target).
        site2 = set_config_value(
            f"pref.{target}", "v",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert site2.startswith("Error:")
        assert "directly at the" not in site2, site2

    def test_a_scalar_target_still_gets_the_direct_set_hint(self, tmp_path):
        """The control — RED if the suppression over-reached and swallowed the hint
        wherever it is actually correct."""
        f = tmp_path / "settings.yaml"
        assert "Set 'agent.claude.model' directly at the agent scope" in (
            set_config_value(
                "pref.agent.claude.model", "opus",
                config_path=f, command_scope=ConfigLevel.system,
            )
        )
        assert "Set 'box.shell' directly at the box scope" in (
            set_config_value(
                "pref.box.shell", "/bin/zsh",
                config_path=f, command_scope=ConfigLevel.box,
            )
        )


class TestPrefGetRendersAllThreeEmptyIdioms:
    """SHOULD-2 — `get` is the verb §2h designates as 'returns the REQUEST'; it
    cannot conflate two of the three empty idioms."""

    def _get(self, tmp_path, f, key):
        return get_config_value(
            key, global_config_path=tmp_path / "g.yaml", project_toml=f,
            command_scope=ConfigLevel.box,
        )

    def test_present_none_terminal_empty_and_absent_are_distinguishable(
        self, tmp_path,
    ):
        # ⚑ RE-POSED AT THE TERMINAL KEY (2026-08-08c). The present-None specimen
        # used to be ``common: {plugins: None}``, read back as
        # ``pref.agent.claude.common.plugins`` — a spelling that is no longer a
        # key. It stayed green through the dest-keying flip because ``get`` walks
        # the stored dotted path and validates nothing; green was not evidence.
        # The whole-category suppression is the present-None a user can now write
        # at a key with no destination in its tail.
        f = tmp_path / "settings.yaml"
        f.write_text(yaml.safe_dump({"pref": {"agent": {"claude": {
            "common": None,
            "template": "",
        }}}}))
        assert self._get(tmp_path, f, "pref.agent.claude.common") == "null"
        assert self._get(tmp_path, f, "pref.agent.claude.template") == '""'
        assert self._get(tmp_path, f, "pref.agent.claude.model") is None


def test_a_reserved_leaf_on_a_category_key_still_returns_an_error_not_a_raise(
    tmp_path,
):
    """The H1 contract — ``set_config_value`` RETURNS an error string, NEVER raises —
    on the key that used to break it.

    ⚑ THE MECHANISM THAT ANSWERS CHANGED, AND SAYING SO IS THE POINT. The escape was
    a ``ReservedKeyError`` (a ``KeyError``) flying out of the E3 probe when the
    candidate edit wrote a RESERVED leaf name (``…common.get``) into a KeyStore; the
    DIRECT category route was the only door that reached that seam (the pref route is
    caught earlier by the key validator). DS-BL1 = (a) retired the direct category
    route, so the key is now refused BY NAME in the preamble and never reaches the
    probe at all — an earlier and better answer, but a DIFFERENT one, so this no
    longer pins the probe-seam catch.

    ⚑ The catch itself survives in ``_category_set_lookups`` for the scalar/pref
    probe. It is currently unreachable through any spelling this suite can name; do
    not delete it on the strength of that without measuring the pref path.
    """
    f = tmp_path / "settings.yaml"
    msg = set_config_value(
        "agent.claude.common.get", "/x",
        config_path=f, command_scope=ConfigLevel.system,
    )
    assert msg.startswith("Error:")           # returned, not raised (H1)
    assert "RETIRED" in msg, msg
    assert not f.exists()


# ---------------------------------------------------------------------------
# SYSTEM-scope file routing — get / set / reset must all name ONE file, and that
# file must be the one the LAUNCH cascade reads (@config.settings), never the
# kanibako_config.yaml CONFIG file.  Three branches were holdouts (F1/F2/F3).
# ---------------------------------------------------------------------------

def _system_scope_files(tmp_path):
    """The two SYSTEM-scope files, kept DISTINCT so a routing slip is visible:
    the bootstrap CONFIG file and the system SETTINGS file."""
    cf = tmp_path / "kanibako_config.yaml"
    ssp = tmp_path / "global" / "settings.yaml"
    ssp.parent.mkdir(parents=True, exist_ok=True)
    return cf, ssp


class TestSystemScopeSecretPathSymmetry:
    """F1 — ``<scope>.secret_path.<VAR>`` at the SYSTEM scope.

    ``set`` wrote (and ``reset`` removed) the system SETTINGS file, but ``get``
    read ``project_toml`` — which the system handler never threads — so every
    system-scope read of a secret pointer said "(not set)" no matter what had
    been set.  All three verbs now read/write the NOUN's settings file.
    """

    def _get(self, cf, ssp):
        return get_config_value(
            "system.secret_path.FOO", global_config_path=cf,
            system_settings_path=ssp, command_scope=ConfigLevel.system,
        )

    def test_set_get_reset_name_one_file(self, tmp_path):
        cf, ssp = _system_scope_files(tmp_path)
        msg = set_config_value(
            "system.secret_path.FOO", "/t/tok",
            config_path=cf, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        assert not msg.startswith("Error:"), msg
        # SET → the settings file; the CONFIG file is never created.
        assert load_doc(ssp)["system"]["secret_path"]["FOO"] == "/t/tok"
        assert not cf.exists()
        # GET → the same file (blind before the fix).
        assert self._get(cf, ssp) == "/t/tok"
        # RESET → clears it, and GET agrees.
        msg = reset_config_value(
            "system.secret_path.FOO", config_path=cf,
            system_settings_path=ssp, command_scope=ConfigLevel.system,
        )
        assert msg.startswith("Cleared"), msg
        assert self._get(cf, ssp) is None

    def test_a_stale_config_file_value_is_not_the_noun_store(self, tmp_path):
        """The CONFIG file is not where a secret pointer lives, so a hand-placed
        value there must NOT be reported — the launch would never read it."""
        cf, ssp = _system_scope_files(tmp_path)
        dump_doc(cf, {"system": {"secret_path": {"FOO": "/stale"}}})
        assert self._get(cf, ssp) is None

    def test_box_scope_secret_get_is_unchanged(self, tmp_path):
        """The fix reads ``noun_file``, which falls back to ``project_toml`` when
        no system settings file is threaded — box/workset behavior is identical."""
        f = tmp_path / "settings.yaml"
        set_config_value(
            "box.secret_path.TOKEN", "/t/box",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert get_config_value(
            "box.secret_path.TOKEN", global_config_path=tmp_path / "g.yaml",
            project_toml=f, command_scope=ConfigLevel.box,
        ) == "/t/box"


class TestSystemScopeCategoryFileRouting:
    """F2 — WHICH FILE a SYSTEM-scope category key lives in.

    The bug: ``set`` repointed the tuple in the kanibako_config.yaml CONFIG file while
    ``get`` read the system SETTINGS file, so a successful set read back as
    "(not set)"; and because ``reset --all`` sweeps the SETTINGS file's scope tables,
    the config-file write SURVIVED ``--all``.

    ⚑⚑ THE SET HALF IS GONE, NOT FIXED-AND-KEPT. DS-BL1 = (a) retired the category
    write route, so there is no ``set`` left to agree with ``get`` — the asymmetry
    died with one of its two sides. What still matters, and is pinned here, is that
    the surviving READ names the file the LAUNCH cascade's system tier actually reads
    (``@config.settings``), never the bootstrap CONFIG file — because hand-editing
    that settings file IS the cure every refusal now prescribes, and a get that read
    the wrong file would make the cure unverifiable.

    ⚑ The VEHICLE stays ``synced`` (also CONCRETE — no declaration root), unchanged.
    """

    KEY = "system.synced.helper"
    SEEDED = ["/old/src", "/home/agent/helper", "ro"]

    def _seed(self, path):
        dump_doc(path, {"system": {"synced": {"helper": list(self.SEEDED)}}})

    def _get(self, cf, ssp):
        return get_config_value(
            self.KEY, global_config_path=cf, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )

    def test_get_reads_the_settings_file_the_launch_reads(self, tmp_path):
        cf, ssp = _system_scope_files(tmp_path)
        self._seed(ssp)
        assert self._get(cf, ssp) == str(self.SEEDED)
        # The CONFIG file is not consulted, and not created.
        assert not cf.exists()

    def test_a_tuple_only_in_the_config_file_is_not_read(self, tmp_path):
        """The control that makes the row above mean something: the SAME key
        hand-written into kanibako_config.yaml instead reads back "(not set)",
        because that file is in NO cascade level. RED if the read ever falls back
        to the config file "to be helpful"."""
        cf, ssp = _system_scope_files(tmp_path)
        self._seed(cf)
        assert self._get(cf, ssp) is None
        assert not ssp.exists()

    def test_the_write_verbs_refuse_and_change_neither_file(self, tmp_path):
        """Both verbs, both files: the refusal is by name and nothing is touched."""
        cf, ssp = _system_scope_files(tmp_path)
        self._seed(ssp)
        for msg in (
            set_config_value(
                self.KEY, str(tmp_path), config_path=cf, system_settings_path=ssp,
                is_system=True, command_scope=ConfigLevel.system,
                cascade_system_path=ssp,
            ),
            reset_config_value(
                self.KEY, config_path=cf, system_settings_path=ssp,
                command_scope=ConfigLevel.system, cascade_system_path=ssp,
            ),
        ):
            assert msg.startswith("Error:") and "RETIRED" in msg, msg
        assert load_doc(ssp)["system"]["synced"]["helper"] == self.SEEDED
        assert not cf.exists()

    def test_reset_all_still_sweeps_the_scope_table(self, tmp_path):
        """``reset --all`` clears the SETTINGS file's nested scope tables wholesale,
        so a hand-authored category tuple goes with them.

        ⚑ DELIBERATE ASYMMETRY WITH THE PER-KEY RESET ABOVE, and it is pre-existing
        rather than introduced here: ``--all`` is a table sweep ("remove every
        override in this file"), not a per-key write, and it does not consult the
        per-key retirement doors. Pinned so the difference is a recorded fact rather
        than a surprise."""
        cf, ssp = _system_scope_files(tmp_path)
        self._seed(ssp)
        reset_all(
            config_path=cf, force=True, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        assert "system" not in load_doc(ssp)
        assert self._get(cf, ssp) is None

    def test_box_scope_read_routing_is_unchanged(self, tmp_path):
        """``settings_dest`` IS ``config_path`` when no system settings file is
        threaded, so a box/workset read is byte-identical to before — and the write
        verbs refuse there too."""
        f = tmp_path / "settings.yaml"
        dump_doc(f, {"box": {"caches": {"x": ["/old", "/home/agent/.cache/x"]}}})
        assert get_config_value(
            "box.caches.x", global_config_path=f, project_toml=f,
            command_scope=ConfigLevel.box,
        ) == str(["/old", "/home/agent/.cache/x"])
        assert reset_config_value(
            "box.caches.x", config_path=f, command_scope=ConfigLevel.box,
        ).startswith("Error:")
        assert load_doc(f)["box"]["caches"]["x"] == ["/old", "/home/agent/.cache/x"]


class TestCategorySetAgentNodeGuardsSuperseded:
    """F3's inline agent-node guards inside ``_set_category_value`` are GONE, and
    what replaced them is STRICTLY EARLIER.

    They enforced the pair every per-node route enforces (the reserved any-agent
    tier is not a persona node; a malformed ref is not a node at all) for the ONE
    family that reached that function with a node to check —
    ``agent.<node>.bindings.*``. R-9 retired that write route, so the refusal now
    happens in the verb PREAMBLE, before a node is parsed at all. Nothing is
    written for EITHER bad node, which is the property the guards existed to
    protect; the message just names the retirement rather than the node.

    ⚑ The guard pair itself is NOT deleted — ``config_dest.check_agent_node`` still
    backs the persona and per-node-secret routes.
    """

    def _agents(self, tmp_path):
        return tmp_path / "agents"

    def _set(self, tmp_path, key):
        """Set *key* and expect the RETIREMENT refusal.

        ⚑ This used to seed the key into the set-time cascade via a
        ``default_categories`` floor entry, so that a must-exist complaint could not
        be what refused — isolating the retirement as the only thing left that
        could. That parameter is gone with the set-time floor thread, and the
        isolation is now STRUCTURAL rather than arranged: the retirement fires in
        the verb PREAMBLE, before any cascade is assembled, so no must-exist check
        runs at all. The tests below still assert ``RETIRED`` by name, which is what
        distinguishes this refusal from any other (see the M1 mutation note on
        ``TestScopeBindRouteRetired.test_reset_is_refused_symmetrically``).
        """
        return set_config_value(
            key, "/x", config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.system, agents_root=self._agents(tmp_path),
        )

    @pytest.mark.parametrize(
        "key",
        [
            "agent.default.bindings.ro.share",   # the RESERVED any-agent tier
            "agent.a+b+c.bindings.ro.share",     # a MALFORMED node ref
            "agent.navigator+claude.bindings.ro.share",   # a GOOD node
        ],
    )
    def test_every_node_shape_is_refused_by_the_retirement_and_writes_nothing(
        self, tmp_path, key,
    ):
        msg = self._set(tmp_path, key)
        assert msg.startswith("Error:"), msg
        assert "RETIRED" in msg, msg
        assert not (tmp_path / "settings.yaml").exists()

    @pytest.mark.parametrize(
        "key",
        ["agent.default.bindings.ro.share", "agent.a+b+c.bindings.ro.share"],
    )
    def test_get_and_reset_still_refuse_the_same_two_nodes(self, tmp_path, key):
        """The read half is unchanged: a get of an unroutable node is still
        ``None``, and a reset is still an error (now the retirement's)."""
        f = tmp_path / "settings.yaml"
        assert get_config_value(
            key, global_config_path=f, system_settings_path=f,
            agents_root=self._agents(tmp_path),
        ) is None
        assert reset_config_value(
            key, config_path=f, command_scope=ConfigLevel.system,
            agents_root=self._agents(tmp_path),
        ).startswith("Error:")

    def test_the_guard_pair_still_backs_the_sibling_secret_route(self, tmp_path):
        """RED if the guards were deleted rather than out-ordered: the per-node
        SECRET route is still live and still enforces both."""
        from kanibako.settings.config_dest import check_agent_node

        assert check_agent_node("default").reason == "reserved"
        assert check_agent_node("a+b+c").reason == "malformed"
        assert check_agent_node("navigator℘claude") is None
