"""Tests for kanibako.templates (the layered home-seed / template trio)."""

from __future__ import annotations

import logging

import pytest
import yaml

from kanibako.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.templates import (
    _packaged_base_template,
    _packaged_manifest_entries,
    copy_resource_tree_if_absent,
    install_packaged_templates,
    packaged_templates_digest,
    plan_template_refresh,
    stage_layers,
    template_seed_defaults,
)
from kanibako.core_defaults import ROM_GUIDE_REL as _GUIDE_REL
from kanibako.workset import add_project, create_workset


class TestStageLayers:
    """The staging primitive: per-file last-wins merged in a temp dir, then
    seeded into the dest with create-if-absent (never clobbers an existing file);
    an absent-dir layer is skipped."""

    def _layer(self, root, name, files):
        d = root / name
        d.mkdir(parents=True)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_ordered_workset_wins_per_file(self, tmp_path):
        """base -> agent -> workset staged in order; highest layer wins per-file."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {
            "base-only.txt": "base",
            "shared.txt": "base version",
            "two.txt": "base two",
        })
        agent = self._layer(tmp_path, "agent", {
            "agent-only.txt": "agent",
            "shared.txt": "agent version",
            "two.txt": "agent two",
        })
        workset = self._layer(tmp_path, "workset", {
            "workset-only.txt": "workset",
            "shared.txt": "workset version",
        })

        stage_layers(home, [base, agent, workset])

        # Each layer's unique files all land.
        assert (home / "base-only.txt").read_text() == "base"
        assert (home / "agent-only.txt").read_text() == "agent"
        assert (home / "workset-only.txt").read_text() == "workset"
        # Highest layer to set a file wins (workset > agent > base).
        assert (home / "shared.txt").read_text() == "workset version"
        # agent overlays base where workset is silent.
        assert (home / "two.txt").read_text() == "agent two"

    def test_existing_home_file_survives_reseed(self, tmp_path):
        """THE CLOBBER REGRESSION: a pre-existing home file survives a re-seed.

        A layer shipping the same relative path must NOT overwrite the user's
        content (create-if-absent seed).  This is the load-bearing data-loss guard.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "shared.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "shared.txt": "base version",
            "base-only.txt": "base",
        })

        stage_layers(home, [base])

        # Pre-existing file preserved (NOT clobbered by the layer's same-path file).
        assert (home / "shared.txt").read_text() == "user changes"
        # New file from the layer still lands.
        assert (home / "base-only.txt").read_text() == "base"

    def test_existing_nested_file_not_clobbered(self, tmp_path):
        """A pre-existing file in a nested home subdir survives a re-seed."""
        home = tmp_path / "home"
        nested = home / ".claude"
        nested.mkdir(parents=True)
        (nested / "settings.json").write_text("user settings")
        agent = self._layer(tmp_path, "agent", {".claude/settings.json": "shipped"})

        stage_layers(home, [agent])

        assert (home / ".claude" / "settings.json").read_text() == "user settings"

    def test_nested_directories(self, tmp_path):
        """Layers with nested directory structure are seeded correctly."""
        home = tmp_path / "home"
        home.mkdir()
        agent = tmp_path / "agent"
        nested = agent / ".claude"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("# Instructions")

        stage_layers(home, [agent])

        assert (home / ".claude" / "CLAUDE.md").read_text() == "# Instructions"

    def test_no_layers_is_noop(self, tmp_path):
        """No layers -> dest is untouched."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "existing.txt").write_text("untouched")

        stage_layers(home, [])

        assert (home / "existing.txt").read_text() == "untouched"
        assert sorted(p.name for p in home.iterdir()) == ["existing.txt"]

    def test_absent_layer_dir_skipped(self, tmp_path):
        """A layer whose source dir does not exist is silently skipped (skip-if-
        absent, spec §2a) — the remaining present layers still seed."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {"base-only.txt": "base"})
        missing = tmp_path / "nope"  # never created

        stage_layers(home, [base, missing])

        assert (home / "base-only.txt").read_text() == "base"

    def test_all_layers_absent_is_noop(self, tmp_path):
        """Every layer absent -> nothing staged, dest untouched (no temp dir churn)."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "keep.txt").write_text("keep")

        stage_layers(home, [tmp_path / "a", tmp_path / "b"])

        assert sorted(p.name for p in home.iterdir()) == ["keep.txt"]


# ---------------------------------------------------------------------------
# The layered ``seeded.template`` DEFAULT-category table (spec §2a; Q1-Q4).
# ---------------------------------------------------------------------------

@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def named_proj(std, config, tmp_home):
    ws_root = tmp_home / "worksets" / "my-set"
    ws = create_workset("my-set", ws_root, std)
    source = tmp_home / "original-project"
    source.mkdir()
    add_project(ws, "cool-app", source)
    return resolve_workset_project(
        WorksetSpec.from_workset(ws), "cool-app", std, config, initialize=True,
    )


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(
        std, config, str(project_dir), initialize=True,
    )


class TestTemplateSeedDefaults:
    """``template_seed_defaults`` declares the three layers as ordinary keystore
    ``seeded`` keys (+ their ``@``-ref SOURCE keys), gated per mode / agent."""

    def test_system_layer_always_present(self, primary_proj):
        defs = template_seed_defaults(primary_proj, "claude")
        # Layer 1 (base) rides the seed system with NO carve-out (Q4).
        assert defs["system.seeded.template"] == ("@system.base_template", "~")

    def test_agent_layer_sources_harness_store(self, primary_proj):
        """Layer 2: agent.<node>.seeded.template reads @agent.<node>.template,
        which defaults to @config.agents/<harness>/template (Q2: node = persona+
        harness; the SOURCE dir is the harness store)."""
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["agent.claude.template"] == "@config.agents/claude/template"
        assert defs["agent.claude.seeded.template"] == ("@agent.claude.template", "~")

    def test_no_agent_omits_agent_layer(self, primary_proj):
        defs = template_seed_defaults(primary_proj, None)
        assert not any(k.startswith("agent.") for k in defs)
        # system + workset layers still declared.
        assert "system.seeded.template" in defs
        assert "workset.seeded.template" in defs

    def test_workset_layer_default_points_at_workset_template(self, primary_proj):
        """Layer 3 default = @meta.workset.path/template (Q3, was <None>)."""
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["workset.template"] == "@meta.workset.path/template"
        assert defs["workset.seeded.template"] == ("@workset.template", "~")

    def test_named_includes_workset_layer(self, named_proj):
        defs = template_seed_defaults(named_proj, "claude")
        assert "workset.seeded.template" in defs

    def test_standalone_omits_workset_layer(self, standalone_proj):
        """STANDALONE has no workset tier -> no workset.template source/layer
        (spec §2c L483 workset.template <None>)."""
        defs = template_seed_defaults(standalone_proj, "claude")
        assert "workset.template" not in defs
        assert "workset.seeded.template" not in defs
        # base + agent layers still present.
        assert "system.seeded.template" in defs
        assert "agent.claude.seeded.template" in defs


# ---------------------------------------------------------------------------
# End-to-end layered home-seed through the keystore (_apply_init_seeds).
# ---------------------------------------------------------------------------

class _FakeTarget:
    """Minimal resolved-agent stand-in: only ``.name`` + ``default_seeds()``
    (empty) are read by the seed seam for a non-descriptor test target."""

    name = "claude"

    def default_seeds(self):
        return {}


def _seed(std, proj, *, agent="claude", deliver_creds=True):
    """Drive the one-time home seed (the unified keystore-routed route)."""
    from kanibako.commands.start import _apply_init_seeds

    _apply_init_seeds(
        std=std,
        proj=proj,
        agent_name=agent,
        target=_FakeTarget() if agent else None,
        global_config_path=std.settings,
        agent_config_path=std.agents / "claude" / "settings.yaml",
        logger=logging.getLogger("test-seed"),
        deliver_creds=deliver_creds,
    )


class TestLayeredHomeSeed:
    """The template trio seeded through the SINGLE keystore ``seeded`` route
    (``_apply_init_seeds``) — base -> agent -> workset, per-file last-wins,
    create-if-absent, skip-if-absent, credential-gate-exempt."""

    def _populate(self, std, primary_proj):
        install_packaged_templates(std, ["claude"])
        (std.base_template / "base-only.txt").write_text("base")
        (std.base_template / "shared.txt").write_text("base")
        agent_tpl = std.agents / "claude" / "template"
        (agent_tpl / "agent-only.txt").write_text("agent")
        (agent_tpl / "shared.txt").write_text("agent")
        ws_tpl = std.primary_workset / "template"
        ws_tpl.mkdir(parents=True, exist_ok=True)
        (ws_tpl / "workset-only.txt").write_text("workset")
        (ws_tpl / "shared.txt").write_text("workset")
        return ws_tpl

    def test_all_three_layers_seed_every_file(self, std, config, primary_proj):
        """Q4: every file present in EACH layer dir is seeded — base + agent +
        workset, packaged content included (not an enumerated subset)."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj)
        home = primary_proj.shell_path
        # Base layer — the packaged playbook/CONTENTS.md AND the custom marker.
        assert (home / "playbook" / "CONTENTS.md").is_file()
        assert (home / "base-only.txt").read_text() == "base"
        # Agent layer — the packaged .claude.json/settings AND the custom marker.
        assert (home / ".claude.json").is_file()
        assert (home / ".claude" / "settings.json").is_file()
        assert (home / "agent-only.txt").read_text() == "agent"
        # Workset layer.
        assert (home / "workset-only.txt").read_text() == "workset"

    def test_layer_order_last_wins_per_file(self, std, config, primary_proj):
        """base -> agent -> workset: the highest layer to ship a file wins."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj)
        assert (primary_proj.shell_path / "shared.txt").read_text() == "workset"

    def test_agent_overlays_base_when_workset_silent(self, std, config, primary_proj):
        """A file shipped by base + agent (not workset) resolves to the AGENT
        version (later layer overlays earlier) — the create-if-absent-per-seed
        FIRST-wins bug this route replaced would have kept the base version."""
        install_packaged_templates(std, ["claude"])
        (std.base_template / "two.txt").write_text("base two")
        (std.agents / "claude" / "template" / "two.txt").write_text("agent two")
        _seed(std, primary_proj)
        assert (primary_proj.shell_path / "two.txt").read_text() == "agent two"

    def test_existing_home_file_never_clobbered(self, std, config, primary_proj):
        """A user-owned home file survives the layered seed (create-if-absent)."""
        self._populate(std, primary_proj)
        home = primary_proj.shell_path
        home.mkdir(parents=True, exist_ok=True)
        (home / "shared.txt").write_text("USER OWNED")
        _seed(std, primary_proj)
        assert (home / "shared.txt").read_text() == "USER OWNED"

    def test_absent_workset_template_skipped(self, std, config, primary_proj):
        """No @workset.template dir on disk -> the layer is skipped (skip-if-
        absent) and base + agent still seed (no crash)."""
        install_packaged_templates(std, ["claude"])
        # Deliberately do NOT create std.primary_workset/template.
        _seed(std, primary_proj)
        home = primary_proj.shell_path
        assert (home / "playbook" / "CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_standalone_has_no_workset_layer(self, std, config, standalone_proj):
        """STANDALONE seeds base + agent only (no workset tier)."""
        install_packaged_templates(std, ["claude"])
        _seed(std, standalone_proj)
        home = standalone_proj.shell_path
        assert (home / "playbook" / "CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_no_agent_box_seeds_base_only(self, std, config, primary_proj):
        """A NO-AGENT box seeds the base layer but NOT the agent layer."""
        install_packaged_templates(std, ["claude"])
        (std.base_template / "base-only.txt").write_text("base")
        _seed(std, primary_proj, agent="")
        home = primary_proj.shell_path
        assert (home / "playbook" / "CONTENTS.md").is_file()
        assert (home / "base-only.txt").is_file()
        # No agent template layer.
        assert not (home / ".claude.json").exists()

    def test_private_box_keeps_template_layers(self, std, config, primary_proj):
        """deliver_creds=False (PRIVATE box) suppresses CREDENTIAL seeds only — the
        template layers are non-credential and STILL seed (D-M4 gate exemption)."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj, deliver_creds=False)
        home = primary_proj.shell_path
        assert (home / "playbook" / "CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()
        assert (home / "workset-only.txt").is_file()

    def test_workset_template_repoint_reroutes_seed(self, std, config, primary_proj, tmp_path):
        """MUTATION PROOF (settable source): setting ``workset.template`` in the
        workset settings file reroutes the layer-3 seed to the new dir — the seed
        reads the KEY, not a hardcoded path."""
        install_packaged_templates(std, ["claude"])
        custom = tmp_path / "custom-tpl"
        custom.mkdir()
        (custom / "CUSTOM.txt").write_text("custom")
        # Default dir populated too — to prove the OVERRIDE wins over the default.
        ws_default = std.primary_workset / "template"
        ws_default.mkdir(parents=True, exist_ok=True)
        (ws_default / "DEFAULT.txt").write_text("default")

        wsf = std.primary_workset / "settings.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("workset", {})["template"] = str(custom)
        wsf.write_text(yaml.safe_dump(doc))

        _seed(std, primary_proj)
        home = primary_proj.shell_path
        assert (home / "CUSTOM.txt").read_text() == "custom"
        # The default workset dir is NOT used once the key is repointed.
        assert not (home / "DEFAULT.txt").exists()


# ---------------------------------------------------------------------------
# Packaged curated-template install (Phase 9c) — the packaged->runtime copy.
# ---------------------------------------------------------------------------

class TestInstallPackagedTemplates:
    def test_base_handbook_landed(self, std):
        """The packaged base tree — all THREE handbook roots — is copied to
        @system.base_template (playbook / notebook / workbook; see HANDBOOK.md)."""
        install_packaged_templates(std, ["claude", "goose", "codex"])
        assert (std.base_template / "playbook" / "CONTENTS.md").is_file()
        assert (std.base_template / "notebook" / "directives" / "BRIEF_BOX.md").is_file()
        assert (std.base_template / "workbook" / "devnotes.md").is_file()

    def test_claude_template_landed(self, std):
        """The claude agent template (.claude.json stub + settings) is copied.

        The agent layer ships harness CONFIG only — its directive stub was dropped
        when the box brief moved to the notebook, so it seeds no playbook file.
        """
        install_packaged_templates(std, ["claude"])
        dest = std.agents / "claude" / "template"
        assert (dest / ".claude.json").is_file()
        assert (dest / ".claude" / "settings.json").is_file()
        assert not (dest / "playbook").exists()
        assert not (dest / "CLAUDE.md").exists()
        import json
        data = json.loads((dest / ".claude.json").read_text())
        assert data.get("hasCompletedOnboarding") is True

    def test_goose_and_codex_templates_landed(self, std):
        install_packaged_templates(std, ["goose", "codex"])
        assert (
            std.agents / "goose" / "template" / ".config" / "goose" / "config.yaml"
        ).is_file()
        assert (
            std.agents / "codex" / "template" / ".codex" / "config.toml"
        ).is_file()

    def test_unknown_agent_is_skipped(self, std):
        """An agent with no packaged template (e.g. no_agent) is a no-op."""
        install_packaged_templates(std, ["no_agent"])
        assert not (std.agents / "no_agent" / "template").exists()

    def test_create_if_absent_does_not_clobber(self, std):
        """A user-edited template file survives a re-install (create-if-absent)."""
        install_packaged_templates(std, ["claude"])
        instr = std.base_template / "INSTRUCTIONS.md"
        instr.write_text("MY EDITS")
        install_packaged_templates(std, ["claude"])
        assert instr.read_text() == "MY EDITS"

    def test_kanibako_md_not_installed_to_host(self, std):
        """The box guide is delivered live (RO bundle + launch-flatten), NOT
        flat-copied to a host runtime path by the template install (the retired
        ``@system.instructions`` vestige)."""
        install_packaged_templates(std, ["claude"])
        assert not (std.data / "global" / "KANIBAKO.md").exists()


# ---------------------------------------------------------------------------
# Template-update setup-gate arc: content-manifest digest, TRUE-REFRESH copy,
# and the refresh plan partition.
# ---------------------------------------------------------------------------

class TestPackagedTemplatesDigest:
    """``packaged_templates_digest`` — deterministic content hash over the
    packaged base + RO canon (carrying the box guide) + each agent template tree."""

    def _fake_trees(self, monkeypatch, tmp_path, *, base="B", guide="G", claude="C"):
        base_dir = tmp_path / "pbase"
        base_dir.mkdir()
        (base_dir / "INSTRUCTIONS.md").write_text(base)
        # The RO packaged CANON carries the box guide at
        # canon/bible/general/directives/ROM_GENERAL.md — the SOLE manifest
        # source of the guide now (C-CANON R1).
        bundle_dir = tmp_path / "pbundle"
        (bundle_dir / _GUIDE_REL).parent.mkdir(parents=True)
        (bundle_dir / _GUIDE_REL).write_text(guide)
        claude_dir = tmp_path / "pclaude"
        claude_dir.mkdir()
        (claude_dir / ".claude.json").write_text(claude)
        monkeypatch.setattr(
            "kanibako.templates._packaged_base_template", lambda: base_dir
        )
        monkeypatch.setattr(
            "kanibako.templates._packaged_shared_bundle", lambda: bundle_dir
        )
        monkeypatch.setattr(
            "kanibako.templates._packaged_agent_template",
            lambda name: claude_dir if name == "claude" else None,
        )
        return base_dir, bundle_dir, claude_dir

    def test_deterministic_and_order_independent(self, monkeypatch, tmp_path):
        self._fake_trees(monkeypatch, tmp_path)
        d1 = packaged_templates_digest(["claude", "no_agent"])
        d2 = packaged_templates_digest(["no_agent", "claude"])
        assert d1 == d2
        assert len(d1) == 64  # sha256 hex

    def test_changes_when_base_file_changes(self, monkeypatch, tmp_path):
        base_dir, _, _ = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (base_dir / "INSTRUCTIONS.md").write_text("CHANGED")
        assert packaged_templates_digest(["claude"]) != before

    def test_changes_when_guide_changes(self, monkeypatch, tmp_path):
        _, bundle_dir, _ = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (bundle_dir / _GUIDE_REL).write_text("NEW GUIDE")
        assert packaged_templates_digest(["claude"]) != before

    def test_kanibako_md_hashed_exactly_once(self, monkeypatch, tmp_path):
        """Regression: the guide is enumerated ONCE (only via the RO bundle).

        The retired ``@system.instructions`` flat-copy used to add a SECOND
        ``instructions/KANIBAKO.md`` manifest entry beside the bundle's own —
        double-hashing the same bytes so a relocation flipped the digest and
        spuriously tripped the setup gate.
        """
        self._fake_trees(monkeypatch, tmp_path)
        keys = [k for k, _ in _packaged_manifest_entries(["claude"])]
        guide = [k for k in keys if k.endswith("ROM_GENERAL.md")]
        assert guide == [f"shared/{_GUIDE_REL}"], keys

    def test_changes_when_agent_template_changes(self, monkeypatch, tmp_path):
        _, _, claude_dir = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (claude_dir / ".claude.json").write_text("NEW")
        assert packaged_templates_digest(["claude"]) != before

    def test_agent_membership_changes_digest(self, monkeypatch, tmp_path):
        self._fake_trees(monkeypatch, tmp_path)
        # ``claude`` contributes a packaged tree; ``no_agent`` contributes none.
        assert packaged_templates_digest(["claude"]) != packaged_templates_digest(
            ["no_agent"]
        )

    def test_real_packaged_digest_stable(self):
        """Over the REAL packaged data the digest is stable across calls."""
        from kanibako.targets import discover_targets

        names = sorted(discover_targets())
        assert packaged_templates_digest(names) == packaged_templates_digest(names)
        assert len(packaged_templates_digest(names)) == 64


class TestInstallPackagedTemplatesRefresh:
    """``install_packaged_templates(..., refresh=True)`` = TRUE REFRESH: shipped
    files overwritten to current packaged versions; user-only files untouched."""

    def test_refresh_overwrites_changed_shipped_file(self, std):
        install_packaged_templates(std, ["claude"])
        shipped = std.base_template / "playbook" / "CONTENTS.md"
        shipped.write_text("STALE USER EDIT")
        install_packaged_templates(std, ["claude"], refresh=True)
        packaged = (
            _packaged_base_template() / "playbook" / "CONTENTS.md"
        ).read_text()
        assert shipped.read_text() == packaged
        assert shipped.read_text() != "STALE USER EDIT"

    def test_refresh_adds_missing_shipped_file(self, std):
        """A never-installed host: refresh ADDS every shipped file."""
        install_packaged_templates(std, ["claude"], refresh=True)
        assert (std.base_template / "playbook" / "CONTENTS.md").is_file()
        assert (std.agents / "claude" / "template" / ".claude.json").is_file()

    def test_refresh_leaves_user_only_file(self, std):
        install_packaged_templates(std, ["claude"])
        user_file = std.base_template / "MY_NOTES.md"
        user_file.write_text("user only")
        install_packaged_templates(std, ["claude"], refresh=True)
        assert user_file.read_text() == "user only"

    def test_refresh_overwrites_agent_file(self, std):
        install_packaged_templates(std, ["claude"])
        stub = std.agents / "claude" / "template" / ".claude.json"
        stub.write_text("{}")
        install_packaged_templates(std, ["claude"], refresh=True)
        assert stub.read_text() != "{}"


class TestCreateIfAbsentRegression:
    """The create-if-absent default (box-seed path) is UNCHANGED by the refresh
    variant — it must still skip existing files."""

    def test_install_default_still_create_if_absent(self, std):
        install_packaged_templates(std, ["claude"])
        instr = std.base_template / "INSTRUCTIONS.md"
        instr.write_text("MY EDITS")
        install_packaged_templates(std, ["claude"])  # refresh defaults False
        assert instr.read_text() == "MY EDITS"

    def test_public_alias_still_skips_existing(self, tmp_path):
        """``copy_resource_tree_if_absent`` (the box-seed apply reuses it) must
        still leave an existing dest file untouched (the data-loss guard)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("shipped")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "a.txt").write_text("USER OWNED")
        copy_resource_tree_if_absent(src, dest)
        assert (dest / "a.txt").read_text() == "USER OWNED"


class TestPlanTemplateRefresh:
    """``plan_template_refresh`` → (added, overwritten) partition; unchanged
    files and user-only files never appear."""

    def test_all_added_on_empty_host(self, std):
        added, overwritten = plan_template_refresh(std, ["claude"])
        assert overwritten == []
        assert any(p.name == "CONTENTS.md" for p in added)

    def test_unchanged_after_install_is_empty(self, std):
        install_packaged_templates(std, ["claude"])
        added, overwritten = plan_template_refresh(std, ["claude"])
        assert added == []
        assert overwritten == []

    def test_changed_file_is_overwritten_partition(self, std):
        install_packaged_templates(std, ["claude"])
        (std.base_template / "playbook" / "CONTENTS.md").write_text("changed")
        added, overwritten = plan_template_refresh(std, ["claude"])
        assert (std.base_template / "playbook" / "CONTENTS.md") in overwritten
        assert added == []

    def test_missing_file_is_added_partition(self, std):
        install_packaged_templates(std, ["claude"])
        contents = std.base_template / "playbook" / "CONTENTS.md"
        contents.unlink()
        added, overwritten = plan_template_refresh(std, ["claude"])
        assert contents in added

    def test_user_only_file_absent_from_plan(self, std):
        install_packaged_templates(std, ["claude"])
        (std.base_template / "USER.md").write_text("mine")
        added, overwritten = plan_template_refresh(std, ["claude"])
        assert all(p.name != "USER.md" for p in added + overwritten)
