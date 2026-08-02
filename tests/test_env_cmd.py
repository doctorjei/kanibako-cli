"""The env key family at the config verbs: bare RETIRED, scoped LIVE.

Bare ``env.<VAR>`` is RETIRED (R-39, spec §2a). The keyspace env family is
SCOPED — ``<scope>.env.<VAR>`` — and the bare spelling wrote the docker ``.env``
FILE instead: an undiscriminated variant that silently meant something different
from the discriminated key. The verbs now REFUSE it with a cure; get returns
``None`` at the engine, with the handler-side read guards carrying the same cure
(see the per-handler command tests).

⚑ RQ-1 (Jei, 2026-08-02): the ``.env`` FILES are RETIRED OUTRIGHT — the
three-tier launch read is gone too, so a hand edit reaches nothing either. These
tests pin that no verb writes them, that no verb reads them, and that the cure
the refusal names is a key that really works.
"""

from __future__ import annotations

from kanibako.settings.config_interface import (
    get_config_value,
    reset_config_value,
    set_config_value,
)
from kanibako.settings.config_io import load_doc
from kanibako.settings.config_keys import ConfigLevel


class TestBareEnvRetired:
    """R-39 refusals through the config engine."""

    def test_set_refuses_with_the_scoped_cure(self, tmp_path):
        env_path = tmp_path / "env"
        msg = set_config_value(
            "env.EDITOR", "vim",
            config_path=tmp_path / "settings.yaml",
            env_path=env_path,
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "'env.EDITOR'" in msg        # the user's spelling, quoted
        assert "box.env.EDITOR" in msg      # the cure names the DISCRIMINATED key
        assert not env_path.exists()        # nothing written

    def test_the_cure_says_the_files_are_not_read(self, tmp_path):
        """RQ-1: the message must NOT suggest hand-editing the .env file."""
        msg = set_config_value(
            "env.EDITOR", "vim",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.box,
        )
        assert "no longer read at all" in msg, msg
        assert "hand-edit" not in msg, msg

    def test_set_cure_defaults_to_the_box_tier_without_a_scope(self, tmp_path):
        msg = set_config_value(
            "env.EDITOR", "vim",
            config_path=tmp_path / "settings.yaml",
            env_path=tmp_path / "env",
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.EDITOR" in msg

    def test_set_cure_names_the_command_scope(self, tmp_path):
        for scope, cure in (
            (ConfigLevel.workset, "workset.env.EDITOR"),
            (ConfigLevel.system, "system.env.EDITOR"),
        ):
            msg = set_config_value(
                "env.EDITOR", "vim",
                config_path=tmp_path / "settings.yaml",
                env_path=tmp_path / "env",
                command_scope=scope,
            )
            assert msg.startswith("Error:"), (scope, msg)
            assert cure in msg, (scope, msg)

    def test_get_returns_none_even_when_the_legacy_file_has_the_var(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("EDITOR=vim\n")
        val = get_config_value(
            "env.EDITOR",
            global_config_path=tmp_path / "kanibako_config.yaml",
            env_project=env_path,
        )
        # The engine returns values, never error strings — the read refusal
        # (verb "read", same cure) lives at the box/workset/system handlers.
        assert val is None

    def test_reset_refuses_and_leaves_the_file_alone(self, tmp_path):
        env_path = tmp_path / "env"
        env_path.write_text("EDITOR=vim\n")
        msg = reset_config_value(
            "env.EDITOR",
            config_path=tmp_path / "settings.yaml",
            env_path=env_path,
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.EDITOR" in msg
        # The file is not an override store any more; the verb does not touch it.
        assert env_path.read_text() == "EDITOR=vim\n"

    def test_null_set_gets_the_retirement_cure(self, tmp_path):
        msg = set_config_value(
            "env.EDITOR", None,
            config_path=tmp_path / "settings.yaml",
            env_path=tmp_path / "env",
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "box.env.EDITOR" in msg
        assert "no null value" not in msg   # the old --null env arm is gone


class TestScopeEnvIsSettable:
    """The cure is REACHABLE — ``<scope>.env.<VAR>`` routes through the verbs.

    Without this the R-39 refusal would be a dead-end: it names a key that would
    itself error as unknown, while the ``.env`` launch read (the bare spelling's
    only delivery) is gone in the same change — leaving NO way to set a container
    env var through config at all.
    """

    def test_set_get_reset_round_trip_at_box_scope(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = set_config_value(
            "box.env.EDITOR", "vim",
            config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "Set box.env.EDITOR=vim", msg
        # Stored at the shape ``_file_partial`` reads into the launch cascade.
        assert load_doc(f)["box"]["env"]["EDITOR"] == "vim"

        assert get_config_value(
            "box.env.EDITOR",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
            command_scope=ConfigLevel.box,
        ) == "vim"

        msg = reset_config_value(
            "box.env.EDITOR", config_path=f, command_scope=ConfigLevel.box,
        )
        assert not msg.startswith("Error:"), msg
        assert get_config_value(
            "box.env.EDITOR",
            global_config_path=tmp_path / "kanibako_config.yaml",
            project_toml=f,
            command_scope=ConfigLevel.box,
        ) is None

    def test_reset_of_an_unset_scope_env_says_no_override(self, tmp_path):
        f = tmp_path / "settings.yaml"
        msg = reset_config_value(
            "box.env.NOPE", config_path=f, command_scope=ConfigLevel.box,
        )
        assert msg == "No override for box.env.NOPE", msg

    def test_the_workset_and_system_arms_route_too(self, tmp_path):
        ws = tmp_path / "ws.yaml"
        assert set_config_value(
            "workset.env.EDITOR", "vim",
            config_path=ws, command_scope=ConfigLevel.workset,
        ) == "Set workset.env.EDITOR=vim"
        assert load_doc(ws)["workset"]["env"]["EDITOR"] == "vim"

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "settings.yaml"
        assert set_config_value(
            "system.env.EDITOR", "nano",
            config_path=cf, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        ) == "Set system.env.EDITOR=nano"
        # SETTINGS, never the Layer-1 CONFIG file.
        assert load_doc(ssp)["system"]["env"]["EDITOR"] == "nano"
        assert not cf.exists() or "system" not in load_doc(cf)

    def test_an_upward_write_is_refused_by_the_direction_guard(self, tmp_path):
        """``<scope>.env.<VAR>`` is scope-TOKENED, so §0's guard applies to it
        exactly as it does to ``<scope>.secret_path.<VAR>``."""
        msg = set_config_value(
            "system.env.EDITOR", "vim",
            config_path=tmp_path / "settings.yaml",
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:"), msg
        assert "cannot be set from the box scope" in msg

    def test_a_reserved_var_name_is_refused_loudly_at_write_time(self, tmp_path):
        """Spec §0 reserved key names — rejected at ``config set`` time, by NAME."""
        f = tmp_path / "settings.yaml"
        for var in ("get", "keys", "__init__"):
            msg = set_config_value(
                f"box.env.{var}", "x", config_path=f, command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (var, msg)
            assert f"'{var}'" in msg, (var, msg)
            assert not f.exists(), var
            # Symmetric on reset — never "No override" for an illegal name.
            msg = reset_config_value(
                f"box.env.{var}", config_path=f, command_scope=ConfigLevel.box,
            )
            assert msg.startswith("Error:"), (var, msg)

    def test_var_matching_is_case_sensitive(self, tmp_path):
        """Spec §0/§2a — ``box.env.Path`` and ``box.env.PATH`` are two keys."""
        f = tmp_path / "settings.yaml"
        set_config_value(
            "box.env.Path", "lower", config_path=f, command_scope=ConfigLevel.box,
        )
        set_config_value(
            "box.env.PATH", "upper", config_path=f, command_scope=ConfigLevel.box,
        )
        env = load_doc(f)["box"]["env"]
        assert env == {"Path": "lower", "PATH": "upper"}, env

    def test_the_scoped_arm_probes_at_set_time(self, tmp_path):
        """The scoped arm IS host-expanded at launch, so it probes (R-39 note).

        The bare spelling was excluded from the probe because its value never
        reached the expander; the scoped key's does, so a dangling ``@``-ref must
        be caught NOW rather than resolving silently to ``""`` at launch.
        """
        from kanibako.settings.config_keys import _probes_at_set_time

        assert _probes_at_set_time("box.env.FOO")
        assert not _probes_at_set_time("env.FOO")
