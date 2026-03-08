"""Tests for kanibako box create command (replaces init)."""

from __future__ import annotations


from kanibako.cli import _SUBCOMMANDS, build_parser
from kanibako.commands.box._parser import run_create


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestBoxCreateParser:
    def test_create_parser_standalone(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone"])
        assert args.command == "box"
        assert args.box_command == "create"
        assert args.standalone is True
        assert args.path is None
        assert args.image is None

    def test_create_parser_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", "/tmp/mydir"])
        assert args.command == "box"
        assert args.path == "/tmp/mydir"

    def test_create_parser_with_image(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", "--image", "kanibako-template-jvm-oci"])
        assert args.image == "kanibako-template-jvm-oci"

    def test_create_parser_short_image_flag(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", "-i", "kanibako-oci"])
        assert args.image == "kanibako-oci"

    def test_init_not_in_subcommands(self):
        assert "init" not in _SUBCOMMANDS

    def test_box_in_subcommands(self):
        assert "box" in _SUBCOMMANDS

    def test_new_removed_from_subcommands(self):
        assert "new" not in _SUBCOMMANDS


# ---------------------------------------------------------------------------
# TestRunCreate
# ---------------------------------------------------------------------------

class TestRunCreate:
    def test_create_standalone_creates_project(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        rc = run_create(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()
        assert (resolved / ".kanibako" / "shell").is_dir()
        assert (resolved / "vault" / "share-ro").is_dir()
        assert (resolved / "vault" / "share-rw").is_dir()

    def test_create_standalone_cwd(
        self, config_file, credentials_dir, project_dir, monkeypatch, capsys,
    ):
        """box create --standalone with no path uses cwd."""
        monkeypatch.chdir(project_dir)
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone"])
        rc = run_create(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()

    def test_create_creates_nonexistent_path(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        target = tmp_home / "brand-new-project"
        assert not target.exists()
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(target)])
        rc = run_create(args)

        assert rc == 0
        assert target.is_dir()
        assert (target / ".kanibako").is_dir()

    def test_create_already_exists_fails(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        run_create(args)

        capsys.readouterr()
        rc = run_create(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "already initialized" in captured.err

    def test_create_local_mode(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """box create without --standalone creates a local project."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", str(project_dir)])
        rc = run_create(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Created" in captured.out

    def test_create_writes_gitignore_for_standalone(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        run_create(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert gitignore.is_file()
        assert ".kanibako/" in gitignore.read_text()

    def test_create_no_gitignore_for_local(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """Local mode should not write .gitignore (state is external)."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", str(project_dir)])
        run_create(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert not gitignore.is_file()


class TestCreateNoVault:
    """Tests for --no-vault flag on box create."""

    def test_create_no_vault_skips_vault_dirs(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "novault-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project), "--no-vault"])
        rc = run_create(args)

        assert rc == 0
        assert (project / ".kanibako").is_dir()
        assert not (project / "vault").exists()

    def test_create_no_vault_new_dir(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        target = tmp_home / "novault-new"
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(target), "--no-vault"])
        rc = run_create(args)

        assert rc == 0
        assert (target / ".kanibako").is_dir()
        assert not (target / "vault").exists()

    def test_create_with_vault_creates_vault_dirs(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "vault-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project)])
        rc = run_create(args)

        assert rc == 0
        assert (project / "vault" / "share-ro").is_dir()
        assert (project / "vault" / "share-rw").is_dir()


class TestCreateDistinctAuth:
    """Tests for --distinct-auth flag on box create."""

    def test_create_distinct_auth_skips_creds(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        project = tmp_home / "distinct-project"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project), "--distinct-auth"])
        rc = run_create(args)

        assert rc == 0
        shell = project / ".kanibako" / "shell"
        assert shell.is_dir()
        # Credentials should NOT have been copied from host.
        assert not (shell / ".claude" / ".credentials.json").exists()

    def test_create_distinct_auth_sets_meta(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.config import read_project_meta
        project = tmp_home / "distinct-meta"
        project.mkdir()
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project), "--distinct-auth"])
        run_create(args)

        meta = read_project_meta(project / ".kanibako" / "project.toml")
        assert meta is not None
        assert meta["auth"] == "distinct"

    def test_parser_accepts_distinct_auth(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", "--distinct-auth"])
        assert args.distinct_auth is True

    def test_create_distinct_auth_new_dir(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        target = tmp_home / "distinct-new"
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(target), "--distinct-auth"])
        rc = run_create(args)

        assert rc == 0
        shell = target / ".kanibako" / "shell"
        assert shell.is_dir()
        assert not (shell / ".claude" / ".credentials.json").exists()


class TestCreateImage:
    """Tests for --image flag persistence."""

    def test_create_persists_image(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--standalone", str(project_dir),
            "--image", "kanibako-template-jvm-oci",
        ])
        run_create(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert merged.container_image == "kanibako-template-jvm-oci"

    def test_create_default_image_persisted(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        run_create(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert "kanibako" in merged.container_image
