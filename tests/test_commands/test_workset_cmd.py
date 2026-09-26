"""Tests for kanibako.commands.workset_cmd."""

from __future__ import annotations

import argparse

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths
from kanibako.settings.settings_resolve import normalize_bind_dest
from kanibako.project.workset import (
    add_project,
    create_workset,
    list_worksets,
)


def _workset_boxes(ws):
    """Read *ws*'s per-workset ``boxes:`` membership (the D10 connection index)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws.root, load_doc(ws.root / "workset.yaml"),
    )
    return workset_registry.load_workset_boxes(registry_path)


def _assert_every_key_declared(doc):
    """Every dotted LEAF path in *doc* must be a DECLARED key.

    The keyspace is CLOSED (spec §0): an undeclared path is not a key at all, so
    a settings file that carries one is a violation, not merely a dead write.
    Derived from ``key_validity`` rather than from a list of expected names.
    """
    from kanibako.settings.settings_keyspace import key_validity

    def walk(node, prefix):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
                continue
            reason = key_validity(path, valid_agents=())
            assert reason is None, reason

    walk(doc, "")


class TestWorksetCreate:
    def test_create_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "myworkset"
        args = argparse.Namespace(
            path=str(ws_root), name=None,
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created working set" in out
        assert ws_root.resolve().is_dir()

    def test_create_with_name_override(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "myworkset2"
        args = argparse.Namespace(
            path=str(ws_root), name="custom-name",
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "custom-name" in out

    def test_create_defaults_to_cwd(self, config_file, tmp_home, capsys, monkeypatch):
        from kanibako.commands.workset_cmd import run_create

        ws_dir = tmp_home / "cwd_ws"
        ws_dir.mkdir()
        monkeypatch.chdir(ws_dir)
        # Since cwd exists and create_workset errors on existing root,
        # test that path=None uses cwd by checking the error message
        args = argparse.Namespace(
            path=None, name="cwdws",
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args)
        # cwd already exists, so this should fail with "already exists"
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_create_duplicate_name_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root1 = tmp_home / "ws1"
        args1 = argparse.Namespace(
            path=str(ws_root1), name="dup",
            standalone=False, image=None, no_vault=False,
        )
        run_create(args1)

        ws_root2 = tmp_home / "ws2"
        args2 = argparse.Namespace(
            path=str(ws_root2), name="dup",
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args2)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already in use" in err

    def test_create_refuses_primary_box_name_collision(
        self, config_file, tmp_home, capsys
    ):
        """Cross-kind (per-kind name policy, Jei 2026-07-08): a new workset whose
        name is ALREADY a primary box name refuses (teaching --force), leaving no
        on-disk skeleton."""
        from kanibako.commands.workset_cmd import run_create
        from kanibako.settings.paths import load_std_paths, register_primary_box_name

        config = load_config(config_file)
        std = load_std_paths(config)
        register_primary_box_name(
            std.primary_workset, std.registry, "common", str(tmp_home / "box"),
        )

        ws_root = tmp_home / "shared_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="common",
            standalone=False, image=None, no_vault=False, force=False,
        )
        rc = run_create(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "primary box" in err and "--force" in err
        # Refused before side effects — no workset skeleton on disk.
        assert not ws_root.exists()

    def test_create_force_allows_primary_box_name_collision(
        self, config_file, tmp_home, capsys
    ):
        """--force lets a workset take a primary-box name (deliberate shadow)."""
        from kanibako.commands.workset_cmd import run_create
        from kanibako.settings.paths import load_std_paths, register_primary_box_name

        config = load_config(config_file)
        std = load_std_paths(config)
        register_primary_box_name(
            std.primary_workset, std.registry, "common", str(tmp_home / "box"),
        )

        ws_root = tmp_home / "shared_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="common",
            standalone=False, image=None, no_vault=False, force=True,
        )
        rc = run_create(args)
        assert rc == 0
        assert "Created working set" in capsys.readouterr().out
        assert ws_root.resolve().is_dir()

    def test_create_reserved_sentinel_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        args = argparse.Namespace(
            path=str(tmp_home / "ws-primary"), name="__PRIMARY__",
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "reserved" in err

    def test_create_auto_name_collision_refused(self, config_file, tmp_home, capsys):
        # The default name derives from path.name; a collision must still refuse
        # cleanly (no auto-suffix) via the WorksetError caught in run_create.
        from kanibako.commands.workset_cmd import run_create

        (tmp_home / "common").mkdir()
        first = tmp_home / "a" / "common"
        args1 = argparse.Namespace(
            path=str(first), name=None,
            standalone=False, image=None, no_vault=False,
        )
        assert run_create(args1) == 0
        capsys.readouterr()

        second = tmp_home / "b" / "common"
        args2 = argparse.Namespace(
            path=str(second), name=None,
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args2)
        assert rc == 1
        assert "already in use" in capsys.readouterr().err

    def test_create_existing_root_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "existing"
        ws_root.mkdir()
        args = argparse.Namespace(
            path=str(ws_root), name="ex",
            standalone=False, image=None, no_vault=False,
        )
        rc = run_create(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_create_with_image(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_create

        ws_root = tmp_home / "image_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="imagews",
            standalone=False, image="custom:latest", no_vault=False,
        )
        rc = run_create(args)
        assert rc == 0

        # The image cascade setting lands in the root workset.yaml — which is
        # created ONLY because something was actually set.  ⚑ It is the ONE file
        # under the root: no identity table anywhere, and no registry.yaml until
        # there is a member to record.
        import yaml
        settings_yaml = ws_root.resolve() / "workset.yaml"
        assert settings_yaml.exists()
        with open(settings_yaml) as f:
            data = yaml.safe_load(f)
        assert data == {"box": {"image": "custom:latest"}}
        assert not (ws_root.resolve() / "registry.yaml").exists()
        # The name is in the GLOBAL registry, and only there.
        std = load_std_paths(load_config(config_file))
        assert list_worksets(std)["imagews"] == ws_root.resolve()

    def test_create_with_image_merges_into_the_existing_box_table(
        self, config_file, tmp_home, monkeypatch
    ):
        """--image sets ``box.image`` WITHIN the table already on disk.

        Assigning the whole ``box:`` table would drop every other ``box.*`` key
        the file already carries — which is exactly what the block's own "MERGE
        into the existing file, never overwrite" comment forbids.  The stamp is
        stubbed to leave a populated file behind, because that is the state the
        merge exists to survive.
        """
        from kanibako.commands.workset_cmd import run_create
        from kanibako.launch import templates

        real_install = templates.install_workset_template

        def _install_and_stamp_settings(std, workset_path, **kwargs):
            real_install(std, workset_path, **kwargs)
            (workset_path / "workset.yaml").write_text(
                "box:\n  shell: /bin/zsh\n  image: mould:stamped\n"
            )

        monkeypatch.setattr(
            templates, "install_workset_template", _install_and_stamp_settings
        )

        ws_root = tmp_home / "merge_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="mergews",
            standalone=False, image="custom:latest", no_vault=False,
        )
        assert run_create(args) == 0

        import yaml
        settings_yaml = ws_root.resolve() / "workset.yaml"
        data = yaml.safe_load(settings_yaml.read_text())
        # The explicit flag wins over the stamped value ...
        assert data["box"]["image"] == "custom:latest"
        # ... and every OTHER box.* key survives it.
        assert data["box"]["shell"] == "/bin/zsh"
        _assert_every_key_declared(data)

    def test_create_no_vault_writes_the_key_the_reader_looks_at(
        self, config_file, tmp_home
    ):
        """--no-vault lands at ``box.enable_vault``.

        That is where ``read_box_enable_vault``/``write_box_enable_vault`` look;
        a TOP-LEVEL ``enable_vault`` is not a declared key at all (spec §0), so
        it was both unread and a keyspace violation.
        """
        from kanibako.commands.workset_cmd import run_create
        from kanibako.settings.config import read_box_enable_vault

        ws_root = tmp_home / "novault_ws"
        args = argparse.Namespace(
            path=str(ws_root), name="novaultws",
            standalone=False, image=None, no_vault=True,
        )
        assert run_create(args) == 0

        import yaml
        settings_yaml = ws_root.resolve() / "workset.yaml"
        data = yaml.safe_load(settings_yaml.read_text())
        assert data == {"box": {"enable_vault": False}}
        _assert_every_key_declared(data)
        assert read_box_enable_vault(settings_yaml) is False

    def test_create_standalone_refused(self, config_file, tmp_home, capsys):
        """``--standalone`` is REFUSED, not accepted-and-inert (Jei, 2026-08-27).

        It still PARSES — the flag stays declared so the refusal can name it and
        hand back a cure — and it produces no working set: nothing on disk and
        nothing in the registry, because the refusal precedes every side effect.
        """
        from kanibako.cli import build_parser
        from kanibako.settings.paths import load_std_paths

        ws_root = tmp_home / "standalone_ws"
        args = build_parser().parse_args(
            ["workset", "create", str(ws_root), "--name", "standalonews",
             "--standalone"],
        )
        assert args.standalone is True

        assert args.func(args) == 1
        err = capsys.readouterr().err
        # The cure, not an inventory of the wording: name the flag, and hand back
        # the spelling that DOES make a standalone box.
        assert "--standalone" in err
        assert "kanibako box create --standalone" in err

        assert not ws_root.exists()
        assert "standalonews" not in list_worksets(load_std_paths(load_config(config_file)))


class TestWorksetList:
    def test_list_empty_shows_only_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # The synthesized default workset is always present.
        assert "default" in out
        assert "<default workset>" in out
        assert "NAME" in out

    def test_list_shows_worksets(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("alpha", tmp_home / "ws_alpha", std)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "NAME" in out

    def test_list_shows_project_count(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("beta", tmp_home / "ws_beta", std)

        src = tmp_home / "proj_src"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "beta" in out
        assert "1" in out

    def test_list_quiet(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("quiet1", tmp_home / "ws_quiet1", std)
        create_workset("quiet2", tmp_home / "ws_quiet2", std)

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        # 2 named worksets + the synthesized default.
        assert len(lines) == 3
        assert "default" in lines
        assert "quiet1" in lines
        assert "quiet2" in lines
        # Quiet mode should not have header
        assert "NAME" not in out

    def test_list_quiet_empty_shows_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Even with no named worksets, the default workset is listed.
        assert out.strip() == "default"


class TestWorksetRm:
    def test_rm_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("todel", tmp_home / "ws_todel", std)

        args = argparse.Namespace(name="todel", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deleted working set 'todel'" in out

    def test_rm_with_purge(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmfiles", tmp_home / "ws_rmfiles", std)
        root = ws.root

        assert root.is_dir()
        args = argparse.Namespace(name="rmfiles", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0
        assert not root.is_dir()

    def test_rm_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        args = argparse.Namespace(name="nonexistent", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err

    def test_rm_with_projects_errors_without_force(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("hasproj", tmp_home / "ws_hasproj", std)

        src = tmp_home / "proj_src_rm"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(name="hasproj", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "has 1 project(s)" in err
        assert "--force" in err

    def test_rm_with_projects_succeeds_with_force(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("hasproj2", tmp_home / "ws_hasproj2", std)

        src = tmp_home / "proj_src_rm2"
        src.mkdir()
        add_project(ws, "myproj", src)

        args = argparse.Namespace(name="hasproj2", purge=False, force=True)
        rc = run_rm(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deleted working set 'hasproj2'" in out


class TestWorksetConnect:
    def test_connect_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("addws", tmp_home / "ws_add", std)

        src = tmp_home / "add_src"
        src.mkdir()

        args = argparse.Namespace(
            workset="addws", source=str(src), project_name=None, force=False,
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Added project" in out
        assert "add_src" in out

    def test_connect_defaults_to_cwd(self, config_file, tmp_home, capsys, monkeypatch):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cwdws", tmp_home / "ws_cwd", std)

        cwd_dir = tmp_home / "cwd_proj"
        cwd_dir.mkdir()
        monkeypatch.chdir(cwd_dir)

        args = argparse.Namespace(
            workset="cwdws", source=None, project_name=None, force=False,
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "cwd_proj" in out

    def test_connect_custom_name(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("namews", tmp_home / "ws_name", std)

        src = tmp_home / "name_src"
        src.mkdir()

        args = argparse.Namespace(
            workset="namews", source=str(src), project_name="custom-name", force=False,
        )
        rc = run_connect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "custom-name" in out

    def test_connect_duplicate_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("dupws", tmp_home / "ws_dup", std)

        src = tmp_home / "dup_src"
        src.mkdir()
        add_project(ws, "proj1", src)

        args = argparse.Namespace(
            workset="dupws", source=str(src), project_name="proj1", force=False,
        )
        rc = run_connect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_connect_external_writes_override_and_symlink(
        self, config_file, tmp_home, capsys
    ):
        """connect to an EXTERNAL dir → workspace override in the per-workset
        boxes: connection record (D10), and a workspaces/{name} symlink to the
        dir.  P8b/Option A: the override is NO LONGER written to box.yaml —
        the per-workset registry is the sole connection record."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("extws", tmp_home / "ws_ext", std)

        # External source: a sibling of the workset root, outside ws.root.
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()

        args = argparse.Namespace(
            workset="extws", source=str(external), project_name="ext", force=False,
        )
        rc = run_connect(args)
        assert rc == 0

        # P8b/Option A: no on-disk ``project:`` identity is written for the
        # connected box — the override lives ONLY in the per-workset registry.
        project_toml = ws.projects_dir / "ext" / "box.yaml"
        assert "project" not in load_doc(project_toml)

        # The per-workset registry has the connection record: box name → external
        # path (the D10 replacement for the global connected: index).
        assert _workset_boxes(ws).get("ext") == str(external)

        # workspaces/ext is a SYMLINK to the external dir, not a real dir.
        link = ws.workspaces_dir / "ext"
        assert link.is_symlink()
        assert link.resolve() == external

    def test_connect_internal_no_override_no_symlink(
        self, config_file, tmp_home, capsys
    ):
        """connect to a dir INSIDE the workset root → a real workspaces/{name} dir,
        no override, and a membership row recording THAT dir."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("intws", tmp_home / "ws_int", std)

        # Internal source: a directory inside the workset root.
        internal = ws.root / "inside_src"
        internal.mkdir()

        args = argparse.Namespace(
            workset="intws", source=str(internal), project_name="int", force=False,
        )
        rc = run_connect(args)
        assert rc == 0

        # No workspace override written (box.yaml not pre-seeded).
        project_toml = ws.projects_dir / "int" / "box.yaml"
        assert "project" not in load_doc(project_toml)

        # ⚑⚑ EVERY member gets a row, in-tree as well as external — and an in-tree
        # member's row records ``workspaces/<name>``, the dir it actually runs on,
        # not the caller's source argument (which was `inside_src` here).
        assert _workset_boxes(ws) == {"int": str(ws.workspaces_dir / "int")}

        # workspaces/int is a real directory, not a symlink.
        wsdir = ws.workspaces_dir / "int"
        assert wsdir.is_dir()
        assert not wsdir.is_symlink()

    def test_connect_standalone_refused_without_force(
        self, config_file, tmp_home, capsys
    ):
        """B2a: connecting a self-declared standalone box (in-place marker) is
        REFUSED without --force (no silent absorb/steal)."""
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("saws", tmp_home / "ws_sa", std)

        external = (tmp_home / "sa_box").resolve()
        external.mkdir()
        (external / "box_data").mkdir()
        (external / "workset.yaml").write_text("project: {}\n")

        args = argparse.Namespace(
            workset="saws", source=str(external), project_name="sb", force=False,
        )
        rc = run_connect(args)
        assert rc == 1
        assert "standalone box" in capsys.readouterr().err
        # Nothing registered — no steal.
        assert "sb" not in _workset_boxes(ws)

    def test_connect_standalone_absorbed_with_force(
        self, config_file, tmp_home, capsys
    ):
        """B2a: --force deliberately absorbs the standalone box as a workset box
        (registers the connection record)."""
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("saws2", tmp_home / "ws_sa2", std)

        external = (tmp_home / "sa_box2").resolve()
        external.mkdir()
        (external / "box_data").mkdir()
        (external / "workset.yaml").write_text("project: {}\n")

        args = argparse.Namespace(
            workset="saws2", source=str(external), project_name="sb", force=True,
        )
        rc = run_connect(args)
        assert rc == 0
        assert _workset_boxes(ws).get("sb") == str(external)


class TestWorksetDisconnect:
    def test_disconnect_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmws", tmp_home / "ws_rm", std)

        src = tmp_home / "rm_src"
        src.mkdir()
        add_project(ws, "rmproj", src)

        args = argparse.Namespace(
            workset="rmws", project="rmproj",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Removed project 'rmproj'" in out

    def test_disconnect_with_files(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmfws", tmp_home / "ws_rmf", std)

        src = tmp_home / "rmf_src"
        src.mkdir()
        add_project(ws, "rmfproj", src)

        # Verify per-project dirs were created.
        assert (ws.projects_dir / "rmfproj").is_dir()

        args = argparse.Namespace(
            workset="rmfws", project="rmfproj",
            remove_files=True, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 0
        assert not (ws.projects_dir / "rmfproj").is_dir()

    @pytest.mark.parametrize("remove_files", [True, False])
    def test_disconnect_remove_files_deletes_the_box_logs(
        self, config_file, tmp_home, remove_files,
    ):
        """``--remove-files`` deletes every per-box log file from the RESOLVED
        ``workset.logs`` (where the helper-log mount is bound from); a plain disconnect
        keeps them."""
        from kanibako.commands.workset_cmd import run_disconnect
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import box_log_files

        config = load_config(config_file)
        std = load_std_paths(config)
        root = (tmp_home / "ws_logs").resolve()
        ws = create_workset("logsws", root, std)
        elsewhere = tmp_home / "log-store"
        dump_doc(root / "workset.yaml", {"workset": {"logs": str(elsewhere)}})
        src = tmp_home / "logs_src"
        src.mkdir()
        add_project(ws, "logsproj", src)
        logs = box_log_files(elsewhere, "logsproj")
        elsewhere.mkdir()
        for log in logs:
            log.write_text("x")

        args = argparse.Namespace(
            workset="logsws", project="logsproj",
            remove_files=remove_files, force=True,
        )
        assert run_disconnect(args) == 0
        assert [log.exists() for log in logs] == [not remove_files] * len(logs)

    def test_disconnect_unresolvable_logs_refuses_before_deleting(
        self, config_file, tmp_home,
    ):
        """A ``workset.logs`` that does not resolve refuses ``--remove-files`` WHOLE:
        the box tree and its membership are still there after the refusal."""
        from kanibako.commands.workset_cmd import run_disconnect
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.settings_resolve import SettingsError

        config = load_config(config_file)
        std = load_std_paths(config)
        root = (tmp_home / "ws_badlogs").resolve()
        ws = create_workset("badlogsws", root, std)
        dump_doc(root / "workset.yaml", {"workset": {"logs": "@workset.nosuchkey/x"}})
        src = tmp_home / "badlogs_src"
        src.mkdir()
        add_project(ws, "badlogsproj", src)

        args = argparse.Namespace(
            workset="badlogsws", project="badlogsproj", remove_files=True, force=True,
        )
        with pytest.raises(SettingsError):
            run_disconnect(args)
        assert (ws.projects_dir / "badlogsproj").is_dir()
        assert "badlogsproj" in _workset_boxes(ws)

    def test_disconnect_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("rmunkws", tmp_home / "ws_rmunk", std)

        args = argparse.Namespace(
            workset="rmunkws", project="nope",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err


class TestWorksetInfo:
    def test_info_success(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("infows", tmp_home / "ws_info", std)

        src = tmp_home / "info_src"
        src.mkdir()
        add_project(ws, "infoproj", src)

        args = argparse.Namespace(name="infows")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "infows" in out
        assert "infoproj" in out
        assert "Root:" in out
        # ⚑ ``created`` is gone from the model, so there is no line to print.
        assert "Created:" not in out

    def test_info_unknown_error(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info

        args = argparse.Namespace(name="nosuchws")
        rc = run_info(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err


class TestWorksetConfig:
    def test_config_show_empty(self, config_file, tmp_home, capsys):
        """Config show with no overrides prints '(no overrides)'."""
        from kanibako.commands.workset_cmd import run_show

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cfgws", tmp_home / "ws_cfg", std)

        args = argparse.Namespace(workset="cfgws", effective=False)
        rc = run_show(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no overrides" in out

    def test_config_set_get_share_allowed_roundtrips(self, config_file, tmp_home, capsys):
        """``workset.auth.share_allowed`` is now an ORDINARY settable bool key;
        setting it then getting it round-trips through the set/get plumbing."""
        from kanibako.commands.workset_cmd import run_get, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("authcfg", tmp_home / "ws_authcfg", std)

        set_args = argparse.Namespace(
            workset="authcfg", key_value="workset.auth.share_allowed=false",
            force=False,
        )
        rc = run_set(set_args)
        assert rc == 0
        capsys.readouterr()

        get_args = argparse.Namespace(workset="authcfg", key="workset.auth.share_allowed")
        rc = run_get(get_args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "false" in out.lower()

    def test_config_set_regular_key(self, config_file, tmp_home, capsys):
        """Setting a regular config key writes to config.yaml.

        Uses ``workset.vault_ro`` — a real workset-scope regular string key,
        legal at the workset scope by construction (same-scope write).
        """
        from kanibako.commands.workset_cmd import run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("regcfg", tmp_home / "ws_regcfg", std)

        args = argparse.Namespace(
            workset="regcfg", key_value="workset.vault_ro=/ro",
            force=False,
        )
        rc = run_set(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Set" in out
        assert "vault" in out

    def test_config_reset_key(self, config_file, tmp_home, capsys):
        """Resetting a config key removes the override."""
        from kanibako.commands.workset_cmd import run_reset, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetcfg", tmp_home / "ws_resetcfg", std)

        # First set a value (workset.vault_ro = a real workset-scope key,
        # legal at the workset scope by construction).
        set_args = argparse.Namespace(
            workset="resetcfg", key_value="workset.vault_ro=/ro",
            force=False,
        )
        run_set(set_args)
        capsys.readouterr()

        # Then reset it.
        reset_args = argparse.Namespace(
            workset="resetcfg", key="workset.vault_ro", reset_all=False, force=False,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        # F7 honest message on a successful clear (or "No override" if absent).
        assert "cleared" in out.lower() or "No override" in out
        assert "reverts to default" not in out

    def test_config_reset_share_allowed(self, config_file, tmp_home, capsys):
        """Resetting the ordinary ``workset.auth.share_allowed`` override removes
        it via the standard reset plumbing."""
        from kanibako.commands.workset_cmd import run_reset, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetauth", tmp_home / "ws_resetauth", std)

        # First set an override.
        set_args = argparse.Namespace(
            workset="resetauth", key_value="workset.auth.share_allowed=false",
            force=False,
        )
        run_set(set_args)
        capsys.readouterr()

        # Then reset it.
        reset_args = argparse.Namespace(
            workset="resetauth", key="workset.auth.share_allowed",
            reset_all=False, force=False,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        out = capsys.readouterr().out
        # F7 honest message on a successful clear (or "No override" if absent).
        assert "cleared" in out.lower() or "No override" in out
        assert "reverts to default" not in out

    def test_config_reset_all(self, config_file, tmp_home, capsys):
        """reset --all clears all overrides."""
        from kanibako.commands.workset_cmd import run_reset, run_set

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("resetall", tmp_home / "ws_resetall", std)

        # Set a value first (workset.vault_ro = a real workset-scope key,
        # legal at the workset scope by construction).
        set_args = argparse.Namespace(
            workset="resetall", key_value="workset.vault_ro=/ro",
            force=False,
        )
        run_set(set_args)
        capsys.readouterr()
        # Two pref REQUESTS beside it (Q86 = (a): --all clears them too).
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc
        ws_config = _workset_config_path(ws)
        doc = load_doc(ws_config)
        doc["pref"] = {
            "system": {"agent": "goose"}, "agent": {"claude": {"model": "opus"}},
        }
        dump_doc(ws_config, doc)

        # Reset all.
        reset_args = argparse.Namespace(
            workset="resetall", key=None, reset_all=True, force=True,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "Reset 3 override(s)."
        assert "pref" not in load_doc(ws_config)

    def test_config_reset_requires_key(self, config_file, tmp_home, capsys):
        """reset without a key or --all is an error."""
        from kanibako.commands.workset_cmd import run_reset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("resetbare", tmp_home / "ws_resetbare", std)

        args = argparse.Namespace(
            workset="resetbare", key=None, reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_config_unknown_workset(self, config_file, tmp_home, capsys):
        """Config on unknown workset returns error."""
        from kanibako.commands.workset_cmd import run_show

        args = argparse.Namespace(workset="nosuchws", effective=False)
        rc = run_show(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not registered" in err


class TestDefaultWorksetCli:
    def _std(self, config_file):
        config = load_config(config_file)
        return load_std_paths(config)

    def test_config_set_regular_key_writes_spec_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        # workset.vault_ro = a real workset-scope regular string key, legal at
        # the workset scope by construction (same-scope write; lands nested under
        # the [workset] table).
        # F4: the write lands in the spec §2c file
        # ``@config.primary_workset/workset.yaml`` (the old
        # ``@config.data/config.yaml`` target was a launch-invisible dead write).
        from kanibako.commands.workset_cmd import run_set
        std = self._std(config_file)

        args = argparse.Namespace(
            workset="default", key_value="workset.vault_ro=/ro",
            force=False,
        )
        rc = run_set(args)
        assert rc == 0
        import yaml
        with open(std.primary_workset / "workset.yaml") as f:
            data = yaml.safe_load(f)
        assert data["workset"]["vault_ro"] == "/ro"
        assert not (std.data_path / "config.yaml").exists()
        assert not (std.data_path / "workset.yaml").exists()

    def test_info_default(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_info
        args = argparse.Namespace(name="default")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "__default__" in out
        assert "<default workset>" in out

    def test_rm_default_refused(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_rm
        for name in ("default", "__default__"):
            args = argparse.Namespace(name=name, purge=False, force=True)
            rc = run_rm(args)
            assert rc == 1
            err = capsys.readouterr().err
            assert "cannot be removed" in err

    def test_disconnect_default_refused(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect
        args = argparse.Namespace(
            workset="default", project="anything",
            remove_files=False, force=True,
        )
        rc = run_disconnect(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot be removed" in err


class TestWorksetParser:
    """Test parser aliases and subcommand registration."""

    def test_aliases_registered(self):
        """Verify that ls, inspect, and delete aliases are registered."""
        import argparse
        from kanibako.commands.workset_cmd import add_parser

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        add_parser(subs)

        # These should parse without error (aliases are recognized).
        # We test by parsing a few known alias forms.
        args = parser.parse_args(["workset", "ls"])
        assert hasattr(args, "func")

        args = parser.parse_args(["workset", "inspect", "myws"])
        assert hasattr(args, "func")

        args = parser.parse_args(["workset", "delete", "myws", "--force"])
        assert hasattr(args, "func")


class TestPrimaryWorksetSpecConvergence:
    """F4: the PRIMARY workset's settings write path AND every launch reader
    converge on the spec location ``@config.primary_workset/workset.yaml``
    (spec §2c: PRIMARY ``meta.workset.path`` = ``@config.primary_workset``;
    ALL-WORKSETS ``meta.workset.settings`` = ``@meta.workset.path/workset.yaml``).

    Pins the fix for the 1.6.0 three-file split: the CLI wrote
    ``@config.data/config.yaml`` (a silent dead write) while the cascade read
    ``@config.data/settings.yaml`` — neither the spec file.
    """

    def _set_default(self, key_value: str) -> int:
        from kanibako.commands.workset_cmd import run_set

        args = argparse.Namespace(
            workset="default", key_value=key_value, force=False,
        )
        return run_set(args)

    def test_set_default_writes_spec_file(self, config_file, tmp_home, capsys):
        """``workset set default <key>`` lands in the §2c spec file."""
        import yaml

        std = load_std_paths(load_config(config_file))
        rc = self._set_default("box.shell=/bin/zsh")
        assert rc == 0
        spec_file = std.primary_workset / "workset.yaml"
        assert spec_file.is_file()
        with open(spec_file) as f:
            data = yaml.safe_load(f)
        assert data["box"]["shell"] == "/bin/zsh"

    def test_set_default_no_longer_writes_legacy_file(
        self, config_file, tmp_home, capsys,
    ):
        """The legacy dead-write target ``@config.data/config.yaml`` stays absent."""
        std = load_std_paths(load_config(config_file))
        rc = self._set_default("box.shell=/bin/zsh")
        assert rc == 0
        assert not (std.data_path / "config.yaml").exists()

    def test_set_default_resolves_in_primary_box_snapshot(
        self, config_file, tmp_home, capsys,
    ):
        """End-to-end (the audit's probe shape): a ``workset set default`` value
        must reach a primary-mode box's REAL launch snapshot — CLI write, then
        the launch's own workset-tier file derivation, then the committed
        ``build_launch_snapshot`` pipeline.
        """
        from kanibako.settings.paths import host_xdg_map, resolve_project
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        config = load_config(config_file)
        std = load_std_paths(config)
        rc = self._set_default("box.shell=/bin/zsh")
        assert rc == 0
        capsys.readouterr()

        (tmp_home / "proj").mkdir()
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "proj"), initialize=True,
        )
        assert proj.group is not None and proj.group.is_default
        # The launch's workset-tier settings-file derivation (start.py).
        from kanibako.settings.paths import workset_settings_path
        workset_path = workset_settings_path(proj.group)
        ctx = ResolveCtx(
            agent_name=None,
            workset_name=None,
            host_home=str(tmp_home),
            xdg=host_xdg_map(std.data_home),
        )
        snap = build_launch_snapshot(
            agent_name="shell",
            ctx=ctx,
            system_path=std.settings,
            agent_path=None,
            workset_path=workset_path,
            box_path=None,
        )
        assert snap.box.shell == "/bin/zsh"

    def test_get_default_roundtrips_spec_file(self, config_file, tmp_home, capsys):
        """``workset get default <key>`` reads back what set wrote."""
        from kanibako.commands.workset_cmd import run_get

        rc = self._set_default("workset.vault_ro=/ro")
        assert rc == 0
        capsys.readouterr()
        args = argparse.Namespace(workset="default", key="workset.vault_ro")
        rc = run_get(args)
        assert rc == 0
        assert "/ro" in capsys.readouterr().out.lower()

    def test_named_workset_settings_file_unchanged(
        self, config_file, tmp_home, capsys,
    ):
        """A NAMED workset keeps its single ``<root>/workset.yaml`` file."""
        import yaml
        from kanibako.commands.workset_cmd import run_set

        std = load_std_paths(load_config(config_file))
        ws = create_workset("namedcfg", tmp_home / "ws_namedcfg", std)
        args = argparse.Namespace(
            workset="namedcfg", key_value="workset.vault_ro=/ro",
            force=False,
        )
        rc = run_set(args)
        assert rc == 0
        with open(ws.root / "workset.yaml") as f:
            data = yaml.safe_load(f)
        assert data["workset"]["vault_ro"] == "/ro"


class TestWorksetEnv:
    """``workset config set workset.env.<VAR>`` — the workset env tier.

    ⚑ FLIPPED by B9. The bare ``env.<VAR>`` spelling wrote a docker ``.env`` FILE
    at the workset root, threaded into the engine as ``env_path``; R-39 retired
    the spelling and RQ-1 retired the launch READ of those files, so both the
    threading and the file are gone. The workset env tier is the settings key
    ``workset.env.<VAR>``, stored in the workset's own ``workset.yaml`` — for
    BOTH named and primary worksets.
    """

    def test_bare_env_set_is_refused_with_the_workset_cure(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_set

        std = load_std_paths(load_config(config_file))
        ws = create_workset("envws", tmp_home / "ws_env", std)
        args = argparse.Namespace(
            workset="envws", key_value="env.EDITOR=vim", force=False,
        )
        assert run_set(args) == 1
        assert "workset.env.EDITOR" in capsys.readouterr().err
        assert not (ws.root / "env").exists()

    def test_bare_env_get_is_refused_at_the_handler(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_get

        std = load_std_paths(load_config(config_file))
        create_workset("envgetbare", tmp_home / "ws_envgetbare", std)
        rc = run_get(argparse.Namespace(workset="envgetbare", key="env.MY_VAR"))
        assert rc == 1
        assert "workset.env.MY_VAR" in capsys.readouterr().err

    def test_set_env_named_workset(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_set

        std = load_std_paths(load_config(config_file))
        ws = create_workset("envws2", tmp_home / "ws_env2", std)
        args = argparse.Namespace(
            workset="envws2", key_value="workset.env.EDITOR=vim", force=False,
        )
        rc = run_set(args)
        assert rc == 0
        assert "Set workset.env.EDITOR=vim" in capsys.readouterr().out
        from kanibako.settings.config_io import load_doc
        assert load_doc(ws.root / "workset.yaml")["workset"]["env"]["EDITOR"] == "vim"

    def test_set_env_default_workset(self, config_file, tmp_home, capsys):
        """PRIMARY: the value lands under ``@config.primary_workset``."""
        from kanibako.commands.workset_cmd import run_set

        std = load_std_paths(load_config(config_file))
        args = argparse.Namespace(
            workset="default", key_value="workset.env.EDITOR=vim", force=False,
        )
        rc = run_set(args)
        assert rc == 0
        assert "Set workset.env.EDITOR=vim" in capsys.readouterr().out
        from kanibako.settings.config_io import load_doc
        assert load_doc(
            std.primary_workset / "workset.yaml",
        )["workset"]["env"]["EDITOR"] == "vim"

    def test_get_env_workset(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_get, run_set

        std = load_std_paths(load_config(config_file))
        create_workset("envget", tmp_home / "ws_envget", std)
        args = argparse.Namespace(
            workset="envget", key_value="workset.env.MY_VAR=hello", force=False,
        )
        assert run_set(args) == 0
        capsys.readouterr()
        get_args = argparse.Namespace(workset="envget", key="workset.env.MY_VAR")
        rc = run_get(get_args)
        assert rc == 0
        assert "hello" in capsys.readouterr().out

    def test_reset_env_workset(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_reset, run_set

        std = load_std_paths(load_config(config_file))
        ws = create_workset("envreset", tmp_home / "ws_envreset", std)
        args = argparse.Namespace(
            workset="envreset", key_value="workset.env.MY_VAR=hello", force=False,
        )
        assert run_set(args) == 0
        reset_args = argparse.Namespace(
            workset="envreset", key="workset.env.MY_VAR",
            reset_all=False, force=False,
        )
        rc = run_reset(reset_args)
        assert rc == 0
        capsys.readouterr()
        from kanibako.settings.config_io import load_doc
        assert "env" not in load_doc(ws.root / "workset.yaml").get("workset", {})

    def _env_from_the_files(self, config_file):
        """The container env a launch would build from what the VERBS wrote.

        The route, in the order the launch runs it: read the two settings files the
        ``system set`` / ``workset set`` verbs produced, adapt them to the entry list
        (``snapshot_category_entries``), fold the env slots
        (``store_collapse.collapse_env`` — ``meta.assembly.env``'s producer) and
        project them through the launch's own consumer.
        """
        from kanibako.commands.start import _build_config_env
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_launch import snapshot_category_entries
        from kanibako.settings.settings_resolve import ResolveCtx
        from kanibako.settings.store_collapse import collapse_env

        std = load_std_paths(load_config(config_file))
        snapshot = KeyStore({
            "system": {
                "env": dict(load_doc(std.settings).get("system", {}).get("env", {})),
            },
            "workset": {
                "env": dict(
                    load_doc(std.primary_workset / "workset.yaml")
                    .get("workset", {}).get("env", {})
                ),
            },
        })
        ctx = ResolveCtx(
            agent_name="claude", workset_name=None,
            host_home="/home/host", xdg={"XDG_DATA_HOME": "/data"},
        )
        return _build_config_env(collapse_env(snapshot_category_entries(
            snapshot, active_agent="claude", box_ctx=ctx,
        )))

    def test_primary_workset_env_reaches_the_launch_env(
        self, config_file, tmp_home, capsys,
    ):
        """A primary-workset env var written by the VERB reaches the container env.

        🛑 REBUILT ONTO THE COLLAPSE, AND ITS OLD ORACLE WAS THE INVERSE OF THE
        RULED MODEL. It declared ``system.env.EDITOR`` AND ``workset.env.EDITOR``
        and asserted ``vim`` — "the workset overrides the system tier" — because
        the consumer's dict-update over a scope-sorted list was the only thing
        deciding a contested VAR, and it landed the LAST entry. The variables
        realize SYSTEM-FIRST, so that config no longer picks a winner in either
        direction: it REFUSES, which the case below now pins. Flipping the assert
        would have pinned an outcome no route produces.

        What survives is the claim the test is FOR: a value only the workset file
        declares is delivered.
        """
        from kanibako.commands.system_cmd import run_set as system_set
        from kanibako.commands.workset_cmd import run_set

        assert system_set(argparse.Namespace(
            key_value="system.env.PAGER=less", force=True,
        )) == 0
        assert run_set(argparse.Namespace(
            workset="default", key_value="workset.env.EDITOR=vim", force=False,
        )) == 0
        capsys.readouterr()

        env = self._env_from_the_files(config_file)
        assert env["EDITOR"] == "vim"   # the workset file's own variable
        assert env["PAGER"] == "less"   # and the system file's, beside it

    def test_the_same_VAR_at_system_and_workset_refuses(
        self, config_file, tmp_home, capsys,
    ):
        """Two scopes' keys, one slot: the launch refuses and names both keys.

        The twin the case above used to assert a winner for. ⚑ Written through the
        VERBS on purpose — this is the config a user can reach by running two
        supported commands, so the refusal has to be the one they meet.
        """
        from kanibako.commands.system_cmd import run_set as system_set
        from kanibako.commands.workset_cmd import run_set
        from kanibako.settings.settings_resolve import SettingsError

        assert system_set(argparse.Namespace(
            key_value="system.env.EDITOR=nano", force=True,
        )) == 0
        assert run_set(argparse.Namespace(
            workset="default", key_value="workset.env.EDITOR=vim", force=False,
        )) == 0
        capsys.readouterr()

        with pytest.raises(SettingsError) as exc:
            self._env_from_the_files(config_file)

        message = str(exc.value)
        assert "system.env.EDITOR" in message
        assert "workset.env.EDITOR" in message
        # The system scope acts FIRST, so it is the holder and the workset key is
        # the arrival — the direction, not just the pair.
        assert message.index("system.env.EDITOR") < message.index(
            "workset.env.EDITOR"
        )


class TestPrimaryWorksetMigration:
    """The approved F4 migration behavior (director ruling (c), 2026-07-02):
    DROP the legacy locations — ``@config.data/settings.yaml`` is never read
    into the cascade and never touched on disk."""

    def test_legacy_data_settings_yaml_dropped(self, config_file, tmp_home):
        """Approved migration behavior (ruling (c) 2026-07-02: drop the legacy location).

        The legacy 1.6.0 read location ``@config.data/settings.yaml`` is
        DROPPED — never read into the cascade, never touched on disk.
        """
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.paths import (
            host_xdg_map,
            resolve_project,
            workset_settings_path,
        )
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        config = load_config(config_file)
        std = load_std_paths(config)
        legacy = std.data_path / "settings.yaml"
        # ⚑ The resolve no longer materializes the store (set-door brick repair),
        # so the test stages its own legacy file — the subject here is the DROP,
        # not directory creation.
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy_text = "box:\n  shell: /bin/legacy\n"
        legacy.write_text(legacy_text)

        (tmp_home / "migproj").mkdir()
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "migproj"), initialize=True,
        )

        ctx = ResolveCtx(
            agent_name=None,
            workset_name=None,
            host_home=str(tmp_home),
            xdg=host_xdg_map(std.data_home),
        )
        snap = build_launch_snapshot(
            agent_name="shell",
            ctx=ctx,
            system_path=std.settings,
            agent_path=None,
            workset_path=workset_settings_path(proj.group),
            box_path=None,
        )
        box = snap.box if "box" in snap else KeyStore()
        assert "shell" not in box       # the legacy value must NOT resolve
        assert legacy.read_text() == legacy_text  # and the file is untouched


class TestWorksetCmdSystemFloor:
    """S-4: ``workset share list --effective`` must resolve the SAME ``system.*`` tier
    a launch does — its whole job is to say what a launch would mount.

    ⚑⚑ THE OLD VERSION OF THIS CLASS DID NOT CATCH A MISSING KEY, and that is why it is
    rewritten rather than extended.  It read the two functions' SOURCE TEXT for four
    hard-coded key names, so it compared each map against a list, not against the other
    map and not against the declared keyspace.  Both maps could be — and both were —
    wrong about a key it did not name: the display floor carried NONE of the five
    ``system.channels.*`` leaves and the launch floor was missing
    ``system.channels.broadcast``, and this class was green throughout.  A pin that
    enumerates what it checks can only ever catch the names its author already thought
    of, which is the opposite of the property.

    Both carriers now read ``settings/paths.system_path_floor``.  The cases below pin
    that they DO (by mutation) and that the display renders everything the launch floor
    answers (by derivation), so no key name appears in this file at all.

    ⚑ The failure this guards is SILENT by construction: an unresolvable ``@``-ref is a
    legitimately-absent referent, not an error, so the binding row simply does not
    print, with rc 0.
    """

    _DEST = "/home/agent/floor-probe"

    def _workset(self, std, tmp_home):
        """One real workset; the caller re-points its single binding per case."""
        ws_root = tmp_home / "worksets" / "floor-set"
        create_workset("floor-set", ws_root, std)
        return "floor-set", ws_root

    def _bind_at(self, ws_root, source):
        """Re-point the workset's one RO binding at *source*."""
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc

        doc_path = ws_root / WORKSET_META_FILE
        doc = load_doc(doc_path) or {}
        doc.setdefault("workset", {}).setdefault("bindings", {})["ro"] = {
            self._DEST: [source],
        }
        dump_doc(doc_path, doc)

    def _effective(self, name, capsys):
        from kanibako.commands import workset_cmd

        rc = workset_cmd.run_share_list(
            argparse.Namespace(workset=name, effective=True)
        )
        assert rc == 0
        return capsys.readouterr().out

    def test_the_display_renders_every_key_the_launch_floor_answers(
        self, std, config, project_dir, tmp_home, capsys,
    ):
        """The value oracle: the launch's OWN output supplies the key list.

        A key present in one carrier and absent in the other reds here whatever it is
        called, which is exactly what naming the keys could not do.
        """
        from kanibako.settings.paths import resolve_project
        from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        resolved_sys = resolve_inputs(
            subject=ResolveSubject.BOX, std=std, proj=proj, agent_name="claude",
            system_path=std.settings,
        ).system_floor
        assert resolved_sys, "the launch floor is empty; this oracle would be vacuous"

        name, ws_root = self._workset(std, tmp_home)
        for key, value in sorted(resolved_sys.items()):
            self._bind_at(ws_root, f"@{key}")
            out = self._effective(name, capsys)
            assert f"{value} -> {self._DEST}" in out, (
                f"@{key} is in the LAUNCH floor and did not render in the "
                f"--effective display: the display omits a binding a launch mounts"
            )

    def test_both_carriers_read_the_one_builder(
        self, std, config, project_dir, tmp_home, capsys, monkeypatch,
    ):
        """MUTATION on the shared builder: drop a key and BOTH sides must lose it.

        Two hand-written maps would each keep their own copy and neither would notice.
        """
        from kanibako.settings import paths as paths_mod
        from kanibako.settings.paths import resolve_project, system_path_floor
        from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs

        dropped = "system.channelroot"
        monkeypatch.setattr(
            paths_mod, "system_path_floor",
            lambda s: {k: v for k, v in system_path_floor(s).items() if k != dropped},
        )
        # Both sides import it lazily (``settings_launch.resolve_inputs`` and
        # ``workset_cmd``), so the one module patch reaches them both.
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        resolved_sys = resolve_inputs(
            subject=ResolveSubject.BOX, std=std, proj=proj, agent_name="claude",
            system_path=std.settings,
        ).system_floor
        assert dropped not in resolved_sys

        name, ws_root = self._workset(std, tmp_home)
        self._bind_at(ws_root, f"@{dropped}")
        out = self._effective(name, capsys)
        assert self._DEST not in out, (
            "the display still resolved a key the shared builder no longer emits, so "
            "it is reading a second map of its own"
        )


class TestWorksetCreateIsAtomicOnRefusal:
    """F2: a whitelist refusal must leave NOTHING behind.

    ⚑ "Loud and leak-free" is not the same as "clean". Refusing part-way through the
    mould stamp satisfied both of those and still left a REGISTERED workset with a
    root, its own ``workset.yaml`` and a PARTIAL chapter copy — recoverable only by
    ``workset rm``. The check therefore runs BEFORE anything is registered or
    created, matching the order ``create_workset`` already uses for its name guards.
    """

    def _plant(self, std, name):
        (std.template / "workset").mkdir(parents=True, exist_ok=True)
        (std.template / "workset" / name).write_text("boxes: {}\n")

    def test_refused_create_leaves_no_registration_and_no_root(
        self, std, config, tmp_path, capsys,
    ):
        import argparse

        from kanibako.commands import workset_cmd
        from kanibako.launch.templates import install_packaged_templates
        from kanibako.project.workset import list_worksets

        install_packaged_templates(std, [])
        self._plant(std, "registry.yaml")

        root = tmp_path / "ws-refused"
        rc = workset_cmd.run_create(
            argparse.Namespace(path=str(root), name="refused", force=False)
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "WORKSET" in err and "registry.yaml" in err
        # NOTHING was created, and nothing was registered.
        assert not root.exists(), sorted(root.rglob("*")) if root.exists() else None
        assert "refused" not in list_worksets(std)

    def test_a_clean_mould_still_creates_normally(self, std, config, tmp_path):
        import argparse

        from kanibako.commands import workset_cmd
        from kanibako.launch.templates import install_packaged_templates
        from kanibako.project.workset import list_worksets

        install_packaged_templates(std, [])
        root = tmp_path / "ws-ok"
        assert workset_cmd.run_create(
            argparse.Namespace(path=str(root), name="okset", force=False)
        ) == 0
        assert "okset" in list_worksets(std)
        assert (root / "canon" / "handbook" / "SYS_WORKSET.md").is_file()


class TestWorksetGetIsWiredToTheClosedKeyspace:
    """spec §0 at the ``workset`` noun — the twin of the box noun's gate.

    ⚑ END-TO-END through the verb; the predicate itself is pinned in
    ``tests/test_settings/test_config_interface.py``.
    """

    def _ws(self, config_file, tmp_home, name):
        std = load_std_paths(load_config(config_file))
        return create_workset(name, tmp_home / f"ws_{name}", std)

    def _merge(self, ws, table):
        """MERGE into the workset's settings file — never overwrite it: the create
        wrote content of its own, and clobbering it tests a file no user has."""
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc

        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc.setdefault("workset", {}).update(table)
        dump_doc(path, doc)

    def test_an_undeclared_key_refuses_at_rc_1_NAMING_it(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_get

        # MUTATION-PROVED: neuter the handler's ``scope_read_key_error`` call and this
        # reds with ``assert 0 == 1``, as does the box noun's twin.
        self._ws(config_file, tmp_home, "keyspacews")
        rc = run_get(argparse.Namespace(workset="keyspacews", key="workset.frob"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "(not set)" not in err
        assert "workset.frob" in err

    def test_a_DECLARED_but_unset_key_still_answers_not_set_at_rc_0(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_get

        self._ws(config_file, tmp_home, "unsetws")
        rc = run_get(argparse.Namespace(workset="unsetws", key="workset.vault_ro"))
        assert rc == 0
        assert "(not set)" in capsys.readouterr().err

    def test_a_HAND_AUTHORED_bind_entry_still_reads_back(
        self, config_file, tmp_home, capsys,
    ):
        """§0: *"Refuse the write; keep the read honest."*"""
        from kanibako.commands.workset_cmd import run_get

        ws = self._ws(config_file, tmp_home, "bindws")
        self._merge(ws, {
            "bindings": {"ro": {"/in/box": ["/on/host", "ro"]}},
            "synced": {"/in/box/s": ["/on/host/s"]},
        })
        for key in ("workset.bindings.ro./in/box", "workset.synced./in/box/s"):
            assert run_get(
                argparse.Namespace(workset="bindws", key=key),
            ) == 0, key
            assert "/on/host" in capsys.readouterr().out, key

    def test_the_BARE_agent_key_refusal_still_wins_over_the_generic_one(
        self, config_file, tmp_home, capsys,
    ):
        """ORDER pin: a recognised spelling keeps the cure written for it. The §0 gate
        runs LAST, or it would overwrite a specific refusal with a vaguer one."""
        from kanibako.commands.workset_cmd import run_get

        self._ws(config_file, tmp_home, "barews")
        assert run_get(argparse.Namespace(workset="barews", key="model")) == 1
        err = capsys.readouterr().err
        assert "pref.agent.<agent>.model" in err
        assert "cannot be read" not in err

    def test_workset_show_marks_a_hand_written_undeclared_entry(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_show

        ws = self._ws(config_file, tmp_home, "showws")
        self._merge(ws, {"vault_ro": "/ro", "frob": 1})
        assert run_show(argparse.Namespace(workset="showws", effective=False)) == 0
        out = capsys.readouterr().out
        assert "undeclared" in out
        assert "workset.frob = 1" in out

    def test_workset_show_prints_no_such_block_for_a_clean_file(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_show

        ws = self._ws(config_file, tmp_home, "cleanws")
        self._merge(ws, {"vault_ro": "/ro"})
        assert run_show(argparse.Namespace(workset="cleanws", effective=False)) == 0
        assert "undeclared" not in capsys.readouterr().out


class TestWorksetShowListsTheAbstractTrio:
    """spec §0: *"They remain real, declared keys: a user sets them in YAML …,
    ``config show`` lists them"* — at the noun that OWNS a workset's declarations.

    The trio (``common`` / ``caches`` / ``seeded``) is YAML-only, so every fixture
    here hand-authors it; a ``set`` would be refused (terminal category).

    ⚑ MUTATION-PROVED: drop ``system_settings_path=ws_config`` from the ``show``
    branch of ``commands/workset_cmd.py`` and all three families vanish from both
    views — which is the defect these pin.
    ⚑ The PLAIN view is the requirement (Jei, 2026-09-19), not only ``--effective``.
    """

    TRIO = {
        "common": {"~/shared/docs": ["teamdocs"]},
        "caches": {"~/.cache/uv": ["uv"]},
        "seeded": {"~/.bashrc": ["bashrc"]},
    }

    def _ws(self, config_file, tmp_home, name):
        std = load_std_paths(load_config(config_file))
        return create_workset(name, tmp_home / f"ws_{name}", std)

    def _merge(self, ws, table):
        """MERGE into the workset's settings file — never overwrite it."""
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc

        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc.setdefault("workset", {}).update(table)
        dump_doc(path, doc)

    def _show(self, name, capsys, *, effective):
        from kanibako.commands.workset_cmd import run_show

        assert run_show(argparse.Namespace(workset=name, effective=effective)) == 0
        return capsys.readouterr().out

    @pytest.mark.parametrize("effective", [False, True])
    def test_every_abstract_family_is_listed(
        self, config_file, tmp_home, capsys, effective,
    ):
        ws = self._ws(config_file, tmp_home, "trio")
        self._merge(ws, self.TRIO)
        out = self._show("trio", capsys, effective=effective)
        for cat, table in self.TRIO.items():
            for dest in table:
                assert f"workset.{cat}.{normalize_bind_dest(dest)}" in out, out

    def test_a_declaration_is_listed_ONCE(self, config_file, tmp_home, capsys):
        """One carrier, one row: the flatten is the only reader of this file."""
        ws = self._ws(config_file, tmp_home, "trioonce")
        self._merge(ws, self.TRIO)
        out = self._show("trioonce", capsys, effective=False)
        for cat, table in self.TRIO.items():
            for dest in table:
                dotted = f"workset.{cat}.{normalize_bind_dest(dest)}"
                assert out.count(dotted) == 1, dotted

    def test_a_row_prints_the_key_the_resolve_uses_and_its_source_as_written(
        self, config_file, tmp_home, capsys,
    ):
        """The destination prints NORMALIZED — the spelling ``parse_bind_map`` keys it by
        (R-11), so this row and the ``--effective`` derived block name one declaration with
        one key — and the entry prints ``src`` / ``src  [options]``, never the Python repr
        ``['uv']``.  A ``masks`` destination is carried as written by the resolve, so it
        prints as written here too.
        MUTATION: route bind maps back through the plain walk in
        ``config_display._flatten_table`` and every assertion below but the ``masks`` one
        reds."""
        ws = self._ws(config_file, tmp_home, "triospell")
        self._merge(ws, {
            "common": {"~/shared/docs/": ["teamdocs"]},
            "caches": {"~/.cache/uv": ["uv", "Z,U"]},
            "masks": {"~/.ssh": True},
        })
        out = self._show("triospell", capsys, effective=False)
        assert f"workset.common.{normalize_bind_dest('~/shared/docs')} = teamdocs\n" in out
        assert f"workset.caches.{normalize_bind_dest('~/.cache/uv')} = uv  [Z,U]\n" in out
        assert "workset.masks.~/.ssh = true\n" in out
        assert "['" not in out, out
        assert "workset.common.~/" not in out, out

    def test_a_pref_request_is_not_doubled_by_the_flatten(
        self, config_file, tmp_home, capsys,
    ):
        """The flatten is this noun's one ``pref`` producer; ``_pref_overrides`` serves the
        box alone, so a request prints once."""
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc

        ws = self._ws(config_file, tmp_home, "trioprefs")
        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc.setdefault("pref", {}).setdefault("system", {})["agent"] = "claude"
        dump_doc(path, doc)
        out = self._show("trioprefs", capsys, effective=False)
        assert out.count("pref.system.agent = claude") == 1

    def test_a_clean_workset_still_reports_no_overrides(
        self, config_file, tmp_home, capsys,
    ):
        """The flatten must not manufacture a row out of a create-written file."""
        self._ws(config_file, tmp_home, "trioclean")
        assert "no overrides" in self._show("trioclean", capsys, effective=False)

    @pytest.mark.parametrize("effective", [False, True])
    def test_a_dropped_upward_table_is_announced_ONCE(
        self, config_file, tmp_home, capsys, caplog, effective,
    ):
        """spec §0: a containing scope's table is dropped *"with a warning naming the file
        and key"*.  The PLAIN view assembled nothing, so it printed no warning at all; both
        views now name the file and key, once.
        MUTATION: drop ``path=path`` from ``config_interface._noun_stored_view``'s
        ``cascade_view`` call and the plain case reds; skip the ``_DROP_WARNED`` check in
        ``settings_assemble._warn_upward_drops`` and the ``--effective`` case reds."""
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc

        ws = self._ws(config_file, tmp_home, f"triodrop{int(effective)}")
        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc["system"] = {"auth": {"share_allowed": False}}
        dump_doc(path, doc)
        with caplog.at_level("WARNING", logger="kanibako.settings.settings_assemble"):
            self._show(ws.name, capsys, effective=effective)
        drops = [
            m for m in caplog.messages
            if m.startswith(
                f"Dropping upward-scope key 'system' from workset settings file {path}"
            )
        ]
        assert len(drops) == 1, caplog.messages


class TestWorksetShowDerivesTheAbstractTrio:
    """keyspec §0: *"``--effective`` shows BOTH the declaration and the derived
    binding and a user can see WHY a mount exists."*

    The DECLARATION half is ``TestWorksetShowListsTheAbstractTrio`` above.  This is
    the DERIVED half, at the noun that owns a working set's declarations — and it is
    an ``--effective`` obligation only.

    ⚑ **EACH TEST NAMES THE GUARD IT DEPENDS ON, rather than one blanket mutation
    claim for the class.** The two tests that assert an ABSENCE cannot red under the
    mutation the four PRESENCE tests red under — dropping the
    ``_print_effective_derivations`` call from the ``show`` branch of
    ``commands/workset_cmd.py`` prints nothing, which is what they assert — so a
    claim spanning all six would be a gate that passes vacuously (P15).  The three
    guards, one per group:

    * the CALL in the ``show`` branch — the four tests asserting a printed row.
    * the ``not args.effective`` guard beside it —
      ``test_the_PLAIN_view_derives_nothing``, whose fixture declares the trio, so
      the block would print for the plain view without it.
    * the ``if not abstract: return 0`` gate in ``_print_effective_derivations`` —
      ``test_a_workset_with_no_abstract_declaration_prints_no_block``, which would
      otherwise receive the heading over an empty row list.

    🛑 Those three are read off the assertions and the guards, NOT measured by
    running the mutations: the tree is shared with other writers and a mutation
    parked in it is a trap for whoever gates next.

    ⚑ Nothing below hard-codes a host path: the expected source is derived from the
    working set's own root, which is what ``@meta.workset.path`` names (§2a
    "Declaration roots"), so a layout change reds instead of quietly outdating.
    """

    TRIO = {
        "common": {"~/shared/docs": ["teamdocs"]},
        "caches": {"~/.cache/uv": ["uv"]},
        "seeded": {"~/.bashrc": ["bashrc"]},
    }

    def _ws(self, config_file, tmp_home, name):
        std = load_std_paths(load_config(config_file))
        return create_workset(name, tmp_home / f"ws_{name}", std)

    def _merge(self, ws, table):
        """MERGE into the workset's settings file — the trio is YAML-only."""
        from kanibako.commands.workset_cmd import _workset_config_path
        from kanibako.settings.config_io import dump_doc, load_doc

        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc.setdefault("workset", {}).update(table)
        dump_doc(path, doc)

    def _show(self, name, capsys, *, effective=True, rc=0):
        from kanibako.commands.workset_cmd import run_show

        assert run_show(
            argparse.Namespace(workset=name, effective=effective),
        ) == rc
        return capsys.readouterr()

    def test_every_abstract_family_derives_its_binding(
        self, config_file, tmp_home, capsys,
    ):
        """One row per declaration, each carrying what it DERIVES — the §0 pair."""
        ws = self._ws(config_file, tmp_home, "deriv")
        self._merge(ws, self.TRIO)
        out = self._show("deriv", capsys).out

        # The MOUNT pair: source rooted at the workset root, arrow to the GUEST dest.
        assert f"{ws.root}/common/teamdocs -> /home/agent/shared/docs" in out
        assert "(mount)" in out
        assert f"{ws.root}/caches/uv -> /home/agent/.cache/uv" in out
        # ⚑ ``seeded`` derives a COPY, not a mount (§0), and the phrase says so.
        assert f"{ws.root}/seeded/bashrc -> /home/agent/.bashrc  (copy)" in out

    def test_the_declaration_key_is_named_beside_its_derivation(
        self, config_file, tmp_home, capsys,
    ):
        """A derivation the user cannot trace to a key is one they cannot act on."""
        ws = self._ws(config_file, tmp_home, "derivkey")
        self._merge(ws, self.TRIO)
        out = self._show("derivkey", capsys).out
        assert "workset.common./home/agent/shared/docs" in out

    def test_the_root_anchor_resolves(self, config_file, tmp_home, capsys):
        """🛑 The REGRESSION this pins: ``@meta.workset.path`` was absent from the
        preview floor, so a rooted declaration resolved to a bare ``/common/<src>``
        — a path that exists nowhere.  Derived from ``ws.root``, never a literal."""
        ws = self._ws(config_file, tmp_home, "derivroot")
        self._merge(ws, {"common": {"~/shared/docs": ["teamdocs"]}})
        out = self._show("derivroot", capsys).out
        assert str(ws.root) in out
        assert " /common/teamdocs" not in out

    def test_a_masked_declaration_prints_the_LOSS_not_a_mount(
        self, config_file, tmp_home, capsys,
    ):
        """The measured defect class: reading the pre-arbitration node alone reports
        a mount for a declaration a mask swallowed, with the mask invisible."""
        ws = self._ws(config_file, tmp_home, "derivmask")
        self._merge(ws, {
            "common": {"~/shared/docs": ["teamdocs"]},
            "masks": {"~/shared/docs": True},
        })
        out = self._show("derivmask", capsys).out
        assert "no mount" in out
        assert "workset.masks.~/shared/docs" in out
        assert "teamdocs -> " not in out

    def test_the_block_names_the_masks_it_does_not_apply(
        self, config_file, tmp_home, capsys,
    ):
        """The heading answers for this working set's file alone, so the line beneath it
        says which masks that leaves out and where the box's own answer is.
        MUTATION: delete the note's ``print`` in ``_print_effective_derivations`` and
        this reds."""
        ws = self._ws(config_file, tmp_home, "derivnote")
        self._merge(ws, self.TRIO)
        lines = self._show("derivnote", capsys).out.splitlines()
        heading = lines.index("Derived bindings for working set 'derivnote':")
        note = lines[heading + 1]
        assert note.startswith(
            "  (Masks from the base or system settings file, an agent file or a box's own file"
        ), note
        assert "'kanibako box show <box> --effective'" in note, note

    def test_the_PLAIN_view_derives_nothing(self, config_file, tmp_home, capsys):
        """The derived half is an ``--effective`` obligation; the plain view shows
        what this file OVERRIDES and must not grow a resolved answer."""
        ws = self._ws(config_file, tmp_home, "derivplain")
        self._merge(ws, self.TRIO)
        out = self._show("derivplain", capsys, effective=False).out
        assert "Derived bindings" not in out

    def test_a_workset_with_no_abstract_declaration_prints_no_block(
        self, config_file, tmp_home, capsys,
    ):
        """Nothing to arbitrate ⇒ no collapse and no block: the output a working set
        without the trio already had is unchanged."""
        ws = self._ws(config_file, tmp_home, "derivnone")
        self._merge(ws, {"bindings": {"ro": {"/opt/ref": ["/on/host"]}}})
        out = self._show("derivnone", capsys).out
        assert "Derived bindings" not in out


class TestWorksetPreviewResolvesTheDeclarationRoot:
    """``@meta.workset.path`` must resolve in BOTH ``--effective`` listings.

    ⚑ The anchor is §2a's workset ``<scope-root>``, so every rooted declaration and
    every value a user spells against it depends on it.  It was absent from the
    preview floor — ``paths.system_path_floor`` carries no ``meta.*`` key — and
    ``settings_expand`` renders an unresolvable anchor as the EMPTY string, so a
    share sourced ``@meta.workset.path/refdir`` listed as ``/refdir`` at rc 0 with no
    warning.  MEASURED on the shipped ``workset share list --effective``.
    """

    def _ws(self, config_file, tmp_home, name):
        std = load_std_paths(load_config(config_file))
        return create_workset(name, tmp_home / f"ws_{name}", std)

    def test_share_list_effective_roots_a_meta_workset_path_source(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import _workset_config_path, run_share_list
        from kanibako.settings.config_io import dump_doc, load_doc

        ws = self._ws(config_file, tmp_home, "anchorws")
        path = _workset_config_path(ws)
        doc = load_doc(path)
        doc.setdefault("workset", {})["bindings"] = {
            "ro": {"/opt/ref": ["@meta.workset.path/refdir"]},
        }
        dump_doc(path, doc)

        assert run_share_list(argparse.Namespace(
            workset="anchorws", effective=True,
        )) == 0
        out = capsys.readouterr().out
        assert f"{ws.root}/refdir -> /opt/ref" in out


class TestWorksetGetThreadsTheAgentsRoot:
    """``workset get <ws> agent.<node>.<key>`` reads the node's own file.

    The twin of the box noun's pin. The per-node families live in
    ``agents/<node>/agent.yaml``, reachable only through ``get_config_value``'s
    ``agents_root``; the handler withheld it, so the read answered "(not set)" at
    rc 0 for a value ``system get`` reported correctly on the same key.

    ⚑ MUTATION-PROVED: drop ``agents_root=std.agents`` from the ``get`` branch of
    ``commands/workset_cmd.py`` and the read-back test reds on "(not set)".
    """

    def _ws_and_agents_root(self, config_file, tmp_home, name):
        std = load_std_paths(load_config(config_file))
        create_workset(name, tmp_home / f"ws_{name}", std)
        return std.agents

    def _write_node(self, agents_root, key, value):
        """Write through the PRODUCTION set route, so this pins the read rather than
        a hand-built file shape the writer would never produce."""
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        msg = set_config_value(
            key, value,
            config_path=agents_root.parent / "unused-workset.yaml",
            command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg

    def test_a_persona_agent_key_reads_back(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_get

        agents_root = self._ws_and_agents_root(config_file, tmp_home, "nodews")
        self._write_node(agents_root, "agent.claude.model", "opus-test")

        rc = run_get(argparse.Namespace(workset="nodews", key="agent.claude.model"))
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "opus-test"
        assert "(not set)" not in captured.err

    def test_an_unset_node_key_is_still_honestly_not_set(
        self, config_file, tmp_home, capsys,
    ):
        """The threading must not fabricate: with nothing stored, "(not set)"
        stays "(not set)"."""
        from kanibako.commands.workset_cmd import run_get

        self._ws_and_agents_root(config_file, tmp_home, "nonodews")
        rc = run_get(argparse.Namespace(
            workset="nonodews", key="agent.claude.model",
        ))
        assert rc == 0
        assert "(not set)" in capsys.readouterr().err


class TestWorksetShareListArbitrates:
    """``workset share list --effective`` must answer with the COLLAPSE, not the entry list.

    Its whole job is to say what a launch would mount, and until 2026-08-26 it printed
    the pre-collapse ENTRY LIST: every stored binding, with no mask, no containment and
    no §0 row applied.  A workset that ALSO declared ``workset.masks`` over a share's
    destination listed that share as a live mount while the box received nothing there —
    rc 0, no message.

    ⚑ The two carriers are now ONE walk: ``store_shape.build_store_shape_set`` +
    ``store_collapse.collapse_store_shapes``, the same pair
    ``commands.start._install_assembly_collapse`` makes, with the per-share outcome read
    off ``store_collapse.pair_declarations`` — the function ``box show --effective``
    already pairs its own declarations with.
    """

    _SRC = "/etc/hostname"

    def _ws(self, std, tmp_home, name, node):
        """One real workset carrying *node* under its ``workset:`` root."""
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc

        ws_root = tmp_home / "worksets" / name
        create_workset(name, ws_root, std)
        doc_path = ws_root / WORKSET_META_FILE
        doc = load_doc(doc_path) or {}
        doc.setdefault("workset", {}).update(node)
        dump_doc(doc_path, doc)
        return ws_root

    def _effective(self, name, capsys):
        """``(rc, stdout, stderr)`` — the refusal path writes to stderr and rc 1."""
        from kanibako.commands import workset_cmd

        rc = workset_cmd.run_share_list(
            argparse.Namespace(workset=name, effective=True)
        )
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    def test_an_undisturbed_share_still_prints_as_a_mount(
        self, std, config, tmp_home, capsys,
    ):
        """The unchanged case: an arrow line, and NO result phrase under it."""
        self._ws(std, tmp_home, "arb-plain", {
            "bindings": {"ro": {"/opt/arb": [self._SRC]}},
        })
        rc, out, _err = self._effective("arb-plain", capsys)
        assert rc == 0
        assert f"  {self._SRC} -> /opt/arb  [ro]" in out
        assert "no mount" not in out

    def test_a_mask_at_the_destination_is_named_instead_of_a_mount(
        self, std, config, tmp_home, capsys,
    ):
        """A mask takes the share's own point: the box sees nothing, and the row says so."""
        self._ws(std, tmp_home, "arb-at", {
            "bindings": {"ro": {"/opt/arb": [self._SRC]}},
            "masks": {"/opt/arb": True},
        })
        rc, out, _err = self._effective("arb-at", capsys)
        assert rc == 0
        assert f"{self._SRC} -> /opt/arb" not in out, (
            "a share a mask swallowed is printed with the delivery arrow, so the "
            "listing claims a mount the box does not receive"
        )
        assert "/opt/arb  [ro]  (declared: /etc/hostname)" in out
        # ⚑ THE MASK'S OWN KEY, not just its destination. A mask has no host source,
        # so before 2026-08-27 this row named nothing the user could match against a
        # key they had written — the destination alone, which for the PARENT case
        # below is not even a path their own key spells.
        assert (
            "no mount — the mask declared by 'workset.masks./opt/arb' at /opt/arb"
        ) in out
        assert "workset.bindings.ro" not in out, (
            "the row named the SHARE's key where the MASK's belongs: the question "
            "is what swallowed this declaration, not what the declaration is"
        )

    def test_a_mask_above_the_destination_is_named_instead_of_a_mount(
        self, std, config, tmp_home, capsys,
    ):
        """The PARENT case: the share's own dest is absent from the map entirely.

        ⚑ This is the one a dest-keyed lookup cannot answer — nothing sits at
        ``/opt/arb/inner`` after the sweep, and reading "absent" as "fine" says nothing.
        The mask's OWN destination is the whole diagnosis and is not a path the user's
        binding key names — so the KEY that declared it is what makes the row
        actionable: it is the thing the reader has to go and edit.
        """
        self._ws(std, tmp_home, "arb-above", {
            "bindings": {"ro": {"/opt/arb/inner": [self._SRC]}},
            "masks": {"/opt/arb": True},
        })
        rc, out, _err = self._effective("arb-above", capsys)
        assert rc == 0
        assert f"{self._SRC} -> /opt/arb/inner" not in out
        assert (
            "no mount — the mask declared by 'workset.masks./opt/arb' at /opt/arb "
        ) in out

    def test_the_masks_key_is_READ_not_rebuilt_from_the_destination(
        self, std, config, tmp_home, capsys,
    ):
        """MUTATION-PROOF for the key: a ``~`` dest makes the two spellings DIFFER.

        A display could fake this row by pasting ``workset.masks.`` in front of the
        destination it already prints, and every absolute-path case would pass.  A
        ``~``-spelled mask breaks that: the KEY keeps the tilde the author wrote,
        while the collapsed destination is the resolved guest path.  Both appear in
        the line, and they are not the same string.
        """
        from kanibako.settings.settings_resolve import GUEST_HOME

        self._ws(std, tmp_home, "arb-tilde", {
            "bindings": {"ro": {"~/masked/inner": [self._SRC]}},
            "masks": {"~/masked": True},
        })
        rc, out, _err = self._effective("arb-tilde", capsys)
        assert rc == 0
        assert "workset.masks.~/masked" in out
        assert f"at {GUEST_HOME}/masked " in out
        assert f"workset.masks.{GUEST_HOME}/masked" not in out, (
            "the key was rebuilt from the destination, so it names a key spelling "
            "that is not in the user's file"
        )

    def test_a_share_nested_inside_another_share_still_mounts(
        self, std, config, tmp_home, capsys,
    ):
        """A bind may NEST inside a bind — both still print, and neither gains a phrase.

        The guard against a fix that reports every containment as a loss.
        """
        self._ws(std, tmp_home, "arb-nest", {
            "bindings": {"ro": {"/opt/n": [self._SRC], "/opt/n/in": ["/etc/hosts"]}},
        })
        rc, out, _err = self._effective("arb-nest", capsys)
        assert rc == 0
        assert f"  {self._SRC} -> /opt/n  [ro]" in out
        assert "  /etc/hosts -> /opt/n/in  [ro]" in out
        assert "no mount" not in out

    def test_declarations_a_launch_refuses_are_refused_here_too(
        self, std, config, tmp_home, capsys,
    ):
        """A workset a launch cannot assemble no longer lists cleanly at rc 0.

        ``workset.common`` extending onto a bound destination is a §0 row-3 refusal.
        Before the collapse ran here the listing printed the binding and returned 0, so
        ``--effective`` reported a working assembly for a workset that has none.

        ⚑ BOTH HALVES ARE PINNED, and they are two different obligations: the FRAMING
        says this refusal is the answer to "what would a box here mount", and the
        DETAIL is the launch's own refusal carried verbatim.  Dropping either one is a
        regression — a bare collision answers a question the user did not ask, and a
        frame with no detail names no key and no cure.
        """
        self._ws(std, tmp_home, "arb-refuse", {
            "bindings": {"ro": {"/opt/arb": [self._SRC]}},
            "common": {"/opt/arb": ["shared"]},
        })
        rc, out, err = self._effective("arb-refuse", capsys)
        assert rc == 1
        # THE FRAMING — context, and it names the dest off the exception.
        assert "Cannot say what working set 'arb-refuse' would mount" in err
        assert "no box in it can launch" in err
        assert "'/opt/arb'" in err
        # THE DETAIL — the launch's own words, including the remedy block.
        assert "workset.common./opt/arb" in err
        assert "already binds" in err
        assert "Set the unwanted key to null" in err
        # ⚑ ONE prefix, not two: ``cli.main`` would have printed exactly one
        # ``Error:`` line, and catching the refusal here must not add a second.
        assert err.count("Error: ") == 1
        assert "Effective bindings for working set" not in out

    def test_the_pre_arbitration_answer_is_a_mutation_that_reds(
        self, std, config, tmp_home, capsys, monkeypatch,
    ):
        """MUTATION: hand the display the RETIRED answer and the defect comes back.

        The stand-in is the listing's own former behaviour — every declared bind, no
        mask arm, no containment — and it is a throwaway, never the real symbol.  If the
        display had gone on reading the entry list, the cases above would pass anyway.
        """
        from kanibako.settings import store_collapse
        from kanibako.settings.kb_store import SCOPE_CONTAINMENT
        from kanibako.settings.store_collapse import CollapsedBind, CollapsedStore

        def _no_arbitration(store_shape_set, home_bind, entries=None):
            return CollapsedStore(
                bindings={
                    dest: CollapsedBind(entry.src, entry.opts)
                    for scope in SCOPE_CONTAINMENT
                    for arm in (
                        store_shape_set[scope].ro, store_shape_set[scope].rw,
                    )
                    for dest, entry in arm.items()
                },
                seeded=[],
                synced=[],
            )

        monkeypatch.setattr(
            store_collapse, "collapse_store_shapes", _no_arbitration,
        )
        self._ws(std, tmp_home, "arb-mutant", {
            "bindings": {"ro": {"/opt/arb": [self._SRC]}},
            "masks": {"/opt/arb": True},
        })
        rc, out, _err = self._effective("arb-mutant", capsys)
        assert rc == 0
        assert f"{self._SRC} -> /opt/arb" in out, (
            "the mutant did not restore the defect, so the cases above are not "
            "pinned on the arbitration at all"
        )
        assert "no mount" not in out

    def test_the_collision_detail_is_carried_from_the_exception_not_rebuilt(
        self, std, config, tmp_home, capsys, monkeypatch,
    ):
        """MUTATION: a throwaway raiser with a SENTINEL message, which must print VERBATIM.

        The stand-in is a fake ``build_store_shape_set`` that refuses with text this
        module could not possibly have composed.  If the display ever restated the
        collision in its own words, the sentinel would not appear and this reds — and
        restating it is precisely what the framing line exists to avoid, because a
        second carrier of a refusal drifts from the launch's and the launch's is the
        one a user has to act on.

        ⚑ The stand-in is a THROWAWAY, monkeypatched for this test alone; no real
        symbol is mutated, so a concurrent writer cannot be handed a false red.
        """
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings import store_shape

        sentinel = "SENTINEL-9f21: this text exists nowhere in the source tree"

        def _always_collides(entries):
            raise CategoryCollisionError(
                sentinel, kind="probe", box_dest="/opt/sentinel",
            )

        monkeypatch.setattr(store_shape, "build_store_shape_set", _always_collides)
        self._ws(std, tmp_home, "arb-sentinel", {
            "bindings": {"ro": {"/opt/arb": [self._SRC]}},
        })
        rc, _out, err = self._effective("arb-sentinel", capsys)
        assert rc == 1
        assert f"Error: {sentinel}" in err, (
            "the collision detail did not come from the caught exception, so this "
            "display is composing a second copy of the launch's refusal"
        )
        # The FRAME reads its destination off the exception too, not off the entries.
        assert "collide at '/opt/sentinel'" in err


class TestWorksetVerbsAreCaseBlind:
    """Spec §0, ``⚑ NAMING RULES`` — and these were LIVE defects, not latent ones.

    Unlike box names, workset names have never been folded on entry, so an exact-match
    lookup in a workset verb is a defect the day it is written.  ``create`` refusing
    ``SAME-NAME`` against a stored ``same-name`` while ``rm``/``connect``/``disconnect``
    answered "not registered" for the same pair made the tree contradict itself.

    ⚑ Each of these fails on the pre-image; they are the evidence, not the gate.
    """

    def test_rm_resolves_a_case_variant_and_reports_the_STORED_name(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("todel", tmp_home / "ws_todel", std)

        rc = run_rm(argparse.Namespace(name="ToDel", purge=False, force=True))
        assert rc == 0
        # The name is reported as REGISTERED — the user is being told what was destroyed.
        assert "Deleted working set 'todel'" in capsys.readouterr().out
        assert list_worksets(std) == {}

    def test_rm_of_the_default_alias_is_refused_in_any_case(
        self, config_file, tmp_home, capsys,
    ):
        """The tuple-literal compare: an exact re-spelling of a set that folds.

        ``workset rm Default`` used to miss this guard entirely and fall through to
        "not registered", which is both the wrong message and the wrong reason.
        """
        from kanibako.commands.workset_cmd import run_rm

        rc = run_rm(argparse.Namespace(name="Default", purge=False, force=True))
        assert rc == 1
        assert "default workset cannot be removed" in capsys.readouterr().err

    def test_connect_resolves_a_case_variant(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_connect

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("addws", tmp_home / "ws_add", std)

        src = tmp_home / "add_src"
        src.mkdir()

        rc = run_connect(argparse.Namespace(
            workset="AddWS", source=str(src), project_name=None, force=False,
        ))
        assert rc == 0
        assert "Added project" in capsys.readouterr().out

    def test_disconnect_resolves_a_case_variant(self, config_file, tmp_home, capsys):
        from kanibako.commands.workset_cmd import run_disconnect

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("rmws", tmp_home / "ws_rm", std)

        src = tmp_home / "rm_src"
        src.mkdir()
        add_project(ws, "rmproj", src)

        rc = run_disconnect(argparse.Namespace(
            workset="RmWS", project="rmproj", remove_files=False, force=True,
        ))
        assert rc == 0
        assert "Removed project 'rmproj'" in capsys.readouterr().out

    def test_disconnect_of_the_default_alias_is_refused_in_any_case(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.commands.workset_cmd import run_disconnect

        rc = run_disconnect(argparse.Namespace(
            workset="DEFAULT", project="x", remove_files=False, force=True,
        ))
        assert rc == 1
        assert "default workset cannot be removed" in capsys.readouterr().err
