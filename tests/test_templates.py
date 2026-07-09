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
    install_packaged_templates,
    stage_layers,
    template_seed_defaults,
)
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


def _seed(std, proj, *, agent="claude", shares=True):
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
        shares=shares,
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
        # Base layer — the packaged INSTRUCTIONS.md AND the custom marker.
        assert (home / "INSTRUCTIONS.md").is_file()
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
        assert (home / "INSTRUCTIONS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_standalone_has_no_workset_layer(self, std, config, standalone_proj):
        """STANDALONE seeds base + agent only (no workset tier)."""
        install_packaged_templates(std, ["claude"])
        _seed(std, standalone_proj)
        home = standalone_proj.shell_path
        assert (home / "INSTRUCTIONS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_no_agent_box_seeds_base_only(self, std, config, primary_proj):
        """A NO-AGENT box seeds the base layer but NOT the agent layer."""
        install_packaged_templates(std, ["claude"])
        (std.base_template / "base-only.txt").write_text("base")
        _seed(std, primary_proj, agent="")
        home = primary_proj.shell_path
        assert (home / "INSTRUCTIONS.md").is_file()
        assert (home / "base-only.txt").is_file()
        # No agent template layer.
        assert not (home / ".claude.json").exists()

    def test_private_box_keeps_template_layers(self, std, config, primary_proj):
        """shares=False (PRIVATE box) suppresses CREDENTIAL seeds only — the
        template layers are non-credential and STILL seed (D-M4 gate exemption)."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj, shares=False)
        home = primary_proj.shell_path
        assert (home / "INSTRUCTIONS.md").is_file()
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
    def test_base_instructions_landed(self, std):
        """The packaged base INSTRUCTIONS.md is copied to @system.base_template."""
        install_packaged_templates(std, ["claude", "goose", "codex"])
        assert (std.base_template / "INSTRUCTIONS.md").is_file()

    def test_claude_template_landed(self, std):
        """The claude agent template (.claude.json stub + settings + AGENTS.md) is copied."""
        install_packaged_templates(std, ["claude"])
        dest = std.agents / "claude" / "template"
        assert (dest / ".claude.json").is_file()
        assert (dest / ".claude" / "settings.json").is_file()
        assert (dest / ".claude" / "AGENTS.md").is_file()
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

    def test_instructions_default_landed(self, std):
        """The shipped default KANIBAKO.md is installed at @system.instructions."""
        assert not std.instructions.exists()
        install_packaged_templates(std, ["claude"])
        assert std.instructions.is_file()
        assert std.instructions == std.data / "global" / "KANIBAKO.md"
        assert std.instructions.read_text().startswith(
            "# KANIBAKO.md — Operating Guide for Agents in a Kanibako Box"
        )

    def test_instructions_create_if_absent_does_not_clobber(self, std):
        """A user-edited KANIBAKO.md survives a re-install (create-if-absent)."""
        install_packaged_templates(std, ["claude"])
        std.instructions.write_text("MY BOX GUIDE")
        install_packaged_templates(std, ["claude"])
        assert std.instructions.read_text() == "MY BOX GUIDE"
