"""Tests for the unified config interface engine."""

from __future__ import annotations

from kanibako.config_io import dump_doc, load_doc
from kanibako.config_interface import (
    ConfigAction,
    ConfigLevel,
    is_known_key,
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
        assert is_known_key("image") is True
        assert is_known_key("model") is True
        assert is_known_key("group_auth") is True

    def test_known_dotted_key(self):
        assert is_known_key("vault.enabled") is True
        assert is_known_key("system.data") is True
        assert is_known_key("system.agents") is True
        assert is_known_key("system.default_agent") is True

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

    def test_box_bootstrap_program_is_known(self):
        """box.bootstrap_program must be a known GET key (set/--reset bypass it)."""
        assert is_known_key("box.bootstrap_program") is True

    def test_dynamic_env_prefix(self):
        assert is_known_key("env.MY_VAR") is True

    def test_dynamic_resource_prefix(self):
        assert is_known_key("resource.plugins") is True

    def test_unknown_key(self):
        assert is_known_key("my-project") is False
        assert is_known_key("foobar") is False


# ---------------------------------------------------------------------------
# get / set / reset for regular config keys
# ---------------------------------------------------------------------------

class TestRegularConfigKeys:
    """Tests for regular (KanibakoConfig) config keys."""

    def test_get_default_image(self, tmp_path):
        """Reading image with no overrides returns the global default."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text('box:\n  image: "my-image:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        val = get_config_value(
            "image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "my-image:latest"

    def test_set_and_get_image(self, tmp_path):
        """Setting a config key writes it and subsequent get returns it."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text('box:\n  image: "default:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        msg = set_config_value(
            "image", "custom:v2",
            config_path=project_toml,
        )
        assert "Set" in msg
        assert "custom:v2" in msg

        val = get_config_value(
            "image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "custom:v2"

    def test_reset_image(self, tmp_path):
        """Resetting a key removes the project-level override."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text('box:\n  image: "default:latest"\n')
        project_toml = tmp_path / "settings.yaml"

        set_config_value("image", "custom:v2", config_path=project_toml)
        msg = reset_config_value("image", config_path=project_toml)
        assert "Reset" in msg

    def test_reset_nonexistent_key(self, tmp_path):
        """Resetting a key that has no override returns informative message."""
        project_toml = tmp_path / "settings.yaml"
        msg = reset_config_value("image", config_path=project_toml)
        assert "No override" in msg

    def test_get_box_shell_unset_returns_none(self, tmp_path):
        """box.shell defaults to empty → get returns None (rendered as not set)."""
        global_cfg = tmp_path / "kanibako.yaml"
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
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        set_config_value("box.shell", "/bin/zsh", config_path=project_toml)
        val = get_config_value(
            "box.shell",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "/bin/zsh"

    def test_get_box_bootstrap_program_unset_returns_default(self, tmp_path):
        """box.bootstrap_program is unset → get returns the built-in default."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        # No "unknown config key" error; falls back to the merged default.
        val = get_config_value(
            "box.bootstrap_program",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "tmux"

    def test_set_and_get_box_bootstrap_program(self, tmp_path):
        """Setting box.bootstrap_program and reading it back returns the value."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("box:\n  image: \"default:latest\"\n")
        project_toml = tmp_path / "settings.yaml"

        set_config_value(
            "box.bootstrap_program", "screen", config_path=project_toml
        )
        val = get_config_value(
            "box.bootstrap_program",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "screen"


# ---------------------------------------------------------------------------
# env.* keys
# ---------------------------------------------------------------------------

class TestEnvKeys:
    """Tests for env.* config keys."""

    def test_set_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.MY_VAR", "hello",
            config_path=tmp_path / "settings.yaml",
            env_path=env_path,
        )
        assert "Set MY_VAR=hello" in msg
        assert env_path.read_text().strip() == "MY_VAR=hello"

    def test_get_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        val = get_config_value(
            "env.FOO",
            global_config_path=tmp_path / "kanibako.yaml",
            env_project=env_path,
        )
        assert val == "bar"

    def test_get_env_var_not_set(self, tmp_path):
        val = get_config_value(
            "env.MISSING",
            global_config_path=tmp_path / "kanibako.yaml",
        )
        assert val is None

    def test_reset_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        msg = reset_config_value("env.FOO", config_path=tmp_path / "p.yaml", env_path=env_path)
        assert "Unset" in msg

    def test_reset_env_var_missing(self, tmp_path):
        msg = reset_config_value(
            "env.MISSING",
            config_path=tmp_path / "p.yaml",
            env_path=tmp_path / "env",
        )
        assert "No override" in msg


# ---------------------------------------------------------------------------
# resource.* keys
# ---------------------------------------------------------------------------

class TestResourceKeys:
    """Tests for resource.* config keys."""

    def test_set_resource(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "resource.plugins", "/my/plugins",
            config_path=project_toml,
        )
        assert "Set resource.plugins=/my/plugins" in msg

        # Verify YAML structure
        data = load_doc(project_toml)
        assert data["resource_overrides"]["plugins"] == "/my/plugins"

    def test_get_resource(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"resource_overrides": {"plugins": "/a/b"}})

        val = get_config_value(
            "resource.plugins",
            global_config_path=tmp_path / "kanibako.yaml",
            project_toml=project_toml,
        )
        assert val == "/a/b"

    def test_reset_resource(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"resource_overrides": {"plugins": "/a/b"}})

        msg = reset_config_value("resource.plugins", config_path=project_toml)
        assert "Reset" in msg

        data = load_doc(project_toml)
        assert "resource_overrides" not in data  # section removed when empty


# ---------------------------------------------------------------------------
# Target settings (model, start_mode, autonomous)
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
            global_config_path=tmp_path / "kanibako.yaml",
            project_toml=project_toml,
        )
        assert val == "opus"

    def test_reset_model(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"model": "opus"}}})

        msg = reset_config_value("model", config_path=project_toml)
        assert "Reset model" in msg


# ---------------------------------------------------------------------------
# show_config
# ---------------------------------------------------------------------------

class TestShowConfig:
    """Tests for the show_config display function."""

    def test_show_no_overrides(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
        )
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.yaml"
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
        global_cfg = tmp_path / "kanibako.yaml"
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
        global_cfg = tmp_path / "kanibako.yaml"
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
        global_cfg = tmp_path / "kanibako.yaml"
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
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"
        project_toml.write_text('agent:\n  default:\n    model: "sonnet"\n')

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            agent_state={"model": "sonnet", "start_mode": "default"},
        )
        captured = capsys.readouterr()
        # model is set at box level -> marked override
        assert "model = sonnet (override)" in captured.out
        # start_mode comes from a lower level -> no marker
        assert "start_mode = default\n" in captured.out
        assert "start_mode = default (override)" not in captured.out

    def test_effective_env_resolved_used_when_supplied(self, tmp_path, capsys):
        """env_resolved is the source dict for the env section when given."""
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
            env_resolved={"RESOLVED_VAR": "yes"},
        )
        captured = capsys.readouterr()
        assert "env.RESOLVED_VAR = yes" in captured.out


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

    def test_set_group_auth_no_crash(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        # Must not raise; lands in [project] as a real bool.
        msg = set_config_value("group_auth", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["project"]["group_auth"] is False

    def test_set_allow_helpers_no_crash(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("allow_helpers", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        # Top-level scalar field, stored as a real bool.
        assert data["allow_helpers"] is False

    def test_set_vault_enabled_lands_in_real_location(self, tmp_path):
        """vault.enabled aliases to its real stored key enable_vault (H1 note)."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("vault.enabled", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["project"]["enable_vault"] is False

    def test_set_mode_no_crash(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        assert set_config_value("mode", "primary", config_path=project_toml).startswith("Set")
        data = load_doc(project_toml)
        assert data["project"]["mode"] == "primary"

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
        global_cfg = tmp_path / "kanibako.yaml"
        global_cfg.write_text("")
        project_toml = tmp_path / "settings.yaml"
        for key, val in [
            ("group_auth", "false"),
            ("mode", "default"),
            ("box.image", "custom:latest"),
            ("vault.ro", "/ro"),
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
        from kanibako.config import load_config

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

    def test_set_allow_helpers_false_loads_as_real_bool(self, tmp_path):
        from kanibako.config import load_config

        project_toml = tmp_path / "settings.yaml"
        set_config_value("allow_helpers", "false", config_path=project_toml)
        cfg = load_config(project_toml)
        assert cfg.allow_helpers is False
        assert not cfg.allow_helpers

    def test_bool_key_rejects_garbage(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("box.share_images", "maybe", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "boolean" in msg


class TestSystemDefaultAgent:
    """system.default_agent: the lone system.*-named SETTING (D-M2).

    It is behavior, not a config path, so it lands in the SYSTEM settings tier
    (the agent.default table) — NOT the [system] config table — and is read
    back by config.read_default_agent.
    """

    def test_set_lands_in_agent_default_not_system_table(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        msg = set_config_value("system.default_agent", "claude", config_path=cf)
        assert msg == "Set system.default_agent=claude"

        data = load_doc(cf)
        # Stored in the settings tier (agent.default), NOT the config [system].
        assert data["agent"]["default"]["default_agent"] == "claude"
        assert "system" not in data

    def test_round_trips_through_real_settings_tier(self, tmp_path):
        from kanibako.config import read_default_agent

        cf = tmp_path / "kanibako.yaml"
        set_config_value("system.default_agent", "goose", config_path=cf)

        # The interface getter and the launch-time reader agree.
        assert get_config_value("system.default_agent", global_config_path=cf) == "goose"
        assert read_default_agent(cf) == "goose"

    def test_get_unset_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        cf.touch()
        assert get_config_value("system.default_agent", global_config_path=cf) is None

    def test_reset_removes_setting(self, tmp_path):
        from kanibako.config import read_default_agent

        cf = tmp_path / "kanibako.yaml"
        set_config_value("system.default_agent", "claude", config_path=cf)
        msg = reset_config_value("system.default_agent", config_path=cf)
        assert msg == "Reset system.default_agent"
        assert read_default_agent(cf) is None

    def test_not_confused_with_system_path_keys(self, tmp_path):
        """system.data (config) is FILE-ONLY (refused) but default_agent (a
        SETTING) is still CLI-settable — the two are not conflated (W4)."""
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako.yaml"
        # system.data is structural config: refused at the CLI with a file pointer.
        msg = set_config_value("system.data", "/custom/data", config_path=cf)
        assert msg.startswith("Error:")
        assert "structural config key" in msg
        # The programmatic writer (what setup uses) still lands it in [system].
        _write_nested_toml_key(cf, ("system",), "data", "/custom/data")
        # system.default_agent (a SETTING) is still settable via the CLI.
        set_config_value("system.default_agent", "claude", config_path=cf)

        data = load_doc(cf)
        assert data["system"]["data"] == "/custom/data"
        assert data["agent"]["default"]["default_agent"] == "claude"


class TestSystemConfigFileOnly:
    """W4: system.* CONFIG keys are FILE-ONLY (CLI set/reset refused).

    config == system.* (structural layout); the CLI reads/shows them but
    refuses to write, pointing at the config file.  system.default_agent (a
    SETTING) is the lone system.*-named exception and stays CLI-settable.
    """

    def test_set_system_config_key_refused(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        for key in ("system.data", "system.agents", "system.channels.commons"):
            msg = set_config_value(key, "x", config_path=cf)
            assert msg.startswith("Error:"), key
            assert "structural config key" in msg
            assert str(cf) in msg  # pointer to the file
        # Nothing was written to the file.
        assert not cf.exists() or "system" not in load_doc(cf)

    def test_reset_system_config_key_refused(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        msg = reset_config_value("system.data", config_path=cf)
        assert msg.startswith("Error:")
        assert "structural config key" in msg

    def test_get_system_config_key_still_reads(self, tmp_path):
        """Reads/shows are unaffected — only writes are refused."""
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako.yaml"
        _write_nested_toml_key(cf, ("system",), "data", "/custom/data")
        assert get_config_value("system.data", global_config_path=cf) == "/custom/data"

    def test_default_agent_setting_still_settable(self, tmp_path):
        """system.default_agent is a SETTING, not config — still CLI-settable."""
        cf = tmp_path / "kanibako.yaml"
        assert set_config_value(
            "system.default_agent", "goose", config_path=cf,
        ).startswith("Set")


class TestResolveBoxAgent:
    """config.resolve_box_agent — the box.agent → system.default_agent chain."""

    def test_explicit_box_agent_wins(self, tmp_path):
        from kanibako.config import resolve_box_agent

        cf = tmp_path / "kanibako.yaml"
        set_config_value("system.default_agent", "goose", config_path=cf)
        # Explicit box.agent overrides system.default_agent.
        assert resolve_box_agent("claude", cf) == "claude"

    def test_falls_back_to_system_default(self, tmp_path):
        from kanibako.config import resolve_box_agent

        cf = tmp_path / "kanibako.yaml"
        set_config_value("system.default_agent", "claude", config_path=cf)
        # box.agent empty -> system.default_agent.
        assert resolve_box_agent("", cf) == "claude"
        assert resolve_box_agent(None, cf) == "claude"

    def test_both_unset_returns_none(self, tmp_path):
        from kanibako.config import resolve_box_agent

        cf = tmp_path / "kanibako.yaml"
        cf.touch()
        # Neither set -> None -> today's auto-detect (no regression).
        assert resolve_box_agent("", cf) is None
        assert resolve_box_agent(None, cf) is None

    def test_no_config_file_returns_none(self, tmp_path):
        from kanibako.config import resolve_box_agent

        cf = tmp_path / "missing.yaml"
        assert resolve_box_agent("", cf) is None
        assert resolve_box_agent(None, None) is None
