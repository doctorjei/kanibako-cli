"""Tests for the unified config interface engine."""

from __future__ import annotations

import tomllib

from kanibako.config_interface import (
    ConfigAction,
    ConfigLevel,
    _serialize_toml,
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
        assert is_known_key("auth") is True

    def test_known_dotted_key(self):
        assert is_known_key("vault.enabled") is True
        assert is_known_key("paths.data_path") is True

    def test_dynamic_env_prefix(self):
        assert is_known_key("env.MY_VAR") is True

    def test_dynamic_resource_prefix(self):
        assert is_known_key("resource.plugins") is True

    def test_dynamic_shared_prefix(self):
        assert is_known_key("shared.cargo-git") is True

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
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[container]\nimage = "my-image:latest"\n')
        project_toml = tmp_path / "project.toml"

        val = get_config_value(
            "image",
            global_config_path=global_cfg,
            project_toml=project_toml,
        )
        assert val == "my-image:latest"

    def test_set_and_get_image(self, tmp_path):
        """Setting a config key writes it and subsequent get returns it."""
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[container]\nimage = "default:latest"\n')
        project_toml = tmp_path / "project.toml"

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
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[container]\nimage = "default:latest"\n')
        project_toml = tmp_path / "project.toml"

        set_config_value("image", "custom:v2", config_path=project_toml)
        msg = reset_config_value("image", config_path=project_toml)
        assert "Reset" in msg

    def test_reset_nonexistent_key(self, tmp_path):
        """Resetting a key that has no override returns informative message."""
        project_toml = tmp_path / "project.toml"
        msg = reset_config_value("image", config_path=project_toml)
        assert "No override" in msg


# ---------------------------------------------------------------------------
# env.* keys
# ---------------------------------------------------------------------------

class TestEnvKeys:
    """Tests for env.* config keys."""

    def test_set_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.MY_VAR", "hello",
            config_path=tmp_path / "project.toml",
            env_path=env_path,
        )
        assert "Set MY_VAR=hello" in msg
        assert env_path.read_text().strip() == "MY_VAR=hello"

    def test_get_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        val = get_config_value(
            "env.FOO",
            global_config_path=tmp_path / "kanibako.toml",
            env_project=env_path,
        )
        assert val == "bar"

    def test_get_env_var_not_set(self, tmp_path):
        val = get_config_value(
            "env.MISSING",
            global_config_path=tmp_path / "kanibako.toml",
        )
        assert val is None

    def test_reset_env_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")
        msg = reset_config_value("env.FOO", config_path=tmp_path / "p.toml", env_path=env_path)
        assert "Unset" in msg

    def test_reset_env_var_missing(self, tmp_path):
        msg = reset_config_value(
            "env.MISSING",
            config_path=tmp_path / "p.toml",
            env_path=tmp_path / "env",
        )
        assert "No override" in msg


# ---------------------------------------------------------------------------
# resource.* keys
# ---------------------------------------------------------------------------

class TestResourceKeys:
    """Tests for resource.* config keys."""

    def test_set_resource(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        msg = set_config_value(
            "resource.plugins", "/my/plugins",
            config_path=project_toml,
        )
        assert "Set resource.plugins=/my/plugins" in msg

        # Verify TOML structure
        with open(project_toml, "rb") as f:
            data = tomllib.load(f)
        assert data["resource_overrides"]["plugins"] == "/my/plugins"

    def test_get_resource(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        project_toml.write_text(_serialize_toml({"resource_overrides": {"plugins": "/a/b"}}))

        val = get_config_value(
            "resource.plugins",
            global_config_path=tmp_path / "kanibako.toml",
            project_toml=project_toml,
        )
        assert val == "/a/b"

    def test_reset_resource(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        project_toml.write_text(_serialize_toml({"resource_overrides": {"plugins": "/a/b"}}))

        msg = reset_config_value("resource.plugins", config_path=project_toml)
        assert "Reset" in msg

        with open(project_toml, "rb") as f:
            data = tomllib.load(f)
        assert "resource_overrides" not in data  # section removed when empty


# ---------------------------------------------------------------------------
# shared.* keys
# ---------------------------------------------------------------------------

class TestSharedKeys:
    """Tests for shared.* config keys."""

    def test_set_shared(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        msg = set_config_value(
            "shared.cargo-git", ".cargo/git",
            config_path=project_toml,
        )
        assert "Set shared.cargo-git" in msg

    def test_get_shared(self, tmp_path):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[shared]\ncargo-git = ".cargo/git"\n')

        val = get_config_value(
            "shared.cargo-git",
            global_config_path=global_cfg,
        )
        assert val == ".cargo/git"


# ---------------------------------------------------------------------------
# Target settings (model, start_mode, autonomous)
# ---------------------------------------------------------------------------

class TestTargetSettings:
    """Tests for target settings keys."""

    def test_set_model(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        msg = set_config_value("model", "sonnet", config_path=project_toml)
        assert "Set model=sonnet" in msg

        with open(project_toml, "rb") as f:
            data = tomllib.load(f)
        assert data["target_settings"]["model"] == "sonnet"

    def test_get_model(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        project_toml.write_text(_serialize_toml({"target_settings": {"model": "opus"}}))

        val = get_config_value(
            "model",
            global_config_path=tmp_path / "kanibako.toml",
            project_toml=project_toml,
        )
        assert val == "opus"

    def test_reset_model(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        project_toml.write_text(_serialize_toml({"target_settings": {"model": "opus"}}))

        msg = reset_config_value("model", config_path=project_toml)
        assert "Reset model" in msg


# ---------------------------------------------------------------------------
# show_config
# ---------------------------------------------------------------------------

class TestShowConfig:
    """Tests for the show_config display function."""

    def test_show_no_overrides(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text("")
        project_toml = tmp_path / "project.toml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
        )
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[container]\nimage = "my:img"\n')
        project_toml = tmp_path / "project.toml"

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
            effective=True,
        )
        captured = capsys.readouterr()
        assert "container_image" in captured.out
        assert "my:img" in captured.out

    def test_show_with_override(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.toml"
        global_cfg.write_text('[container]\nimage = "default"\n')
        project_toml = tmp_path / "project.toml"
        project_toml.write_text('[container]\nimage = "custom"\n')

        show_config(
            global_config_path=global_cfg,
            config_path=project_toml,
        )
        captured = capsys.readouterr()
        assert "container_image" in captured.out
        assert "custom" in captured.out


# ---------------------------------------------------------------------------
# reset_all
# ---------------------------------------------------------------------------

class TestResetAll:
    """Tests for the reset-all operation."""

    def test_reset_all_with_force(self, tmp_path):
        project_toml = tmp_path / "project.toml"
        project_toml.write_text('[container]\nimage = "custom"\n')
        env_path = tmp_path / "env"
        env_path.write_text("FOO=bar\n")

        msg = reset_all(config_path=project_toml, env_path=env_path, force=True)
        assert "Reset" in msg

    def test_reset_all_nothing_to_reset(self, tmp_path):
        project_toml = tmp_path / "project.toml"
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
