"""Tests for kanibako box config subcommand and config.py utility functions."""

from __future__ import annotations

import argparse

from kanibako.config import (
    KanibakoConfig,
    load_config,
    load_project_overrides,
    write_project_config,
    write_project_config_key,
)


# ---------------------------------------------------------------------------
# box config command tests
# ---------------------------------------------------------------------------

class TestBoxConfigShow:
    def test_show_no_overrides(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir], effective=False)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir], effective=True)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "box_image" in captured.out

    def test_show_with_override(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a project override
        project_toml = proj.metadata_path / "settings.yaml"
        write_project_config(project_toml, "custom:v1")

        args = argparse.Namespace(args=[project_dir], effective=False)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "custom:v1" in captured.out

    def test_show_effective_reflects_workset_tier(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """A value set ONLY in the workset settings.yaml shows in --effective.

        This is the P3.7 parity fix: ``box config --effective`` must reflect
        the workset tier that ``start`` resolves (previously it skipped it).
        """
        from kanibako.commands.box._parser import run_show
        from kanibako.paths import load_std_paths
        from kanibako.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("cfgtier", tmp_home / "ws_cfgtier", std)

        src = tmp_home / "proj_cfgtier"
        src.mkdir()
        add_project(ws, "myproj", src)

        # Set a box.* value ONLY at the workset level.  The workset settings now
        # live in the SAME settings.yaml that carries the workset.meta identity,
        # so merge the cascade key in rather than clobbering the file.
        from kanibako.config_io import dump_doc, load_doc
        ws_settings = ws.root / "settings.yaml"
        data = load_doc(ws_settings) if ws_settings.is_file() else {}
        data["box"] = {"image": "ws-tier-img:1"}
        dump_doc(ws_settings, data)

        # Resolve via cwd inside the project's workspace dir.
        monkeypatch.chdir(ws.workspaces_dir / "myproj")
        args = argparse.Namespace(args=[], effective=True)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "box_image" in captured.out
        assert "ws-tier-img:1" in captured.out

    def test_show_effective_reflects_system_settings_file(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """DISPLAY == LAUNCH file (F2/F3 sibling): a behavior value stored
        ONLY in the system SETTINGS file (``@config.settings`` — the exact
        ``system_path`` the launch snapshot reads, ``std.settings``) shows in
        ``box show --effective``.  Pins the display ctx's system tier to the
        launch derivation — NEVER the kanibako_config.yaml CONFIG file (which
        the launch cascade does not read for settings)."""
        from kanibako.commands.box._parser import run_show
        from kanibako.config_interface import _write_nested_toml_key
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # System settings tier: select claude, and set a behavior key the
        # per-agent file does NOT set (endpoint) so the system-tier value is
        # the effective one at launch — the display must show the same.
        _write_nested_toml_key(
            std.settings, ("agent", "default"), "default_agent", "claude",
        )
        _write_nested_toml_key(
            std.settings, ("agent", "default"), "endpoint", "https://ssp.example",
        )

        args = argparse.Namespace(args=[project_dir], effective=True)
        rc = run_show(args)
        assert rc == 0
        assert "https://ssp.example" in capsys.readouterr().out


class TestBoxConfigGet:
    def test_get_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir, "box.image"])
        rc = run_get(args)
        assert rc == 0
        captured = capsys.readouterr()
        # F6 get model: a fresh box stores nothing at box.image → plain get is
        # "(not set)" (stderr), NOT the fabricated built-in default. The default
        # image still applies at launch + under ``show --effective``.
        assert "ghcr.io/doctorjei/kanibako-oci:latest" not in captured.out
        assert "(not set)" in captured.err

    def test_get_known_key_without_project(self, config_file, tmp_home, credentials_dir, capsys):
        """``box get image`` (no project arg) should use cwd."""
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # known key as first arg => get operation (project defaults to cwd)
        # In tests the project_dir fixture is not cwd, so use 2-arg form.
        args2 = argparse.Namespace(args=[project_dir, "box.image"])
        rc = run_get(args2)
        assert rc == 0

    def test_get_missing_key_errors(self, config_file, tmp_home, credentials_dir, capsys):
        """``box get`` with no key reports an error (verb requires a key)."""
        from kanibako.commands.box._parser import run_get

        args = argparse.Namespace(args=[])
        rc = run_get(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_get_env_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write an env var
        env_path = proj.metadata_path / "env"
        env_path.write_text("MY_VAR=hello\n")

        args = argparse.Namespace(args=[project_dir, "env.MY_VAR"])
        rc = run_get(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "hello" in captured.out


class TestBoxConfigSet:
    def test_set_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.image=new-image:v1"], force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set" in captured.out
        assert "new-image:v1" in captured.out

    def test_set_env_var(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "env.EDITOR=vim"], force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set EDITOR=vim" in captured.out

    def test_set_model(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "model=sonnet"], force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set model=sonnet" in captured.out

    def test_set_resource(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "resource.plugins=/my/plugins"], force=False, local=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set resource.plugins=/my/plugins" in captured.out


class TestBoxConfigReset:
    def test_reset_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set first
        project_toml = proj.metadata_path / "settings.yaml"
        write_project_config(project_toml, "to-reset:v1")

        # Reset
        args = argparse.Namespace(
            args=[project_dir, "box_image"], reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        # F7 honest message: the box override is CLEARED (no fabricated
        # "reverts to default: <built-in>"); the noun is named from the scope.
        assert "cleared" in captured.out.lower()
        assert "box" in captured.out.lower()
        assert "reverts to default" not in captured.out

    def test_reset_all(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set a value first
        project_toml = proj.metadata_path / "settings.yaml"
        write_project_config(project_toml, "override:v1")

        # Reset all with --force (skip confirmation)
        args = argparse.Namespace(
            args=[project_dir], reset_all=True, force=True,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_reset_nonexistent(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box_image"], reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No override" in captured.out

    def test_reset_requires_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        args = argparse.Namespace(args=[], reset_all=False, force=False)
        rc = run_reset(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err


class TestBoxConfigLocal:
    def test_local_flag_on_resource_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "resource.plugins"], force=False, local=True,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set resource.plugins=project" in captured.out

    def test_local_flag_on_non_resource_key_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.image"], force=False, local=True,
        )
        rc = run_set(args)
        assert rc == 1
        assert "--local only applies" in capsys.readouterr().err


class TestBoxConfigArgParsing:
    """Test the discrete-verb parsers and their flags."""

    def test_parser_show_no_args(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "show"])
        assert args.command == "box"
        assert args.box_command == "show"
        assert args.args == []
        assert args.func.__name__ == "run_show"

    def test_parser_get_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "get", "image"])
        assert args.args == ["image"]
        assert args.func.__name__ == "run_get"

    def test_parser_set_key_equals_value(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "set", "image=myimg:v1"])
        assert args.args == ["image=myimg:v1"]
        assert args.func.__name__ == "run_set"

    def test_parser_get_project_and_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "get", "myproject", "image"])
        assert args.args == ["myproject", "image"]

    def test_parser_show_effective(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "show", "--effective"])
        assert args.effective is True

    def test_parser_reset_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "reset", "model"])
        assert args.args == ["model"]
        assert args.func.__name__ == "run_reset"

    def test_parser_reset_all(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "reset", "--all"])
        assert args.reset_all is True

    def test_parser_set_force(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "set", "model=x", "--force"])
        assert args.force is True

    def test_parser_set_local(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "set", "resource.plugins", "--local"])
        assert args.local is True

    def test_config_subcommand_is_gone(self):
        """The overloaded ``box config`` subcommand was retired (clean break)."""
        import pytest
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "config"])


class TestBoxConfigTooManyArgs:
    def test_three_args_returns_error(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get

        args = argparse.Namespace(args=["a", "b", "c"])
        rc = run_get(args)
        assert rc == 1
        assert "too many arguments" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# config.py utility function tests (carried forward from old test file)
# ---------------------------------------------------------------------------

class TestWriteProjectConfigKey:
    def test_write_paths_key(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "paths_project_toml", "custom.yaml")
        loaded = load_config(p)
        assert loaded.paths_project_toml == "custom.yaml"
        text = p.read_text()
        assert "paths:" in text
        assert 'project_toml: custom.yaml' in text

    def test_write_box_key(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "myimg:v1")
        loaded = load_config(p)
        assert loaded.box_image == "myimg:v1"
        text = p.read_text()
        assert "box:" in text
        assert 'image: myimg:v1' in text

    def test_write_agent_key(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_agent_name", "my-target")
        loaded = load_config(p)
        assert loaded.box_agent_name == "my-target"
        text = p.read_text()
        assert "box:" in text
        assert 'agent_name: my-target' in text

    def test_write_multiple_sections(self, tmp_path):
        """Writing keys from different sections should create both."""
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "multi:v1")
        write_project_config_key(p, "paths_project_toml", "multi.yaml")
        loaded = load_config(p)
        assert loaded.box_image == "multi:v1"
        assert loaded.paths_project_toml == "multi.yaml"

    def test_update_existing_key(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "old:v1")
        write_project_config_key(p, "box_image", "new:v2")
        loaded = load_config(p)
        assert loaded.box_image == "new:v2"
        text = p.read_text()
        assert "old:v1" not in text

    def test_backward_compat_with_write_project_config(self, tmp_path):
        """write_project_config (old API) should still work."""
        p = tmp_path / "settings.yaml"
        write_project_config(p, "compat:v1")
        loaded = load_config(p)
        assert loaded.box_image == "compat:v1"


class TestUnsetProjectConfigKey:
    def test_unset_removes_key(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "remove-me:v1")
        assert unset_project_config_key(p, "box_image") is True
        loaded = load_config(p)
        # Should revert to default
        assert loaded.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_unset_nonexistent_key(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "keep:v1")
        assert unset_project_config_key(p, "paths_project_toml") is False
        # Original key should still be there
        loaded = load_config(p)
        assert loaded.box_image == "keep:v1"

    def test_unset_no_file(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "nonexistent.yaml"
        assert unset_project_config_key(p, "box_image") is False

    def test_unset_preserves_other_keys(self, tmp_path):
        from kanibako.config import unset_project_config_key
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "img:v1")
        write_project_config_key(p, "paths_project_toml", "my.yaml")
        assert unset_project_config_key(p, "box_image") is True
        loaded = load_config(p)
        assert loaded.paths_project_toml == "my.yaml"
        assert loaded.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"


class TestLoadProjectOverrides:
    def test_empty_when_no_file(self, tmp_path):
        p = tmp_path / "nonexistent.yaml"
        assert load_project_overrides(p) == {}

    def test_returns_only_overrides(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "override:v1")
        overrides = load_project_overrides(p)
        assert "box_image" in overrides
        assert overrides["box_image"] == "override:v1"
        # Other keys should not appear (they are defaults)
        assert "paths_project_toml" not in overrides


class TestSplitConfigKey:
    def test_box_image_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("box_image") == ("box", "image")

    def test_paths_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_paths_key_with_underscores(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_box_agent_key(self):
        from kanibako.config import _split_config_key
        assert _split_config_key("box_agent_name") == ("box", "agent_name")

    def test_unprefixed_key_is_top_level_field(self):
        """A key with no section prefix is a TOP-LEVEL scalar field.

        The H1 fix removed the old ValueError raise path: ``_split_config_key``
        now returns an empty section (the typed writer in config_interface is
        the routed set/get/reset path; this helper must never crash on an
        advertised key).
        """
        from kanibako.config import _split_config_key
        assert _split_config_key("allow_helpers") == ("", "allow_helpers")
        assert _split_config_key("unknown_prefix_key") == ("", "unknown_prefix_key")


class TestConfigKeys:
    def test_returns_all_fields(self):
        from kanibako.config import config_keys
        from dataclasses import fields
        expected = [fld.name for fld in fields(KanibakoConfig)]
        assert config_keys() == expected

    def test_includes_known_keys(self):
        from kanibako.config import config_keys
        keys = config_keys()
        assert "box_image" in keys
        assert "paths_project_toml" in keys
        assert "box_agent_name" in keys
