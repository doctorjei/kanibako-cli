"""Tests for kanibako init command."""

from __future__ import annotations


from kanibako.cli import _SUBCOMMANDS, build_parser
from kanibako.commands.init import run_init


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestInitParser:
    def test_init_parser_local(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local"])
        assert args.command == "init"
        assert args.local is True
        assert args.path is None
        assert args.image is None

    def test_init_parser_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "/tmp/mydir"])
        assert args.command == "init"
        assert args.path == "/tmp/mydir"

    def test_init_parser_with_image(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "--image", "kanibako-template-jvm-oci"])
        assert args.image == "kanibako-template-jvm-oci"

    def test_init_parser_short_image_flag(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "-i", "kanibako-oci"])
        assert args.image == "kanibako-oci"

    def test_init_in_subcommands(self):
        assert "init" in _SUBCOMMANDS

    def test_new_removed_from_subcommands(self):
        assert "new" not in _SUBCOMMANDS


# ---------------------------------------------------------------------------
# TestRunInit
# ---------------------------------------------------------------------------

class TestRunInit:
    def test_init_local_creates_project(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        rc = run_init(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()
        assert (resolved / ".kanibako" / "shell").is_dir()
        assert (resolved / "vault" / "share-ro").is_dir()
        assert (resolved / "vault" / "share-rw").is_dir()

    def test_init_local_cwd(
        self, config_file, credentials_dir, project_dir, monkeypatch, capsys,
    ):
        """init --local with no path uses cwd."""
        monkeypatch.chdir(project_dir)
        parser = build_parser()
        args = parser.parse_args(["init", "--local"])
        rc = run_init(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()

    def test_init_creates_nonexistent_path(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        target = tmp_home / "brand-new-project"
        assert not target.exists()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(target)])
        rc = run_init(args)

        assert rc == 0
        assert target.is_dir()
        assert (target / ".kanibako").is_dir()

    def test_init_already_exists_fails(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        capsys.readouterr()
        rc = run_init(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "already initialized" in captured.err

    def test_init_ac_mode(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """init without --local creates an AC project."""
        parser = build_parser()
        args = parser.parse_args(["init", str(project_dir)])
        rc = run_init(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Initialized" in captured.out

    def test_init_writes_gitignore_for_local(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert gitignore.is_file()
        assert ".kanibako/" in gitignore.read_text()

    def test_init_no_gitignore_for_ac(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """AC mode should not write .gitignore (state is external)."""
        parser = build_parser()
        args = parser.parse_args(["init", str(project_dir)])
        run_init(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert not gitignore.is_file()


class TestInitNoVault:
    """Tests for --no-vault flag on init."""

    def test_init_no_vault_skips_vault_dirs(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "novault-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project), "--no-vault"])
        rc = run_init(args)

        assert rc == 0
        assert (project / ".kanibako").is_dir()
        assert not (project / "vault").exists()

    def test_init_no_vault_new_dir(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        target = tmp_home / "novault-new"
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(target), "--no-vault"])
        rc = run_init(args)

        assert rc == 0
        assert (target / ".kanibako").is_dir()
        assert not (target / "vault").exists()

    def test_init_with_vault_creates_vault_dirs(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "vault-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project)])
        rc = run_init(args)

        assert rc == 0
        assert (project / "vault" / "share-ro").is_dir()
        assert (project / "vault" / "share-rw").is_dir()


class TestInitDistinctAuth:
    """Tests for --distinct-auth flag on init."""

    def test_init_distinct_auth_skips_creds(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "distinct-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project), "--distinct-auth"])
        rc = run_init(args)

        assert rc == 0
        shell = project / ".kanibako" / "shell"
        assert shell.is_dir()
        # Credentials should NOT have been copied from host.
        assert not (shell / ".claude" / ".credentials.json").exists()

    def test_init_distinct_auth_sets_meta(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.config import read_project_meta
        project = tmp_home / "distinct-meta"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project), "--distinct-auth"])
        run_init(args)

        meta = read_project_meta(project / ".kanibako" / "project.toml")
        assert meta is not None
        assert meta["auth"] == "distinct"

    def test_parser_accepts_distinct_auth(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "--distinct-auth"])
        assert args.distinct_auth is True

    def test_init_distinct_auth_new_dir(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        target = tmp_home / "distinct-new"
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(target), "--distinct-auth"])
        rc = run_init(args)

        assert rc == 0
        shell = target / ".kanibako" / "shell"
        assert shell.is_dir()
        assert not (shell / ".claude" / ".credentials.json").exists()


class TestInitImage:
    """Tests for --image flag persistence."""

    def test_init_persists_image(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args([
            "init", "--local", str(project_dir),
            "--image", "kanibako-template-jvm-oci",
        ])
        run_init(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert merged.container_image == "kanibako-template-jvm-oci"

    def test_init_default_image_persisted(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert "kanibako" in merged.container_image
