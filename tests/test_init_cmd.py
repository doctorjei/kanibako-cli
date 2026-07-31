"""Tests for kanibako box create command (replaces init)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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

    def test_create_parser_private(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", "--private"])
        assert args.private is True

    def test_create_parser_private_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone"])
        assert args.private is False

    def test_top_level_create_parser_private(self):
        parser = build_parser()
        args = parser.parse_args(["create", "--standalone", "--private"])
        assert args.command == "create"
        assert args.private is True


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
        assert (resolved / "box_data").is_dir()
        assert (resolved / "box_data" / "home").is_dir()
        assert (resolved / "vault" / "ro").is_dir()
        assert (resolved / "vault" / "rw").is_dir()

    def test_create_seeds_at_create_before_any_launch(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """B7: `box create` seeds the home ATOMICALLY at creation (not launch).

        `run_create` must invoke the one-time seed for the freshly-registered box
        (``proj.is_new``) — so the home is populated BEFORE the box is ever
        started.  Asserts the create command routes through ``seed_new_box`` with
        the just-created box.
        """
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        with patch(
            "kanibako.commands.start.seed_new_box"
        ) as m_seed:
            rc = run_create(args)
        assert rc == 0
        m_seed.assert_called_once()
        # The seeded subject is the box that was just created (is_new).
        seeded_proj = m_seed.call_args.args[2]
        assert seeded_proj.is_new is True
        assert seeded_proj.mode.value == "standalone"

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
        assert (resolved / "box_data").is_dir()

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
        assert (target / "box_data").is_dir()

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
        """box create without --standalone creates a default-mode project."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", str(project_dir)])
        rc = run_create(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Created" in captured.out

    def test_create_at_home_local_refused(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        """Local create at $HOME is refused outright (no escape hatch)."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", str(Path.home())])
        rc = run_create(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "$HOME" in captured.err
        assert "--standalone" in captured.err
        # No project metadata should have been created at $HOME.
        assert not (Path.home() / "box_data").exists()

    def test_create_at_home_standalone_requires_allow_home(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        """Standalone create at $HOME without --allow-home is refused."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(Path.home())])
        rc = run_create(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "--allow-home" in captured.err
        assert not (Path.home() / "box_data").exists()

    def test_create_at_home_standalone_with_allow_home(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        """Standalone create at $HOME succeeds with the explicit --allow-home."""
        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", "--standalone", "--allow-home", str(Path.home())]
        )
        rc = run_create(args)

        assert rc == 0
        assert (Path.home() / "box_data").is_dir()

    def test_create_writes_gitignore_for_standalone(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        run_create(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert gitignore.is_file()
        assert "box_data/" in gitignore.read_text()

    def test_create_no_gitignore_for_local(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """Default mode should not write .gitignore (state is external)."""
        parser = build_parser()
        args = parser.parse_args(["box", "create", str(project_dir)])
        run_create(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert not gitignore.is_file()

    def test_create_with_name_override_registers_that_name(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """`box create --name X` registers the project under name X,
        not the directory basename."""
        from kanibako.config import config_file_path, load_config
        from kanibako.paths import load_primary_boxes, load_std_paths, xdg

        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", str(project_dir), "--name", "custom-name"]
        )
        rc = run_create(args)

        assert rc == 0
        std = load_std_paths(load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config"))))
        boxes = load_primary_boxes(std.primary_workset)
        assert "custom-name" in boxes, (
            f"Expected 'custom-name' in the PRIMARY membership, got: {boxes}"
        )
        assert project_dir.name not in boxes, (
            f"Directory basename should NOT be registered when --name given, "
            f"got: {boxes}"
        )

    def test_resolve_project_accepts_registered_name(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """After `create --name X`, resolve_project('X') must find the
        registered path (not error as if X were a relative path)."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", str(project_dir), "--name", "named-proj"]
        )
        assert run_create(args) == 0

        # Now look up by bare name — must NOT raise.
        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir="named-proj")
        assert proj.project_path == project_dir.resolve(), (
            f"Expected {project_dir.resolve()}, got {proj.project_path}"
        )

    def test_resolve_any_project_accepts_registered_name(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """resolve_any_project (the CLI front-door used by `start`) must do
        bare-name lookup BEFORE path resolution, otherwise the name gets
        path-ified into cwd/<name> and resolution misses."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_any_project

        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", str(project_dir), "--name", "frontdoor-proj"]
        )
        assert run_create(args) == 0

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_any_project(std, config, project_dir="frontdoor-proj")
        assert proj.project_path == project_dir.resolve(), (
            f"Expected {project_dir.resolve()}, got {proj.project_path}"
        )


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
        assert (project / "box_data").is_dir()
        assert not (project / "vault").exists()

    def test_create_no_vault_new_dir(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        target = tmp_home / "novault-new"
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(target), "--no-vault"])
        rc = run_create(args)

        assert rc == 0
        assert (target / "box_data").is_dir()
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
        assert (project / "vault" / "ro").is_dir()
        assert (project / "vault" / "rw").is_dir()


def _standalone_tiers(config_file, project_dir):
    """The standalone box's ``(box_tier, workset_tier)`` settings files.

    Sourced from ``box_workset_settings_paths`` — the SAME single source production
    uses (M-8) — rather than hard-coded, so a test can never assert a location the
    code no longer writes.  (Before P2 these tests spelled ``<root>/settings.yaml``
    directly; that IS the workset tier now, and the box keys live one level down in
    ``box_data/``.)

    ⚑ Sourcing BOTH positions from the code under test would make every caller
    self-consistent and therefore BLIND to a swapped pair — the exact defect found in
    the cascade-order test.  So this helper ASSERTS the two literal spec positions
    (§5 L1403/L1407) before returning them: the blindness is closed here, once, for
    every caller.
    """
    from kanibako.config import load_config
    from kanibako.paths import (
        box_workset_settings_paths,
        load_std_paths,
        resolve_standalone_project,
    )

    config = load_config(config_file)
    std = load_std_paths(config)
    proj = resolve_standalone_project(
        std, config, str(project_dir.resolve()), initialize=False,
    )
    box_tier, ws_tier = box_workset_settings_paths(proj)
    root = project_dir.resolve()
    assert box_tier == root / "box_data" / "settings.yaml", box_tier
    assert ws_tier == root / "settings.yaml", ws_tier
    return box_tier, ws_tier


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

        box_tier, ws_tier = _standalone_tiers(config_file, project_dir)
        merged = load_merged_config(config_file, box_tier, workset_path=ws_tier)
        assert merged.box_image == "kanibako-template-jvm-oci"
        # And it is the BOX tier that holds it, not the workset/root file.
        from kanibako.config_io import load_doc
        assert load_doc(box_tier)["box"]["image"] == "kanibako-template-jvm-oci"

    def test_create_default_image_persisted(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args(["box", "create", "--standalone", str(project_dir)])
        run_create(args)

        box_tier, ws_tier = _standalone_tiers(config_file, project_dir)
        merged = load_merged_config(config_file, box_tier, workset_path=ws_tier)
        assert "kanibako" in merged.box_image
        # Assert PERSISTENCE, not just the merged default: the create-time write must
        # actually be in the box tier.  (Reading merged alone passes vacuously — the
        # built-in default also contains "kanibako".)
        from kanibako.config_io import load_doc
        assert "kanibako" in load_doc(box_tier)["box"]["image"]


class TestCreatePrivate:
    """`create --private` closes the OAuth-token-leak-at-create.

    A private box persists ``box.auth.global_enabled=false`` +
    ``box.auth.workset_enabled=false`` to the box settings.yaml BEFORE the home
    seed runs, so ``seed_new_box``'s ``resolve_auth_source`` resolves tier
    ``"box"`` (source_root None) and the host OAuth credential is never copied
    into the box.
    """

    @staticmethod
    def _box_auth(config_file, project_dir):
        """Read ``box.auth`` from the BOX TIER — the file the launch snapshot reads.

        ⚑ AUTH-CRITICAL that this reads the same file ``resolve_auth_source`` does: if
        --private wrote somewhere the snapshot does not read as the box tier, a
        supposedly-private box would resolve a sharing tier and forward the host OAuth
        token into the seed.  Sourced from ``box_workset_settings_paths`` so the test
        cannot drift from production (M-8).
        """
        from kanibako.config import load_doc
        box_tier, _ = _standalone_tiers(config_file, project_dir)
        return (load_doc(box_tier).get("box") or {}).get("auth") or {}

    @staticmethod
    def _shell_home(project_dir):
        return project_dir.resolve() / "box_data" / "home"

    def test_private_writes_both_auth_toggles(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """--private persists BOTH box-scope auth enables OFF (as real bools)."""
        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", "--standalone", "--private", str(project_dir)]
        )
        assert run_create(args) == 0
        auth = self._box_auth(config_file, project_dir)
        assert auth.get("global_enabled") is False
        assert auth.get("workset_enabled") is False

    def test_private_writes_only_the_two_toggles(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """--private writes NOTHING beyond the two auth toggles (additive)."""
        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", "--standalone", "--private", str(project_dir)]
        )
        assert run_create(args) == 0
        auth = self._box_auth(config_file, project_dir)
        assert set(auth) == {"global_enabled", "workset_enabled"}

    def test_default_create_writes_no_auth_toggles(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """WITHOUT --private, no box.auth override is written (unchanged)."""
        parser = build_parser()
        args = parser.parse_args(
            ["box", "create", "--standalone", str(project_dir)]
        )
        assert run_create(args) == 0
        assert self._box_auth(config_file, project_dir) == {}

    # ---- mechanism proof: the seed reads the toggles (tier="box") ----------

    def test_private_seed_resolves_tier_box_no_forward(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """--private → seed_box_credentials sees auth.tier "box" (no sharing).

        Mutation-proof: the toggles are written BEFORE the seed, so the seed's
        ``resolve_auth_source`` resolves tier "box".  Drop/reorder the write and
        a share-capable (claude) box would resolve tier "global" — this reddens.
        """
        from unittest.mock import patch
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--standalone", "--private",
            "--agent", "claude", str(project_dir),
        ])
        with patch("kanibako.commands.start.credsync") as m_credsync:
            assert run_create(args) == 0
        m_credsync.seed_box_credentials.assert_called_once()
        auth = m_credsync.seed_box_credentials.call_args.kwargs["auth"]
        assert auth.tier == "box"
        assert auth.creds_shared is False

    def test_default_seed_resolves_tier_global_forwards(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """WITHOUT --private, a share-capable box resolves tier "global"."""
        from unittest.mock import patch
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--standalone",
            "--agent", "claude", str(project_dir),
        ])
        with patch("kanibako.commands.start.credsync") as m_credsync:
            assert run_create(args) == 0
        m_credsync.seed_box_credentials.assert_called_once()
        auth = m_credsync.seed_box_credentials.call_args.kwargs["auth"]
        assert auth.tier == "global"
        assert auth.creds_shared is True

    # ---- black-box proof: the real credential file is / isn't seeded -------

    def test_private_does_not_seed_credentials_file(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """End-to-end: --private leaves NO .credentials.json in the box home."""
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--standalone", "--private",
            "--agent", "claude", str(project_dir),
        ])
        assert run_create(args) == 0
        seeded = list(self._shell_home(project_dir).rglob(".credentials.json"))
        assert seeded == []

    def test_default_seeds_credentials_file(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """End-to-end control: a default share-capable box DOES seed the cred."""
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--standalone",
            "--agent", "claude", str(project_dir),
        ])
        assert run_create(args) == 0
        seeded = list(self._shell_home(project_dir).rglob(".credentials.json"))
        assert len(seeded) == 1

    # ---- coverage: primary/named (non-standalone) mode is closed too -------

    def test_private_primary_mode_resolves_tier_box(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """--private closes the leak in DEFAULT (primary/named) mode too — not
        just standalone.  The box's settings.yaml lives under the data dir here,
        so assert via the seed's resolved auth (tier "box", no forward)."""
        from unittest.mock import patch
        parser = build_parser()
        args = parser.parse_args([
            "box", "create", "--private", "--agent", "claude", str(project_dir),
        ])
        with patch("kanibako.commands.start.credsync") as m_credsync:
            assert run_create(args) == 0
        m_credsync.seed_box_credentials.assert_called_once()
        auth = m_credsync.seed_box_credentials.call_args.kwargs["auth"]
        assert auth.tier == "box"
        assert auth.creds_shared is False
