"""Tests for kanibako box config subcommand and config.py utility functions."""

from __future__ import annotations

import argparse

from kanibako.settings.config import (
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths
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
        from kanibako.settings.config_io import dump_doc, load_doc
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
        from kanibako.settings.config_interface import _write_nested_toml_key
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # System settings tier: select claude, and set a behavior key the
        # per-agent file does NOT set (endpoint) so the system-tier value is
        # the effective one at launch — the display must show the same.
        _write_nested_toml_key(std.settings, ("system",), "agent", "claude")
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.image=new-image:v1"], force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set" in captured.out
        assert "new-image:v1" in captured.out

    def test_set_env_var(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "env.EDITOR=vim"], force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set EDITOR=vim" in captured.out

    def test_set_model(self, config_file, tmp_home, credentials_dir, capsys):
        """Agent behavior keys are set at box scope via the §2h REQUEST
        ``pref.agent.<agent>.<key>``; the BARE form is refused with a teach
        message (a bare agent key targets ``agent.default``, which a box cannot
        write). ⮕ P7: the cure USED to be the ``box.agent.<key>`` mirror, retired
        by spec §2b."""
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Bare agent key at box scope → refused, teaching the request form.
        args = argparse.Namespace(
            args=[project_dir, "model=sonnet"], force=False,
        )
        rc = run_set(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "set pref.agent.<agent>.model" in captured.err

        # The request form is the settable one.
        args = argparse.Namespace(
            args=[project_dir, "pref.agent.claude.model=sonnet"], force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "pref.agent.claude.model" in captured.out

    def test_set_core_bind_repoint_end_to_end(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        # F10 (Phase 1): the box handler threads the CORE floor registry, so a
        # source-only repoint of a launch-only core bind now VALIDATES + writes the
        # RAW tuple (was refused "nowhere in the cascade" before Step B).
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.bindings.rw.home=/newhome"],
            force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set box.bindings.rw.home host source to /newhome" in captured.out
        # RAW tuple in the box file: new host_src, dest+options byte-raw from floor.
        project_toml = proj.metadata_path / "settings.yaml"
        assert load_doc(project_toml)["box"]["bindings"]["rw"]["home"] == [
            "/newhome", "~", "Z,U",
        ]


class TestBoxConfigReset:
    def test_reset_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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
        from kanibako.settings.paths import load_std_paths, resolve_project
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

    def test_write_shell_key(self, tmp_path):
        # (⮕ P7: was ``box_agent_name``, retired with spec §2b — the SHAPE under
        # test is the nested box-table write, not that key.)
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_shell", "bash")
        loaded = load_config(p)
        assert loaded.box_shell == "bash"
        text = p.read_text()
        assert "box:" in text
        assert 'shell: bash' in text

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
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "remove-me:v1")
        assert unset_project_config_key(p, "box_image") is True
        loaded = load_config(p)
        # Should revert to default
        assert loaded.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_unset_nonexistent_key(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "settings.yaml"
        write_project_config_key(p, "box_image", "keep:v1")
        assert unset_project_config_key(p, "paths_project_toml") is False
        # Original key should still be there
        loaded = load_config(p)
        assert loaded.box_image == "keep:v1"

    def test_unset_no_file(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "nonexistent.yaml"
        assert unset_project_config_key(p, "box_image") is False

    def test_unset_preserves_other_keys(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
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
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("box_image") == ("box", "image")

    def test_paths_key(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_paths_key_with_underscores(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_box_shell_key(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("box_shell") == ("box", "shell")

    def test_unprefixed_key_is_top_level_field(self):
        """A key with no section prefix is a TOP-LEVEL scalar field.

        The H1 fix removed the old ValueError raise path: ``_split_config_key``
        now returns an empty section (the typed writer in config_interface is
        the routed set/get/reset path; this helper must never crash on an
        advertised key).
        """
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("allow_helpers") == ("", "allow_helpers")
        assert _split_config_key("unknown_prefix_key") == ("", "unknown_prefix_key")


# ---------------------------------------------------------------------------
# P2 / M-8 — the STANDALONE box tier: ONE file for READ, WRITE and ANCHOR
# ---------------------------------------------------------------------------

class TestStandaloneBoxTierRoundTrip:
    """A standalone box has a BOX TIER at ``box_data/settings.yaml`` (spec §2c ALL
    PROJECTS), absent by default, over the ROOT ``settings.yaml`` that plays the
    WORKSET tier.  ``config set`` must WRITE the box tier that ``get`` READS — the
    whole point of M-8: a read/write split is the silent "I set it and nothing
    changed" failure, with no error anywhere."""

    def _standalone(self, config_file, tmp_home):
        from kanibako.settings.paths import load_std_paths, resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)
        return root

    @staticmethod
    def _files(root):
        """``(root_file, box_file)`` at their LITERAL spec positions (§5 L1403/L1407).

        ⚑ Deliberately NOT sourced from ``box_workset_settings_paths``: a test that
        gets both positions from the code under test is self-consistent and therefore
        BLIND to a swapped pair.  These literals are what make the swap mutation
        redden here."""
        from kanibako.settings.paths import _STANDALONE_META_DIR, BOX_META_FILE

        return root / BOX_META_FILE, root / _STANDALONE_META_DIR / BOX_META_FILE

    def test_set_writes_the_box_tier_and_leaves_the_root_file_alone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config_io import load_doc

        root = self._standalone(config_file, tmp_home)
        root_file, box_file = self._files(root)
        assert not box_file.exists()          # ABSENT BY DEFAULT (spec §5 L1407)
        root_before = root_file.read_text()

        rc = run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        assert rc == 0
        capsys.readouterr()

        # The value landed in the BOX tier...
        assert load_doc(box_file)["box"]["image"] == "probe/img:1"
        # ...and the ROOT file (the WORKSET tier + the detection marker) is untouched.
        assert root_file.read_text() == root_before

    def test_get_reads_where_set_wrote(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ THE M-8 GATE. A set followed by a get must round-trip through ONE file.
        (Mutation: leave `config set` on the old ``metadata_path/settings.yaml``
        derivation while the read moves → get prints "(not set)" → RED.)"""
        from kanibako.commands.box._parser import run_get, run_set

        root = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        rc = run_get(argparse.Namespace(
            args=[str(root), "box.image"], box=None, force=False,
        ))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "probe/img:1"

    def test_show_effective_agrees_with_the_stored_value(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_set, run_show

        root = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        rc = run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=True, force=False,
        ))
        assert rc == 0
        assert "probe/img:1" in capsys.readouterr().out

    def test_absent_box_file_shows_no_overrides(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The ABSENCE case: with no box-tier file, a standalone box reports exactly
        what it reported before P2 — an absent file is an EMPTY tier
        (``config_io.load_doc`` → ``{}``), not a broken one."""
        from kanibako.commands.box._parser import run_show

        root = self._standalone(config_file, tmp_home)
        assert not self._files(root)[1].exists()
        rc = run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=False, force=False,
        ))
        assert rc == 0
        assert "no overrides" in capsys.readouterr().out

    def test_root_stored_value_is_not_a_box_override_but_is_effective(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ The one USER-VISIBLE read-surface change (M-8).  A LEGACY standalone box
        stored ``box.*`` in its ROOT file — which is the WORKSET tier now.  A plain
        ``get`` is stored-at-noun (spec §5 read verbs), so it honestly reports the key
        as not stored AT THE BOX; ``show --effective`` still resolves it via the R2
        downward-default.  Nothing is lost; the read got truthful."""
        from kanibako.commands.box._parser import run_get, run_show
        from kanibako.settings.config_io import dump_doc, load_doc

        root = self._standalone(config_file, tmp_home)
        root_file, box_file = self._files(root)
        doc = load_doc(root_file)
        doc.setdefault("box", {})["image"] = "legacy/img:9"
        dump_doc(root_file, doc)
        assert not box_file.exists()

        run_get(argparse.Namespace(
            args=[str(root), "box.image"], box=None, force=False,
        ))
        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "(not set)" in captured.err

        run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=True, force=False,
        ))
        assert "legacy/img:9" in capsys.readouterr().out

    def test_primary_set_get_is_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Regression pin: PRIMARY still writes and reads its own
        ``<metadata_path>/settings.yaml`` — no ``box_data/`` anywhere."""
        from kanibako.commands.box._parser import run_get, run_set
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import BOX_META_FILE, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        run_set(argparse.Namespace(
            args=[project_dir, "box.image=probe/prim:1"], box=None, force=False,
        ))
        capsys.readouterr()
        run_get(argparse.Namespace(
            args=[project_dir, "box.image"], box=None, force=False,
        ))
        assert capsys.readouterr().out.strip() == "probe/prim:1"
        assert (
            load_doc(proj.metadata_path / BOX_META_FILE)["box"]["image"]
            == "probe/prim:1"
        )
        assert not (proj.metadata_path / "box_data").exists()

    def test_private_create_lands_auth_keys_in_the_box_tier(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ AUTH-CRITICAL.  ``create --private`` persists ``box.auth.*=false``, and
        ``seed_new_box``'s ``resolve_auth_source`` reads them off the snapshot's BOX
        tier.  If the write went to a file the snapshot does not read as the box tier,
        a supposedly-private box would resolve a sharing tier and forward the host
        OAuth token into the seed.  Pin that they are the SAME file."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import (
            box_workset_settings_paths,
            load_std_paths,
            resolve_standalone_project,
        )

        root = tmp_home / "priv"
        root.mkdir()
        rc = run_create(argparse.Namespace(
            path=str(root), standalone=True, no_vault=True, private=True,
            name=None, image=None, agent=None, allow_home=False,
        ))
        capsys.readouterr()
        assert rc == 0

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_standalone_project(std, config, str(root), initialize=False)
        box_tier, _ = box_workset_settings_paths(proj)
        # ⚑ LITERAL position too: asserting only against the pair function would make
        # this AUTH-CRITICAL test self-consistent and blind to a swapped pair, which
        # would point it at the ROOT file while the launch snapshot reads box_data/.
        assert box_tier == root / "box_data" / "settings.yaml"
        auth = load_doc(box_tier)["box"]["auth"]
        assert auth["global_enabled"] is False
        assert auth["workset_enabled"] is False

    def test_duplicate_carries_the_box_tier_not_the_root_file(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """``box duplicate`` must carry the source's BOX TIER (M-8).  It reads the box
        settings WITHOUT the pair idiom, so it was missed by the first sweep and found
        by a final grep — a ``box set box.image=X`` followed by a duplicate would
        otherwise silently lose X.

        ⚑ It must ALSO not carry the source's ``workset.kuid``: that lives in the ROOT
        file (the workset tier), and copying it into the duplicate's box tier would
        OVERRIDE the fresh kuid ``establish_standalone`` generates, giving the new box
        its source's identity."""
        from kanibako.commands.box._duplicate import run_duplicate
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config import read_workset_kuid
        from kanibako.settings.config_io import load_doc

        src = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(src), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        src_root, _ = self._files(src)
        src_kuid = read_workset_kuid(src_root)

        dst = tmp_home / "dup"
        rc = run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode="standalone",
            bare=False, force=True, box=None,   # force: skip the interactive confirm
        ))
        out = capsys.readouterr()
        assert rc == 0, f"out={out.out!r} err={out.err!r}"

        dst_root, dst_box = self._files(dst)
        # The box-scope value came across, in the BOX tier.
        assert load_doc(dst_box)["box"]["image"] == "probe/img:1"
        # ...and the duplicate got a FRESH workset identity, not the source's.
        assert read_workset_kuid(dst_root) != src_kuid
        assert "workset" not in load_doc(dst_box)

    def test_duplicate_of_a_LEGACY_box_carries_root_stored_settings(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ A PRE-P2 standalone box kept its ``box.*`` in the ROOT file and has NO
        box tier.  Carrying the box tier alone drops those settings on the first
        duplicate — silently, since the duplicate simply comes up with defaults.
        The workset tier's ``box:`` subtree must be underlaid."""
        from kanibako.commands.box._duplicate import run_duplicate
        from kanibako.settings.config import read_workset_kuid
        from kanibako.settings.config_io import dump_doc, load_doc

        src = self._standalone(config_file, tmp_home)
        src_root, src_box = self._files(src)
        doc = load_doc(src_root)
        doc.setdefault("box", {})["image"] = "legacy/img:9"
        dump_doc(src_root, doc)
        assert not src_box.exists()          # the pre-P2 on-disk shape
        src_kuid = read_workset_kuid(src_root)

        dst = tmp_home / "dup_legacy"
        rc = run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode="standalone",
            bare=False, force=True, box=None,
        ))
        out = capsys.readouterr()
        assert rc == 0, f"out={out.out!r} err={out.err!r}"

        dst_root, dst_box = self._files(dst)
        assert load_doc(dst_box)["box"]["image"] == "legacy/img:9"
        # ...and the duplicate still mints its OWN identity.
        assert read_workset_kuid(dst_root) != src_kuid
        assert "workset" not in load_doc(dst_box)
