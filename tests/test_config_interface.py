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
        assert is_known_key("system.default_agent") is True
        # P3: the per-workset registry key is a known settable key.
        assert is_known_key("workset.registry") is True
        # P6a: the workset LAYOUT anchors are now known settable keys.
        for k in (
            "workset.auth.path",
            "workset.boxes",
            "workset.vault_ro",
            "workset.vault_rw",
            "workset.logs",
            "workset.channels.commons",
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
        """bootstrap is an agent-scope behavior key — a known GET key (spec §2d L579).

        The old box-scope ``box.bootstrap_program`` is RETIRED (relocated to the
        agent scope, 1.7.0-rc clean break — no alias)."""
        assert is_known_key("bootstrap") is True
        # Per-agent override form (persona key), mirroring model/auto_approve.
        assert is_known_key("agent.claude.bootstrap") is True
        # The retired box-scope key is no longer known.
        assert is_known_key("box.bootstrap_program") is False

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
        LAUNCH + ``--effective`` (spec §2d L579 agent.default.bootstrap=tmux)."""
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
            "agent": {"bootstrap": "none"},
        }


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
        from kanibako.config import read_workset_kuid
        assert read_workset_kuid(project_toml) == "abcde"

    def test_set_skip_kuid_check_coerces_bool(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        set_config_value(
            "workset.skip_kuid_check", "false", config_path=project_toml,
        )
        data = load_doc(project_toml)
        # KEY_TYPES bool coercion: stored as a real False, not the string "false".
        assert data["workset"]["skip_kuid_check"] is False
        from kanibako.config import read_workset_skip_kuid_check
        assert read_workset_skip_kuid_check(project_toml) is False

    def test_kuid_default_is_sentinel_for_absent_file(self, tmp_path):
        # #3: primary/named (and any unset box) default workset.kuid = "00000".
        from kanibako import kuid
        from kanibako.config import (
            read_workset_kuid,
            read_workset_skip_kuid_check,
        )
        absent = tmp_path / "nope.yaml"
        assert read_workset_kuid(absent) == kuid.SENTINEL == "00000"
        # And skip_kuid_check defaults TRUE (advisory is opt-in strictness).
        assert read_workset_skip_kuid_check(absent) is True


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
            global_config_path=tmp_path / "kanibako_config.yaml",
            env_project=env_path,
        )
        assert val == "bar"

    def test_get_env_var_not_set(self, tmp_path):
        val = get_config_value(
            "env.MISSING",
            global_config_path=tmp_path / "kanibako_config.yaml",
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
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "/a/b"

    def test_reset_resource(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"resource_overrides": {"plugins": "/a/b"}})

        msg = reset_config_value("resource.plugins", config_path=project_toml)
        # Honest cleared-form (F7), consistent with every other reset branch.
        assert msg == (
            "Cleared resource.plugins set on this scope; it now falls back "
            "through the cascade."
        ), msg

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
        """allow_helpers moved to the AGENT keyspace (spec §2d L557): the bare
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

    def test_set_auto_approve_lands_in_agent_default_tier(self, tmp_path):
        """auto_approve is an AGENT-scope bool key (spec §2d L556): the bare key is
        the any-agent ``agent.default`` tier (mirrors ``model``/``allow_helpers``),
        written VERBATIM (no KEY_TYPES coercion — read-coerced at launch)."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("auto_approve", "false", config_path=project_toml)
        assert msg.startswith("Set")
        data = load_doc(project_toml)
        assert data["agent"]["default"]["auto_approve"] == "false"
        # No flat top-level scalar leaks out.
        assert "auto_approve" not in data

    def test_set_explicit_agent_default_auto_approve_refused(self, tmp_path):
        """An explicit ``agent.default.auto_approve`` write is REFUSED — the
        any-agent default is the BARE key (``default`` is the reserved tier, never
        a persona node)."""
        cf = tmp_path / "kanibako_config.yaml"
        msg = set_config_value(
            "agent.default.auto_approve", "false",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=tmp_path / "agents",
        )
        assert msg.startswith("Error:")
        assert "reserved any-agent tier" in msg

    def test_set_auto_approve_typo_is_rejected(self, tmp_path):
        """auto_approve is AUTH-CRITICAL: it read-coerces at launch, where an
        UNRECOGNISED value falls back PERMISSIVE (True). A typo (``flase``) must be
        REJECTED at set time, never silently accepted + written (Editor finding B).
        Mutation proof: dropping the ``_is_auto_approve_key`` write-guard lets this
        through and this assertion reddens."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("auto_approve", "flase", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "auto_approve must be a boolean" in msg
        # NOT written: the typo never lands in the file.
        assert not project_toml.exists() or "auto_approve" not in (
            load_doc(project_toml).get("agent", {}).get("default", {})
        )

    def test_set_auto_approve_accepts_all_bool_literals(self, tmp_path):
        """Every recognised bool literal (any case) is accepted + written VERBATIM
        (happy path unchanged; coerce_bool's full truth table)."""
        for literal in ("false", "true", "1", "0", "no", "yes", "ON", "off"):
            project_toml = tmp_path / f"settings_{literal}.yaml"
            msg = set_config_value(
                "auto_approve", literal, config_path=project_toml,
            )
            assert msg.startswith("Set"), literal
            data = load_doc(project_toml)
            # Written verbatim (the string as typed) — launch does the coercion.
            assert data["agent"]["default"]["auto_approve"] == literal

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

    def test_set_workset_channels_commons_nests_under_channels(self, tmp_path):
        """P6a: ``workset.channels.commons`` nests under ``workset.channels``."""
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value(
            "workset.channels.commons", "/srv/commons", config_path=project_toml
        )
        assert msg.startswith("Set"), msg
        assert (
            load_doc(project_toml)["workset"]["channels"]["commons"] == "/srv/commons"
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

    def test_set_mode_rejected_not_settable(self, tmp_path):
        """``mode`` is no longer settable via config set (block B1, spec §2b L486 /
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
            "agent": {"allow_helpers": "false"},
        }

    def test_get_auto_approve_round_trips_agent_default(self, tmp_path):
        """The bare ``auto_approve`` get reads the value STORED at the any-agent
        ``agent.default`` tier (symmetric with set; mirrors ``model``)."""
        project_toml = tmp_path / "settings.yaml"
        dump_doc(project_toml, {"agent": {"default": {"auto_approve": "false"}}})
        val = get_config_value(
            "auto_approve",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=project_toml,
        )
        assert val == "false"

    def test_set_auto_approve_per_agent_override(self, tmp_path):
        """A per-agent override ``agent.<agent>.auto_approve`` is a PERSONA key: it
        lands on the agent's OWN ``agents/<agent>/settings.yaml`` flat slot the
        launch reader picks over ``agent.default`` (§2d active-over-default)."""
        cf = tmp_path / "kanibako_config.yaml"
        agents_root = tmp_path / "agents"
        msg = set_config_value(
            "agent.claude.auto_approve", "false",
            config_path=cf, is_system=True, command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(agents_root / "claude" / "settings.yaml") == {
            "agent": {"auto_approve": "false"},
        }

    def test_retired_autonomous_and_access_do_not_route(self, tmp_path):
        """The dead ``autonomous`` persisted leaf and the claude-only ``access``
        string leaf are RETIRED (folded into ``auto_approve``): neither is a known
        key, and a bare set is refused as unknown (never lands in agent.default)."""
        assert is_known_key("autonomous") is False
        assert is_known_key("access") is False
        # A bare ``autonomous`` set no longer takes the agent.default agent-setting
        # route (which model/auto_approve take) — it is refused as an unknown key.
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("autonomous", "true", config_path=project_toml)
        assert msg == "Error: unknown config key: autonomous"
        assert not project_toml.exists()

    def test_bool_key_rejects_garbage(self, tmp_path):
        project_toml = tmp_path / "settings.yaml"
        msg = set_config_value("box.share_images", "maybe", config_path=project_toml)
        assert msg.startswith("Error:")
        assert "boolean" in msg


def _seed_default_agent(path, name):
    """Programmatically write system.default_agent (the path setup uses).

    Writes the agent.default table — exactly what config.read_default_agent
    reads back and where the CLI ``set`` now also writes (F3).
    """
    from kanibako.config_interface import _write_nested_toml_key

    _write_nested_toml_key(path, ("agent", "default"), "default_agent", name)


class TestSystemDefaultAgent:
    """system.default_agent: a SETTINGS key (F3 — the old ALL-system.*
    FILE-ONLY pin is deliberately flipped).

    set/reset route to the settings tier's ``agent.default`` table — the exact
    location ``config.read_default_agent`` (the launch reader) and ``setup``
    already use — so set → get → launch agree on one storage location.
    """

    def test_set_writes_the_settings_tier(self, tmp_path):
        from kanibako.config import read_default_agent

        f = tmp_path / "settings.yaml"
        msg = set_config_value("system.default_agent", "claude", config_path=f)
        assert not msg.startswith("Error:"), msg
        # Stored exactly where the shipped reader reads.
        assert load_doc(f)["agent"]["default"]["default_agent"] == "claude"
        assert read_default_agent(f) == "claude"

    def test_reset_removes_the_setting(self, tmp_path):
        from kanibako.config import read_default_agent

        f = tmp_path / "settings.yaml"
        _seed_default_agent(f, "claude")
        msg = reset_config_value("system.default_agent", config_path=f)
        assert not msg.startswith("Error:"), msg
        assert read_default_agent(f) is None

    def test_get_reads_programmatic_write(self, tmp_path):
        from kanibako.config import read_default_agent

        # Residuals item 2: default_agent lives in the system SETTINGS file
        # (@config.settings, where read_default_agent + set/reset all agree), so
        # a system-scope get reads it via ``system_settings_path`` — NOT the
        # kanibako_config.yaml CONFIG file. The old test seeded/read it through
        # ``global_config_path``, the exact clause-5 leak this item closes: no
        # real caller stores default_agent in the config file.
        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        _seed_default_agent(ssp, "goose")
        # The interface getter and the launch-time reader agree on the settings file.
        assert get_config_value(
            "system.default_agent", global_config_path=cf, system_settings_path=ssp,
        ) == "goose"
        assert read_default_agent(ssp) == "goose"

    def test_get_unset_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        cf.touch()
        assert get_config_value("system.default_agent", global_config_path=cf) is None

    def test_box_get_does_not_leak_global_default_agent(self, tmp_path):
        # Residuals item 2 (spec §2a Read verbs, clause 5): a plain get at a
        # box/workset noun must NOT surface the GLOBAL default_agent — that is
        # another (containing) tier's value, reserved for --effective. Baseline-RED
        # at 6340dad: the (project_toml, global_config_path) fallback returned the
        # global value; GREEN here → "(not set)" (None) since the box file is empty.
        cf = tmp_path / "kanibako_config.yaml"
        _seed_default_agent(cf, "claude")  # a global default exists
        box_file = tmp_path / "box-settings.yaml"
        box_file.touch()  # the box noun stores nothing
        assert (
            get_config_value(
                "system.default_agent",
                global_config_path=cf,
                project_toml=box_file,
            )
            is None
        )
        # But when the box file DOES store it, plain get returns the box value.
        _seed_default_agent(box_file, "goose")
        assert (
            get_config_value(
                "system.default_agent",
                global_config_path=cf,
                project_toml=box_file,
            )
            == "goose"
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
            "system.base_template", "system.runtime",
            "system.channels.commons", "system.setup_completed",
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
        ``_system_key_refusal`` that named ``setup``)."""
        for scope in (None, ConfigLevel.system, ConfigLevel.box, ConfigLevel.workset):
            cf = tmp_path / "kanibako_config.yaml"
            for key in (
                "config.data", "config.settings", "config.agents",
                "config.primary_workset", "config.registry",
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
            for key in ("config.data", "config.registry"):
                msg = reset_config_value(key, config_path=cf, command_scope=scope)
                assert msg.startswith(
                    "Error: config.* keys can only be changed by editing"
                ), (key, scope)
                assert "structural config key" not in msg
                assert "kanibako setup" not in msg

    def test_get_system_config_key_still_reads(self, tmp_path):
        """Reads/shows are unaffected — only writes are refused."""
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako_config.yaml"
        _write_nested_toml_key(cf, ("system",), "cache", "/custom/cache")
        assert (
            get_config_value("system.cache", global_config_path=cf)
            == "/custom/cache"
        )

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
        from kanibako.config import read_setup_completed
        from kanibako.config_interface import _write_nested_toml_key, write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        _write_nested_toml_key(cf, ("system",), "data", "/keep/me")
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

    def test_default_agent_set_routes_to_settings_file(self, tmp_path):
        """F3 flip: the set SUCCEEDS and lands in the system SETTINGS file's
        agent.default table — never the kanibako_config.yaml CONFIG file."""
        cf = tmp_path / "kanibako_config.yaml"        # CONFIG file
        ssp = tmp_path / "global" / "settings.yaml"  # SETTINGS file
        ssp.parent.mkdir(parents=True, exist_ok=True)
        msg = set_config_value(
            "system.default_agent", "claude",
            config_path=cf, system_settings_path=ssp,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ssp)["agent"]["default"]["default_agent"] == "claude"
        assert not cf.exists()

    def test_default_agent_reads_from_settings_file(self, tmp_path):
        from kanibako.config import read_default_agent
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        # setup writes it programmatically into the settings file's table.
        _write_nested_toml_key(ssp, ("agent", "default"), "default_agent", "goose")
        # Read back via interface getter (system scope) + launch-time reader.
        assert get_config_value(
            "system.default_agent", global_config_path=cf, system_settings_path=ssp,
        ) == "goose"
        # The launch-time reader points at the SETTINGS file, not kanibako_config.yaml.
        assert read_default_agent(ssp) == "goose"
        # A stale value in kanibako_config.yaml's agent table does NOT feed the tier.
        assert read_default_agent(cf) is None

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
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        _write_nested_toml_key(cf, ("system",), "cache", "/custom/cache")
        assert get_config_value(
            "system.cache", global_config_path=cf, system_settings_path=ssp,
        ) == "/custom/cache"
        # The settings file was never touched by a CONFIG read.
        assert not ssp.exists()

    def test_reset_default_agent_removes_from_settings_file(self, tmp_path):
        """F3 flip: reset removes the setting from the SETTINGS file (where the
        launch reader looks), leaving the CONFIG file untouched."""
        from kanibako.config import read_default_agent
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        _write_nested_toml_key(ssp, ("agent", "default"), "default_agent", "claude")
        msg = reset_config_value(
            "system.default_agent", config_path=cf, system_settings_path=ssp,
        )
        # Honest cleared-form (F7), consistent with every other reset branch.
        assert msg == (
            "Cleared system.default_agent set on this scope; it now falls back "
            "through the cascade."
        ), msg
        assert read_default_agent(ssp) is None
        assert not cf.exists()

    def test_reset_all_clears_settings_and_config_separately(self, tmp_path):
        from kanibako.config_interface import _write_nested_toml_key

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"
        # A SETTING (settings file) + a config override (config file).
        set_config_value(
            "system.default_agent", "claude",
            config_path=cf, system_settings_path=ssp,
        )
        _write_nested_toml_key(cf, ("box",), "image", "ghcr.io/x:1")
        reset_all(config_path=cf, force=True, system_settings_path=ssp)
        # The SETTING is gone from the settings file.
        assert not load_doc(ssp).get("agent")

    def test_absent_settings_file_is_graceful(self, tmp_path):
        """Missing global/settings.yaml → empty system tier, no error."""
        from kanibako.config import read_default_agent

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "global" / "settings.yaml"  # never created
        assert read_default_agent(ssp) is None
        assert get_config_value(
            "system.default_agent", global_config_path=cf, system_settings_path=ssp,
        ) is None


# ---------------------------------------------------------------------------
# Category `config set` — the source-only RAW host_src repoint (block 7c).
# Drives the REAL `set_config_value` router (not the unit `settings_configset`).
# Spec §2a / design §6d / SEAMS S24/S25.
# ---------------------------------------------------------------------------

class TestCategoryConfigSet:
    """`config set <category-key> <value>` through the live CLI setter."""

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

    def test_ok_repoint_preserves_dest_and_opts_raw(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old/src", "/home/agent/vault", "ro"],
        )
        msg = set_config_value("box.bindings.ro.vault", "/tmp", config_path=f)
        assert not msg.startswith("Error:")
        assert "Warning" not in msg
        # host_src swapped; box_dest + options PRESERVED RAW (structured list).
        assert load_doc(f)["box"]["bindings"]["ro"]["vault"] == [
            "/tmp", "/home/agent/vault", "ro",
        ]

    def test_warn_on_not_yet_existent_literal_proceeds(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "caches", "x"],
            ["/old", "/home/agent/.cache/x"],
        )
        missing = str(tmp_path / "does" / "not" / "exist")
        msg = set_config_value("box.caches.x", missing, config_path=f)
        assert not msg.startswith("Error:")
        assert "Warning" in msg  # WARN fired (host_exists obligation honored)
        # ... and the write still PROCEEDED.
        assert load_doc(f)["box"]["caches"]["x"][0] == missing

    def test_error_colon_src_dest_notation_refused(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old", "/home/agent/vault", "ro"],
        )
        msg = set_config_value("box.bindings.ro.vault", "/a:/b", config_path=f)
        assert msg.startswith("Error:")
        assert ":" in msg  # the src:dest refusal message
        # the file is NOT poisoned by a refused write
        assert load_doc(f)["box"]["bindings"]["ro"]["vault"][0] == "/old"

    def test_error_dangling_ref_is_hard_error(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old", "/home/agent/vault", "ro"],
        )
        msg = set_config_value(
            "box.bindings.ro.vault", "@nope.not.a.key/x", config_path=f,
        )
        assert msg.startswith("Error:")
        assert "dangling" in msg
        assert load_doc(f)["box"]["bindings"]["ro"]["vault"][0] == "/old"

    def test_ok_system_ref_stored_raw_never_expanded(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old", "/home/agent/vault", "ro"],
        )
        msg = set_config_value(
            "box.bindings.ro.vault", "@config.data/foo", config_path=f,
        )
        assert not msg.startswith("Error:")
        # stored RAW — the @-ref is NOT resolved to a literal (§0 files unresolved).
        assert load_doc(f)["box"]["bindings"]["ro"]["vault"][0] == "@config.data/foo"

    def test_error_key_must_already_exist(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old", "/home/agent/vault", "ro"],
        )
        msg = set_config_value(
            "box.bindings.rw.absent", "/x", config_path=f,
        )
        assert msg.startswith("Error:")
        assert "must already exist" in msg

    def test_error_unknown_var_is_hard_error(self, tmp_path):
        f = self._seed(
            tmp_path, ["box", "bindings", "ro", "vault"],
            ["/old", "/home/agent/vault", "ro"],
        )
        msg = set_config_value(
            "box.bindings.ro.vault", "$NOPE_UNKNOWN_VAR_XYZ/x", config_path=f,
        )
        assert msg.startswith("Error:")
        assert "unknown variable" in msg.lower()

    def test_system_scope_category_repoint_not_refused(self, tmp_path):
        """A system-scope category key reaches the set path (D2) — categories
        exist at every scope, so it is NOT a structural-config refusal."""
        f = self._seed(
            tmp_path, ["system", "caches", "x"],
            ["/old", "/home/agent/.cache/x"],
        )
        msg = set_config_value(
            "system.caches.x", "/tmp", config_path=f, is_system=True,
        )
        assert not msg.startswith("Error:")
        assert load_doc(f)["system"]["caches"]["x"] == ["/tmp", "/home/agent/.cache/x"]

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
        assert is_known_key("box.bindings.rw.home")
        assert is_known_key("system.caches.x")
        assert is_known_key("workset.shared.plugins")
        assert not is_known_key("some-project-name")


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
    """`config set` set-time E3 over the FULL launch cascade (not cmd-file only)."""

    def _seed_box(self, tmp_path, key_path, tuple_val):
        """Write a box-scope category bind into the box settings file."""
        f = tmp_path / "box-settings.yaml"
        data: dict = {}
        node = data
        for seg in key_path[:-1]:
            node = node.setdefault(seg, {})
        node[key_path[-1]] = tuple_val
        dump_doc(f, data)
        return f

    def _seed_workset(self, tmp_path, leaf, value):
        """Write a workset-scope key into a workset settings file."""
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {leaf: value}})
        return f

    def test_cross_scope_ref_resolves_with_full_cascade(self, tmp_path):
        """A box-scope set whose value @-refs a key set ONLY at the workset scope
        is ALLOWED (the false-block the first cut produced is GONE)."""
        box_f = self._seed_box(
            tmp_path, ["box", "bindings", "ro", "foo"],
            ["/old", "/home/agent/foo", "ro"],
        )
        ws_f = self._seed_workset(tmp_path, "vault_ro", "/srv/vault/ro")
        msg = set_config_value(
            "box.bindings.ro.foo", "@workset.vault_ro/bar",
            config_path=box_f,
            cascade_workset_path=ws_f,
            cascade_box_path=box_f,
        )
        # ALLOWED: @workset.vault_ro is visible in the full cascade -> resolves.
        assert not msg.startswith("Error:"), msg
        # stored RAW (the @-ref, NOT a literal — §0 files unresolved).
        assert load_doc(box_f)["box"]["bindings"]["ro"]["foo"][0] == (
            "@workset.vault_ro/bar"
        )

    def test_cross_scope_ref_false_blocked_without_workset_file(self, tmp_path):
        """Control: the SAME @workset.* ref with NO workset file in the cascade
        is dangling -> BLOCKED. Proves the prior test's pass is the cascade's
        doing (the workset key really is the only thing that resolves it)."""
        box_f = self._seed_box(
            tmp_path, ["box", "bindings", "ro", "foo"],
            ["/old", "/home/agent/foo", "ro"],
        )
        msg = set_config_value(
            "box.bindings.ro.foo", "@workset.vault_ro/bar",
            config_path=box_f,
            cascade_box_path=box_f,
        )
        assert msg.startswith("Error:"), msg
        assert "dangling" in msg
        assert "workset.vault_ro" in msg  # names the broken upstream dep

    def test_cross_scope_genuinely_dangling_still_blocks(self, tmp_path):
        """A value @-ref to a key set NOWHERE in the cascade still BLOCKS, naming
        the dangling target (E3 upstream rule holds over the full cascade)."""
        box_f = self._seed_box(
            tmp_path, ["box", "bindings", "ro", "foo"],
            ["/old", "/home/agent/foo", "ro"],
        )
        ws_f = self._seed_workset(tmp_path, "vault_ro", "/srv/vault/ro")
        msg = set_config_value(
            "box.bindings.ro.foo", "@workset.nope_absent/bar",
            config_path=box_f,
            cascade_workset_path=ws_f,
            cascade_box_path=box_f,
        )
        assert msg.startswith("Error:"), msg
        assert "dangling" in msg
        assert "workset.nope_absent" in msg

    def test_workset_scope_set_refs_system_floor_and_sibling(self, tmp_path):
        """A workset-scope set referencing the config.* floor (@config.data) AND a
        sibling workset key in the same file both resolve -> ALLOWED."""
        # The workset file holds the sibling target + the edited key.
        ws_f = tmp_path / "ws-settings.yaml"
        dump_doc(ws_f, {
            "workset": {
                "vault_ro": "/srv/vault/ro",
                "shared": {"x": ["/old", "/home/agent/x"]},
            },
        })
        # Reference BOTH the system.* floor and a sibling workset key. The system.*
        # floor is folded in by _category_set_lookups regardless of cascade files.
        msg = set_config_value(
            "workset.shared.x", "@workset.vault_ro/sub",
            config_path=ws_f,
            cascade_workset_path=ws_f,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(ws_f)["workset"]["shared"]["x"][0] == "@workset.vault_ro/sub"


# ---------------------------------------------------------------------------
# F10 — category repoint must-exist checks the CASCADE (Jei ruling 2026-07-02d,
# reconfirming the 2026-06-27 walkthrough model). Spec §2a: "The key MUST
# ALREADY EXIST in the cascade — the CLI can only REPOINT an existing bind,
# never CREATE one." The baseline checked the COMMAND-scope FILE only.
# ---------------------------------------------------------------------------

class TestRepointFromCascade:
    """`config set` repoints a bind set ANYWHERE in the set-time cascade; only a
    key NO scope sets is refused. The write still lands in the COMMAND-scope
    file (full raw tuple: user's host_src VERBATIM + cascade dest/opts RAW)."""

    def _seed_system(self, tmp_path):
        """A system-scope settings file holding the only vault bind tuple."""
        f = tmp_path / "global-settings.yaml"
        dump_doc(f, {"box": {"bindings": {"rw": {"vault": [
            "@config.data/vault", "$XDG_DATA_HOME/vault", "z"]}}}})
        return f

    def test_box_set_repoints_bind_from_higher_scope(self, tmp_path):
        """The F10 probe: the bind is set ONLY at the system scope; `box set`
        repoints it. FAILED on baseline ("must already exist at this scope").
        dest + opts preserved BYTE-RAW from the cascade tuple; the new host_src
        stored VERBATIM (unresolved, §0); the write lands in the BOX file; the
        system file is untouched."""
        sys_f = self._seed_system(tmp_path)
        box_f = tmp_path / "box-settings.yaml"  # does not exist yet
        msg = set_config_value(
            "box.bindings.rw.vault", "$XDG_DATA_HOME/mine",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_system_path=sys_f, cascade_box_path=box_f,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(box_f)["box"]["bindings"]["rw"]["vault"] == [
            "$XDG_DATA_HOME/mine", "$XDG_DATA_HOME/vault", "z",
        ]
        assert load_doc(sys_f)["box"]["bindings"]["rw"]["vault"] == [
            "@config.data/vault", "$XDG_DATA_HOME/vault", "z",
        ]

    def test_workset_downward_repoint_from_cascade(self, tmp_path):
        """`workset set box.bindings.rw.<name>` with the bind set only at the
        system scope writes the full raw tuple into the WORKSET file (the
        downward path the containment relaxation made user-visible)."""
        sys_f = self._seed_system(tmp_path)
        ws_f = tmp_path / "ws-settings.yaml"
        dump_doc(ws_f, {"workset": {"foo": "bar"}})
        msg = set_config_value(
            "box.bindings.rw.vault", "$XDG_DATA_HOME/team",
            config_path=ws_f, command_scope=ConfigLevel.workset,
            cascade_system_path=sys_f, cascade_workset_path=ws_f,
        )
        assert not msg.startswith("Error:"), msg
        doc = load_doc(ws_f)
        assert doc["box"]["bindings"]["rw"]["vault"] == [
            "$XDG_DATA_HOME/team", "$XDG_DATA_HOME/vault", "z",
        ]
        assert doc["workset"]["foo"] == "bar"  # sibling content untouched

    def test_nowhere_in_cascade_still_refused(self, tmp_path):
        """A key NO scope sets is still refused, and nothing is written."""
        sys_f = self._seed_system(tmp_path)
        box_f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.bindings.rw.absent", "$XDG_DATA_HOME/x",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_system_path=sys_f, cascade_box_path=box_f,
        )
        assert msg.startswith("Error:"), msg
        assert "must already exist" in msg
        assert not box_f.exists()  # nothing created by the refused write

    def test_command_file_tuple_still_wins_over_cascade(self, tmp_path):
        """Same-scope repoint unchanged: when the command file sets the key its
        OWN dest/opts are preserved, not a higher scope's."""
        sys_f = self._seed_system(tmp_path)
        box_f = tmp_path / "box-settings.yaml"
        dump_doc(box_f, {"box": {"bindings": {"rw": {"vault": [
            "/old", "/box-own-dest"]}}}})
        msg = set_config_value(
            "box.bindings.rw.vault", "/tmp",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_system_path=sys_f, cascade_box_path=box_f,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(box_f)["box"]["bindings"]["rw"]["vault"] == [
            "/tmp", "/box-own-dest",
        ]

    # --- reset symmetry (F10 step 3) ----------------------------------------

    def test_reset_category_key_removes_command_scope_tuple(self, tmp_path):
        """Reset removes the command-scope tuple (pruning emptied tables) so the
        cascade's tuple resurfaces. FAILED on baseline ("unknown config key")."""
        sys_f = self._seed_system(tmp_path)
        box_f = tmp_path / "box-settings.yaml"
        set_config_value(
            "box.bindings.rw.vault", "/tmp",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_system_path=sys_f, cascade_box_path=box_f,
        )
        msg = reset_config_value(
            "box.bindings.rw.vault",
            config_path=box_f, command_scope=ConfigLevel.box,
        )
        # Bug 2: the honest cleared-message form. No floor registry is threaded on
        # THIS call, so there is no reverted-to floor to name → the cleared-only
        # clause (same information as the old plain "Reset", via the honest
        # formatter).
        assert msg == (
            "Cleared box.bindings.rw.vault set on the box scope; "
            "it now falls back through the cascade."
        )
        doc = load_doc(box_f)
        assert "vault" not in doc.get("box", {}).get("bindings", {}).get("rw", {})
        # ROUNDTRIP: with the override gone the cascade tuple is the base again —
        # a fresh repoint sources dest/opts from the SYSTEM tuple once more.
        msg2 = set_config_value(
            "box.bindings.rw.vault", "$XDG_DATA_HOME/again",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_system_path=sys_f, cascade_box_path=box_f,
        )
        assert not msg2.startswith("Error:"), msg2
        assert load_doc(box_f)["box"]["bindings"]["rw"]["vault"] == [
            "$XDG_DATA_HOME/again", "$XDG_DATA_HOME/vault", "z",
        ]

    def test_reset_category_key_without_override_reports_none(self, tmp_path):
        box_f = tmp_path / "box-settings.yaml"
        msg = reset_config_value(
            "box.bindings.rw.vault",
            config_path=box_f, command_scope=ConfigLevel.box,
        )
        assert msg == "No override for box.bindings.rw.vault"

    def test_reset_core_bind_names_reverted_to_floor(self, tmp_path):
        """Bug 2 — a CORE bind reset (``box.bindings.rw.home``) with the core-bind
        floor registry threaded NAMES the reverted-to descriptor floor
        (dest [+ opts]); the set-time placeholder host_src is NEVER printed."""
        from kanibako.core_defaults import (
            FLOOR_PLACEHOLDER_SRC,
            core_default_bind_keys,
        )

        box_f = tmp_path / "box-settings.yaml"
        reg = dict(core_default_bind_keys())
        # The core bind ``home`` lives only in the launch floor; thread the core
        # registry into the SET so the must-exist gate passes (the real box handler
        # does exactly this) — the write lands in the box file.
        set_msg = set_config_value(
            "box.bindings.rw.home", "/newhome",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f, default_categories=reg,
        )
        assert not set_msg.startswith("Error:"), set_msg
        msg = reset_config_value(
            "box.bindings.rw.home",
            config_path=box_f, command_scope=ConfigLevel.box,
            default_categories=reg,
        )
        # The reverted-to floor's static box_dest is named; the sentinel is not.
        _placeholder, dest, opts = reg["box.bindings.rw.home"]
        assert "effective is now" in msg, msg
        assert dest in msg, msg
        assert opts in msg, msg
        assert FLOOR_PLACEHOLDER_SRC not in msg, msg
        assert "descriptor floor" in msg, msg
        # The override is really gone.
        assert "home" not in (
            load_doc(box_f).get("box", {}).get("bindings", {}).get("rw", {})
        )

    def test_reset_non_core_category_key_stays_cleared_only(self, tmp_path):
        """Bug 2 — a NON-core category key reset (registry threaded but the key is
        absent from it) has no floor to name → the cleared-only honest form (the
        same information as the old plain "Reset", never a fabricated value)."""
        from kanibako.core_defaults import core_default_bind_keys

        box_f = tmp_path / "box-settings.yaml"
        dump_doc(box_f, {"box": {"caches": {"foo": ["/src", "/dest"]}}})
        msg = reset_config_value(
            "box.caches.foo",
            config_path=box_f, command_scope=ConfigLevel.box,
            default_categories=dict(core_default_bind_keys()),
        )
        assert msg == (
            "Cleared box.caches.foo set on the box scope; "
            "it now falls back through the cascade."
        )


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
            "agent": {"model": "opus"},
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

    def test_box_scope_allows_box_agent_key(self, tmp_path):
        """``box.agent.<key>`` (the §2b B5 downward-tweak mirror) is the BOX
        namespace — the guard keys on the TOP-LEVEL ``box`` token, so it passes
        as a same-scope box write AND (B5 now implemented) actually lands in the
        box settings file at the nested ``box.agent.<key>`` location."""
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
        )
        # The scope-direction guard MUST NOT be the thing that refuses it.
        assert "cannot be set from the box scope" not in msg
        # B5: it is now a settable box-scope key — the write lands nested.
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["box"]["agent"]["model"] == "opus"

    def test_workset_scope_allows_workset_key(self, tmp_path):
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {"shared": {"x": ["/old", "/home/agent/x"]}}})
        msg = set_config_value(
            "workset.shared.x", "/new",
            config_path=f, cascade_workset_path=f,
            command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["workset"]["shared"]["x"][0] == "/new"

    def test_system_config_key_refused_with_ruled_message(self, tmp_path):
        """Block B2: ``config.*`` foundation keys are NEVER CLI-settable — refused
        from EVERY scope (including SYSTEM, which the B4 direction guard would
        otherwise own) with the ruled bootstrap-file message, BEFORE the scope
        guard. Not the direction-guard message, not the older generic
        ``_system_key_refusal`` (which named ``setup``)."""
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

    # --- scopeless keys always pass the guard -----------------------------

    def test_scopeless_env_key_allowed_at_box(self, tmp_path):
        env_f = tmp_path / "env"
        msg = set_config_value(
            "env.FOO", "bar",
            config_path=tmp_path / "settings.yaml", env_path=env_f,
            command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg

    def test_scopeless_allow_helpers_key_allowed_at_workset(self, tmp_path):
        # ``allow_helpers`` is a SCOPELESS scalar key — legal at any scope by
        # construction (own-file write); the directional guard does not apply.
        f = tmp_path / "ws-settings.yaml"
        msg = set_config_value(
            "allow_helpers", "true",
            config_path=f, command_scope=ConfigLevel.workset,
        )
        assert not msg.startswith("Error:"), msg

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

    def test_downward_category_key_still_must_exist(self, tmp_path):
        """A downward category repoint (``workset set box.bindings.rw.X``) now
        passes the direction guard, but the source-only MUST-EXIST rule is
        unrelaxed: a key NO scope in the cascade sets refuses via
        ConfigSetError and writes NOTHING. (The F10 fix broadened the lookup
        from exists-in-COMMAND-FILE to exists-in-CASCADE per spec §2a —
        TestRepointFromCascade covers the hit cases; this pins the miss.)"""
        f = tmp_path / "ws-settings.yaml"
        dump_doc(f, {"workset": {"foo": "bar"}})  # file exists, key absent
        msg = set_config_value(
            "box.bindings.rw.newmount", str(tmp_path),
            config_path=f, cascade_workset_path=f,
            command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error:"), msg
        assert "must already exist" in msg
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
        from kanibako.settings_launch import build_launch_snapshot
        from kanibako.settings_resolve import ResolveCtx

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
# box.agent.* mirror config-set (block B5 — spec §2b L380, JC-B5-2)
# ---------------------------------------------------------------------------

class TestBoxAgentMirrorConfigSet:
    """``box.agent.<key>`` is a settable BOX-scope key (the §2b B5 downward-tweak
    mirror): recognized by ``is_known_key``, set/reset land in the box settings
    file at the nested ``box.agent.<key>`` location, and the B4 guard permits it
    as a same-scope box write (covered above + here)."""

    def test_box_agent_key_is_known(self):
        assert is_known_key("box.agent.model") is True
        assert is_known_key("box.agent.auto_approve") is True
        assert is_known_key("box.agent.bindings.ro.share") is True

    def test_box_agent_name_scalar_not_a_mirror_key(self):
        # ``box.agent_name`` (the flat scalar) must NOT be mistaken for the mirror
        # (it has no dotted tail) — it stays its own known scalar key.
        from kanibako.config_interface import _is_box_agent_key
        assert _is_box_agent_key("box.agent_name") is False
        assert _is_box_agent_key("box.agent.model") is True
        assert is_known_key("box.agent_name") is True  # still its own known key

    def test_set_box_agent_scalar_lands_nested(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.model", "sonnet",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert load_doc(f)["box"]["agent"]["model"] == "sonnet"

    def test_set_box_agent_deep_category_key_lands_nested(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        msg = set_config_value(
            "box.agent.bindings.ro.share", "/user/share",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        doc = load_doc(f)
        assert doc["box"]["agent"]["bindings"]["ro"]["share"] == "/user/share"

    def test_reset_box_agent_key_removes_override(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"box": {"agent": {"model": "sonnet"}}})
        msg = reset_config_value(
            "box.agent.model", config_path=f, command_scope=ConfigLevel.box,
        )
        # Residuals item 5: the box.agent reset uses the standard HONEST
        # cleared-message form (consistency), not the old plain "Reset <key>".
        # Mutation guard: the old prefix must be GONE; the honest phrase PRESENT.
        assert not msg.startswith("Reset "), msg
        assert "cleared" in msg.lower(), msg
        assert "box.agent.model" in msg, msg
        # The override is gone (and the now-empty box.agent table pruned).
        doc = load_doc(f)
        assert "agent" not in doc.get("box", {})

    def test_reset_box_agent_key_absent_reports_no_override(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        dump_doc(f, {"box": {"image": "img:1"}})
        msg = reset_config_value("box.agent.model", config_path=f)
        assert "No override" in msg, msg
        # The unrelated box.image key is untouched.
        assert load_doc(f)["box"]["image"] == "img:1"

    def test_box_agent_set_does_not_touch_agent_namespace(self, tmp_path):
        # The mirror override lands under box.agent, NEVER under a top-level
        # agent.* table (no leak into the shared agent settings tier).
        f = tmp_path / "box-settings.yaml"
        set_config_value(
            "box.agent.model", "sonnet",
            config_path=f, command_scope=ConfigLevel.box,
        )
        doc = load_doc(f)
        assert "agent" not in doc  # no top-level agent.* table


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
    """F5 — ``box get box.agent.<key>`` must read back what ``box set
    box.agent.<key>`` wrote.  ``get_config_value`` lacked the
    ``_is_box_agent_key`` branch that set/reset have (SET was test-pinned; GET
    untested), so it returned "(not set)" for a value that IS stored."""

    def test_box_agent_scalar_set_then_get_roundtrips(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        set_config_value(
            "box.agent.model", "opus",
            config_path=f, command_scope=ConfigLevel.box,
        )
        # RED at baseline: get has no box.agent.* branch → returns None.
        val = get_config_value(
            "box.agent.model",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
        )
        assert val == "opus"

    def test_box_agent_deep_key_set_then_get_roundtrips(self, tmp_path):
        f = tmp_path / "box-settings.yaml"
        set_config_value(
            "box.agent.bindings.ro.share", "/user/share",
            config_path=f, command_scope=ConfigLevel.box,
        )
        val = get_config_value(
            "box.agent.bindings.ro.share",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
        )
        assert val == "/user/share"

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
        ws = tmp_path / "ws.yaml"
        box = tmp_path / "box.yaml"
        dump_doc(ws, {"allow_helpers": False})  # a lower-tier value
        set_config_value(
            "allow_helpers", "true", config_path=box,
            command_scope=ConfigLevel.box,
        )
        msg = reset_config_value(
            "allow_helpers", config_path=box, command_scope=ConfigLevel.box,
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
    canonical :func:`kanibako.paths.host_xdg_map` builder (spec §1 XDG clause +
    L2 §3 single-source-of-truth: ONE builder supplies every host-side context),
    not a hand-rolled 5-var dict.  (The builder's own resolution coverage lives
    in test_system_paths.py ``TestHostXdgMap`` — this only proves the wiring.)"""

    def test_set_time_ctx_xdg_equals_host_xdg_map(self):
        from kanibako.config_interface import _set_time_ctx
        from kanibako.paths import host_xdg_map

        ctx = _set_time_ctx()
        assert ctx.xdg == host_xdg_map()

    def test_set_time_ctx_calls_host_xdg_map(self, monkeypatch):
        # Non-vacuous: prove the wiring routes THROUGH the builder (so a revert
        # to a hand-rolled map that happens to produce equal output still fails).
        # RED at baseline: the inline dict never calls host_xdg_map → sentinel
        # is not observed in ctx.xdg.
        import kanibako.config_interface as ci

        sentinel = {"XDG_DATA_HOME": "/SENTINEL"}
        monkeypatch.setattr(
            ci, "_host_xdg_map", lambda *a, **k: dict(sentinel), raising=False,
        )
        ctx = ci._set_time_ctx()
        assert ctx.xdg == sentinel


# ---------------------------------------------------------------------------
# F10 — expose the launch-only CORE box-mount floor to config-set (Phase 1)
# ---------------------------------------------------------------------------

class TestF10CoreFloorRegistry:
    """``core_defaults.core_default_bind_keys`` — the context-light set-time floor
    registry (F10): the CORE box-mount KEYS with STATIC box_dest+options and a
    placeholder host_src, built WITHOUT any proj/std probe."""

    def test_emits_the_launch_core_keys_host_free(self):
        from kanibako.core_defaults import core_default_bind_keys, FLOOR_PLACEHOLDER_SRC

        reg = core_default_bind_keys()
        # The SAME keys the launch core floor emits — home + workspace + vault ro/rw.
        assert set(reg) == {
            "box.bindings.rw.home",
            "box.bindings.rw.workspace",
            "box.bindings.ro.vault",
            "box.bindings.rw.vault",
        }
        # box_dest + options are the STATIC declarative literals; host_src is the
        # discarded placeholder (mutation: swap FLOOR_PLACEHOLDER_SRC to a proj-
        # probed path and this equality goes RED — proving it is host-FREE).
        assert reg["box.bindings.ro.vault"] == (FLOOR_PLACEHOLDER_SRC, "~/vault/ro", "ro")
        assert reg["box.bindings.rw.home"] == (FLOOR_PLACEHOLDER_SRC, "~", "Z,U")

    def test_vault_keys_present_regardless_of_enable_vault(self):
        # The gate is about the KEY existing at set-time, not the runtime host value:
        # both vault binds are exposed even though launch may disable vault.
        from kanibako.core_defaults import core_default_bind_keys

        reg = core_default_bind_keys()
        assert "box.bindings.ro.vault" in reg
        assert "box.bindings.rw.vault" in reg


class TestF10CoreFloorRepoint:
    """A source-only repoint of a launch-only CORE bind (``box.bindings.{ro,rw}.
    <key>``) validates + writes RAW once the floor registry is threaded — it was
    REFUSED as "nowhere in the cascade" before (Step B / F10)."""

    def _reg(self):
        from kanibako.core_defaults import core_default_bind_keys

        return dict(core_default_bind_keys())

    def test_repoint_core_bind_writes_raw_tuple(self, tmp_path):
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "box.bindings.ro.vault", "/newsrc",
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box, default_categories=self._reg(),
        )
        # Validated + wrote (was refused before) — the confirm line, warn allowed.
        assert msg.startswith("Set box.bindings.ro.vault host source to /newsrc"), msg
        # RAW tuple: new host_src, dest + options BYTE-RAW from the floor.
        assert load_doc(box)["box"]["bindings"]["ro"]["vault"] == [
            "/newsrc", "~/vault/ro", "ro",
        ]

    def test_repoint_without_registry_is_refused(self, tmp_path):
        # Mutation-proof the registry is load-bearing: drop default_categories and
        # the SAME set is refused (the pre-Step-B behavior). RED if the fold leaked
        # the floor in from elsewhere.
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "box.bindings.ro.vault", "/newsrc",
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box,
        )
        assert msg.startswith("Error:") and "must already exist in the cascade" in msg

    def test_unknown_bind_name_still_refused(self, tmp_path):
        # A genuinely-unknown bind name is NOT in the registry → still refused even
        # with the registry threaded (the gate creates nothing).
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "box.bindings.ro.nonexistent", "/x",
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box, default_categories=self._reg(),
        )
        assert msg.startswith("Error:") and "nonexistent" in msg

    def test_rw_bind_options_preserved(self, tmp_path):
        box = tmp_path / "box.yaml"
        set_config_value(
            "box.bindings.rw.home", "/newhome",
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box, default_categories=self._reg(),
        )
        # Z,U options + ~ dest carried through byte-raw from the floor.
        assert load_doc(box)["box"]["bindings"]["rw"]["home"] == ["/newhome", "~", "Z,U"]

    def test_already_file_set_bind_repoints_from_file_not_floor(self, tmp_path):
        # No regression: when the box FILE already sets the key, the repoint sources
        # box_dest/options from the FILE tuple (the cascade winner at box scope), NOT
        # the floor default — proving the floor is only a FALLBACK.
        box = tmp_path / "box.yaml"
        dump_doc(box, {"box": {"bindings": {"ro": {"vault": [
            "/old", "/custom/dest", "ro",
        ]}}}})
        set_config_value(
            "box.bindings.ro.vault", "/new2",
            config_path=box, command_scope=ConfigLevel.box,
            cascade_box_path=box, default_categories=self._reg(),
        )
        assert load_doc(box)["box"]["bindings"]["ro"]["vault"] == [
            "/new2", "/custom/dest", "ro",
        ]

    def test_written_box_tuple_overrides_floor_at_launch(self, tmp_path):
        # Take-effect (reconcile precedence): a box-scope written tuple sits at the
        # box level and BEATS the base floor when the launch cascade merges.
        from kanibako.settings_assemble import assemble_levels
        from kanibako.settings_merge import merge

        box = tmp_path / "box.yaml"
        dump_doc(box, {"box": {"bindings": {"rw": {"home": [
            "/BOXWIN", "~", "Z,U",
        ]}}}})
        floor = {"box.bindings.rw.home": ("/FLOOR", "~", "Z,U")}
        snap = merge(assemble_levels(agent_name="", box_path=box, floor=floor))
        node = snap
        for seg in ("box", "bindings", "rw", "home"):
            node = dict.get(node, seg)
        assert node.host == "/BOXWIN"  # box beats the base floor
        # And with NO box file the floor value is the fallback (proves reachability).
        snap2 = merge(assemble_levels(agent_name="", floor=floor))
        n2 = snap2
        for seg in ("box", "bindings", "rw", "home"):
            n2 = dict.get(n2, seg)
        assert n2.host == "/FLOOR"


# ---------------------------------------------------------------------------
# item-0 — per-node DESCRIPTOR bind repoint (agent.<node>.bindings.{ro,rw}.<name>)
# ---------------------------------------------------------------------------

class TestAgentNodeBindRouting:
    """The ``agent.<node>.bindings.{ro,rw}.<name>`` predicate + its routing order:
    it is a per-node DESCRIPTOR bind (item-0), NOT a persona scalar, NOT a box.agent
    mirror, NOT the bare-``agent`` category form."""

    def test_predicate_matches_node_bind_only(self):
        from kanibako.config_interface import (
            _is_agent_node_bind_key,
            _is_box_agent_key,
            _is_path_category_key,
            _is_persona_agent_key,
        )
        # A node bind key: node-bind True, and the others False (no mis-capture).
        k = "agent.claude.bindings.ro.launcher"
        assert _is_agent_node_bind_key(k)
        assert not _is_box_agent_key(k)
        assert not _is_path_category_key(k)  # BIND_KEY_RE never matches the node form
        assert not _is_persona_agent_key(k)  # launcher is not a state leaf

    def test_bind_named_model_is_a_bind_not_a_persona_scalar(self):
        # COLLISION: a bind literally NAMED ``model`` — the ``bindings.ro`` segment
        # disambiguates it from the persona state leaf ``agent.claude.model``. Both
        # predicates fire, but the node-bind is checked FIRST in the dispatch.
        from kanibako.config_interface import (
            _is_agent_node_bind_key,
            _is_persona_agent_key,
        )
        k = "agent.claude.bindings.ro.model"
        assert _is_agent_node_bind_key(k)
        assert _is_persona_agent_key(k)  # would mis-capture if checked first

    def test_persona_scalar_is_not_a_node_bind(self):
        from kanibako.config_interface import _is_agent_node_bind_key
        assert not _is_agent_node_bind_key("agent.claude.model")
        assert not _is_agent_node_bind_key("agent.claude.endpoint")

    def test_box_agent_bind_is_not_a_node_bind(self):
        from kanibako.config_interface import (
            _is_agent_node_bind_key,
            _is_box_agent_key,
        )
        assert not _is_agent_node_bind_key("box.agent.bindings.ro.x")
        assert _is_box_agent_key("box.agent.bindings.ro.x")

    def test_bare_agent_category_is_not_a_node_bind(self):
        # The bare ``agent.bindings.*`` (no node) stays on the ordinary category
        # (BIND_KEY_RE) path — the node-bind regex requires a node segment.
        from kanibako.config_interface import (
            _is_agent_node_bind_key,
            _is_path_category_key,
        )
        assert not _is_agent_node_bind_key("agent.bindings.ro.foo")
        assert _is_path_category_key("agent.bindings.ro.foo")

    def test_resolve_key_canonicalizes_node_plus_form(self):
        from kanibako.config_interface import _resolve_key
        assert (
            _resolve_key("agent.navigator+claude.bindings.rw.plugins")
            == "agent.navigator℘claude.bindings.rw.plugins"
        )
        # A bind named ``model`` under a persona keeps its bind shape (NOT the
        # persona-scalar re-root).
        assert (
            _resolve_key("agent.nav+claude.bindings.ro.model")
            == "agent.nav℘claude.bindings.ro.model"
        )


class TestAgentNodeBindRepoint:
    """A source-only repoint of a per-node descriptor bind writes the RAW tuple to
    the node file, sourcing box_dest/opts from the descriptor floor registry."""

    def _reg(self):
        from kanibako.agent_representation import agent_default_bind_keys
        return agent_default_bind_keys("claude")

    def test_repoint_writes_raw_tuple_to_node_file(self, tmp_path):
        node = tmp_path / "settings.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node, command_scope=ConfigLevel.system,
            cascade_agent_path=node, cascade_agent_name="claude",
            default_categories=self._reg(),
        )
        assert msg.startswith(
            "Set agent.claude.bindings.ro.launcher host source to /newsrc"
        ), msg
        # RAW tuple: new host_src, descriptor box_dest + opts BYTE-RAW from the floor,
        # nested at agent.<node>.bindings.ro.launcher (the shape _agent_partial reads).
        reg = self._reg()
        _, dest, opts = reg["agent.claude.bindings.ro.launcher"]
        assert load_doc(node)["agent"]["claude"]["bindings"]["ro"]["launcher"] == [
            "/newsrc", dest, opts,
        ]

    def test_repoint_without_registry_is_refused(self, tmp_path):
        # Mutation-proof the registry is load-bearing: drop default_categories and the
        # SAME repoint is refused (nowhere in the cascade — the descriptor floor is
        # launch-only). RED if the floor leaked in from elsewhere.
        node = tmp_path / "settings.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node, command_scope=ConfigLevel.system,
            cascade_agent_path=node, cascade_agent_name="claude",
        )
        assert msg.startswith("Error:") and "must already exist in the cascade" in msg

    def test_unknown_bind_name_still_refused(self, tmp_path):
        node = tmp_path / "settings.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.nonexistent", "/x",
            config_path=node, command_scope=ConfigLevel.system,
            cascade_agent_path=node, cascade_agent_name="claude",
            default_categories=self._reg(),
        )
        assert msg.startswith("Error:") and "nonexistent" in msg

    def test_box_scope_repoint_is_refused_upward(self, tmp_path):
        # Directional guard (unchanged): agent.* from box scope is UPWARD → refused.
        box = tmp_path / "box.yaml"
        msg = set_config_value(
            "agent.claude.bindings.ro.launcher", "/new",
            config_path=box, command_scope=ConfigLevel.box,
            default_categories=self._reg(),
        )
        assert msg.startswith("Error:") and "cannot be set" in msg
        assert not box.exists()  # nothing written

    def test_written_tuple_overrides_descriptor_floor_at_launch(self, tmp_path):
        # (unit) the node-file tuple beats the descriptor default (agent_default_
        # partial) at launch — the agent-file rung out-precedes the descriptor rung.
        from kanibako.agent_representation import agent_default_partial
        from kanibako.config_io import dump_doc
        from kanibako.settings_launch import build_launch_snapshot
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

        # The exact shape our config-set write produces in the node file.
        node = tmp_path / "settings.yaml"
        dump_doc(node, {"agent": {"claude": {"bindings": {"ro": {
            "launcher": ["/REPOINT", "/box/launcher", "ro"]}}}}})

        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=_bind_launch_ctx(),
            system_path=None, agent_path=node, workset_path=None, box_path=None,
            agent_partial=partial,
        )
        assert snap.agent.claude.bindings.ro.launcher.host == "/REPOINT"


def _bind_launch_ctx():
    from kanibako.settings_resolve import ResolveCtx
    return ResolveCtx(
        agent_name="claude", workset_name=None, host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


# ---------------------------------------------------------------------------
# Step B Phase 3 — get/reset read-back symmetry for repointed binds
# ---------------------------------------------------------------------------

class TestAgentNodeBindGetReset:
    """The get/set/reset symmetry for a per-node DESCRIPTOR bind
    ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0): what a repoint SET wrote is
    read back by GET and removed by RESET, all against the node's OWN settings file
    ``agents/<node>/settings.yaml`` (the previously-missing read-back half)."""

    def _reg(self):
        from kanibako.agent_representation import agent_default_bind_keys
        return agent_default_bind_keys("claude")

    def _agents_root(self, tmp_path):
        # A node file under an agents root: agents/<node>/settings.yaml.
        root = tmp_path / "agents"
        (root / "claude").mkdir(parents=True)
        return root

    def test_set_then_get_reads_back_the_stored_tuple(self, tmp_path):
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        reg = self._reg()
        set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node_file, command_scope=ConfigLevel.system,
            cascade_agent_path=node_file, cascade_agent_name="claude",
            default_categories=reg,
        )
        # GET reads back the RAW tuple STORED at the node file (stored-at-noun).
        val = get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        )
        _, dest, opts = reg["agent.claude.bindings.ro.launcher"]
        assert val == str(["/newsrc", dest, opts])
        assert "/newsrc" in val

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

    def test_reset_removes_the_override_and_get_reverts(self, tmp_path):
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        reg = self._reg()
        set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node_file, command_scope=ConfigLevel.system,
            cascade_agent_path=node_file, cascade_agent_name="claude",
            default_categories=reg,
        )
        msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        assert msg.startswith("Cleared agent.claude.bindings.ro.launcher")
        # The override is GONE from the node file; GET reverts to "(not set)".
        assert load_doc(node_file) == {}
        assert get_config_value(
            "agent.claude.bindings.ro.launcher",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        ) is None

    def test_reset_reports_reverted_to_floor_destination(self, tmp_path):
        # item 3 — with the floor registry threaded, the honest cleared-message
        # names the reverted-to descriptor destination [+ opts], NEVER the set-time
        # placeholder host_src (evidence-honesty).
        from kanibako.core_defaults import FLOOR_PLACEHOLDER_SRC

        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        reg = self._reg()
        set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node_file, command_scope=ConfigLevel.system,
            cascade_agent_path=node_file, cascade_agent_name="claude",
            default_categories=reg,
        )
        msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents, default_categories=reg,
        )
        _, dest, _opts = reg["agent.claude.bindings.ro.launcher"]
        assert "effective is now" in msg
        assert dest in msg
        assert FLOOR_PLACEHOLDER_SRC not in msg  # the sentinel is never printed

    def test_reset_without_registry_keeps_cleared_only_form(self, tmp_path):
        # Mutation-proof the registry is load-bearing for item 3: drop
        # default_categories and the message has NO "effective is now" clause.
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        set_config_value(
            "agent.claude.bindings.ro.launcher", "/newsrc",
            config_path=node_file, command_scope=ConfigLevel.system,
            cascade_agent_path=node_file, cascade_agent_name="claude",
            default_categories=self._reg(),
        )
        msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        assert msg.startswith("Cleared agent.claude.bindings.ro.launcher")
        assert "effective is now" not in msg

    def test_reset_unset_node_bind_reports_no_override(self, tmp_path):
        agents = self._agents_root(tmp_path)
        msg = reset_config_value(
            "agent.claude.bindings.ro.launcher",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents, default_categories=self._reg(),
        )
        assert msg == "No override for agent.claude.bindings.ro.launcher"

    def test_get_reset_bind_named_after_state_leaf_routes_to_bind(self, tmp_path):
        # Collision: a bind literally NAMED ``model`` must route to the node-bind
        # get/reset path (the bindings.ro segment), NOT the persona ``model`` scalar.
        agents = self._agents_root(tmp_path)
        node_file = agents / "claude" / "settings.yaml"
        # Seed the file with a bind at the descriptor-shaped location so a repoint
        # (which must-exist in the cascade) is not needed to prove routing.
        dump_doc(node_file, {"agent": {"claude": {"bindings": {"ro": {
            "model": ["/hostmodel", "/box/model", "ro"]}}}}})
        val = get_config_value(
            "agent.claude.bindings.ro.model",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        )
        assert val == str(["/hostmodel", "/box/model", "ro"])
        # The PERSONA scalar ``agent.claude.model`` is a DIFFERENT key (flat slot),
        # unaffected by the bind write.
        assert get_config_value(
            "agent.claude.model",
            global_config_path=tmp_path / "cfg.yaml", agents_root=agents,
        ) is None
        # Reset routes to the bind, removing the nested tuple (not the flat scalar).
        reset_config_value(
            "agent.claude.bindings.ro.model",
            config_path=tmp_path / "cfg.yaml", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        assert load_doc(node_file) == {}


class TestPersonaScalarGetResetUnchanged:
    """The Phase-3 node-bind get/reset branches must NOT divert a persona SCALAR
    key (``agent.<node>.model`` / ``.endpoint``) — it still routes stored-at-noun to
    the flat ``agent:`` slot (byte-unchanged collision guard)."""

    def test_persona_model_get_reset_unchanged(self, tmp_path):
        agents = tmp_path / "agents"
        (agents / "claude").mkdir(parents=True)
        node_file = agents / "claude" / "settings.yaml"
        set_config_value(
            "agent.claude.model", "opus",
            config_path=tmp_path / "x", command_scope=ConfigLevel.system,
            agents_root=agents,
        )
        # Stored at the FLAT persona slot ``agent.model`` (NOT nested bindings).
        assert load_doc(node_file) == {"agent": {"model": "opus"}}
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
    """item 4 — the CORE/path-category bind (``box.bindings.{ro,rw}.<name>``)
    get/set/reset round-trip at box scope reads/removes the box settings file."""

    def test_core_bind_set_get_reset_round_trip(self, tmp_path):
        from kanibako.core_defaults import core_default_bind_keys

        box_f = tmp_path / "box.yaml"
        # Seed an existing tuple so the source-only repoint has a bind to repoint.
        dump_doc(box_f, {"box": {"bindings": {"ro": {
            "vault_ro": ["/old", "/vault/ro", "ro"]}}}})
        set_config_value(
            "box.bindings.ro.vault_ro", "/newvault",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f,
            default_categories=dict(core_default_bind_keys()),
        )
        # GET reads back the repointed tuple (was previously unread — get lacked a
        # path-category branch and returned None; RED before the Phase-3 fix).
        val = get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        )
        assert val == str(["/newvault", "/vault/ro", "ro"])
        # RESET removes the box-scope tuple. Bug 2: no floor registry threaded on
        # this call → the honest cleared-only form (same info as the old plain
        # "Reset", via the honest formatter).
        msg = reset_config_value(
            "box.bindings.ro.vault_ro", config_path=box_f,
            command_scope=ConfigLevel.box,
        )
        assert msg == (
            "Cleared box.bindings.ro.vault_ro set on the box scope; "
            "it now falls back through the cascade."
        )
        assert load_doc(box_f) == {}
        assert get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        ) is None

    def test_core_bind_get_unset_is_none(self, tmp_path):
        box_f = tmp_path / "box.yaml"
        dump_doc(box_f, {"box": {"image": "x"}})
        assert get_config_value(
            "box.bindings.ro.vault_ro",
            global_config_path=tmp_path / "cfg.yaml", project_toml=box_f,
        ) is None
