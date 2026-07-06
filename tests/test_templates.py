"""Tests for kanibako.templates."""

from __future__ import annotations

import pytest

from kanibako.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.templates import (
    agent_template_dir,
    base_template_dir,
    stage_and_seed_templates,
    template_layer_specs,
    workset_template_dir,
)
from kanibako.workset import add_project, create_workset


class TestStageAndSeedTemplates:
    """The TEMP-STORE stage+seed: per-file last-wins merged in staging, then
    seeded into home with create-if-absent (never clobbers an existing file)."""

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

        stage_and_seed_templates(home, [base, agent, workset])

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

        A marker-less migrated box has a home full of user-edited files; a layer
        shipping the same relative path must NOT overwrite the user's content
        (create-if-absent seed).  This is the load-bearing data-loss guard.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "shared.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "shared.txt": "base version",
            "base-only.txt": "base",
        })

        stage_and_seed_templates(home, [base])

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

        stage_and_seed_templates(home, [agent])

        assert (home / ".claude" / "settings.json").read_text() == "user settings"

    def test_new_layer_files_land_in_nonempty_home(self, tmp_path):
        """Files unique to a layer are seeded into an existing non-empty home."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "user-edited.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "base-only.txt": "base",
            "nested/deep.txt": "deep",
        })

        stage_and_seed_templates(home, [base])

        # Pre-existing user file untouched.
        assert (home / "user-edited.txt").read_text() == "user changes"
        # New unique files (including nested) land.
        assert (home / "base-only.txt").read_text() == "base"
        assert (home / "nested" / "deep.txt").read_text() == "deep"

    def test_nested_directories(self, tmp_path):
        """Layers with nested directory structure are seeded correctly."""
        home = tmp_path / "home"
        home.mkdir()
        agent = tmp_path / "agent"
        nested = agent / ".claude"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("# Instructions")

        stage_and_seed_templates(home, [agent])

        assert (home / ".claude" / "CLAUDE.md").read_text() == "# Instructions"

    def test_no_layers_is_noop(self, tmp_path):
        """No layers -> home is untouched."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "existing.txt").write_text("untouched")

        stage_and_seed_templates(home, [])

        assert (home / "existing.txt").read_text() == "untouched"
        assert sorted(p.name for p in home.iterdir()) == ["existing.txt"]


# ---------------------------------------------------------------------------
# Layered-template path resolution (Phase 7a) — pure derivations, no apply.
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


class TestBaseTemplateDir:
    def test_is_flat_base_template_root(self, std):
        """Layer 1 reads @system.base_template FLAT (no general/ subdir)."""
        assert base_template_dir(std) == std.base_template


class TestAgentTemplateDir:
    def test_derived_under_agents_store(self, std):
        """Layer 2 = @system.agents/<agent>/template."""
        assert agent_template_dir(std, "claude") == std.agents / "claude" / "template"

    def test_agent_name_varies(self, std):
        assert agent_template_dir(std, "goose") == std.agents / "goose" / "template"


class TestWorksetTemplateDir:
    def test_primary_roots_at_primary_workset(self, primary_proj, std):
        assert (
            workset_template_dir(primary_proj, std)
            == std.primary_workset / "template"
        )

    def test_named_roots_at_workset_root(self, named_proj, std):
        assert (
            workset_template_dir(named_proj, std)
            == named_proj.group.root / "template"
        )

    def test_standalone_is_none(self, standalone_proj, std):
        assert workset_template_dir(standalone_proj, std) is None


class _FakeTarget:
    """Minimal stand-in for a resolved agent ``Target`` (only ``.name`` is read)."""

    def __init__(self, name):
        self.name = name


class TestTemplateLayerSpecs:
    """The pure ordered-layer resolver: LOWEST -> HIGHEST = base, agent, workset;
    any layer whose source is None/absent is skipped."""

    def _mk(self, path):
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_primary_orders_base_agent_workset(self, primary_proj, std):
        """All three layers present -> [base, agent, workset] in that order."""
        target = _FakeTarget("claude")
        base = self._mk(base_template_dir(std))
        agent = self._mk(agent_template_dir(std, "claude"))
        workset = self._mk(workset_template_dir(primary_proj, std))

        specs = template_layer_specs(target, primary_proj, std)

        assert specs == [base, agent, workset]

    def test_no_agent_target_skips_agent_layer(self, primary_proj, std):
        """A None target (no-agent box) omits the layer-2 agent template."""
        base = self._mk(base_template_dir(std))
        workset = self._mk(workset_template_dir(primary_proj, std))
        # Agent dir exists on disk but no target -> still skipped.
        self._mk(agent_template_dir(std, "claude"))

        specs = template_layer_specs(None, primary_proj, std)

        assert specs == [base, workset]

    def test_standalone_skips_workset_layer(self, standalone_proj, std):
        """STANDALONE: workset_template_dir() is None -> only [base, agent]."""
        target = _FakeTarget("claude")
        base = self._mk(base_template_dir(std))
        agent = self._mk(agent_template_dir(std, "claude"))

        specs = template_layer_specs(target, standalone_proj, std)

        assert specs == [base, agent]

    def test_absent_layer_dir_skipped(self, primary_proj, std):
        """A layer whose source dir does not exist on disk is skipped."""
        target = _FakeTarget("claude")
        base = self._mk(base_template_dir(std))
        # agent + workset dirs deliberately NOT created -> absent -> skipped.

        specs = template_layer_specs(target, primary_proj, std)

        assert specs == [base]

    def test_empty_when_no_layer_dirs_exist(self, primary_proj, std):
        """No layer dirs on disk at all -> empty list (nothing to seed)."""
        target = _FakeTarget("claude")
        assert template_layer_specs(target, primary_proj, std) == []


# ---------------------------------------------------------------------------
# Packaged curated-template install + fresh-box seeding (Phase 9c).
# ---------------------------------------------------------------------------

from kanibako.templates import install_packaged_templates  # noqa: E402


class TestInstallPackagedTemplates:
    def test_base_instructions_landed(self, std):
        """The packaged base INSTRUCTIONS.md is copied to @system.base_template."""
        install_packaged_templates(std, ["claude", "goose", "codex"])
        assert (std.base_template / "INSTRUCTIONS.md").is_file()

    def test_claude_template_landed(self, std):
        """The claude agent template (.claude.json stub + settings + AGENTS.md) is copied."""
        install_packaged_templates(std, ["claude"])
        dest = agent_template_dir(std, "claude")
        assert (dest / ".claude.json").is_file()
        assert (dest / ".claude" / "settings.json").is_file()
        # STEP 2b — the editable user-instructions stub the loader @import's.
        assert (dest / ".claude" / "AGENTS.md").is_file()
        # The old home-root CLAUDE.md "Project notes" stub is gone (its user-notes
        # role moved to ~/.claude/AGENTS.md; the ~/.claude/CLAUDE.md loader is an RO
        # bind, NOT a seeded template file).
        assert not (dest / "CLAUDE.md").exists()
        # The onboarding stub marks onboarding complete.
        import json
        data = json.loads((dest / ".claude.json").read_text())
        assert data.get("hasCompletedOnboarding") is True

    def test_claude_agents_md_seeds_create_if_absent(self, std, tmp_path):
        """STEP 2b — the editable ~/.claude/AGENTS.md seeds once, never clobbering edits.

        It lands in a fresh box home via the agent template layer, and a subsequent
        re-seed leaves a user's edits intact (create-if-absent).
        """
        install_packaged_templates(std, ["claude"])
        home = tmp_path / "box-home"
        layers = [agent_template_dir(std, "claude")]
        stage_and_seed_templates(home, layers)
        seeded = home / ".claude" / "AGENTS.md"
        assert seeded.is_file()

        # User edits their instructions; a re-seed must NOT overwrite them.
        seeded.write_text("MY AGENT NOTES")
        stage_and_seed_templates(home, layers)
        assert seeded.read_text() == "MY AGENT NOTES"

    def test_goose_and_codex_templates_landed(self, std):
        install_packaged_templates(std, ["goose", "codex"])
        assert (
            agent_template_dir(std, "goose") / ".config" / "goose" / "config.yaml"
        ).is_file()
        assert (
            agent_template_dir(std, "codex") / ".codex" / "config.toml"
        ).is_file()

    def test_unknown_agent_is_skipped(self, std):
        """An agent with no packaged template (e.g. no_agent) is a no-op."""
        install_packaged_templates(std, ["no_agent"])
        assert not (agent_template_dir(std, "no_agent")).exists()

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
        # Verbatim shipped content (header line of the packaged default).
        assert std.instructions.read_text().startswith(
            "# KANIBAKO.md — Operating Guide for Agents in a Kanibako Box"
        )

    def test_instructions_create_if_absent_does_not_clobber(self, std):
        """A user-edited KANIBAKO.md survives a re-install (create-if-absent)."""
        install_packaged_templates(std, ["claude"])
        std.instructions.write_text("MY BOX GUIDE")
        install_packaged_templates(std, ["claude"])
        assert std.instructions.read_text() == "MY BOX GUIDE"

    def test_fresh_box_seeds_base_and_agent(self, std, tmp_path):
        """End-to-end: install packaged templates, then the layered seed-once
        stage+seed lands the base INSTRUCTIONS.md + the agent files into a box
        home (standalone: no workset layer -> only base + agent are present)."""
        install_packaged_templates(std, ["claude"])
        home = tmp_path / "box-home"
        layers = [
            base_template_dir(std),
            agent_template_dir(std, "claude"),
        ]
        stage_and_seed_templates(home, layers)
        # Base layer seeded.
        assert (home / "INSTRUCTIONS.md").is_file()
        # Agent layer seeded.
        assert (home / ".claude.json").is_file()
        assert (home / ".claude" / "settings.json").is_file()
