"""Tests for kanibako.commands.workset_cmd."""

from __future__ import annotations

import argparse


from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths
from kanibako.project.workset import (
    add_project,
    create_workset,
)


def _workset_boxes(ws):
    """Read *ws*'s per-workset ``boxes:`` membership (the D10 connection index)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws.root, load_doc(ws.root / "settings.yaml"),
    )
    return workset_registry.load_workset_boxes(registry_path)


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

        # The image cascade setting AND the workset.meta identity coexist in the
        # single root settings.yaml.
        import yaml
        settings_yaml = ws_root.resolve() / "settings.yaml"
        assert settings_yaml.exists()
        with open(settings_yaml) as f:
            data = yaml.safe_load(f)
        assert data["box"]["image"] == "custom:latest"
        assert data["workset"]["meta"]["name"] == "imagews"


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
        dir.  P8b/Option A: the override is NO LONGER written to settings.yaml —
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
        project_toml = ws.projects_dir / "ext" / "settings.yaml"
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
        """connect to a dir INSIDE the workset root → normal behavior: a real
        workspaces/{name} dir, no override, no external connection record."""
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

        # No workspace override written (settings.yaml not pre-seeded).
        project_toml = ws.projects_dir / "int" / "settings.yaml"
        assert "project" not in load_doc(project_toml)

        # No external connection record for an internal source (the per-workset
        # registry only records EXTERNAL connects; an internal source keeps the
        # normal real-dir behavior).
        assert "int" not in _workset_boxes(ws)

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
        (external / "settings.yaml").write_text("project: {}\n")

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
        (external / "settings.yaml").write_text("project: {}\n")

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
        assert "Created:" in out

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
        create_workset("resetall", tmp_home / "ws_resetall", std)

        # Set a value first (workset.vault_ro = a real workset-scope key,
        # legal at the workset scope by construction).
        set_args = argparse.Namespace(
            workset="resetall", key_value="workset.vault_ro=/ro",
            force=False,
        )
        run_set(set_args)
        capsys.readouterr()

        # Reset all.
        reset_args = argparse.Namespace(
            workset="resetall", key=None, reset_all=True, force=True,
        )
        rc = run_reset(reset_args)
        assert rc == 0

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
        # ``@config.primary_workset/settings.yaml`` (the old
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
        with open(std.primary_workset / "settings.yaml") as f:
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
    converge on the spec location ``@config.primary_workset/settings.yaml``
    (spec §2c: PRIMARY ``meta.workset.path`` = ``@config.primary_workset``;
    ALL-WORKSETS ``meta.workset.settings`` = ``@meta.workset.path/settings.yaml``).

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
        spec_file = std.primary_workset / "settings.yaml"
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
            agent_name="general",
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
        """A NAMED workset keeps its single ``<root>/settings.yaml`` file."""
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
        with open(ws.root / "settings.yaml") as f:
            data = yaml.safe_load(f)
        assert data["workset"]["vault_ro"] == "/ro"


class TestWorksetEnv:
    """``workset config set workset.env.<VAR>`` — the workset env tier.

    ⚑ FLIPPED by B9. The bare ``env.<VAR>`` spelling wrote a docker ``.env`` FILE
    at the workset root, threaded into the engine as ``env_path``; R-39 retired
    the spelling and RQ-1 retired the launch READ of those files, so both the
    threading and the file are gone. The workset env tier is the settings key
    ``workset.env.<VAR>``, stored in the workset's own ``settings.yaml`` — for
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
        assert load_doc(ws.root / "settings.yaml")["workset"]["env"]["EDITOR"] == "vim"

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
            std.primary_workset / "settings.yaml",
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
        assert "env" not in load_doc(ws.root / "settings.yaml").get("workset", {})

    def test_primary_workset_env_reaches_the_launch_snapshot(
        self, config_file, tmp_home, capsys,
    ):
        """A primary-workset env var reaches the launch env accumulation and
        overrides the system tier (precedence system < workset).

        ⚑ Proven through the SNAPSHOT now, not a ``.env`` file layering: the
        reconcile picks the per-VAR winner and ``_build_config_env`` applies it.
        """
        from kanibako.commands.start import _build_config_env
        from kanibako.commands.system_cmd import run_set as system_set
        from kanibako.commands.workset_cmd import run_set
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_categories import reconcile_categories
        from kanibako.settings.settings_launch import snapshot_category_entries
        from kanibako.settings.settings_resolve import ResolveCtx

        assert system_set(argparse.Namespace(
            key_value="system.env.EDITOR=nano", force=True,
        )) == 0
        assert system_set(argparse.Namespace(
            key_value="system.env.PAGER=less", force=True,
        )) == 0
        assert run_set(argparse.Namespace(
            workset="default", key_value="workset.env.EDITOR=vim", force=False,
        )) == 0
        capsys.readouterr()

        from kanibako.settings.config_io import load_doc
        std = load_std_paths(load_config(config_file))
        system_tbl = load_doc(std.settings)["system"]["env"]
        workset_tbl = load_doc(
            std.primary_workset / "settings.yaml",
        )["workset"]["env"]

        snapshot = KeyStore({
            "system": {"env": dict(system_tbl)},
            "workset": {"env": dict(workset_tbl)},
        })
        ctx = ResolveCtx(
            agent_name="claude", workset_name=None,
            host_home="/home/host", xdg={"XDG_DATA_HOME": "/data"},
        )
        reconciled = reconcile_categories(snapshot_category_entries(
            snapshot, active_agent="claude", box_ctx=ctx,
        ))
        env = _build_config_env({}, reconciled.envs)
        assert env["EDITOR"] == "vim"   # workset overrides system
        assert env["PAGER"] == "less"   # system tier still present


class TestPrimaryWorksetMigration:
    """The approved F4 migration behavior (director ruling (c), 2026-07-02):
    DROP the legacy locations + document; a read-only one-shot warning while a
    legacy ``@config.data/settings.yaml`` exists without the spec file."""

    def _set_default(self, key_value: str) -> int:
        from kanibako.commands.workset_cmd import run_set

        args = argparse.Namespace(
            workset="default", key_value=key_value, force=False,
        )
        return run_set(args)

    def test_legacy_data_settings_yaml_dropped_with_warning(
        self, config_file, tmp_home, capsys, monkeypatch,
    ):
        """Approved migration behavior (ruling (c) 2026-07-02: drop + document).

        The legacy 1.6.0 read location ``@config.data/settings.yaml`` is
        DROPPED — never read into the cascade, never touched on disk — with a
        one-shot stderr warning while it exists without the spec file.
        """
        import kanibako.settings.paths as paths_mod
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.paths import (
            host_xdg_map,
            resolve_project,
            workset_settings_path,
        )
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        monkeypatch.setattr(paths_mod, "_legacy_primary_settings_warned", False)
        config = load_config(config_file)
        std = load_std_paths(config)
        legacy = std.data_path / "settings.yaml"
        legacy_text = "box:\n  shell: /bin/legacy\n"
        legacy.write_text(legacy_text)

        (tmp_home / "migproj").mkdir()
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "migproj"), initialize=True,
        )
        err = capsys.readouterr().err
        assert "no longer read" in err

        ctx = ResolveCtx(
            agent_name=None,
            workset_name=None,
            host_home=str(tmp_home),
            xdg=host_xdg_map(std.data_home),
        )
        snap = build_launch_snapshot(
            agent_name="general",
            ctx=ctx,
            system_path=std.settings,
            agent_path=None,
            workset_path=workset_settings_path(proj.group),
            box_path=None,
        )
        box = snap.box if "box" in snap else KeyStore()
        assert "shell" not in box       # the legacy value must NOT resolve
        assert legacy.read_text() == legacy_text  # and the file is untouched

    def test_legacy_warning_silent_once_spec_file_exists(
        self, config_file, tmp_home, capsys, monkeypatch,
    ):
        """The warning falls silent once the spec file exists (migrated)."""
        import kanibako.settings.paths as paths_mod
        from kanibako.settings.paths import resolve_project

        monkeypatch.setattr(paths_mod, "_legacy_primary_settings_warned", False)
        config = load_config(config_file)
        std = load_std_paths(config)
        (std.data_path / "settings.yaml").write_text("box:\n  shell: /bin/legacy\n")
        assert self._set_default("box.shell=/bin/zsh") == 0  # creates spec file
        capsys.readouterr()

        (tmp_home / "migproj2").mkdir()
        resolve_project(
            std, config, project_dir=str(tmp_home / "migproj2"), initialize=True,
        )
        assert "no longer read" not in capsys.readouterr().err


class TestWorksetCmdSystemFloor:
    """S-4: the SECOND ``resolved_sys`` map must carry the SAME ``system.*`` tier
    the launch floor does.

    ⚑ MUTATION-PROVEN GAP. Reverting ``workset_cmd``'s floor to the retired
    ``system.base_template`` spelling turned ZERO tests red — and the brief flagged
    exactly this divergence risk ("both must gain the key or ``workset config show
    --effective`` diverges from launch"). The failure is silent by construction: a
    workset binding whose source ``@``-refs ``@system.template`` or ``@system.canon``
    would render as an EMPTY-prefixed path in the display while mounting correctly at
    launch — a display that lies about what a launch does.
    """

    def _floor_keys(self):
        import inspect

        from kanibako.commands import workset_cmd

        src = inspect.getsource(workset_cmd._print_effective_shares)
        return {
            key for key in
            ("system.channelroot", "system.template", "system.canon",
             "system.base_template")
            if f'"{key}"' in src
        }

    def test_floor_carries_the_current_system_path_tier(self):
        keys = self._floor_keys()
        assert "system.template" in keys, (
            "workset_cmd's resolved_sys floor lost system.template — a workset "
            "binding @-refing it would resolve differently here than at launch"
        )
        assert "system.canon" in keys
        assert "system.base_template" not in keys, (
            "the RETIRED spelling is back in workset_cmd's floor (M-11)"
        )

    def test_floor_matches_the_launch_floor(self):
        """The two maps are the divergence risk itself, so compare them directly."""
        import inspect

        from kanibako.commands import start as start_mod

        launch = inspect.getsource(start_mod._launch_snapshot_inputs)
        for key in ("system.template", "system.canon", "system.channelroot"):
            assert f'"{key}"' in launch, key


class TestWorksetCreateIsAtomicOnRefusal:
    """F2: a whitelist refusal must leave NOTHING behind.

    ⚑ "Loud and leak-free" is not the same as "clean". Refusing part-way through the
    mould stamp satisfied both of those and still left a REGISTERED workset with a
    root, its own ``settings.yaml`` and a PARTIAL chapter copy — recoverable only by
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
        assert (
            root / "canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        ).is_file()
